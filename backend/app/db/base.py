"""Async SQLAlchemy engine/session setup. SQLite -- a single local file, per the
publishing spec's "prefer local database" instruction. No separate DB service."""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import DateTime
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.config import settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """SQLite has no real timezone-aware datetime type -- SQLAlchemy's own
    DateTime(timezone=True) silently loses tzinfo on the round-trip through
    aiosqlite, coming back naive. Every datetime this app stores is UTC, so
    this type normalizes on write and always reattaches UTC tzinfo on read,
    making `db_value - datetime.now(timezone.utc)` safe everywhere instead of
    an intermittent "can't compare offset-naive and offset-aware datetimes"."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


engine = create_async_engine(settings.DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    import app.db.models  # noqa: F401 -- must be imported so Base.metadata knows about every table;
    # relying on some other module having imported it first is fragile import-order magic.

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
