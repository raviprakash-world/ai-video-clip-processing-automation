"""Idempotency hashing (section 21): source file + selected clips + config -> stable key."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.schemas.processing_config import ProcessingConfig
from app.schemas.validated import ValidatedClip

_CHUNK_SIZE = 1024 * 1024


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def hash_clips(clips: list[ValidatedClip]) -> str:
    canonical = json.dumps(
        [c.to_metadata_dict() for c in sorted(clips, key=lambda c: c.clip_id)],
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_config(config: ProcessingConfig) -> str:
    canonical = json.dumps(config.canonical(), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_idempotency_key(source_hash: str, clips_hash: str, config_hash: str) -> str:
    combined = f"{source_hash}:{clips_hash}:{config_hash}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
