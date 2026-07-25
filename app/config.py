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
    # Per-purpose ceilings. Keep these as the source of truth: the global
    # Werkzeug limit below is derived from them, because a global limit lower
    # than a feature's own limit silently breaks that feature (a 100 MB video
    # rejected with 413 long before the video check ever runs).
    MAX_DOCUMENT_UPLOAD_BYTES = _env_int("MAX_UPLOAD_MB", 8) * 1024 * 1024
    MEDIA_MAX_IMAGE_BYTES = _env_int("MEDIA_MAX_IMAGE_MB", 10) * 1024 * 1024
    MEDIA_MAX_VIDEO_BYTES = _env_int("MEDIA_MAX_VIDEO_MB", 100) * 1024 * 1024
    # Attachments (PDF, Office, archives, text). Generous enough for a real
    # deck or a scanned contract without inviting a disk-filling upload.
    MEDIA_MAX_FILE_BYTES = _env_int("MEDIA_MAX_FILE_MB", 50) * 1024 * 1024

    # Hard ceiling enforced by Werkzeug -> triggers a 413 page. Sized for the
    # largest legitimate upload, with room for multipart framing. Endpoints
    # that accept something smaller enforce their own limit first.
    MAX_CONTENT_LENGTH = (
        max(
            MAX_DOCUMENT_UPLOAD_BYTES,
            MEDIA_MAX_IMAGE_BYTES,
            MEDIA_MAX_VIDEO_BYTES,
            MEDIA_MAX_FILE_BYTES,
        )
        + 1024 * 1024
    )

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
    UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR") or (INSTANCE_DIR / "uploads"))

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
    # Generated per process, never a literal. A checked-in key would let anyone
    # reading the repository forge session cookies against any deployment that
    # happens to start with FLASK_ENV=testing.
    SECRET_KEY = secrets.token_urlsafe(32)
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
