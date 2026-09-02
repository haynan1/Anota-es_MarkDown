from flask import Blueprint

# No url_prefix: this blueprint owns two address spaces on purpose - the pages
# under ``/mapas`` and the canvas API under ``/api/mapas``. They belong to one
# feature, and the error handler decides "answer in JSON" from the ``/api/``
# prefix, so the JSON routes have to live there rather than under the pages.
mindmaps_bp = Blueprint("mindmaps", __name__)

from app.blueprints.mindmaps import routes  # noqa: E402,F401
