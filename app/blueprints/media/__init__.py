from flask import Blueprint

media_bp = Blueprint("media", __name__)

from app.blueprints.media import routes  # noqa: E402,F401
