"""Async SQLAlchemy session factory."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = create_async_engine(
    os.environ.get("DATABASE_URL", "postgresql+asyncpg://router:secret@localhost:5432/model_router"),
    pool_pre_ping=True,
)

_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


async def init_db() -> None:
    """Called at startup — runs migrations via raw SQL if needed."""
    # Migrations are applied by Docker's initdb.d; this is a no-op in prod.
    # Add Alembic here if you want programmatic migration management.
    pass


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        yield session
