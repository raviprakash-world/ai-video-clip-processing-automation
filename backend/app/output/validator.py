"""Post-encode output validation (section 20). Only after this passes may a clip be COMPLETED."""
from __future__ import annotations

from pathlib import Path

from app.errors import OutputValidationFailedError
from app.video_metadata.probe import VideoMetadata, probe_video

_DURATION_TOLERANCE_SECONDS = 1.5
_DIMENSION_TOLERANCE_PX = 2


async def validate_output_clip(
    output_path: Path,
    *,
    expected_duration_seconds: float,
    expected_width: int,
    expected_height: int,
    source_audio_present: bool,
) -> VideoMetadata:
    if not output_path.exists():
        raise OutputValidationFailedError(f"Output file does not exist: {output_path.name}")
    if output_path.stat().st_size == 0:
        raise OutputValidationFailedError(f"Output file is empty: {output_path.name}")

    metadata = await probe_video(output_path)

    if abs(metadata.duration_seconds - expected_duration_seconds) > _DURATION_TOLERANCE_SECONDS:
        raise OutputValidationFailedError(
            f"Output duration {metadata.duration_seconds:.2f}s does not match the "
            f"requested {expected_duration_seconds:.2f}s (tolerance {_DURATION_TOLERANCE_SECONDS}s).",
            details={"output_file": output_path.name, "actual": metadata.duration_seconds, "expected": expected_duration_seconds},
        )

    if (
        abs(metadata.width - expected_width) > _DIMENSION_TOLERANCE_PX
        or abs(metadata.height - expected_height) > _DIMENSION_TOLERANCE_PX
    ):
        raise OutputValidationFailedError(
            f"Output resolution {metadata.width}x{metadata.height} does not match the "
            f"requested {expected_width}x{expected_height}.",
            details={"output_file": output_path.name, "actual": [metadata.width, metadata.height], "expected": [expected_width, expected_height]},
        )

    if source_audio_present and not metadata.audio_present:
        raise OutputValidationFailedError(
            f"Output file '{output_path.name}' is missing an audio stream even though the source had audio.",
            details={"output_file": output_path.name},
        )

    return metadata
