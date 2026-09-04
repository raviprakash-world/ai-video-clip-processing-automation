import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

import app.db.base as db_base  # noqa: E402


@pytest.fixture(scope="session")
def sample_video_file(tmp_path_factory) -> Path:
    """A tiny real, valid MP4 shared read-only across tests that just need
    _pre_publish_validate to pass -- not for tests that assert on its content."""
    path = tmp_path_factory.mktemp("publishing_fixtures") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture
async def isolated_db(tmp_path, monkeypatch):
    """Points app.db.base's engine/SessionLocal at a fresh temp SQLite file for the
    duration of one test, so publishing tests never touch the real storage/app.db.

    Every module that does `from app.db.base import session_scope` looks up
    app.db.base's module globals at call time, so patching them here transparently
    redirects every caller -- queue_manager, worker, oauth modules, all of it.
    """
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_local = async_sessionmaker(engine, expire_on_commit=False, class_=db_base.AsyncSession)

    monkeypatch.setattr(db_base, "engine", engine)
    monkeypatch.setattr(db_base, "SessionLocal", session_local)

    import app.db.models  # noqa: F401 -- register all tables on Base.metadata

    async with engine.begin() as conn:
        await conn.run_sync(db_base.Base.metadata.create_all)

    yield

    await engine.dispose()
