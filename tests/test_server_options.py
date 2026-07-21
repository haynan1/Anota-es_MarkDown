"""Rules that govern how the development server is allowed to run.

Found during the QA pass: with ``debug=True`` Flask mounts the Werkzeug
debugger, which serves an interactive Python console at ``/console``. In an
application with no authentication that is a remote code execution endpoint
guarded only by a PIN printed to the server log.
"""

from __future__ import annotations

import pytest

from app.server import build_run_options, is_loopback


def config(**overrides):
    base = {"HOST": "127.0.0.1", "PORT": 5000, "DEBUG": True}
    base.update(overrides)
    return base


class TestDebuggerIsNeverMounted:
    def test_debugger_is_off_even_in_debug_mode(self):
        options = build_run_options(config(DEBUG=True))
        assert options.debug is True
        assert options.use_debugger is False

    def test_debugger_is_off_in_production_mode(self):
        options = build_run_options(config(DEBUG=False))
        assert options.use_debugger is False

    def test_reloader_still_works_in_debug(self):
        """Convenience is kept; only the code-execution surface is removed."""
        assert build_run_options(config(DEBUG=True)).use_reloader is True

    def test_run_kwargs_pin_the_debugger_off(self):
        kwargs = build_run_options(config(DEBUG=True)).as_kwargs()
        assert kwargs["use_debugger"] is False

    def test_entrypoint_does_not_bypass_the_helper(self):
        """run.py must not call app.run with its own flags."""
        from pathlib import Path

        source = Path(__file__).resolve().parent.parent / "run.py"
        text = source.read_text(encoding="utf-8")
        assert "build_run_options" in text
        assert "app.run(**options.as_kwargs())" in text


class TestNonLoopbackBinding:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_hosts_are_recognised(self, host):
        assert is_loopback(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.0.10", "10.0.0.5", ""])
    def test_non_loopback_hosts_are_recognised(self, host):
        assert is_loopback(host) is False or host == ""

    def test_debug_is_dropped_when_exposed_to_the_network(self):
        options = build_run_options(config(HOST="0.0.0.0", DEBUG=True))
        assert options.debug is False
        assert options.use_debugger is False
        assert options.use_reloader is False

    def test_exposure_produces_a_warning(self):
        options = build_run_options(config(HOST="0.0.0.0", DEBUG=True))
        joined = " ".join(options.warnings)
        assert "não possui autenticação" in joined.lower() or "autentica" in joined
        assert any("debug" in warning.lower() for warning in options.warnings)

    def test_loopback_produces_no_warning(self):
        assert build_run_options(config()).warnings == ()

    def test_port_and_host_are_carried_through(self):
        options = build_run_options(config(HOST="0.0.0.0", PORT=8080))
        assert options.host == "0.0.0.0"
        assert options.port == 8080


class TestBootstrapDoesNotRaceMigrations:
    """Found in QA: the schema existed but Alembic had never stamped it.

    ``create_app()`` runs for every ``flask`` invocation, and ``FLASK_APP``
    imports ``run.py``. Bootstrapping in either place creates the tables
    before ``flask db upgrade`` can run, leaving a correct-looking schema with
    an empty ``alembic_version`` — and every later autogenerate confused.
    """

    def test_create_app_does_not_create_tables(self, tmp_path):
        from sqlalchemy import inspect

        from app import create_app
        from app.extensions import db

        database = tmp_path / "untouched.db"
        application = create_app(
            "testing",
            {
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
                "BACKUP_DIR": tmp_path / "b",
                "EXPORT_DIR": tmp_path / "e",
                "UPLOAD_DIR": tmp_path / "u",
            },
        )

        with application.app_context():
            assert inspect(db.engine).get_table_names() == []
            db.session.remove()

    def test_bootstrap_is_guarded_by_the_main_block(self):
        """Module-level bootstrapping would run on `flask db upgrade`."""
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "run.py").read_text(
            encoding="utf-8"
        )
        main_index = source.index('if __name__ == "__main__":')
        bootstrap_index = source.index("bootstrap_database(app)")

        assert bootstrap_index > main_index, (
            "bootstrap_database deve ficar dentro do bloco __main__"
        )

    def test_bootstrap_creates_the_schema_when_asked(self, tmp_path):
        from sqlalchemy import inspect

        from app import bootstrap_database, create_app
        from app.extensions import db

        database = tmp_path / "bootstrapped.db"
        application = create_app(
            "testing",
            {
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database.as_posix()}",
                "BACKUP_DIR": tmp_path / "b",
                "EXPORT_DIR": tmp_path / "e",
                "UPLOAD_DIR": tmp_path / "u",
            },
        )

        with application.app_context():
            bootstrap_database(application)
            tables = inspect(db.engine).get_table_names()
            assert "documents" in tables
            assert "media_assets" in tables
            db.session.remove()


class TestServerBanner:
    def test_version_is_not_disclosed(self, client):
        response = client.get("/")
        server = response.headers.get("Server", "")
        assert "Werkzeug" not in server
        assert "Python" not in server
