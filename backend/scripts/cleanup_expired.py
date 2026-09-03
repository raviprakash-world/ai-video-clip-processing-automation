#!/usr/bin/env python3
"""
Standalone retention sweep -- deletes job/upload directories older than
RETENTION_HOURS (default 24) directly from disk. No FastAPI app, no import
of anything that spawns background tasks; safe to run as a one-shot process.

The running server already does this itself every RETENTION_CHECK_INTERVAL_MINUTES
(see app/jobs/retention.py). Use this script only if you also want cleanup to
happen while the server is NOT running -- e.g. via a real system cron entry:

    # crontab -e
    0 * * * * cd /path/to/backend && .venv/bin/python scripts/cleanup_expired.py >> /tmp/clip-cleanup.log 2>&1

This script does not modify your crontab; add it yourself if you want that.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.storage.paths import cleanup_old_jobs, cleanup_old_uploads  # noqa: E402


def main() -> None:
    removed_jobs = cleanup_old_jobs(settings.RETENTION_HOURS)
    removed_uploads = cleanup_old_uploads(settings.RETENTION_HOURS)
    print(f"Removed {len(removed_jobs)} job dir(s) and {len(removed_uploads)} upload dir(s) "
          f"older than {settings.RETENTION_HOURS}h.")
    if removed_jobs:
        print("  jobs:", ", ".join(removed_jobs))
    if removed_uploads:
        print("  uploads:", ", ".join(removed_uploads))


if __name__ == "__main__":
    main()
