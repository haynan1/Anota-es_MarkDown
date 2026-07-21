# Markdown Studio - inicialização no Windows (PowerShell)
#
# Cria o ambiente virtual se faltar, instala dependências, aplica as migrations
# e sobe o servidor local.
#
#   .\start.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot "venv\Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Criando ambiente virtual..." -ForegroundColor Cyan
    python -m venv venv
}

Write-Host "Instalando dependências..." -ForegroundColor Cyan
& $python -m pip install --upgrade pip --quiet
& $python -m pip install -r requirements.txt --quiet

if (-not (Test-Path ".env")) {
    Write-Host "Criando .env a partir do exemplo..." -ForegroundColor Cyan
    Copy-Item ".env.example" ".env"

    $key = & $python -c "import secrets; print(secrets.token_urlsafe(48))"
    (Get-Content ".env") -replace "^SECRET_KEY=.*$", "SECRET_KEY=$key" |
        Set-Content ".env" -Encoding utf8
    Write-Host "Chave secreta gerada." -ForegroundColor Green
}

Write-Host "Aplicando migrations..." -ForegroundColor Cyan
$env:FLASK_APP = "run.py"
& $python -m flask db upgrade

Write-Host ""
Write-Host "Markdown Studio iniciando..." -ForegroundColor Green
Write-Host "Encerre com Ctrl+C." -ForegroundColor DarkGray
Write-Host ""

& $python run.py
