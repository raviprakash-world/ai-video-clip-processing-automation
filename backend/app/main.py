from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_analysis, routes_jobs, routes_publishing, routes_video, routes_watermark
from app.db.base import init_db
from app.errors import AppError
from app.jobs.retention import retention_loop
from app.publishing.worker import publishing_worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    background_tasks = [asyncio.create_task(retention_loop()), asyncio.create_task(publishing_worker_loop())]
    try:
        yield
    finally:
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(
    title="AI Video Clip Processing Automation",
    description=(
        "Execution engine that turns AI-generated clip-selection JSON into polished "
        "vertical video clips. Does not perform any AI content analysis itself."
    ),
    version="0.1.0",
    lifespan=lifespan,
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


app.include_router(routes_video.router)
app.include_router(routes_analysis.router)
app.include_router(routes_jobs.router)
app.include_router(routes_watermark.router)
app.include_router(routes_publishing.router)

class NoCacheStaticFiles(StaticFiles):
    """This frontend is plain static files edited directly on disk during
    development, with no cache-busting filename hashing. Without an explicit
    Cache-Control header, browsers apply heuristic freshness to the bare
    ETag/Last-Modified response and can keep serving an old app.js/styles.css
    after an edit without ever revalidating -- confusing enough to debug that
    it's worth the (negligible, local-app-scale) cost of disabling caching
    entirely rather than chasing stale-script bugs during development."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"
        return response


_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend"
if _FRONTEND_DIR.exists():
    app.mount("/", NoCacheStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
