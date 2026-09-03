"""
Translation adapters for third-party AI-tool JSON shapes that aren't our
native schema_version document.

CRITICAL BOUNDARY: a translator may read ONLY timing information
(start_time/end_time and a stable step/clip identifier) out of a foreign
document. Anything that looks like an instruction -- an "action" name, an
"execution_instruction", "detect_overlay", pixel regions, or any other
directive -- is at most folded into a human-readable `context_warning`
string for the operator to read. It is NEVER dispatched on, and it never
reaches ffmpeg. If an operator wants watermark removal, they configure it
explicitly through this app's own ProcessingConfig.watermark (with their
own authorized:true confirmation) -- never automatically from a foreign
document's fields. This mirrors section 12's FLAG_FOR_REVIEW behavior:
surface it, never act on it.

Translators produce a dict shaped like our native schema_version "1.0"
document and then flow through the exact same validate() pipeline as a
native document -- duration recalculation, bounds checking, everything.
Nothing downstream of this module needs to know a translation happened.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

_PLACEHOLDER_METADATA = {
    "viral_score": 0,
    "category": "untitled",
    "speaker": "",
    "hook": "",
    "title_options": {},
    "caption": "",
    "hashtags": [],
    "reason": "",
    "payoff": "",
    "copyright_warning": None,
}


def _looks_like_processing_steps_document(data: dict) -> bool:
    # Deliberately requires the ABSENCE of schema_version: a native document
    # (any version) always carries one. Only truly unversioned/foreign
    # documents get translated, so a mis-versioned native document still
    # fails loudly with UNSUPPORTED_SCHEMA_VERSION instead of being silently
    # reinterpreted here.
    return "schema_version" not in data and isinstance(data.get("processing_steps"), list)


def _describe_non_trim_step(step: dict) -> Optional[str]:
    """Best-effort, display-only summary of a step this adapter does not execute."""
    action = step.get("action")
    if action != "handle_watermark":
        return f"Source tool step '{action}' was ignored (not a recognized trim instruction)."

    params = step.get("parameters") or {}
    if not params.get("detect_overlay"):
        return None
    message = params.get("fallback_message") or "Source tool flagged a possible watermark/overlay."
    regions = params.get("known_regions") or []
    region_text = ""
    if regions and isinstance(regions[0], dict):
        r = regions[0]
        region_text = f" Suggested source-pixel region: x={r.get('x')}, y={r.get('y')}, w={r.get('width')}, h={r.get('height')}."
    return (
        f"{message}{region_text} This was NOT acted on automatically -- if you own this "
        "source and want it removed, configure it yourself under Step 4 -> Watermark -> "
        "Authorized overlay/remove."
    )


def _translate_processing_steps(data: dict) -> dict:
    clips: list[dict[str, Any]] = []
    warnings: list[str] = []

    for step in data.get("processing_steps", []):
        if not isinstance(step, dict):
            continue
        if step.get("action") != "trim":
            note = _describe_non_trim_step(step)
            if note:
                warnings.append(note)
            continue

        params = step.get("parameters") or {}
        start_time, end_time = params.get("start_time"), params.get("end_time")
        if not start_time or not end_time:
            continue

        step_number = step.get("step", len(clips) + 1)
        clips.append(
            {
                "clip_id": f"step_{step_number}",
                "rank": len(clips) + 1,
                "start_time": start_time,
                "end_time": end_time,
                "duration_seconds": 0,  # recalculated deterministically downstream regardless of this value
                "context_warning": " ".join(warnings) or None,
                **_PLACEHOLDER_METADATA,
            }
        )
        warnings = []  # attach each warning to the clip immediately following it

    # Any warnings with no subsequent trim step still deserve to reach the operator.
    if warnings and clips:
        clips[-1]["context_warning"] = (
            (clips[-1]["context_warning"] + " " if clips[-1]["context_warning"] else "") + " ".join(warnings)
        )

    return {
        "schema_version": "1.0",
        "analysis": {
            "source_type": "video",
            "language": "unknown",
            "total_clips_found": len(clips),
            "analysis_status": "complete",
        },
        "clips": clips,
    }


# Registry of (detector, translator) pairs, checked in order. Add a new
# foreign format by appending a pair here -- nothing else changes.
_FOREIGN_FORMATS: list[tuple[Callable[[dict], bool], Callable[[dict], dict]]] = [
    (_looks_like_processing_steps_document, _translate_processing_steps),
]


def translate_if_foreign(data: dict) -> Optional[dict]:
    """Returns a native-shaped dict if `data` matches a known foreign format, else None."""
    for detector, translator in _FOREIGN_FORMATS:
        if detector(data):
            return translator(data)
    return None
