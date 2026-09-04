# AI Video Clip Processing Automation

[![GitHub repo](https://img.shields.io/badge/GitHub-raviprakash--world%2Fai--video--clip--processing--automation-blue?logo=github)](https://github.com/raviprakash-world/ai-video-clip-processing-automation)
[![Tests](https://github.com/raviprakash-world/ai-video-clip-processing-automation/actions/workflows/tests.yml/badge.svg)](https://github.com/raviprakash-world/ai-video-clip-processing-automation/actions/workflows/tests.yml)

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
- **Database**: SQLite (via SQLAlchemy async + aiosqlite) at `storage/app.db`, used
  only by the publishing queue (see below) — the core clip pipeline stays in-memory.

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
| `app/db` | SQLAlchemy async models + engine/session (`UTCDateTime` type works around SQLite silently dropping tzinfo on read) |
| `app/publishing/cost_guard.py` | AVAILABLE / CHECK_REQUIRED / NOT_AVAILABLE per platform — never enables a paid/unsupported path |
| `app/publishing/provider.py` + `youtube_provider.py` / `facebook_provider.py` / `instagram_provider.py` | `PublishingProvider` ABC + one official-API implementation each |
| `app/publishing/queue_manager.py` | Scheduling from a completed job, idempotent creation, pause/resume/retry/skip/publish-now |
| `app/publishing/worker.py` | Atomic claim, retry/backoff classification, pre-publish validation, per-item failure isolation |
| `app/publishing/oauth` | Google / Meta OAuth authorization-code flows, CSRF state, encrypted token storage |
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

## Auto-publishing queue (YouTube / Instagram / Facebook)

On top of the clip pipeline, there's an optional automated publishing queue:
paste a video + AI JSON, pick platforms and an interval, hit **Generate +
Queue**, and every valid clip (ordered by `rank`, then `viral_score` desc)
gets generated and automatically posted one at a time, hours apart, without
anyone manually queueing individual clips.

**Cost/legality guarantee**: only official APIs are used (YouTube Data API
v3 via Google's own client library; Meta Graph API via direct HTTPS calls to
its documented REST endpoints) — no browser automation, no scraping, no
unofficial posting services, no paid third-party publishing tool (Buffer,
Hootsuite, etc.), ever. Before any provider is allowed to publish, a cost
guard (`app/publishing/cost_guard.py`) checks whether it's actually usable —
missing OAuth app credentials, no connected account, or an ineligible
account type (Instagram needs a Business/Creator account linked to a
Facebook Page) all report `NOT_AVAILABLE` / `CHECK_REQUIRED` rather than the
app attempting to force it through. Check `GET /api/publishing/capabilities`
or the "Connected Accounts" panel in the UI at any time.

### Setting it up for real

This app cannot create Google Cloud or Meta Developer apps for you — that
needs your own login and acceptance of their terms. To go from "architecture
ready" to "actually posting":

1. **YouTube**: in Google Cloud Console, enable the YouTube Data API v3 and
   create an OAuth client ID (Web application), with
   `http://localhost:8077/api/publishing/youtube/callback` (or your real
   deployed URL) as an authorized redirect URI. Set:
   ```
   GOOGLE_OAUTH_CLIENT_ID=...
   GOOGLE_OAUTH_CLIENT_SECRET=...
   ```
2. **Facebook + Instagram**: Meta has moved to a "use case" based app
   creation flow, and business permissions like `pages_manage_posts` /
   `instagram_content_publish` now go through a **Login Configuration**
   rather than a plain scope list. As of writing (verified against Meta's
   own current docs — this is exactly the part most likely to have moved
   again by the time you read this):
   1. Go to **developers.facebook.com → My Apps → Create App**.
   2. **App details**: name it, give a contact email, Next.
   3. **Use cases**: select **"Manage everything on your Page"** *and*
      **"Manage messaging & content on Instagram"** (both selectable
      together), Next.
   4. **Business**: pick "I don't want to connect a business portfolio yet"
      to start (fine for testing with your own accounts), Next through
      Requirements and Overview, then **Go to dashboard**.
   5. In the dashboard, open the **Facebook Login for Business** product
      (added automatically by those use cases) → **Configurations** → **+
      Create configuration**.
      - Access token type: **User access token**.
      - Assets: select the Facebook Page (and its linked Instagram account,
        if you've already linked one) you want to publish to.
      - Permissions: `pages_show_list`, `pages_read_engagement`,
        `pages_manage_posts`, `business_management`, `instagram_basic`,
        `instagram_content_publish`.
      - Click **Create** — you get a **Configuration ID**.
   6. Under **App Settings → Basic**, copy the **App ID** and **App
      Secret**, and add the redirect URI from `.env.example` under the
      Facebook Login for Business product's client OAuth settings.
   ```
   META_APP_ID=...
   META_APP_SECRET=...
   META_LOGIN_CONFIG_ID=...   # from step 5 above -- required, not optional
   ```
   Instagram publishing additionally requires the connected Page to have a
   linked Instagram **Business or Creator** account, *and* a real public
   HTTPS URL for this server (Meta's servers fetch the video themselves —
   there's no direct-upload option for Reels):
   ```
   PUBLIC_BASE_URL=https://your-real-domain.example
   ```
   Facebook Page video publishing does not need `PUBLIC_BASE_URL` — it
   accepts direct binary upload.
3. **Token encryption**: set a stable key so stored tokens survive a
   restart (otherwise a random one is generated per-process and logged as a
   loud warning):
   ```
   TOKEN_ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
   ```
4. Restart the server, click **Connect YouTube** / **Connect Facebook /
   Instagram** in the UI, and complete the consent screen. The capabilities
   panel should flip to `AVAILABLE`.

None of this was tested against real YouTube/Instagram/Facebook accounts in
this session (that requires credentials only you can generate, plus your own
consent-screen approval) — what's been verified is the full architecture end
to end with the cost guard correctly reporting `NOT_AVAILABLE` for every
platform pre-setup, and the queue/scheduler/worker running for real against
locally-generated clips (rank-based scheduling, retry/backoff, one-platform
and one-clip failure isolation, pause/resume, idempotent resubmission, and
restart-safe state), all through the live UI and a comprehensive test suite.

### How it works

```
GENERATE + QUEUE
      |
      v
[validate JSON] -> [create processing job, all valid clips auto-selected] -> [existing pipeline runs]
      |
      v (once every clip resolves)
[create_queue_from_job: order by rank/score, one time-slot per clip,
 every enabled platform shares that clip's slot, copyright_warning-flagged
 clips excluded by default]
      |
      v
publishing_queue (SQLite) <---- PublishingWorker polls every 30s, claims one
      |                          due row at a time via an atomic
      v                          UPDATE ... WHERE status=... (restart-safe,
PublishingProvider                multi-worker-safe), never more than
 (youtube | instagram | facebook) MAX_CONCURRENT_PUBLISHES=1 at once
```

- **States**: `WAITING -> PUBLISHING -> PUBLISHED`, or `FAILED` /
  `RETRYING` (transient errors, backoff 0/5/30 min, max 3 attempts) /
  `AUTH_REQUIRED` (bad/missing token) / `NOT_SUPPORTED` (account/media
  ineligible) / `QUOTA_WAIT` (rate-limited, waits out a cooldown) /
  `PAUSED` / `CANCELLED`. A platform failure never touches other platforms'
  items for the same clip; a clip failure never blocks the rest of the queue.
- **Idempotency**: `idempotency_key = job_id:clip_id:platform` is a DB
  unique constraint — the same clip can never be queued twice for the same
  platform, and `retry_item()` refuses to revert an already-`PUBLISHED` item
  back to `WAITING` (the one thing that could otherwise cause a real
  duplicate repost).
- **Restart safety**: the whole queue lives in SQLite, not memory — a
  `PUBLISHED` row stays `PUBLISHED` across a restart, and the worker just
  resumes finding due rows normally. `app/jobs/manager.py`'s in-memory
  processing state is unaffected; only already-completed clips ever get
  queued for publishing in the first place.
- **Human control**: Pause/Resume (per job), Retry, Skip, Publish Now — all
  in the dashboard and at `/api/publishing/queue/...`.
- **Metadata**: title prefers `title_options.curiosity`, falling back to
  `title_options.direct`, then `hook`; caption/hashtags come only from the
  JSON, never invented (`app/publishing/metadata.py`).
- **Safety**: a clip with a non-null `copyright_warning` is excluded from
  auto-publishing by default (`BLOCK_WARNINGS=true`); this is a policy
  toggle, not a legal determination.

## Explicitly out of scope

AI-generated captions/hashtags/titles, automatic viral-moment detection or
clip discovery, social analytics, recommendation algorithms, engagement
bots, auto-commenting/liking, browser automation or CAPTCHA/restriction
bypass of any kind, and paid third-party publishing services. The
publishing queue is deliberately just a queue: it posts exactly what the
JSON says, to exactly the platforms you enable, on the schedule you set —
nothing about what or whether to post is inferred.
