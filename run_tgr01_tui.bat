@echo off
setlocal EnableExtensions
cd /d "%~dp0"

python -c "import textual" >nul 2>nul
if errorlevel 1 (
  echo [ERRO] Dependencia Textual ausente.
  echo Rode: python -m pip install -r .\backend\requirements.txt
  exit /b 1
)

python .\backend\tui.py
