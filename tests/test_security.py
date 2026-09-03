"""Security headers, CSRF, limits and error handling."""

from __future__ import annotations

import io

import pytest

from app.security import CSP_DIRECTIVES, build_csp


class TestSecurityHeaders:
    def test_headers_are_present_on_every_response(self, client):
        response = client.get("/")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "Content-Security-Policy" in response.headers
        assert "Permissions-Policy" in response.headers

    def test_csp_forbids_inline_code(self, client):
        policy = client.get("/").headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in policy
        assert "'unsafe-eval'" not in policy
        assert "default-src 'self'" in policy
        assert "frame-ancestors 'none'" in policy
        assert "object-src 'none'" in policy
        assert "base-uri 'none'" in policy

    def test_csp_is_built_from_the_declared_directives(self):
        policy = build_csp()
        for directive in CSP_DIRECTIVES:
            assert directive in policy

    def test_headers_are_present_on_error_pages(self, client):
        response = client.get("/nao-existe")
        assert response.status_code == 404
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    def test_downloads_are_not_cached(self, client, document):
        response = client.get(f"/exportar/{document.uuid}/markdown")
        assert response.headers.get("Cache-Control") == "no-store"


class TestRenderedPagesHaveNoInlineCode:
    """The strict CSP only works because no template emits inline code."""

    @pytest.mark.parametrize(
        "path",
        [
            "/", "/documentos/", "/lixeira/", "/configuracoes/",
            "/documentos/categorias",
            "/metas/", "/metas/esteira", "/metas/plano", "/metas/conquistas",
            "/metas/nova", "/metas/frases",
        ],
    )
    def test_pages_contain_no_inline_style_or_handler(self, client, document, path):
        body = client.get(path).data.decode("utf-8")
        assert " style=" not in body
        assert " onclick=" not in body
        assert " onerror=" not in body
        assert " onload=" not in body

    def test_editor_contains_no_inline_code(self, client, document):
        body = client.get(f"/editor/{document.uuid}").data.decode("utf-8")
        assert " style=" not in body
        assert " onclick=" not in body

    def test_theme_stylesheet_is_served_same_origin(self, client):
        response = client.get("/assets/theme.css")
        assert response.status_code == 200
        assert response.mimetype == "text/css"
        assert "--accent" in response.data.decode("utf-8")


class TestCsrf:
    def test_post_without_a_token_is_rejected(self, csrf_app):
        client = csrf_app.test_client()
        response = client.post("/documentos/novo")
        assert response.status_code == 400

    def test_api_without_a_token_is_rejected(self, csrf_app):
        from app.services.document_service import DocumentService

        with csrf_app.app_context():
            document = DocumentService.create(title="Protegido", content_markdown="x")
            uuid = document.uuid

        client = csrf_app.test_client()
        response = client.post(
            f"/api/documentos/{uuid}/autosave",
            json={"title": "Invadido", "content_markdown": "hack", "revision": 1},
        )
        assert response.status_code == 400

    def test_the_token_is_available_to_the_frontend(self, csrf_app):
        client = csrf_app.test_client()
        body = client.get("/").data.decode("utf-8")
        assert 'name="csrf-token"' in body

    def test_a_bulk_action_over_every_result_is_rejected_without_a_token(
        self, csrf_app
    ):
        """The widest action in the app is not the one that skips the check.

        "Todos os resultados" is the only request that names no document and
        still writes to many, so it is the one worth pinning: a forged form
        must not be able to archive - or bin - a whole library.
        """
        from app.services.document_service import DocumentService

        with csrf_app.app_context():
            DocumentService.create(title="Alvo", content_markdown="x")

        response = csrf_app.test_client().post(
            "/documentos/acoes-em-massa",
            data={"acao": "trash", "selecao": "filtro", "filtros": ""},
        )
        assert response.status_code == 400

    def test_a_valid_token_is_accepted(self, csrf_app):
        client = csrf_app.test_client()
        page = client.get("/").data.decode("utf-8")
        marker = 'name="csrf-token" content="'
        token = page.split(marker, 1)[1].split('"', 1)[0]

        response = client.post("/documentos/novo", data={"csrf_token": token})
        assert response.status_code == 302


class TestLimits:
    def test_upload_over_the_limit_returns_413(self, app, client):
        app.config["MAX_CONTENT_LENGTH"] = 1024
        response = client.post(
            "/documentos/importar",
            data={"file": (io.BytesIO(b"x" * 5000), "grande.md"), "action": "import"},
            content_type="multipart/form-data",
        )
        assert response.status_code == 413

    def test_the_413_page_is_friendly(self, app, client):
        app.config["MAX_CONTENT_LENGTH"] = 1024
        response = client.post(
            "/documentos/importar",
            data={"file": (io.BytesIO(b"x" * 5000), "grande.md")},
            content_type="multipart/form-data",
        )
        assert "grande demais".encode() in response.data

    def test_preview_rejects_an_oversized_body(self, client, app):
        response = client.post(
            "/api/preview", json={"content_markdown": "x" * (3 * 1024 * 1024)}
        )
        assert response.status_code == 400
        assert response.get_json()["ok"] is False


class TestErrorHandling:
    def test_404_page_offers_a_way_forward(self, client):
        response = client.get("/caminho/inexistente")
        assert response.status_code == 404
        body = response.data.decode("utf-8")
        assert "não encontrada" in body
        assert "/documentos/" in body

    def test_errors_never_leak_internals(self, client):
        body = client.get("/caminho/inexistente").data.decode("utf-8")
        assert "Traceback" not in body
        assert "sqlalchemy" not in body.lower()
        assert "werkzeug" not in body.lower()

    def test_api_errors_return_json(self, client):
        response = client.post("/api/documentos/nao-existe/autosave", json={})
        assert response.status_code == 404
        assert response.is_json
        assert response.get_json()["ok"] is False

    def test_service_errors_become_json_on_the_api(self, client, document):
        response = client.post(
            f"/api/documentos/{document.uuid}/autosave", json={"title": None}
        )
        assert response.status_code == 400
        assert response.is_json


class TestOpenRedirect:
    def test_next_parameter_cannot_point_off_site(self, client, document):
        response = client.post(
            f"/documentos/{document.uuid}/favorito",
            data={"next": "https://evil.test/phish"},
        )
        assert response.status_code == 302
        assert "evil.test" not in response.headers["Location"]

    def test_protocol_relative_next_is_rejected(self, client, document):
        response = client.post(
            f"/documentos/{document.uuid}/favorito", data={"next": "//evil.test"}
        )
        assert "evil.test" not in response.headers["Location"]

    def test_a_local_next_is_honoured(self, client, document):
        response = client.post(
            f"/documentos/{document.uuid}/favorito", data={"next": "/lixeira/"}
        )
        assert response.headers["Location"].endswith("/lixeira/")


class TestServerBinding:
    def test_the_default_host_is_loopback(self, app):
        assert app.config["HOST"] == "127.0.0.1"

    def test_cookies_are_hardened(self, app):
        assert app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
