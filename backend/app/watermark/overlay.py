"""
Authorization-aware watermark/overlay handling (section 12).

Two modes are supported, both gated behind an explicit `authorized: true`
flag the user must set (enforced in app.schemas.processing_config, not
here -- this module trusts that gate has already been checked):

  - authorized_overlay: composite a user-supplied image (their own
    branding) onto the output.
  - authorized_remove: blur out a user-specified pixel region of the
    SOURCE video (e.g. their own placeholder graphic). This is a manual,
    user-drawn region -- never anything inferred from AI metadata or
    automatic content analysis.

This module never attempts to detect or remove third-party watermarks; that
would require content analysis, which is explicitly out of scope (section 2).
"""
from __future__ import annotations

from app.schemas.processing_config import WatermarkConfig, WatermarkMode

_POSITION_EXPR = {
    "bottom_right": "W-w-{m}:H-h-{m}",
    "bottom_left": "{m}:H-h-{m}",
    "top_right": "W-w-{m}:{m}",
    "top_left": "{m}:{m}",
    "center": "(W-w)/2:(H-h)/2",
}


def build_remove_region_filter(config: WatermarkConfig, *, input_label: str = "0:v") -> str:
    """Returns a filter fragment applying delogo BEFORE cropping, output label [delogoed]."""
    assert config.mode == WatermarkMode.AUTHORIZED_REMOVE and config.remove_region
    r = config.remove_region
    return f"[{input_label}]delogo=x={r.x}:y={r.y}:w={r.width}:h={r.height}:show=0[delogoed]"


def build_overlay_filter(config: WatermarkConfig, *, base_label: str, output_label: str = "outv") -> str:
    """Returns a filter fragment compositing the overlay input (index 1) onto base_label.

    Assumes the overlay image has already been added as ffmpeg input index 1.
    """
    assert config.mode == WatermarkMode.AUTHORIZED_OVERLAY
    position_template = _POSITION_EXPR.get(config.position, _POSITION_EXPR["bottom_right"])
    position_expr = position_template.format(m=config.margin_px)
    opacity = max(0.0, min(1.0, config.opacity))
    return (
        f"[1:v]format=rgba,colorchannelmixer=aa={opacity}[wm];"
        f"[{base_label}][wm]overlay={position_expr}:format=auto[{output_label}]"
    )
