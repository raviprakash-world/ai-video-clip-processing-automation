"""Extract and represent source/output video metadata via ffprobe."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.errors import FfprobeFailedError, NoVideoStreamError, VideoCorruptedError
from app.ffmpeg_utils.runner import run_ffprobe


@dataclass(frozen=True)
class VideoMetadata:
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    audio_codec: Optional[str]
    audio_present: bool
    size_bytes: int

    @property
    def aspect_ratio(self) -> float:
        return self.width / self.height if self.height else 0.0


def _parse_fps(rate: str) -> float:
    if not rate or rate == "0/0":
        return 0.0
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            num_f, den_f = float(num), float(den)
            return num_f / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(rate)
    except ValueError:
        return 0.0


async def probe_video(path: Path) -> VideoMetadata:
    if not path.exists() or path.stat().st_size == 0:
        raise VideoCorruptedError(f"Video file is missing or empty: {path.name}")

    args = [
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        result = await run_ffprobe(args)
    except FfprobeFailedError as exc:
        raise VideoCorruptedError(
            f"The video file '{path.name}' could not be read (corrupted or unsupported format).",
            details=exc.details,
        ) from exc

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FfprobeFailedError(f"ffprobe returned unparseable output for '{path.name}'.") from exc

    streams = data.get("streams", [])
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

    if not video_streams:
        raise NoVideoStreamError(f"'{path.name}' does not contain a video stream.")

    v = video_streams[0]
    fmt = data.get("format", {})

    duration_raw = fmt.get("duration") or v.get("duration")
    if duration_raw is None:
        raise VideoCorruptedError(f"Could not determine duration for '{path.name}'.")

    return VideoMetadata(
        duration_seconds=float(duration_raw),
        width=int(v.get("width") or 0),
        height=int(v.get("height") or 0),
        fps=_parse_fps(v.get("r_frame_rate", "0/0")),
        video_codec=v.get("codec_name", "unknown"),
        audio_codec=(audio_streams[0].get("codec_name") if audio_streams else None),
        audio_present=bool(audio_streams),
        size_bytes=int(fmt.get("size") or path.stat().st_size),
    )
