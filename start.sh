#!/usr/bin/env bash
# Markdown Studio - inicialização no Linux e macOS
#
# Cria o ambiente virtual se faltar, instala dependências, aplica as migrations
# e sobe o servidor local.
#
#   chmod +x start.sh && ./start.sh

set -euo pipefail
cd "$(dirname "$0")"

PYTHON="venv/bin/python"

if [ ! -x "$PYTHON" ]; then
    echo "Criando ambiente virtual..."
    python3 -m venv venv
fi

echo "Instalando dependências..."
"$PYTHON" -m pip install --upgrade pip --quiet
"$PYTHON" -m pip install -r requirements.txt --quiet

if [ ! -f ".env" ]; then
    echo "Criando .env a partir do exemplo..."
    cp .env.example .env

    KEY="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    # O delimitador "|" evita conflito com "/" e "+" presentes na chave.
    sed -i.bak "s|^SECRET_KEY=.*$|SECRET_KEY=${KEY}|" .env && rm -f .env.bak
    echo "Chave secreta gerada."
fi

echo "Aplicando migrations..."
export FLASK_APP=run.py
"$PYTHON" -m flask db upgrade

echo
echo "Markdown Studio iniciando... encerre com Ctrl+C."
echo

exec "$PYTHON" run.py
