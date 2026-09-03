"""
Job orchestration (sections 17, 18, 21, 25).

- One failed clip never aborts the others (each clip runs independently and
  catches its own exceptions).
- Clip processing across the whole app is limited by a single semaphore
  (MAX_CONCURRENT_JOBS) so we don't fork unbounded ffmpeg processes.
- Idempotent: identical (source file, selected clips, config) reuses a
  previously COMPLETED job instead of reprocessing.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Optional

from app.clip_processing.pipeline import process_clip
from app.config import settings
from app.errors import NotFoundError
from app.jobs.hashing import compute_idempotency_key, hash_clips, hash_config, hash_file
from app.jobs.models import ClipJob, ClipStatus, JobStatus, ProcessingJob
from app.json_validation.video_bounds import check_all_clips
from app.output.metadata_writer import write_clip_metadata
from app.schemas.processing_config import ProcessingConfig
from app.schemas.validated import ValidatedClip
from app.storage.paths import job_dir as job_dir_path
from app.storage.paths import validate_id
from app.video_ingestion.local_source import LocalFileSource
from app.video_metadata.probe import probe_video

logger = logging.getLogger("clip_pipeline.jobs")


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, ProcessingJob] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_JOBS)
        self._idempotency_index: dict[str, str] = self._load_index()
        self._index_lock = asyncio.Lock()

    # -- idempotency index persistence -------------------------------------------------
    def _load_index(self) -> dict[str, str]:
        if settings.IDEMPOTENCY_INDEX_PATH.exists():
            try:
                return json.loads(settings.IDEMPOTENCY_INDEX_PATH.read_text())
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    async def _save_index(self) -> None:
        async with self._index_lock:
            settings.IDEMPOTENCY_INDEX_PATH.write_text(json.dumps(self._idempotency_index, indent=2))

    # -- lookups -------------------------------------------------------------------------
    def get_job(self, job_id: str) -> ProcessingJob:
        job = self._jobs.get(validate_id(job_id, kind="job_id"))
        if not job:
            raise NotFoundError(f"No job found with id '{job_id}'.")
        return job

    def list_jobs(self) -> list[ProcessingJob]:
        return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    # -- creation --------------------------------------------------------------------------
    async def create_job(
        self,
        *,
        upload_path: Path,
        selected_clips: list[ValidatedClip],
        config: ProcessingConfig,
    ) -> ProcessingJob:
        source_hash = hash_file(upload_path)
        clips_hash = hash_clips(selected_clips)
        config_hash = hash_config(config)
        idempotency_key = compute_idempotency_key(source_hash, clips_hash, config_hash)

        existing_job_id = self._idempotency_index.get(idempotency_key)
        if existing_job_id and existing_job_id in self._jobs:
            existing = self._jobs[existing_job_id]
            if existing.status == JobStatus.COMPLETED:
                existing.reused = True
                return existing

        job_id = uuid.uuid4().hex[:16]
        jdir = job_dir_path(job_id)
        jdir.mkdir(parents=True, exist_ok=True)

        job = ProcessingJob(
            job_id=job_id,
            idempotency_key=idempotency_key,
            config=config,
            clips={c.clip_id: ClipJob(clip=c) for c in selected_clips},
            status=JobStatus.QUEUED,
        )
        self._jobs[job_id] = job

        task = asyncio.create_task(self._run_job(job, upload_path, jdir))
        self._tasks[job_id] = task
        return job

    def cancel_job(self, job_id: str) -> ProcessingJob:
        job = self.get_job(job_id)
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        job.status = JobStatus.CANCELLED
        for clip_job in job.clips.values():
            if clip_job.status in (ClipStatus.QUEUED, ClipStatus.PROCESSING):
                clip_job.status = ClipStatus.CANCELLED
        return job

    # -- execution -----------------------------------------------------------------------
    async def _run_job(self, job: ProcessingJob, upload_path: Path, jdir: Path) -> None:
        try:
            job.status = JobStatus.DOWNLOADING
            source = LocalFileSource(upload_path)
            source_path = await source.obtain(jdir)
            job.status = JobStatus.DOWNLOADED

            job.status = JobStatus.VALIDATING
            source_meta = await probe_video(source_path)
            job.source_video = source_meta

            out_of_range = check_all_clips([cj.clip for cj in job.clips.values()], source_meta)
            for clip_id, exc in out_of_range.items():
                clip_job = job.clips[clip_id]
                clip_job.status = ClipStatus.FAILED
                clip_job.error = exc.to_dict()
                write_clip_metadata(job_dir=jdir, clip=clip_job.clip, output_file=None, processing_status="failed", error=exc.to_dict())

            job.status = JobStatus.PROCESSING
            runnable = [cj for cj in job.clips.values() if cj.status == ClipStatus.QUEUED]

            overlay_path: Optional[Path] = None
            if job.config.watermark.overlay_asset_id:
                asset_id = validate_id(job.config.watermark.overlay_asset_id, kind="overlay_asset_id")
                candidate = settings.UPLOADS_DIR / f"{asset_id}.png"
                if candidate.exists():
                    overlay_path = candidate

            await asyncio.gather(*(self._run_one_clip(job, jdir, source_path, source_meta, cj, overlay_path) for cj in runnable))

            statuses = {cj.status for cj in job.clips.values()}
            if statuses <= {ClipStatus.COMPLETED}:
                job.status = JobStatus.COMPLETED
            elif ClipStatus.COMPLETED in statuses:
                job.status = JobStatus.COMPLETED  # partial success: see per-clip status for failures
            else:
                job.status = JobStatus.FAILED
                job.error = {"error": "ALL_CLIPS_FAILED", "message": "Every clip in this job failed to process."}

            if job.status == JobStatus.COMPLETED:
                self._idempotency_index[job.idempotency_key] = job.job_id
                await self._save_index()

        except asyncio.CancelledError:
            job.status = JobStatus.CANCELLED
            raise
        except Exception as exc:  # noqa: BLE001 - top-level job guard; per-clip errors are handled below
            logger.exception("Job %s failed", job.job_id)
            job.status = JobStatus.FAILED
            code = getattr(exc, "code", "UNKNOWN_ERROR")
            message = getattr(exc, "message", str(exc))
            details = getattr(exc, "details", {})
            job.error = {"error": code, "message": message, "details": details}

    async def _run_one_clip(self, job, jdir, source_path, source_meta, clip_job: ClipJob, overlay_path: Optional[Path]) -> None:
        clip_job.status = ClipStatus.PROCESSING

        async def _progress(pct: float) -> None:
            clip_job.progress_pct = pct

        async with self._semaphore:
            try:
                output_path = await process_clip(
                    job_dir=jdir,
                    source_path=source_path,
                    source_meta=source_meta,
                    clip_job=clip_job,
                    config=job.config,
                    overlay_path=overlay_path,
                    on_progress=_progress,
                    timeout_seconds=settings.PROCESSING_TIMEOUT_SECONDS,
                )
                clip_job.status = ClipStatus.COMPLETED
                clip_job.output_file = output_path.name
                clip_job.progress_pct = 100.0
                write_clip_metadata(job_dir=jdir, clip=clip_job.clip, output_file=output_path.name, processing_status="completed")
            except asyncio.CancelledError:
                clip_job.status = ClipStatus.CANCELLED
                raise
            except Exception as exc:  # noqa: BLE001 - isolate this clip's failure from the rest of the job
                logger.exception("Clip %s in job %s failed", clip_job.clip.clip_id, job.job_id)
                clip_job.status = ClipStatus.FAILED
                code = getattr(exc, "code", "UNKNOWN_ERROR")
                message = getattr(exc, "message", str(exc))
                details = getattr(exc, "details", {})
                clip_job.error = {"error": code, "message": message, "details": details}
                write_clip_metadata(job_dir=jdir, clip=clip_job.clip, output_file=None, processing_status="failed", error=clip_job.error)


job_manager = JobManager()
