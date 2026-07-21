from flask import Blueprint

history_bp = Blueprint("history", __name__, url_prefix="/documentos")

from app.blueprints.history import routes  # noqa: E402,F401
