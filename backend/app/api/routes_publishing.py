"""
Publishing queue API (section 31). Endpoint naming adapted from the spec's
suggested list to fit this app's existing FastAPI/router conventions:

  GET  /api/publishing/capabilities        -- cost-guard report (section 29)
  GET  /api/publishing/connect/youtube      -- redirect to Google consent screen
  GET  /api/publishing/youtube/callback
  GET  /api/publishing/connect/meta         -- redirect to Meta consent screen (covers FB + IG)
  GET  /api/publishing/meta/callback
  GET  /api/publishing/accounts             -- never includes tokens
  POST /api/publishing/queue/generate       -- "GENERATE + QUEUE" (section 24)
  GET  /api/publishing/queue                -- ?job_id= optional filter
  POST /api/publishing/queue/{item_id}/retry
  POST /api/publishing/queue/{item_id}/skip
  POST /api/publishing/queue/{item_id}/cancel
  POST /api/publishing/queue/{item_id}/publish-now
  POST /api/publishing/queue/job/{job_id}/pause
  POST /api/publishing/queue/job/{job_id}/resume

Connect endpoints are GET (not POST, as the spec's literal list suggests)
because they're plain browser redirects to a consent screen -- a normal
link/window.location navigation, not an API call the frontend parses.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional
from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.db.base import session_scope
from app.db.models import PublishQueueItem, SocialAccount
from app.errors import AuthorizationRequiredError, InvalidJsonError, NotFoundError
from app.jobs.manager import job_manager
from app.json_validation.validator import validate_analysis_json
from app.json_validation.video_bounds import check_all_clips
from app.publishing import queue_manager
from app.publishing.auto_queue import watch_job_and_enqueue
from app.publishing.cost_guard import full_capability_report
from app.publishing.oauth import meta_oauth, youtube_oauth
from app.publishing.registry import PLATFORMS
from app.publishing.states import PublishError
from app.schemas.processing_config import ProcessingConfig
from app.storage.paths import find_upload_source
from app.video_metadata.probe import probe_video

router = APIRouter(prefix="/api/publishing", tags=["publishing"])
logger = logging.getLogger("clip_pipeline.publishing")


def _describe_oauth_error(exc: Exception) -> str:
    """OAuth callbacks must never 500 -- any failure here (invalid/reused code,
    a Graph or Google API error, a network blip) should become a readable
    message the user sees in the app, with the full traceback still going to
    the server log for debugging. PublishError.message and ValueError's own
    string are already safe to show (they're built from API error responses,
    never from a token); anything else gets a generic fallback rather than
    risking an unexpected exception's repr leaking internal detail."""
    if isinstance(exc, PublishError):
        return exc.message
    if isinstance(exc, ValueError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


# -- capabilities / accounts -----------------------------------------------------
@router.get("/capabilities")
async def get_capabilities():
    async with session_scope() as session:
        result = await session.execute(select(SocialAccount))
        accounts = list(result.scalars().all())
    latest_by_platform: dict[str, SocialAccount] = {}
    for account in accounts:
        latest_by_platform[account.platform] = account
    return full_capability_report(latest_by_platform)


@router.get("/accounts")
async def list_accounts():
    async with session_scope() as session:
        result = await session.execute(select(SocialAccount))
        accounts = list(result.scalars().all())
    return [
        {
            "id": a.id,
            "platform": a.platform,
            "account_id": a.account_id,
            "account_name": a.account_name,
            "status": a.status,
            "token_expires_at": a.token_expires_at.isoformat() if a.token_expires_at else None,
        }
        for a in accounts
    ]


# -- OAuth: YouTube ---------------------------------------------------------------
@router.get("/connect/youtube")
async def connect_youtube():
    if not settings.GOOGLE_OAUTH_CLIENT_ID or not settings.GOOGLE_OAUTH_CLIENT_SECRET:
        raise AuthorizationRequiredError(
            "YouTube OAuth is not configured on this server (GOOGLE_OAUTH_CLIENT_ID/SECRET missing)."
        )
    url = await youtube_oauth.build_authorization_url()
    return RedirectResponse(url)


@router.get("/youtube/callback")
async def youtube_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return _oauth_result_redirect(success=False, message=f"YouTube authorization was not completed: {error}")
    try:
        account = await youtube_oauth.handle_callback(code, state)
    except Exception as exc:  # noqa: BLE001 - an OAuth callback must never 500; see _describe_oauth_error
        logger.exception("YouTube OAuth callback failed")
        return _oauth_result_redirect(success=False, message=_describe_oauth_error(exc))
    return _oauth_result_redirect(success=True, message=f"Connected YouTube channel '{account.account_name}'.")


# -- OAuth: Meta (Facebook + Instagram) -------------------------------------------
@router.get("/connect/meta")
async def connect_meta():
    if not settings.META_APP_ID or not settings.META_APP_SECRET:
        raise AuthorizationRequiredError("Meta OAuth is not configured on this server (META_APP_ID/SECRET missing).")
    url = await meta_oauth.build_authorization_url()
    return RedirectResponse(url)


@router.get("/meta/callback")
async def meta_callback(code: str = "", state: str = "", error: str = ""):
    if error:
        return _oauth_result_redirect(success=False, message=f"Meta authorization was not completed: {error}")
    try:
        accounts, warnings = await meta_oauth.handle_callback(code, state)
    except Exception as exc:  # noqa: BLE001 - an OAuth callback must never 500; see _describe_oauth_error
        logger.exception("Meta OAuth callback failed")
        return _oauth_result_redirect(success=False, message=_describe_oauth_error(exc))
    names = ", ".join(f"{a.platform} ({a.account_name})" for a in accounts)
    message = f"Connected {names}." if names else "Connected."
    if warnings:
        message += " " + " ".join(warnings)
    return _oauth_result_redirect(success=bool(accounts), message=message)


def _oauth_result_redirect(*, success: bool, message: str) -> RedirectResponse:
    status = "success" if success else "error"
    return RedirectResponse(f"/?oauth={status}&message={quote(message)}")


# -- Generate + Queue (section 24) ------------------------------------------------
class GenerateAndQueueRequest(BaseModel):
    upload_id: str
    json_text: Optional[str] = None
    json_data: Optional[dict[str, Any]] = None
    config: ProcessingConfig = ProcessingConfig()
    platforms: list[str] = Field(default_factory=list)
    interval_minutes: Optional[int] = None


@router.post("/queue/generate")
async def generate_and_queue(payload: GenerateAndQueueRequest):
    raw = payload.json_data if payload.json_data is not None else payload.json_text
    if raw is None:
        raise InvalidJsonError("Provide either json_text or json_data in the request body.")

    validated = validate_analysis_json(raw)
    source_path = find_upload_source(payload.upload_id)
    video_meta = await probe_video(source_path)

    # Section 24/6: every valid clip is auto-selected, ordered by rank -- the user
    # never manually picks clips here. A clip outside the video's actual duration
    # is skipped (reported, not silently dropped) rather than blocking the rest.
    bounds_errors = check_all_clips(validated.clips, video_meta)
    selected = [c for c in validated.clips if c.clip_id not in bounds_errors]
    skipped = [{"clip_id": clip_id, "error": exc.to_dict()} for clip_id, exc in bounds_errors.items()]

    platforms = [p for p in payload.platforms if p in PLATFORMS]

    job = await job_manager.create_job(upload_path=source_path, selected_clips=selected, config=payload.config)

    if platforms:
        asyncio.create_task(
            watch_job_and_enqueue(job_manager, job.job_id, enabled_platforms=platforms, interval_minutes=payload.interval_minutes)
        )

    return {**job.to_public_dict(), "skipped_clips": skipped, "publishing_platforms": platforms}


# -- Queue listing + item/queue actions --------------------------------------------
def _item_to_dict(item: PublishQueueItem) -> dict:
    return {
        "id": item.id,
        "job_id": item.job_id,
        "clip_id": item.clip_id,
        "platform": item.platform,
        "status": item.status,
        "scheduled_at": item.scheduled_at.isoformat(),
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "attempt_count": item.attempt_count,
        "last_error": item.last_error,
        "external_post_id": item.external_post_id,
        "title": item.metadata_json.get("title"),
    }


@router.get("/queue")
async def get_queue(job_id: Optional[str] = None):
    items = await queue_manager.list_queue(job_id)
    return [_item_to_dict(i) for i in items]


@router.post("/queue/{item_id}/retry")
async def retry_queue_item(item_id: str):
    item = await queue_manager.retry_item(item_id)
    if not item:
        raise NotFoundError(f"No queue item '{item_id}'.")
    return _item_to_dict(item)


@router.post("/queue/{item_id}/skip")
async def skip_queue_item(item_id: str):
    item = await queue_manager.skip_item(item_id)
    if not item:
        raise NotFoundError(f"No queue item '{item_id}'.")
    return _item_to_dict(item)


@router.post("/queue/{item_id}/cancel")
async def cancel_queue_item(item_id: str):
    item = await queue_manager.cancel_item(item_id)
    if not item:
        raise NotFoundError(f"No queue item '{item_id}'.")
    return _item_to_dict(item)


@router.post("/queue/{item_id}/publish-now")
async def publish_now_queue_item(item_id: str):
    item = await queue_manager.publish_now(item_id)
    if not item:
        raise NotFoundError(f"No queue item '{item_id}'.")
    return _item_to_dict(item)


@router.post("/queue/job/{job_id}/pause")
async def pause_queue(job_id: str):
    count = await queue_manager.pause_job_queue(job_id)
    return {"paused": count}


@router.post("/queue/job/{job_id}/resume")
async def resume_queue(job_id: str):
    count = await queue_manager.resume_job_queue(job_id)
    return {"resumed": count}
