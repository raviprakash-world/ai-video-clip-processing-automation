# AI Video Clip Processing Automation

An execution engine, not a content-analysis engine. It takes a source video
plus an externally-generated AI analysis JSON (clip timestamps + metadata)
and produces polished 9:16 vertical MP4 clips. It performs **no** video
understanding, transcription, clip discovery, captioning, or LLM calls —
the JSON's `start_time`/`end_time` (and `clip_id`/`rank`) are the only
AI-derived values that ever influence an ffmpeg command.

```
EXTERNAL AI --JSON--> [validate] -> [ingest] -> [extract] -> [crop] ->
                       [authorized overlay] -> [encode] -> [validate] -> [export]
```

## Stack

- **Backend**: Python 3.13 + FastAPI, ffmpeg/ffprobe via `asyncio.create_subprocess_exec`
  (argument lists only — never a shell string).
- **Frontend**: plain HTML/CSS/JS, no build step, served as static files by FastAPI.

## Running it

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8077
```

Open http://localhost:8077.

## Tests

```bash
cd backend && source .venv/bin/activate
python -m pytest -q
```

Includes fast unit tests (JSON schema validation, timestamp math, filename
sanitization) and a real ffmpeg/ffprobe integration test that generates a
synthetic source video and runs it through the full extract → crop → encode
→ validate pipeline for both crop strategies plus a no-audio source.

## Module map

| Module | Responsibility |
|---|---|
| `app/json_validation` | Schema-version registry, required-field checks, deterministic `duration_seconds` recalculation |
| `app/clip_processing/timecode.py` | `HH:MM:SS` parsing, pure/no I/O |
| `app/video_metadata` | ffprobe-based source/output inspection |
| `app/video_ingestion` | `VideoSource` abstraction — `LocalFileSource`, `DirectUrlSource` (SSRF-guarded), and `YouTubeSource` (via yt-dlp, no auth/DRM bypass) |
| `app/cropping` | `CENTER_CROP` / `FIT_WITH_BLUR_BACKGROUND` ffmpeg filter graphs |
| `app/watermark` | Authorization-gated overlay-add / region-remove (no automatic watermark detection) |
| `app/clip_processing/pipeline.py` | The only module that assembles ffmpeg command lines |
| `app/output` | Filesystem-safe naming, per-clip metadata sidecar, post-encode ffprobe validation |
| `app/jobs` | Job/clip state machine, concurrency-limited orchestration, idempotency hashing, retention sweep |
| `app/storage` | Path-traversal-safe job/upload directories, quota + retention |
| `app/video_ingestion/ingest_manager.py` | Tracks real download progress for URL/YouTube ingestion so the UI can poll it |
| `app/api` | FastAPI routes only — no business logic |

## Progress reporting

- **File upload** (Step 1, "Upload Video"): the browser already has the bytes,
  so `app.js` uses `XMLHttpRequest`'s `upload.onprogress` for real, instant
  byte-level progress — no polling needed.
- **URL / YouTube ingestion** ("Fetch from URL"): the download happens
  server-side, invisible to the browser, so `POST /api/video/ingest-url`
  returns an `ingest_id` immediately and the frontend polls
  `GET /api/video/ingest-url/{ingest_id}` every ~700ms. Progress comes from
  real byte counts — `Content-Length` vs. bytes streamed for a direct URL,
  yt-dlp's own `downloaded_bytes`/`total_bytes` progress hook for YouTube.
  When total size truly isn't known yet, the UI shows an indeterminate
  animated bar plus a running MB count instead of a fabricated percentage
  (section 18: never fake progress).
- Clip **processing** progress (Step 5) already worked this way — real
  `-progress pipe:1` output parsed from the running ffmpeg process.

## Retention / storage cleanup

`RETENTION_HOURS` (default 24) controls how long a job's output clips (and
the matching upload's source video) are kept before automatic deletion.
This runs as a background asyncio task inside the FastAPI process itself
(`app/jobs/retention.py`, started from `app/main.py`'s lifespan) every
`RETENTION_CHECK_INTERVAL_MINUTES` (default 60) — no OS-level cron/launchd
setup required, and it also runs once immediately at startup. A job whose
processing task is still actively running is never swept, however old its
directory looks. When a stale entry is removed, the in-memory job/idempotency
caches are cleaned up to match — a completed-but-deleted job is never
reported as a false "already have that, here it is" (see the idempotency
fix in git history for exactly the bug this prevents).

If you want cleanup to also happen while the server isn't running, there's
a standalone equivalent at `backend/scripts/cleanup_expired.py` you can wire
into a real system cron entry yourself, e.g.:

```
0 * * * * cd /path/to/backend && .venv/bin/python scripts/cleanup_expired.py >> /tmp/clip-cleanup.log 2>&1
```

This repo does not install that crontab entry for you.

## Security notes

- Every ffmpeg/ffprobe call uses an argument list, never `shell=True`.
- Only `clip_id`/`rank`/`viral_score` (sanitized to `[A-Za-z0-9_-]`) ever
  become part of a filename — free-text AI fields (hook, caption, hashtags,
  reason, payoff, category) never touch the filesystem or a command line.
- All ID-based path lookups go through `storage/paths.py::safe_join`, which
  resolves and verifies the path stays inside the intended root.
- Watermark modes (`authorized_overlay`, `authorized_remove`) require an
  explicit `authorized: true` flag from the caller; the app never attempts
  to detect or strip third-party watermarks on its own.
- Remote video sources: `POST /api/video/ingest-url {"url": ...}` accepts either
  a direct video-file URL or a youtube.com/youtu.be URL (auto-detected by host).
  - `DirectUrlSource`: http/https only; every hostname (including each redirect
    hop) is checked against private/loopback/link-local/reserved ranges before
    connecting (`app/video_ingestion/ssrf_guard.py`); size is capped while
    streaming regardless of what `Content-Length` claims; the download is
    written to a temp path and only exposed after it completes, and the
    downstream job-creation path always re-probes with ffprobe before trusting
    it as a real video.
  - `YouTubeSource`: restricted to youtube.com/youtu.be hosts, uses yt-dlp with
    no cookies, no age-gate/geo/DRM bypass, and no authentication of any kind —
    if a video isn't fetchable anonymously and legitimately, this raises rather
    than working around the restriction. yt-dlp needs to keep pace with
    YouTube's player/signature changes, so expect to bump its pinned version in
    `requirements.txt` periodically if downloads start failing.

## Explicitly out of scope for this MVP

Automatic viral-moment detection, transcription, captions, hashtags,
titles, hooks, social publishing, scheduling, analytics, billing,
multi-tenancy. See section 27/28 of the build spec this was written against
— those land later, behind `ClipDiscoveryProvider` / `CaptionProvider` /
`PublishingProvider` interfaces this app's JSON-in boundary already models,
without touching the processing engine.
