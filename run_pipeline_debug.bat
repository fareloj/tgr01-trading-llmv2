@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo TGR-01 Trading LLM V2 - Pipeline Debug
echo Workspace: %CD%
echo ============================================================
echo.

echo [1/6] Inicializando SQLite e mostrando diagnostico...
python .\backend\core\database.py
if errorlevel 1 goto error
echo.
pause

echo [2/6] Seed historico opcional para evitar esperar 30+ minutos.
choice /C SN /M "Rodar seed_historical_data.py --limit 100 agora"
if errorlevel 2 goto skip_seed

python .\backend\tests\seed_historical_data.py --limit 100
if errorlevel 1 goto error
echo.

:skip_seed
echo [3/6] Abrindo price_worker em uma nova janela...
start "TGR-01 price_worker" cmd /k "cd /d ""%CD%"" && python .\backend\data\price_worker.py"
echo.

echo [4/6] Abrindo news_worker em uma nova janela...
start "TGR-01 news_worker" cmd /k "cd /d ""%CD%"" && python .\backend\data\news_worker.py --mode real --interval 900"
echo.

echo Aguarde alguns segundos para os workers registrarem heartbeat/candles.
pause

echo [5/6] Validando payload_builder...
python .\backend\features\payload_builder.py
if errorlevel 1 goto error
echo.
echo Confira acima:
echo - DB path correto
echo - technical_context.status igual a OK
echo.
pause

echo [6/6] Paper trading opcional.
choice /C SN /M "Rodar run_paper_trading.py --cycles 3 --sleep 10 agora"
if errorlevel 2 goto done

python .\backend\tests\run_paper_trading.py --cycles 3 --sleep 10
if errorlevel 1 goto error

:done
echo.
echo Pipeline debug finalizado.
echo Workers continuam nas janelas separadas ate voce fechar cada uma.
pause
exit /b 0

:error
echo.
echo [ERRO] Um comando falhou. Revise o output acima antes de continuar.
pause
exit /b 1
