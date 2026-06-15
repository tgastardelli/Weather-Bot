"""Engine/Session async com PRAGMAs de SQLite (skill python-backend §4)."""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(db_url: str) -> AsyncEngine:
    url = make_url(db_url)
    database = url.database
    if (
        url.drivername.startswith("sqlite")
        and database is not None
        and database not in ("", ":memory:")
    ):
        Path(database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(db_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Dependency FastAPI: session transacional por request."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        async with session.begin():
            yield session
