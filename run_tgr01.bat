@echo off
setlocal EnableExtensions
cd /d "%~dp0"

call .\run_tgr01_tui.bat
exit /b %errorlevel%
