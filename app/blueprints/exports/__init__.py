from flask import Blueprint

exports_bp = Blueprint("exports", __name__, url_prefix="/exportar")

from app.blueprints.exports import routes  # noqa: E402,F401
