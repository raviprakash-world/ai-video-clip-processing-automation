"""Internal, trusted representations produced only by the validation layer."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.schemas.analysis import AnalysisMeta


@dataclass(frozen=True)
class ValidatedClip:
    """A clip that has passed schema + timestamp validation.

    start_seconds/end_seconds/duration_seconds are DETERMINISTICALLY
    recalculated from start_time/end_time and are the only timing values
    ever passed to ffmpeg. Every other field is descriptive metadata.
    """

    clip_id: str
    rank: int
    start_time: str
    end_time: str
    start_seconds: int
    end_seconds: int
    duration_seconds: int
    ai_reported_duration_seconds: int
    duration_mismatch: bool
    viral_score: int
    category: str
    speaker: str
    hook: str
    title_options: dict[str, str]
    caption: str
    hashtags: list[str]
    reason: str
    payoff: str
    context_warning: Optional[str]
    copyright_warning: Optional[str]
    extra: dict[str, Any] = field(default_factory=dict)

    def to_metadata_dict(self) -> dict[str, Any]:
        return {
            "clip_id": self.clip_id,
            "rank": self.rank,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_seconds": self.duration_seconds,
            "viral_score": self.viral_score,
            "category": self.category,
            "speaker": self.speaker,
            "hook": self.hook,
            "title_options": self.title_options,
            "caption": self.caption,
            "hashtags": self.hashtags,
            "reason": self.reason,
            "payoff": self.payoff,
            "context_warning": self.context_warning,
            "copyright_warning": self.copyright_warning,
        }


@dataclass(frozen=True)
class ValidatedAnalysis:
    schema_version: str
    analysis: AnalysisMeta
    clips: list[ValidatedClip]
