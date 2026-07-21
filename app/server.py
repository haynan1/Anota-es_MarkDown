"""Development server options.

Isolated from ``run.py`` so the safety rules below are unit-testable rather
than being implicit in a script nobody tests.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def make_console_utf8_safe() -> None:
    """Keep a non-ASCII character from killing the process on startup.

    On Windows, a redirected stdout defaults to the ANSI code page (cp1252),
    which cannot encode the arrow in the startup banner or the accents in the
    warnings. ``python run.py > server.log`` therefore died with
    UnicodeEncodeError before the server ever bound its port - the app was
    unrunnable under any redirect, pipe or service wrapper.

    The messages are not dumbed down to ASCII; the stream is told what they
    are. ``errors="replace"`` guarantees that a console which genuinely cannot
    represent a character prints a placeholder rather than raising.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # Already wrapped by something else; leave it alone.
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - detached stream
            pass


@dataclass(frozen=True, slots=True)
class RunOptions:
    host: str
    port: int
    debug: bool
    use_debugger: bool
    use_reloader: bool
    warnings: tuple[str, ...]

    def as_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "debug": self.debug,
            "use_debugger": self.use_debugger,
            "use_reloader": self.use_reloader,
        }


def is_loopback(host: str) -> bool:
    return (host or "").strip().lower() in LOOPBACK_HOSTS


def hide_server_banner() -> None:
    """Stop the development server announcing its exact versions.

    Werkzeug writes the ``Server`` header inside the WSGI layer, below Flask,
    so an ``after_request`` hook cannot replace it - the value has to be
    changed on the request handler class itself.
    """
    from werkzeug.serving import WSGIRequestHandler

    WSGIRequestHandler.server_version = "Markdown Studio"
    WSGIRequestHandler.sys_version = ""


def build_run_options(config) -> RunOptions:
    """Decide how the development server may run.

    Two rules, both non-negotiable:

    1. **The interactive debugger is never enabled.** Flask's ``debug=True``
       normally mounts the Werkzeug debugger, which exposes an interactive
       Python console at ``/console``. That console is remote code execution
       guarded only by a PIN that is printed to the server log and derived
       from machine data. Auto-reload is worth keeping; a code-execution
       endpoint in an app with no authentication is not.

    2. **Debug mode is dropped entirely when not bound to loopback.** If the
       user follows the README instructions to reach the app from another
       device, verbose tracebacks stop being a convenience and become
       information disclosure to the whole network.
    """
    host = (config.get("HOST") or "127.0.0.1").strip()
    port = int(config.get("PORT") or 5000)
    debug = bool(config.get("DEBUG", False))

    warnings: list[str] = []
    loopback = is_loopback(host)

    if not loopback:
        warnings.append(
            f"A aplicação está escutando em {host} e NÃO possui autenticação. "
            "Qualquer pessoa na mesma rede poderá ler, editar e apagar seus "
            "documentos."
        )
        if debug:
            debug = False
            warnings.append(
                "Modo debug desativado automaticamente porque o servidor não "
                "está restrito a 127.0.0.1."
            )

    return RunOptions(
        host=host,
        port=port,
        debug=debug,
        # Never mount the Werkzeug debugger - see rule 1 above.
        use_debugger=False,
        use_reloader=debug,
        warnings=tuple(warnings),
    )
