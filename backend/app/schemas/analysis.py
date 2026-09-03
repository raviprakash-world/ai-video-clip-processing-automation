"""
Pydantic models for the externally-generated AI analysis JSON (schema_version 1.0).

These models are DATA CONTAINERS ONLY. Nothing here (or downstream) is
permitted to feed AI-authored free text into a shell command or filesystem
path -- see app.output.naming and app.ffmpeg_utils.runner. Only start_time /
end_time (and clip_id / rank, sanitized) may influence ffmpeg invocations.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AnalysisMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    source_type: str
    language: str
    total_clips_found: int
    analysis_status: str


class ClipIn(BaseModel):
    """A single clip as authored by the external AI analysis step, pre-recalculation."""

    model_config = ConfigDict(extra="allow")

    clip_id: str = Field(min_length=1, max_length=128)
    rank: int
    start_time: str
    end_time: str
    duration_seconds: int
    viral_score: int = Field(ge=0, le=100)
    category: str
    speaker: str
    hook: str
    title_options: dict[str, str] = Field(default_factory=dict)
    caption: str
    hashtags: list[str] = Field(default_factory=list)
    reason: str
    payoff: str
    context_warning: Optional[str] = None
    copyright_warning: Optional[str] = None

    @field_validator("hashtags")
    @classmethod
    def hashtags_must_be_list_of_str(cls, v: Any) -> list[str]:
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ValueError("hashtags must be an array of strings")
        return v


class AnalysisDocument(BaseModel):
    """Top-level AI analysis JSON document."""

    model_config = ConfigDict(extra="allow")

    schema_version: str
    analysis: AnalysisMeta
    clips: list[ClipIn]
