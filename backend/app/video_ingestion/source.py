"""
VideoSource abstraction (section 6).

The clip-processing engine only ever deals with a local file path. How that
file arrived -- upload, direct URL, YouTube, cloud storage -- is entirely
encapsulated behind this interface so the processing engine never changes
when a new source type is added.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional

ProgressCallback = Callable[[int, Optional[int]], None]


class VideoSource(ABC):
    """A source that can materialize itself as a local file."""

    source_type: str

    @abstractmethod
    async def obtain(self, dest_dir: Path, on_progress: Optional[ProgressCallback] = None) -> Path:
        """Place the source video inside dest_dir and return its path.

        Implementations MUST NOT begin returning a path until the file is
        completely and verifiably downloaded/copied (section 7: never let
        the processing engine start on an incomplete file).

        `on_progress`, when given, is called with (bytes_so_far, total_bytes)
        -- total_bytes is None when it isn't known in advance. Implementations
        that have no meaningful "downloading" phase (e.g. a plain local copy)
        may simply ignore it.
        """
        raise NotImplementedError
