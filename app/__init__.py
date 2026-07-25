"""Application factory."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, render_template
from sqlalchemy.exc import SQLAlchemyError

from app.config import resolve_config
from app.errors import register_error_handlers
from app.extensions import csrf, db, migrate
from app.security import register_security
from app.utils.files import ensure_directory


def create_app(
    config_name: str | None = None, overrides: dict | None = None
) -> Flask:
    """Build the application.

    ``overrides`` is applied before the extensions are initialised. That
    matters: Flask-SQLAlchemy builds its engine inside ``init_app``, so a
    database URI assigned to ``app.config`` afterwards is silently ignored and
    the app keeps talking to the original database.
    """
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(resolve_config(config_name)())
    if overrides:
        app.config.update(overrides)

    _prepare_filesystem(app)
    _configure_logging(app)

    db.init_app(app)
    migrate.init_app(app, db, directory=str(Path(app.root_path).parent / "migrations"))
    csrf.init_app(app)

    # Importing the models registers every mapper before create_all/Alembic run.
    from app import models  # noqa: F401

    _register_blueprints(app)
    register_error_handlers(app)
    register_security(app)
    _register_template_helpers(app)
    _register_cli(app)
    _register_generated_assets(app)

    # Deliberately no database bootstrap here. create_app() runs for every
    # `flask` CLI invocation, including `flask db upgrade` - creating tables
    # at this point would win the race against Alembic and leave the schema
    # in place but unstamped. Serving entry points call bootstrap_database()
    # explicitly; see run.py.
    return app


# ── Wiring ──────────────────────────────────────────────────────────────────


def _prepare_filesystem(app: Flask) -> None:
    ensure_directory(Path(app.instance_path))
    ensure_directory(Path(app.config["INSTANCE_DIR"]))
    ensure_directory(Path(app.config["BACKUP_DIR"]))
    ensure_directory(Path(app.config["EXPORT_DIR"]))
    ensure_directory(Path(app.config["UPLOAD_DIR"]))


def _configure_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return

    log_dir = ensure_directory(Path(app.config["INSTANCE_DIR"]) / "logs")
    handler = RotatingFileHandler(
        log_dir / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        # Document bodies are never logged - only structural information.
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    handler.setLevel(logging.INFO)

    app.logger.addHandler(handler)
    app.logger.setLevel(logging.DEBUG if app.config.get("DEBUG") else logging.INFO)
    logging.getLogger("app").addHandler(handler)
    logging.getLogger("app").setLevel(logging.INFO)


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.api import api_bp
    from app.blueprints.dashboard import dashboard_bp
    from app.blueprints.documents import documents_bp
    from app.blueprints.editor import editor_bp
    from app.blueprints.exports import exports_bp
    from app.blueprints.groups import groups_bp
    from app.blueprints.history import history_bp
    from app.blueprints.media import media_bp
    from app.blueprints.settings import settings_bp
    from app.blueprints.trash import trash_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(groups_bp)
    app.register_blueprint(editor_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(trash_bp)
    app.register_blueprint(exports_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(media_bp)
    app.register_blueprint(api_bp)


def _register_template_helpers(app: Flask) -> None:
    from app.services.settings_service import SettingsService
    from app.utils.dates import format_date, format_datetime, humanize

    empty_counts = {
        "total": 0, "active": 0, "favorites": 0,
        "archived": 0, "trashed": 0, "words": 0,
    }

    @app.context_processor
    def inject_globals():
        from app.repositories.document_repository import DocumentRepository

        # A database problem must still leave the error page renderable, so
        # both lookups fall back rather than propagate.
        try:
            settings = SettingsService.all()
        except SQLAlchemyError:  # pragma: no cover
            settings = SettingsService.defaults()

        try:
            # One aggregate query feeds every badge in the sidebar.
            nav_counts = DocumentRepository.stats()
        except SQLAlchemyError:  # pragma: no cover
            nav_counts = empty_counts

        return {
            "settings": settings,
            "app_name": settings["app_name"],
            "app_version": app.config["APP_VERSION"],
            "nav_counts": nav_counts,
        }

    @app.template_filter("datetime_br")
    def _datetime_br(value):
        return format_datetime(value, SettingsService.get("timezone"))

    @app.template_filter("date_br")
    def _date_br(value):
        return format_date(value, SettingsService.get("timezone"))

    @app.template_filter("since")
    def _since(value):
        return humanize(value, SettingsService.get("timezone"))

    @app.template_filter("number_br")
    def _number_br(value):
        try:
            return f"{int(value):,}".replace(",", ".")
        except (TypeError, ValueError):
            return value


def _register_generated_assets(app: Flask) -> None:
    """Serve the user-chosen accent colour as a stylesheet.

    A ``style`` attribute would require ``'unsafe-inline'`` in the CSP; a
    same-origin stylesheet keeps the policy strict.
    """

    @app.route("/assets/theme.css")
    def theme_css() -> Response:
        from app.services.settings_service import SettingsService

        accent = SettingsService.get("accent_color", "#4F46E5")
        css = render_template("theme.css.jinja", accent=accent)
        response = Response(css, mimetype="text/css")
        response.headers["Cache-Control"] = "no-cache"
        return response


def _register_cli(app: Flask) -> None:
    from app.cli import register_commands

    register_commands(app)


def bootstrap_database(app: Flask) -> None:
    """Create tables on first run and make sure the search index exists.

    Called by serving entry points only, never during ``create_app``.
    Migrations remain the canonical schema path (``flask db upgrade``); this
    exists so a fresh clone runs without a setup step.
    """
    from sqlalchemy import inspect

    from app.services.search_service import search_index

    try:
        inspector = inspect(db.engine)
        if app.config.get("AUTO_CREATE_DB", True) and not inspector.has_table("documents"):
            db.create_all()
            app.logger.info("Banco de dados criado.")

        # The FTS5 virtual table lives outside the ORM metadata, so it is
        # created here regardless of how the schema itself was built.
        if inspector.has_table("documents"):
            search_index.ensure()
    except SQLAlchemyError:  # pragma: no cover - never block startup on this
        app.logger.exception("Não foi possível preparar o banco de dados.")
