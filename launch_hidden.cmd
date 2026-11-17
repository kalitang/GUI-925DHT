@echo off
REM === PowerCollector hidden launcher (no console window) ===
REM Paths are derived from this script's own directory (%~dp0) — no hardcoded drive letters.

cd /d "%~dp0"
set APPDIR=%~dp0
if "%APPDIR:~-1%"=="\" set APPDIR=%APPDIR:~0,-1%

echo PowerCollector v1.0 (one-click start)
echo Dir:   %APPDIR%
echo Log:   %APPDIR%\startup.log
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [X] Python not in PATH. Install from https://www.python.org/
    pause
    exit /b 1
)

python -c "import PySide6, xlrd, requests" 2>nul
if errorlevel 1 (
    echo [!] Installing dependencies...
    python -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto :fail
)
if not exist web\index.html goto :fail
if not exist main.py goto :fail
echo === Launching main app ===
python -u main.py
if errorlevel 1 goto :fail
echo Done.
exit /b 0

:fail
echo [X] Startup failed. See log: %APPDIR%\startup.log
pause
exit /b 1
