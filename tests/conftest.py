"""Shared fixtures.

Every test runs against a throwaway SQLite file created per test, so the
search index (a real FTS5 virtual table) behaves exactly as it does in
production. An in-memory database would be reset between connections.
"""

from __future__ import annotations

import pytest

from app import create_app
from app.extensions import db as _db
from app.services.document_service import DocumentService
from app.services.search_service import search_index


@pytest.fixture()
def app(tmp_path):
    database = tmp_path / "test.db"
    backups = tmp_path / "backups"
    exports = tmp_path / "exports"

    # Passed as overrides, not assigned afterwards: the engine is built during
    # init_app, so a later assignment would leave the app on an in-memory
    # database while the config claimed otherwise.
    application = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
            "BACKUP_DIR": backups,
            "EXPORT_DIR": exports,
            "WTF_CSRF_ENABLED": False,
            "TESTING": True,
        },
    )
    backups.mkdir(parents=True, exist_ok=True)
    exports.mkdir(parents=True, exist_ok=True)

    with application.app_context():
        _db.create_all()
        search_index.ensure()
        yield application
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db(app):
    return _db


@pytest.fixture()
def make_document(app):
    """Factory creating a persisted document."""

    def _make(title="Documento de teste", content="# Título\n\nCorpo do texto.", **kwargs):
        return DocumentService.create(title=title, content_markdown=content, **kwargs)

    return _make


@pytest.fixture()
def document(make_document):
    return make_document()


@pytest.fixture()
def csrf_app(tmp_path):
    """A second app with CSRF protection left on, for the CSRF tests."""
    database = tmp_path / "csrf.db"
    application = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
            "BACKUP_DIR": tmp_path / "backups",
            "EXPORT_DIR": tmp_path / "exports",
            "WTF_CSRF_ENABLED": True,
            "TESTING": True,
        },
    )
    with application.app_context():
        _db.create_all()
        search_index.ensure()
        yield application
        _db.session.remove()
        _db.drop_all()
