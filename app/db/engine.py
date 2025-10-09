"""SQLAlchemy engine factory for TubeCord."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url

from app.config.settings import settings


logger = logging.getLogger(__name__)


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

    engine = create_engine(url, **kwargs)

    masked_url = _mask_connection_url(url)

    def _log_connect(dbapi_connection, connection_record):  # pragma: no cover - side effect only
        if connection_record.info.get("_tubecord_logged"):
            return
        logger.info("Database connection established to %s", masked_url)
        connection_record.info["_tubecord_logged"] = True

    event.listen(engine, "connect", _log_connect, once=False)

    return engine


def _mask_connection_url(raw_url: str) -> str:
    """Hide credentials in a SQLAlchemy URL when logging."""
    try:
        url = make_url(raw_url)
    except Exception:  # pragma: no cover - defensive
        return raw_url

    if url.password:
        url = url.set(password="***")
    return str(url)
