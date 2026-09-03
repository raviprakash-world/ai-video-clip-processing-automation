"""
The FFmpeg processing engine (sections 9-14).

This is the ONLY module that assembles ffmpeg command lines. It accepts
exactly two AI-derived values -- clip.start_seconds and clip.end_seconds
(both already deterministically recomputed and range-checked well before
this runs) -- plus clip_id/rank for filenames. Every other input is
operator-controlled config or ffprobe-derived source metadata. See section 23.

Extraction uses a two-stage seek (coarse -ss before -i, accurate residual
-ss after -i) so re-encoded output starts at the exact requested frame
without paying the cost of decoding from the start of a long source file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Optional

from app.cropping.strategies import build_crop_filter
from app.ffmpeg_utils.runner import run_ffmpeg_with_progress
from app.jobs.models import ClipJob
from app.output.naming import clip_output_basename
from app.output.validator import validate_output_clip
from app.schemas.processing_config import ProcessingConfig, WatermarkMode
from app.video_metadata.probe import VideoMetadata
from app.watermark.overlay import build_overlay_filter, build_remove_region_filter

_COARSE_SEEK_MARGIN_SECONDS = 2.0


def _build_filter_complex(config: ProcessingConfig, overlay_present: bool) -> tuple[str, str]:
    parts: list[str] = []
    video_label = "0:v"

    if config.watermark.mode == WatermarkMode.AUTHORIZED_REMOVE:
        parts.append(build_remove_region_filter(config.watermark, input_label=video_label))
        video_label = "delogoed"

    parts.append(build_crop_filter(config.crop_strategy, width=config.output_width, height=config.output_height, input_label=video_label))
    video_label = "cropped"

    if config.watermark.mode == WatermarkMode.AUTHORIZED_OVERLAY and overlay_present:
        parts.append(build_overlay_filter(config.watermark, base_label=video_label, output_label="wmapplied"))
        video_label = "wmapplied"

    if config.fps:
        parts.append(f"[{video_label}]fps={config.fps}[fpsed]")
        video_label = "fpsed"

    return ";".join(parts), video_label


def build_ffmpeg_args(
    *,
    source_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    config: ProcessingConfig,
    source_audio_present: bool,
    source_audio_codec: Optional[str],
    overlay_path: Optional[Path],
) -> list[str]:
    coarse_seek = max(0.0, start_seconds - _COARSE_SEEK_MARGIN_SECONDS)
    residual_seek = start_seconds - coarse_seek

    args: list[str] = ["-hide_banner", "-y", "-ss", f"{coarse_seek:.3f}", "-i", str(source_path)]

    overlay_present = config.watermark.mode == WatermarkMode.AUTHORIZED_OVERLAY and overlay_path is not None
    if overlay_present:
        args += ["-i", str(overlay_path)]

    args += ["-ss", f"{residual_seek:.3f}", "-t", f"{duration_seconds:.3f}"]

    filter_complex, final_video_label = _build_filter_complex(config, overlay_present)
    args += ["-filter_complex", filter_complex, "-map", f"[{final_video_label}]"]

    if source_audio_present:
        args += ["-map", "0:a?"]
        if source_audio_codec == config.audio_codec:
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", config.audio_codec, "-b:a", "192k"]
    else:
        args += ["-an"]

    args += [
        "-c:v", config.video_codec,
        "-crf", str(config.crf),
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        "-avoid_negative_ts", "make_zero",
        "-movflags", "+faststart",
        str(output_path),
        "-progress", "pipe:1",
        "-nostats",
    ]
    return args


async def process_clip(
    *,
    job_dir: Path,
    source_path: Path,
    source_meta: VideoMetadata,
    clip_job: ClipJob,
    config: ProcessingConfig,
    overlay_path: Optional[Path],
    on_progress: Callable[[float], Awaitable[None]],
    timeout_seconds: float,
) -> Path:
    """Runs the full extract->crop->watermark->encode pipeline for one clip and validates output."""
    clip = clip_job.clip
    basename = clip_output_basename(clip.clip_id, clip.rank, clip.viral_score)
    output_path = job_dir / f"{basename}.mp4"

    args = build_ffmpeg_args(
        source_path=source_path,
        output_path=output_path,
        start_seconds=clip.start_seconds,
        duration_seconds=clip.duration_seconds,
        config=config,
        source_audio_present=source_meta.audio_present,
        source_audio_codec=source_meta.audio_codec,
        overlay_path=overlay_path,
    )

    await run_ffmpeg_with_progress(
        args,
        total_duration_seconds=clip.duration_seconds,
        on_progress=on_progress,
        timeout=timeout_seconds,
    )

    await validate_output_clip(
        output_path,
        expected_duration_seconds=clip.duration_seconds,
        expected_width=config.output_width,
        expected_height=config.output_height,
        source_audio_present=source_meta.audio_present,
    )

    return output_path
