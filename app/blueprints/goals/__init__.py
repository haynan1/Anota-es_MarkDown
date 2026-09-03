from flask import Blueprint

# Duas áreas de endereço, uma funcionalidade - o mesmo arranjo dos mapas
# mentais. ``/metas`` é HTML que funciona com um formulário e um recarregar de
# página; ``/api/metas`` é a esteira falando enquanto um cartão é arrastado, e
# precisa viver sob ``/api/`` porque é daí que o tratador de erros decide
# responder em JSON.
goals_bp = Blueprint("goals", __name__)

from app.blueprints.goals import routes  # noqa: E402,F401
