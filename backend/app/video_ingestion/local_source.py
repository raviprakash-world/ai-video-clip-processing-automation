from __future__ import annotations

import shutil
from pathlib import Path

from app.errors import VideoNotFoundError
from app.video_ingestion.source import VideoSource


class LocalFileSource(VideoSource):
    """A video already sitting on local disk (e.g. a completed upload)."""

    source_type = "local_file"

    def __init__(self, existing_path: Path):
        self.existing_path = existing_path

    async def obtain(self, dest_dir: Path) -> Path:
        if not self.existing_path.exists():
            raise VideoNotFoundError(f"Uploaded source video was not found: {self.existing_path.name}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / f"source{self.existing_path.suffix}"
        if self.existing_path.resolve() != dest_path.resolve():
            shutil.copy2(self.existing_path, dest_path)
        return dest_path
