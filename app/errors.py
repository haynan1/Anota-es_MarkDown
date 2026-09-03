"""Error handling.

Users get a calm, actionable page. Operators get the stack trace in the log.
The two never swap places: no internal detail is rendered in a response.
"""

from __future__ import annotations

import logging

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app.extensions import db
from app.services.exceptions import ServiceError

logger = logging.getLogger(__name__)

ERROR_COPY: dict[int, tuple[str, str]] = {
    400: (
        "Pedido inválido",
        "Não foi possível interpretar esta solicitação. Verifique os dados "
        "informados e tente novamente.",
    ),
    403: (
        "Acesso não permitido",
        "Esta ação foi bloqueada por segurança. Recarregue a página e tente "
        "novamente.",
    ),
    404: (
        "Página não encontrada",
        "O endereço acessado não existe ou o documento foi removido.",
    ),
    413: (
        "Arquivo grande demais",
        "O arquivo enviado ultrapassa o limite permitido. Reduza o tamanho e "
        "tente novamente.",
    ),
    423: (
        "Protegido por cadeado",
        "Este item está travado contra alterações. Destrave-o antes de "
        "editá-lo ou removê-lo.",
    ),
    500: (
        "Algo deu errado",
        "Não foi possível concluir esta ação. Seus dados continuam protegidos. "
        "Tente novamente ou volte para os documentos.",
    ),
}


def wants_json() -> bool:
    """True when the caller is the editor's fetch layer rather than a browser."""
    if request.path.startswith("/api/"):
        return True
    return request.accept_mimetypes.best == "application/json"


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ServiceError)
    def handle_service_error(error: ServiceError):
        # Uma recusa é um evento operacional, e até aqui ela não deixava
        # rastro nenhum: o log de acesso mostrava um 400 vermelho e o log da
        # aplicação, nada. Quem fosse diagnosticar tinha uma captura de tela
        # do console e adivinhação.
        #
        # WARNING e não ERROR porque nada quebrou - o pedido foi entendido e
        # respondido. E é barato: estas exceções são recusas, não o caminho
        # feliz, então a linha só aparece quando alguém precisa dela.
        logger.warning(
            "%s %s recusado (%s): %s",
            request.method,
            request.path,
            error.status_code,
            error.message,
        )
        payload = {"ok": False, "error": error.message}
        if getattr(error, "server_state", None):
            payload["server_state"] = error.server_state
        if wants_json():
            return jsonify(payload), error.status_code
        return render_error(error.status_code, detail=error.message)

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException):
        code = error.code or 500
        if code >= 500:
            logger.error("HTTP %s em %s", code, request.path, exc_info=error)
        if wants_json():
            title, message = ERROR_COPY.get(code, ERROR_COPY[500])
            return jsonify({"ok": False, "error": message, "title": title}), code
        return render_error(code)

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception):
        # Never leave a half-applied transaction behind.
        db.session.rollback()
        logger.exception("Erro não tratado em %s", request.path)
        if app.config.get("DEBUG") and not app.config.get("TESTING"):
            raise error
        if wants_json():
            return jsonify({"ok": False, "error": ERROR_COPY[500][1]}), 500
        return render_error(500)


def render_error(code: int, detail: str | None = None):
    title, message = ERROR_COPY.get(code, ERROR_COPY[500])
    return (
        render_template(
            "errors/error.html",
            code=code,
            title=title,
            message=detail or message,
        ),
        code,
    )
