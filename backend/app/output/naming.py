"""
Filesystem-safe output naming (section 15 / 22).

Only clip_id and integer fields (rank, viral_score) ever touch a filename.
Free-text AI fields (hook, caption, category, title_options, ...) are NEVER
used to build a path -- they are untrusted data that could contain path
separators, null bytes, or traversal sequences.
"""
from __future__ import annotations

import re

_SAFE_RE = re.compile(r"[^A-Za-z0-9_-]+")


def sanitize_component(value: str, *, max_len: int = 64) -> str:
    cleaned = _SAFE_RE.sub("_", value.strip())
    cleaned = cleaned.strip("_.") or "clip"
    return cleaned[:max_len]


def clip_output_basename(clip_id: str, rank: int, viral_score: int) -> str:
    safe_id = sanitize_component(clip_id)
    safe_rank = max(0, int(rank))
    safe_score = max(0, min(100, int(viral_score)))
    return f"{safe_id}_rank_{safe_rank:02d}_score_{safe_score}"
