"""User-controlled (never AI-controlled) processing configuration for a job."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class CropStrategy(str, Enum):
    CENTER_CROP = "CENTER_CROP"
    FIT_WITH_BLUR_BACKGROUND = "FIT_WITH_BLUR_BACKGROUND"


class WatermarkMode(str, Enum):
    NONE = "none"
    AUTHORIZED_OVERLAY = "authorized_overlay"
    AUTHORIZED_REMOVE = "authorized_remove"


class WatermarkConfig(BaseModel):
    mode: WatermarkMode = WatermarkMode.NONE
    authorized: bool = False
    # authorized_overlay: path to an overlay image asset uploaded alongside the job
    overlay_asset_id: Optional[str] = None
    position: str = "bottom_right"  # bottom_right | bottom_left | top_right | top_left | center
    margin_px: int = Field(default=32, ge=0, le=500)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    # authorized_remove: source-pixel-space region to blur out (delogo), user-supplied only
    remove_region: Optional["RemoveRegion"] = None

    @model_validator(mode="after")
    def check_authorization(self) -> "WatermarkConfig":
        if self.mode != WatermarkMode.NONE and not self.authorized:
            raise ValueError(
                "watermark.authorized must be true to use mode "
                f"'{self.mode.value}'. The system will not modify overlays "
                "without explicit user confirmation of authorization."
            )
        if self.mode == WatermarkMode.AUTHORIZED_OVERLAY and not self.overlay_asset_id:
            raise ValueError("watermark.overlay_asset_id is required for mode 'authorized_overlay'.")
        if self.mode == WatermarkMode.AUTHORIZED_REMOVE and not self.remove_region:
            raise ValueError("watermark.remove_region is required for mode 'authorized_remove'.")
        return self


class RemoveRegion(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


WatermarkConfig.model_rebuild()


class ProcessingConfig(BaseModel):
    output_width: int = Field(default=1080, ge=128, le=4096)
    output_height: int = Field(default=1920, ge=128, le=4096)
    crop_strategy: CropStrategy = CropStrategy.CENTER_CROP
    watermark: WatermarkConfig = Field(default_factory=WatermarkConfig)

    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = Field(default=21, ge=0, le=51)
    fps: Optional[int] = Field(default=None, ge=1, le=120)  # None = keep source fps

    def canonical(self) -> dict:
        """Stable dict for idempotency hashing (excludes nothing, sorted by pydantic json)."""
        return self.model_dump(mode="json")
