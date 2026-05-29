@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set LOG_DIR=%CD%\backend\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:menu
cls
echo ============================================================
echo TGR-01 Trading LLM V2 - Testes e Pipeline Real
echo Workspace: %CD%
echo ============================================================
echo.
echo 1. Diagnostico SQLite
echo 2. Iniciar workers reais em background
echo 3. Preflight de data para teste
echo 4. Rodar teste curto paper trading ^(10 ciclos / 30s^)
echo 5. Preflight estrito para pipeline real
echo 6. Rodar pipeline real paper trading ^(60 ciclos / 60s^)
echo 7. Analisar trade_logs
echo 8. Relatorio operacional completo
echo 9. Avaliar decisoes por movimento futuro
echo A. Revisao LLM do ultimo relatorio de avaliacao
echo B. Ver processos workers/paper
echo 0. Sair
echo.
choice /C 123456789AB0 /M "Escolha"

if errorlevel 12 goto done
if errorlevel 11 goto processes
if errorlevel 10 goto llm_review
if errorlevel 9 goto evaluate_decisions
if errorlevel 8 goto readiness_report
if errorlevel 7 goto analyze
if errorlevel 6 goto real_pipeline
if errorlevel 5 goto strict_preflight
if errorlevel 4 goto short_test
if errorlevel 3 goto test_preflight
if errorlevel 2 goto start_workers
if errorlevel 1 goto diagnostics

:diagnostics
echo.
echo [1] Diagnostico SQLite...
python .\backend\core\database.py
if errorlevel 1 goto error
pause
goto menu

:start_workers
echo.
echo [2] Iniciando workers reais em background...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$logDir = Join-Path (Get-Location) 'backend\logs';" ^
  "New-Item -ItemType Directory -Force -Path $logDir | Out-Null;" ^
  "$existingPrice = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'price_worker\.py' };" ^
  "if ($existingPrice) { Write-Host '[OK] price_worker ja esta rodando.' } else { Start-Process -FilePath 'python' -ArgumentList @('-u', '.\backend\data\price_worker.py') -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'price_worker.out.log') -RedirectStandardError (Join-Path $logDir 'price_worker.err.log'); Write-Host '[OK] price_worker iniciado.' };" ^
  "$existingNews = Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'news_worker\.py' };" ^
  "if ($existingNews) { Write-Host '[OK] news_worker ja esta rodando.' } else { Start-Process -FilePath 'python' -ArgumentList @('-u', '.\backend\data\news_worker.py', '--mode', 'real', '--interval', '900') -WorkingDirectory (Get-Location) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'news_worker.out.log') -RedirectStandardError (Join-Path $logDir 'news_worker.err.log'); Write-Host '[OK] news_worker iniciado.' }"
if errorlevel 1 goto error
echo.
echo Aguarde 30-60s antes do preflight real se os workers acabaram de iniciar.
pause
goto menu

:test_preflight
echo.
echo [3] Preflight de data para teste...
python .\backend\tests\preflight_data_date.py --max-kline-age-seconds 300
if errorlevel 1 goto preflight_failed
pause
goto menu

:short_test
echo.
echo [4] Teste curto: preflight + paper trading 10 ciclos / 30s...
python .\backend\tests\preflight_data_date.py --max-kline-age-seconds 300
if errorlevel 1 goto preflight_failed
python .\backend\tests\run_paper_trading.py --cycles 10 --sleep 30
if errorlevel 1 goto error
python .\backend\tests\analyze_trade_logs.py --limit 20
pause
goto menu

:strict_preflight
echo.
echo [5] Preflight estrito para pipeline real...
python .\backend\tests\preflight_data_date.py --require-news-today --require-workers --max-kline-age-seconds 300
if errorlevel 1 goto preflight_failed
pause
goto menu

:real_pipeline
echo.
echo [6] Pipeline real paper trading: preflight estrito + 60 ciclos / 60s...
python .\backend\tests\preflight_data_date.py --require-news-today --require-workers --max-kline-age-seconds 300
if errorlevel 1 goto preflight_failed
python .\backend\tests\run_paper_trading.py --cycles 60 --sleep 60
if errorlevel 1 goto error
python .\backend\tests\analyze_trade_logs.py --limit 30
pause
goto menu

:analyze
echo.
echo [7] Analisando trade_logs...
python .\backend\tests\analyze_trade_logs.py --limit 30
if errorlevel 1 goto error
pause
goto menu

:readiness_report
echo.
echo [8] Relatorio operacional completo...
python .\backend\tests\trading_readiness_report.py
if errorlevel 1 goto error
pause
goto menu

:evaluate_decisions
echo.
echo [9] Avaliar decisoes por movimento futuro...
set /p SINCE_ID=Desde qual trade_logs.id? Exemplo 112:
if "%SINCE_ID%"=="" goto error
if not exist ".\backend\reports" mkdir ".\backend\reports"
python .\backend\tests\evaluate_decisions.py --since-id %SINCE_ID% --horizons 5,15,30,60 --threshold 0.20 --limit 20 --json-out .\backend\reports\last_decision_evaluation.json
if errorlevel 1 goto error
pause
goto menu

:llm_review
echo.
echo [A] Revisao LLM do ultimo relatorio de avaliacao...
if not exist ".\backend\reports\last_decision_evaluation.json" (
  echo [ERRO] Relatorio .\backend\reports\last_decision_evaluation.json nao encontrado. Rode a opcao 9 antes.
  pause
  goto menu
)
python .\backend\tests\llm_review_decisions.py --input .\backend\reports\last_decision_evaluation.json --output .\backend\reports\last_llm_review.md
if errorlevel 1 goto error
pause
goto menu

:processes
echo.
echo [B] Processos ativos...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'price_worker\.py|news_worker\.py|run_paper_trading\.py' } | Select-Object ProcessId,CommandLine | Format-Table -AutoSize"
if errorlevel 1 goto error
pause
goto menu

:preflight_failed
echo.
echo [BLOQUEADO] Preflight falhou. Nao rode pipeline real com dados fora do dia ou stale.
echo Sugestao: use a opcao 2 para iniciar workers, aguarde 30-60s e rode o preflight novamente.
pause
goto menu

:error
echo.
echo [ERRO] Um comando falhou. Revise o output acima antes de continuar.
pause
goto menu

:done
echo.
echo Saindo.
exit /b 0
