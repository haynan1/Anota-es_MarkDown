"""Response hardening.

The Content-Security-Policy is deliberately strict: no ``'unsafe-inline'``
anywhere. That is only possible because the application ships zero inline
scripts and zero inline styles - the sanitizer rewrites markdown's inline
``style`` attributes into classes, and the accent colour is served from a
generated stylesheet rather than a ``style`` attribute.
"""

from __future__ import annotations

from flask import Flask

CSP_DIRECTIVES: dict[str, str] = {
    "default-src": "'self'",
    "base-uri": "'none'",
    "object-src": "'none'",
    # Modern equivalent of X-Frame-Options; both are sent for older browsers.
    "frame-ancestors": "'none'",
    "form-action": "'self'",
    "script-src": "'self'",
    "style-src": "'self'",
    "font-src": "'self'",
    "connect-src": "'self'",
    "manifest-src": "'self'",
    # Uploaded audio/video is served from this origin only.
    "media-src": "'self'",
    # Remote images render in the browser (a client-side fetch, same risk as
    # any web page). They are still blocked during PDF generation, where the
    # fetch would happen server-side and become an SSRF vector.
    "img-src": "'self' data: https:",
}

SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
        "magnetometer=(), microphone=(), payment=(), usb=()"
    ),
}


def build_csp() -> str:
    return "; ".join(f"{key} {value}" for key, value in CSP_DIRECTIVES.items())


def register_security(app: Flask) -> None:
    csp_value = build_csp()

    @app.after_request
    def apply_headers(response):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault("Content-Security-Policy", csp_value)

        # Werkzeug advertises its own and Python's exact version, which just
        # hands an attacker a version to look up advisories against.
        response.headers["Server"] = "Markdown Studio"

        # Documents and exports must never be cached. Assigned rather than
        # defaulted: send_file already sets "no-cache", which is weaker.
        if response.mimetype in {"application/pdf", "text/markdown", "application/zip"}:
            response.headers["Cache-Control"] = "no-store"
        return response
