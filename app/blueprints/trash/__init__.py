from flask import Blueprint

trash_bp = Blueprint("trash", __name__, url_prefix="/lixeira")

from app.blueprints.trash import routes  # noqa: E402,F401
