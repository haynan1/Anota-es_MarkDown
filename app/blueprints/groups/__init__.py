from flask import Blueprint

groups_bp = Blueprint("groups", __name__, url_prefix="/grupos")

from app.blueprints.groups import routes  # noqa: E402,F401
