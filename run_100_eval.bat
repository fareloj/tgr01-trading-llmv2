@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo TGR-01 Trading LLM V2 - Experimento 100 ciclos + relatorios
echo Workspace: %CD%
echo ============================================================
echo.
echo Escolha o intervalo entre ciclos:
echo 1. 30 segundos
echo 2. 60 segundos
echo.
choice /C 12 /M "Intervalo"
if errorlevel 2 (
  set SLEEP_SECONDS=60
) else (
  set SLEEP_SECONDS=30
)

python .\backend\ops\run_experiment.py --cycles 100 --sleep %SLEEP_SECONDS%
if errorlevel 1 (
  echo.
  echo [ERRO] Experimento interrompido. Revise o output acima.
  pause
  exit /b 1
)

echo.
echo [OK] Experimento concluido. Veja backend\reports.
pause
