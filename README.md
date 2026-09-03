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
| `app/jobs` | Job/clip state machine, concurrency-limited orchestration, idempotency hashing |
| `app/storage` | Path-traversal-safe job/upload directories, quota + retention |
| `app/api` | FastAPI routes only — no business logic |

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
