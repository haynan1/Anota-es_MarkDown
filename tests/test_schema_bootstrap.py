"""Schema reconciliation on startup.

The bug these cover: a database created by an earlier release stays behind
when new code arrives, and the feature whose migration never ran answers 500
on its first request. The application owns bringing its own schema forward.

Each test builds its own application against its own file, because the point
under test is what happens *before* a session exists - the shared ``app``
fixture has already created the schema by the time a test sees it.
"""

from __future__ import annotations

import sqlite3

from alembic.script import ScriptDirectory
from flask_migrate import downgrade

from app import SNAPSHOTS_KEPT, bootstrap_database, create_app
from app.extensions import db as _db
from app.extensions import migrate


def build(tmp_path, database, **overrides):
    application = create_app(
        "testing",
        {
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
            "BACKUP_DIR": tmp_path / "backups",
            "EXPORT_DIR": tmp_path / "exports",
            "UPLOAD_DIR": tmp_path / "uploads",
            "TESTING": True,
            **overrides,
        },
    )
    with application.app_context():
        bootstrap_database(application)
    return application


def head(application):
    with application.app_context():
        return ScriptDirectory.from_config(migrate.get_config()).get_current_head()


def revision(database):
    connection = sqlite3.connect(database)
    try:
        row = connection.execute("select version_num from alembic_version").fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        return None
    finally:
        connection.close()


def tables(database):
    connection = sqlite3.connect(database)
    try:
        return {
            name for (name,) in connection.execute(
                "select name from sqlite_master where type='table'"
            )
        }
    finally:
        connection.close()


def columns(database, table):
    connection = sqlite3.connect(database)
    try:
        return {row[1] for row in connection.execute(f"pragma table_info({table})")}
    finally:
        connection.close()


def rewind(application):
    """Put a database back in the state the previous release left it in.

    By running the head migration's own ``downgrade`` rather than by undoing
    its effects by hand here. Two things follow from that. It stays correct
    when the head moves - a hardcoded list of tables to drop describes one
    particular migration, and quietly stops describing the schema the moment
    another one lands, which is how a test about *being behind* ends up
    testing nothing. And it means every release's migration is exercised in
    both directions, which is the promise each of them makes.
    """
    with application.app_context():
        downgrade(revision="-1")


def previous(application):
    with application.app_context():
        script = ScriptDirectory.from_config(migrate.get_config())
        return script.get_revision(script.get_current_head()).down_revision


def test_fresh_database_is_created_and_stamped(tmp_path):
    """A fresh clone must not leave Alembic with nothing to build on."""
    database = tmp_path / "fresh.db"
    application = build(tmp_path, database)

    assert "documents" in tables(database)
    assert revision(database) == head(application)


def test_pending_migration_is_applied_on_startup(tmp_path):
    database = tmp_path / "behind.db"
    application = build(tmp_path, database)
    behind = previous(application)
    rewind(application)

    assert revision(database) == behind
    # Whatever the head migration adds, named concretely: a test that only
    # compared revision numbers would pass against a migration that ran and
    # did nothing.
    assert "layout" not in columns(database, "mind_map_nodes")

    build(tmp_path, database)

    assert revision(database) == head(application)
    assert "layout" in columns(database, "mind_map_nodes")


def test_pending_migration_leaves_a_snapshot_behind(tmp_path):
    database = tmp_path / "snapshot.db"
    application = build(tmp_path, database)
    behind = previous(application)
    rewind(application)

    build(tmp_path, database)

    snapshots = list((tmp_path / "backups" / "pre-migracao").glob("*.db"))
    assert len(snapshots) == 1
    # A copy, not a placeholder: it still answers as the database it was.
    assert revision(snapshots[0]) == behind
    # And it stays out of the backup list the user actually chose to build.
    assert not list((tmp_path / "backups").glob("*.zip"))


def test_snapshots_do_not_accumulate(tmp_path):
    database = tmp_path / "many.db"
    application = build(tmp_path, database)
    behind = previous(application)

    for _ in range(SNAPSHOTS_KEPT + 2):
        rewind(application)
        build(tmp_path, database)

    snapshots = list((tmp_path / "backups" / "pre-migracao").glob("*.db"))
    assert len(snapshots) == SNAPSHOTS_KEPT


def test_startup_is_idempotent(tmp_path):
    database = tmp_path / "again.db"
    application = build(tmp_path, database)

    build(tmp_path, database)
    build(tmp_path, database)

    assert revision(database) == head(application)
    assert not (tmp_path / "backups" / "pre-migracao").exists()


def test_unversioned_database_is_left_alone(tmp_path):
    """Guessing a revision would apply the wrong upgrade to real data."""
    database = tmp_path / "legacy.db"
    build(tmp_path, database)
    connection = sqlite3.connect(database)
    connection.execute("delete from alembic_version")
    connection.commit()
    connection.close()

    build(tmp_path, database)

    assert revision(database) is None
    assert not (tmp_path / "backups" / "pre-migracao").exists()


def test_opting_out_leaves_the_schema_to_flask_db(tmp_path):
    """AUTO_CREATE_DB=0 is what generating a migration relies on."""
    database = tmp_path / "manual.db"
    build(tmp_path, database, AUTO_CREATE_DB=False)

    assert "documents" not in tables(database)
