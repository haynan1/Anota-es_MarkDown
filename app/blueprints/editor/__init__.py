from flask import Blueprint

editor_bp = Blueprint("editor", __name__, url_prefix="/editor")

from app.blueprints.editor import routes  # noqa: E402,F401
