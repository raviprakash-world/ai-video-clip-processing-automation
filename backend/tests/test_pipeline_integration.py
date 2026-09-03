"""
End-to-end integration test against the real ffmpeg/ffprobe binaries.

Generates a small synthetic source video with ffmpeg's testsrc/sine
generators (no external fixture files needed), runs it through the full
extract -> crop -> encode -> validate pipeline for both crop strategies,
and asserts on the real output file's probed properties. Skips cleanly if
ffmpeg/ffprobe are not on PATH.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from app.clip_processing.pipeline import process_clip
from app.jobs.models import ClipJob
from app.schemas.processing_config import CropStrategy, ProcessingConfig
from app.schemas.validated import ValidatedClip
from app.video_metadata.probe import probe_video

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available on PATH",
)


def _make_synthetic_video(path: Path, duration_seconds: int = 20) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration_seconds}:size=1280x720:rate=30",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _make_clip(clip_id: str, start_seconds: int, duration_seconds: int) -> ValidatedClip:
    return ValidatedClip(
        clip_id=clip_id,
        rank=1,
        start_time=f"00:00:{start_seconds:02d}",
        end_time=f"00:00:{start_seconds + duration_seconds:02d}",
        start_seconds=start_seconds,
        end_seconds=start_seconds + duration_seconds,
        duration_seconds=duration_seconds,
        ai_reported_duration_seconds=duration_seconds,
        duration_mismatch=False,
        viral_score=90,
        category="test",
        speaker="Test",
        hook="hook",
        title_options={},
        caption="caption",
        hashtags=[],
        reason="reason",
        payoff="payoff",
        context_warning=None,
        copyright_warning=None,
    )


@pytest.mark.asyncio
async def test_center_crop_pipeline_produces_valid_vertical_clip(tmp_path: Path):
    source_path = tmp_path / "source.mp4"
    _make_synthetic_video(source_path, duration_seconds=15)
    source_meta = await probe_video(source_path)
    assert source_meta.audio_present is True

    clip = _make_clip("clip_001", start_seconds=2, duration_seconds=5)
    clip_job = ClipJob(clip=clip)
    config = ProcessingConfig(output_width=1080, output_height=1920, crop_strategy=CropStrategy.CENTER_CROP)

    progress_values: list[float] = []

    async def on_progress(pct: float) -> None:
        progress_values.append(pct)

    output_path = await process_clip(
        job_dir=tmp_path,
        source_path=source_path,
        source_meta=source_meta,
        clip_job=clip_job,
        config=config,
        overlay_path=None,
        on_progress=on_progress,
        timeout_seconds=60,
    )

    assert output_path.exists()
    out_meta = await probe_video(output_path)
    assert out_meta.width == 1080
    assert out_meta.height == 1920
    assert abs(out_meta.duration_seconds - 5) < 1.0
    assert out_meta.audio_present is True
    assert progress_values, "expected real ffmpeg progress callbacks, not zero"


@pytest.mark.asyncio
async def test_fit_with_blur_background_pipeline(tmp_path: Path):
    source_path = tmp_path / "source.mp4"
    _make_synthetic_video(source_path, duration_seconds=10)
    source_meta = await probe_video(source_path)

    clip = _make_clip("clip_002", start_seconds=1, duration_seconds=3)
    clip_job = ClipJob(clip=clip)
    config = ProcessingConfig(output_width=720, output_height=1280, crop_strategy=CropStrategy.FIT_WITH_BLUR_BACKGROUND)

    async def on_progress(pct: float) -> None:
        pass

    output_path = await process_clip(
        job_dir=tmp_path,
        source_path=source_path,
        source_meta=source_meta,
        clip_job=clip_job,
        config=config,
        overlay_path=None,
        on_progress=on_progress,
        timeout_seconds=60,
    )

    out_meta = await probe_video(output_path)
    assert out_meta.width == 720
    assert out_meta.height == 1280


@pytest.mark.asyncio
async def test_video_without_audio_is_handled_gracefully(tmp_path: Path):
    source_path = tmp_path / "source_no_audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=640x360:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(source_path),
        ],
        check=True,
        capture_output=True,
    )
    source_meta = await probe_video(source_path)
    assert source_meta.audio_present is False

    clip = _make_clip("clip_003", start_seconds=0, duration_seconds=3)
    clip_job = ClipJob(clip=clip)
    config = ProcessingConfig()

    async def on_progress(pct: float) -> None:
        pass

    output_path = await process_clip(
        job_dir=tmp_path,
        source_path=source_path,
        source_meta=source_meta,
        clip_job=clip_job,
        config=config,
        overlay_path=None,
        on_progress=on_progress,
        timeout_seconds=60,
    )
    out_meta = await probe_video(output_path)
    assert out_meta.audio_present is False
