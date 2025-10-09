"""SQLAlchemy engine factory for TubeCord."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url

from app.config.settings import settings


def _prepare_sqlite_directory(database_url: str) -> dict:
    """Return connect args for SQLite engines and ensure directories exist."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return {}

    database = url.database
    if database and database not in {":memory:", ""}:
        # Ensure parent directory exists for file-based SQLite databases
        db_path = Path(database)
        if not db_path.parent.exists():
            db_path.parent.mkdir(parents=True, exist_ok=True)
    return {"check_same_thread": False}


@lru_cache(maxsize=None)
def get_engine(database_url: Optional[str] = None, *, echo: Optional[bool] = None) -> Engine:
    """Create (or reuse) a SQLAlchemy engine for the configured database."""
    url = database_url or settings.DATABASE_URL
    should_echo = settings.DATABASE_ECHO if echo is None else echo

    kwargs = {"future": True, "pool_pre_ping": True, "echo": should_echo}

    connect_args = _prepare_sqlite_directory(url)
    if connect_args:
        kwargs["connect_args"] = connect_args

    return create_engine(url, **kwargs)
