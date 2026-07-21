"""Development entry point.

Binds to 127.0.0.1 by default. The application has no authentication, so
exposing it on 0.0.0.0 makes every document readable by anyone on the network -
do that only deliberately, via HOST in .env, and read the README first.

This is Flask's development server: convenient, not a production server.
The interactive debugger is never enabled; see app/server.py for why.
"""

from __future__ import annotations

from app import bootstrap_database, create_app
from app.server import build_run_options, hide_server_banner, make_console_utf8_safe

app = create_app()


if __name__ == "__main__":
    # Inside the __main__ guard on purpose: `FLASK_APP=run.py` makes the CLI
    # import this module, so bootstrapping at module level would run during
    # `flask db upgrade` and create the schema before Alembic could stamp it.
    # Before the first print: the banner is not ASCII, and a redirected stdout
    # on Windows would otherwise raise UnicodeEncodeError instead of starting.
    make_console_utf8_safe()

    with app.app_context():
        bootstrap_database(app)

    hide_server_banner()
    options = build_run_options(app.config)

    for warning in options.warnings:
        app.logger.warning(warning)
        print(f"  AVISO: {warning}\n")

    print(f"\n  {app.config['APP_NAME']} → http://{options.host}:{options.port}\n")
    app.run(**options.as_kwargs())
