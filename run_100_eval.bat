@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

if not exist ".\backend\reports" mkdir ".\backend\reports"

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

echo.
echo [1/6] Preflight estrito...
python .\backend\tests\preflight_data_date.py --require-news-today --require-workers --max-kline-age-seconds 300
if errorlevel 1 goto preflight_failed

echo.
echo [2/6] Capturando trade_logs.id inicial...
for /f %%i in ('python .\backend\tests\get_next_trade_log_id.py') do set SINCE_ID=%%i
echo Run since-id: %SINCE_ID%

echo %SINCE_ID% > .\backend\reports\last_run_since_id.txt

echo.
echo [3/6] Rodando paper trading: 100 ciclos / %SLEEP_SECONDS%s...
python .\backend\tests\run_paper_trading.py --cycles 100 --sleep %SLEEP_SECONDS%
if errorlevel 1 goto error

echo.
echo [4/6] Gerando relatorio limpo de trade_logs desde id %SINCE_ID%...
python .\backend\tests\analyze_trade_logs.py --since-id %SINCE_ID% --limit 50 > .\backend\reports\last_100_trade_log_report.txt
if errorlevel 1 goto error
type .\backend\reports\last_100_trade_log_report.txt

echo.
echo [5/6] Gerando relatorio deterministico por movimento futuro...
python .\backend\tests\evaluate_decisions.py --since-id %SINCE_ID% --horizons 5,15,30,60 --threshold 0.20 --limit 30 --json-out .\backend\reports\last_100_decision_evaluation.json > .\backend\reports\last_100_decision_evaluation.txt
if errorlevel 1 goto error
type .\backend\reports\last_100_decision_evaluation.txt

echo.
echo [6/6] Gerando revisao LLM do relatorio deterministico...
python .\backend\tests\llm_review_decisions.py --input .\backend\reports\last_100_decision_evaluation.json --output .\backend\reports\last_100_llm_review.md > .\backend\reports\last_100_llm_review.out.txt
if errorlevel 1 goto llm_review_failed
type .\backend\reports\last_100_llm_review.out.txt

echo.
echo ============================================================
echo Experimento concluido.
echo Arquivos gerados:
echo - backend\reports\last_run_since_id.txt
echo - backend\reports\last_100_trade_log_report.txt
echo - backend\reports\last_100_decision_evaluation.txt
echo - backend\reports\last_100_decision_evaluation.json
echo - backend\reports\last_100_llm_review.md
echo ============================================================
pause
exit /b 0

:preflight_failed
echo.
echo [BLOQUEADO] Preflight falhou. Nao rode 100 ciclos com dados stale/fora do dia.
echo Use run_tgr01.bat opcao 2 para iniciar workers, aguarde 30-60s e tente novamente.
pause
exit /b 1

:llm_review_failed
echo.
echo [WARN] Paper trading e relatorio deterministico foram gerados, mas a revisao LLM falhou.
echo Veja os arquivos em backend\reports e rode manualmente depois:
echo python .\backend\tests\llm_review_decisions.py --input .\backend\reports\last_100_decision_evaluation.json --output .\backend\reports\last_100_llm_review.md
pause
exit /b 2

:error
echo.
echo [ERRO] Um comando falhou. Revise o output acima.
pause
exit /b 1
