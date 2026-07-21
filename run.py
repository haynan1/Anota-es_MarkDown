"""Development entry point.

Binds to 127.0.0.1 by default. The application has no authentication, so
exposing it on 0.0.0.0 makes every document readable by anyone on the network -
do that only deliberately, via HOST in .env, and read the README first.

This is Flask's development server: convenient, not a production server.
"""

from __future__ import annotations

from app import create_app

app = create_app()


if __name__ == "__main__":
    host = app.config["HOST"]
    port = app.config["PORT"]

    if host not in {"127.0.0.1", "localhost", "::1"}:
        app.logger.warning(
            "A aplicação está escutando em %s e NÃO possui autenticação. "
            "Qualquer pessoa na mesma rede poderá ler e editar seus documentos.",
            host,
        )

    print(f"\n  {app.config['APP_NAME']} → http://{host}:{port}\n")
    app.run(host=host, port=port, debug=app.config.get("DEBUG", False))
