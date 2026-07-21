"""Application configuration.

Secrets are read from the environment (``.env`` in development). Nothing in
this module ever hardcodes a production secret.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
INSTANCE_DIR = BASE_DIR / "instance"

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (TypeError, ValueError):
        return default


class Config:
    """Base configuration shared by every environment."""

    # ── Core ────────────────────────────────────────────────────────────────
    APP_NAME = os.getenv("APP_NAME", "Markdown Studio")
    APP_VERSION = "1.0.0"
    BACKUP_FORMAT_VERSION = 1

    SECRET_KEY = os.getenv("SECRET_KEY") or secrets.token_urlsafe(48)

    # ── Database ────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{(INSTANCE_DIR / 'app.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # ── Uploads / limits ────────────────────────────────────────────────────
    # Hard ceiling enforced by Werkzeug -> triggers a 413 page.
    MAX_CONTENT_LENGTH = _env_int("MAX_UPLOAD_MB", 8) * 1024 * 1024
    # Soft ceiling for a single markdown document body.
    MAX_MARKDOWN_BYTES = _env_int("MAX_MARKDOWN_MB", 2) * 1024 * 1024
    ALLOWED_IMPORT_EXTENSIONS = {".md", ".markdown", ".mdown", ".txt"}

    # ── Security ────────────────────────────────────────────────────────────
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Only meaningful behind HTTPS; the local server is plain HTTP.
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    # Editing sessions can be long - a rotating CSRF token would break autosave.
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_ENABLED = True

    # ── Filesystem ──────────────────────────────────────────────────────────
    INSTANCE_DIR = INSTANCE_DIR
    BACKUP_DIR = Path(os.getenv("BACKUP_DIR") or (INSTANCE_DIR / "backups"))
    EXPORT_DIR = Path(os.getenv("EXPORT_DIR") or (INSTANCE_DIR / "exports"))

    # Create missing tables on startup so a fresh clone just runs. Migrations
    # remain the canonical schema path; set AUTO_CREATE_DB=0 when generating
    # them, so Alembic can diff against an empty database.
    AUTO_CREATE_DB = _env_bool("AUTO_CREATE_DB", True)

    # ── Behaviour ───────────────────────────────────────────────────────────
    DOCUMENTS_PER_PAGE = _env_int("DOCUMENTS_PER_PAGE", 12)
    VERSIONS_PER_PAGE = _env_int("VERSIONS_PER_PAGE", 20)
    # Words per minute used for the reading-time estimate.
    READING_WPM = _env_int("READING_WPM", 200)

    # ── PDF ─────────────────────────────────────────────────────────────────
    # "auto" picks WeasyPrint when its native libraries are importable and
    # transparently falls back to xhtml2pdf otherwise.
    PDF_ENGINE = os.getenv("PDF_ENGINE", "auto")
    # Remote images are blocked by default: fetching arbitrary URLs during PDF
    # rendering is an SSRF vector. See README > Seguranca.
    PDF_ALLOW_REMOTE_IMAGES = _env_bool("PDF_ALLOW_REMOTE_IMAGES", False)

    # ── Server ──────────────────────────────────────────────────────────────
    # Never default to 0.0.0.0: the app has no authentication.
    HOST = os.getenv("HOST", "127.0.0.1")
    PORT = _env_int("PORT", 5000)


class DevelopmentConfig(Config):
    DEBUG = True
    TEMPLATES_AUTO_RELOAD = True


class ProductionConfig(Config):
    DEBUG = False

    def __init__(self) -> None:
        if not os.getenv("SECRET_KEY"):
            raise RuntimeError(
                "SECRET_KEY must be set in the environment for production runs."
            )


class TestingConfig(Config):
    TESTING = True
    DEBUG = False
    SECRET_KEY = "testing-secret-key-not-used-anywhere-else"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    WTF_CSRF_ENABLED = False
    DOCUMENTS_PER_PAGE = 5


CONFIGS: dict[str, type[Config]] = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}


def resolve_config(name: str | None = None) -> type[Config]:
    key = (name or os.getenv("FLASK_ENV") or "development").strip().lower()
    return CONFIGS.get(key, DevelopmentConfig)
