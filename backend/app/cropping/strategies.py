"""
Modular crop/reframe strategies (section 11).

Each strategy takes the input label(s) already present in an ffmpeg
filter_complex graph and returns (filter_fragment, output_label). Never
distorts aspect ratio: CENTER_CROP scales-to-cover then crops; the blur
strategy scales-to-fit and pads with a blurred, scaled-to-cover copy of the
same frame -- no stretching in either case.

Adding FACE_TRACKING / SPEAKER_TRACKING / CUSTOM_CROP later means adding a
new function here and a new enum value in app.schemas.processing_config; the
ffmpeg invocation code in clip_processing/pipeline.py does not change.
"""
from __future__ import annotations

from app.schemas.processing_config import CropStrategy


def build_crop_filter(strategy: CropStrategy, *, width: int, height: int, input_label: str = "0:v") -> str:
    if strategy == CropStrategy.CENTER_CROP:
        return _center_crop(width, height, input_label)
    if strategy == CropStrategy.FIT_WITH_BLUR_BACKGROUND:
        return _fit_with_blur_background(width, height, input_label)
    raise ValueError(f"Unknown crop strategy: {strategy}")


def _center_crop(width: int, height: int, input_label: str) -> str:
    return (
        f"[{input_label}]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},setsar=1[cropped]"
    )


def _fit_with_blur_background(width: int, height: int, input_label: str) -> str:
    return (
        f"[{input_label}]split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=25,eq=brightness=-0.05[bg];"
        f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2:format=auto,setsar=1[cropped]"
    )
