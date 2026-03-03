"""Async SQLAlchemy session factory.

ROUTER_MODE (env var, default "mock") selects which database to use:
  mock → model_router          (existing data is preserved)
  real → model_router_real     (created automatically on first startup)

URL resolution order (highest to lowest priority):
  DATABASE_URL_MOCK / DATABASE_URL_REAL  — per-mode explicit URL
  DATABASE_URL                           — legacy / docker-compose override
  derived from ROUTER_MODE               — local-dev default
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

log = logging.getLogger(__name__)

ROUTER_MODE = os.environ.get("ROUTER_MODE", "mock").lower()

_DB_NAMES: dict[str, str] = {
    "mock": "model_router",
    "real": "model_router_real",
}

# URL selection: mode-specific → generic → derived default
DATABASE_URL: str = (
    os.environ.get(f"DATABASE_URL_{ROUTER_MODE.upper()}")
    or os.environ.get("DATABASE_URL")
    or f"postgresql+asyncpg://router:secret@localhost:5432/{_DB_NAMES.get(ROUTER_MODE, 'model_router')}"
)

_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
_SessionLocal = async_sessionmaker(_engine, expire_on_commit=False)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Ensure the target database exists and migrations are applied.

    Uses asyncpg directly so we can (a) CREATE DATABASE outside a transaction
    and (b) run the full multi-statement migration script in one call.
    """
    import asyncpg

    parsed = urlparse(DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    db_name = parsed.path.lstrip("/")
    host = parsed.hostname or "localhost"
    port = parsed.port or 5432
    user = parsed.username or "router"
    password = parsed.password or "secret"

    # --- Step 1: create the database if it doesn't exist ---
    sys_conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database="postgres"
    )
    try:
        exists = await sys_conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            log.info("Creating database '%s'...", db_name)
            # CREATE DATABASE cannot run inside a transaction; asyncpg's
            # execute() is fine here because it's not inside a transaction block.
            await sys_conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await sys_conn.close()

    # --- Step 2: apply migrations (all DDL is IF NOT EXISTS — safe to re-run) ---
    migration_sql = (
        Path(__file__).parent / "migrations" / "001_initial.sql"
    ).read_text()

    db_conn = await asyncpg.connect(
        host=host, port=port, user=user, password=password, database=db_name
    )
    try:
        await db_conn.execute(migration_sql)
        log.info("Migrations applied to '%s' (ROUTER_MODE=%s)", db_name, ROUTER_MODE)
    finally:
        await db_conn.close()
