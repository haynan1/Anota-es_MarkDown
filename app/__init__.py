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
from app.utils.files import ensure_directory, unique_path


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
    from app.blueprints.mindmaps import mindmaps_bp
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
    app.register_blueprint(mindmaps_bp)
    app.register_blueprint(api_bp)


def _register_template_helpers(app: Flask) -> None:
    from app.services.settings_service import SettingsService
    from app.utils.dates import format_date, format_datetime, humanize
    from app.utils.humanize import format_number_br

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
        return format_number_br(value)


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
    """Bring the schema up to the version this code expects, then serve.

    Called by serving entry points only, never during ``create_app``.

    A database arrives here in one of four states, and only one of them used
    to be handled:

    * **empty** - a fresh clone. Build the schema from the models and stamp it
      at the migration head, so the first ``flask db upgrade`` has a baseline
      instead of trying to create tables that are already there.
    * **behind** - an install that pulled new code. Upgrade it. This is the
      state that matters: a feature whose migration was never applied answers
      *Internal Server Error* on its very first request, and the person who
      just pulled has no reason to suspect the database. The file is copied
      first, so an interrupted migration is never the end of the story.
    * **current** - nothing to do.
    * **unstamped** - tables present, no Alembic version: a schema built
      before migrations existed. Left untouched and reported, because
      guessing which revision it matches would apply the wrong upgrade to
      real data.

    ``AUTO_CREATE_DB=0`` opts out of all of it and hands the schema back to
    ``flask db``, which is what generating a migration needs.
    """
    from sqlalchemy import inspect

    from app.services.search_service import search_index

    try:
        if app.config.get("AUTO_CREATE_DB", True):
            _reconcile_schema(app)

        # The FTS5 virtual table lives outside the ORM metadata, so it is
        # created here regardless of how the schema itself was built.
        if inspect(db.engine).has_table("documents"):
            search_index.ensure()
    except SQLAlchemyError:  # pragma: no cover - never block startup on this
        app.logger.exception("Não foi possível preparar o banco de dados.")


def _reconcile_schema(app: Flask) -> None:
    """Move the database to the migration head, or explain why it cannot be."""
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from flask_migrate import stamp, upgrade
    from sqlalchemy import inspect

    head = ScriptDirectory.from_config(migrate.get_config()).get_current_head()
    with db.engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    if not inspect(db.engine).has_table("documents"):
        db.create_all()
        if head:
            stamp(revision=head)
        app.logger.info("Banco de dados criado na revisão %s.", head or "inicial")
        return

    if current == head:
        return

    if current is None:
        app.logger.warning(
            "O banco existe mas não está versionado pelo Alembic. Rode "
            "'flask db stamp <revisão>' seguido de 'flask db upgrade' antes "
            "de usar recursos novos."
        )
        return

    snapshot = _snapshot_database(app, label=current)
    if snapshot:
        app.logger.info("Cópia do banco antes da migração: %s", snapshot)

    app.logger.info("Atualizando o banco de %s para %s.", current, head)
    upgrade()
    app.logger.info("Banco atualizado para %s.", head)


# How many pre-migration snapshots to keep. Enough to walk back a bad upgrade,
# few enough that a database measured in tens of megabytes does not quietly
# fill the disk one release at a time.
SNAPSHOTS_KEPT = 3


def _snapshot_database(app: Flask, label: str) -> Path | None:
    """Copy a SQLite database aside before migrating it.

    Uses SQLite's online backup API rather than copying the file: the
    connection runs in WAL mode, so the ``.db`` on its own can be missing the
    most recent writes. Returns ``None`` for anything that is not SQLite - a
    server database has its own backup story and is not ours to copy.

    These live in their own directory rather than among the ZIP backups. They
    are not the same thing: a backup is a portable export the user asked for,
    this is a raw file kept in case a migration goes wrong, and mixing them
    would put a 50 MB file nobody chose in a list of files somebody did.
    """
    import sqlite3
    from datetime import datetime, timezone

    url = db.engine.url
    if url.get_backend_name() != "sqlite" or not url.database:
        return None

    source = Path(url.database)
    if not source.exists():
        return None

    directory = ensure_directory(Path(app.config["BACKUP_DIR"]) / "pre-migracao")
    stamped = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    # Through unique_path: the name has a resolution of one second, and two
    # starts inside the same second would otherwise overwrite the copy the
    # first one took - losing exactly the state worth keeping.
    target = unique_path(directory, f"{source.stem}-{label}-{stamped}.db")

    try:
        with sqlite3.connect(source) as origin, sqlite3.connect(target) as copy:
            origin.backup(copy)
    except (sqlite3.Error, OSError):
        app.logger.exception("Não foi possível copiar o banco antes da migração.")
        return None

    # Pruned after the copy succeeds, never before: the newest snapshot is the
    # one worth keeping, and it does not exist until this point.
    # By modification time, not by name: the revision sits in the middle of the
    # filename, so an alphabetical sort groups by revision and only then by
    # date - which would delete the newest snapshot of the newest revision.
    # ``target`` is held out rather than ranked, because Windows timestamps are
    # coarse enough that the copy just taken can tie with the one before it.
    others = (path for path in directory.glob("*.db") if path != target)
    by_age = sorted(others, key=lambda item: item.stat().st_mtime, reverse=True)
    for stale in by_age[SNAPSHOTS_KEPT - 1 :]:
        try:
            stale.unlink()
        except OSError:  # pragma: no cover - a locked file is not worth failing over
            app.logger.warning("Não foi possível remover a cópia antiga %s", stale.name)

    return target
