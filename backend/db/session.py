"""Database session management."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://schatten:schatten@localhost:5432/schatten",
)

engine = create_async_engine(DATABASE_URL, echo=False)
_session_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncGenerator[AsyncSession, None]:
    async with _session_factory() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
