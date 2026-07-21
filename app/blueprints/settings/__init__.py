from flask import Blueprint

settings_bp = Blueprint("settings", __name__, url_prefix="/configuracoes")

from app.blueprints.settings import routes  # noqa: E402,F401
