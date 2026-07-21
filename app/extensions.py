"""Flask extension singletons.

Kept in their own module so models, services and the factory can import them
without creating circular imports.
"""

from __future__ import annotations

import sqlite3

from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for every model (SQLAlchemy 2.0 style)."""


db = SQLAlchemy(model_class=Base)
migrate = Migrate()
csrf = CSRFProtect()


@event.listens_for(Engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record) -> None:
    """Apply per-connection SQLite pragmas.

    SQLite ignores foreign keys unless they are enabled on every connection.
    Without this, ``ON DELETE CASCADE`` silently does nothing and deleting a
    document leaves its versions behind as orphans.

    WAL improves concurrency between the request handling a save and a
    long-running export; NORMAL synchronous is the usual companion to WAL.
    """
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()
