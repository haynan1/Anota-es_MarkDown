from flask import Blueprint

documents_bp = Blueprint("documents", __name__, url_prefix="/documentos")

from app.blueprints.documents import routes  # noqa: E402,F401
