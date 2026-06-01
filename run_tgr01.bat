@echo off
setlocal EnableExtensions
cd /d "%~dp0"

call .\run_tgr01_tui.bat
if not errorlevel 1 exit /b 0

echo.
echo [WARN] TUI indisponivel. Abrindo menu legado.
call .\run_tgr01_legacy.bat
