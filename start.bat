@echo off
REM Markdown Studio - inicializacao no Windows (Prompt de Comando)
REM Cria o ambiente virtual se faltar, instala dependencias, aplica as
REM migrations e sobe o servidor local.

setlocal
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Criando ambiente virtual...
    python -m venv venv
    if errorlevel 1 goto :erro
)

set PYTHON=venv\Scripts\python.exe

echo Instalando dependencias...
"%PYTHON%" -m pip install --upgrade pip --quiet
"%PYTHON%" -m pip install -r requirements.txt --quiet
if errorlevel 1 goto :erro

if not exist ".env" (
    echo Criando .env a partir do exemplo...
    copy /y ".env.example" ".env" >nul
    echo Abra o arquivo .env e defina uma SECRET_KEY antes de usar em producao.
)

echo Aplicando migrations...
set FLASK_APP=run.py
"%PYTHON%" -m flask db upgrade
if errorlevel 1 goto :erro

echo.
echo Markdown Studio iniciando... encerre com Ctrl+C.
echo.
"%PYTHON%" run.py
goto :fim

:erro
echo.
echo Falha na inicializacao. Consulte a secao "Solucao de problemas" no README.
exit /b 1

:fim
endlocal
