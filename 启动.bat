@echo off
REM === PowerCollector One-Click Launcher ===
REM Pure ASCII (no chcp) — safe in cmd.exe ANSI/GBK.

cd /d "%~dp0"

set LOGFILE=%~dp0startup.log

echo ======================================== >  "%LOGFILE%"
echo   PowerCollector startup log                >> "%LOGFILE%"
echo   Time: %date% %time%                       >> "%LOGFILE%"
echo ======================================== >> "%LOGFILE%"

echo.
echo ========================================
echo   PowerCollector v1.0  (one-click start)
echo ========================================
echo Dir:   %CD%
echo Log:   %LOGFILE%
echo.

REM === 0. file sanity ===
if not exist "main.py" (
    echo [X] main.py not found in %CD%.
    echo     Place this .bat in the PowerCollector folder.
    pause
    exit /b 1
)
if not exist "web\index.html" (
    echo [X] web\index.html not found in %CD%.
    pause
    exit /b 1
)

REM === 1. Python detection ===
set PYEXE=
where python >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=*" %%p in ('where python') do set PYEXE=%%p
)
if "%PYEXE%"=="" (
    where py >nul 2>&1
    if not errorlevel 1 (
        set PYEXE=py -3
    )
)
if "%PYEXE%"=="" (
    echo [X] Python not found in PATH.
    echo.
    echo     1) Install Python 3.10+ from https://www.python.org/downloads/
    echo        Tick "Add Python to PATH" during install.
    echo     2) Or install from Microsoft Store.
    echo.
    echo     After install, re-run this .bat.
    pause
    exit /b 1
)
echo [OK] Python: %PYEXE%

REM === 2. Dependency check + install ===
echo.
echo === Checking dependencies ===
%PYEXE% -c "import PySide6, xlrd, requests" 2>nul
if errorlevel 1 (
    echo [!] Missing deps, installing...
    echo     ^(this may take 30-120 seconds on first run^)
    %PYEXE% -m pip install --disable-pip-version-check -r requirements.txt >>"%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [X] Install failed. Check %LOGFILE%
        echo.
        echo --- last 20 lines of log ---
        powershell -NoProfile -Command "Get-Content '%LOGFILE%' -Tail 20"
        echo.
        pause
        exit /b 1
    )
    %PYEXE% -c "import PySide6, xlrd, requests" 2>nul
    if errorlevel 1 (
        echo [X] Installed but still cannot import.
        echo     Try: %PYEXE% -m pip install --upgrade PySide6 xlrd requests
        pause
        exit /b 1
    )
    echo [OK] Dependencies installed
) else (
    echo [OK] Dependencies ready
)

REM === 3. Data directory hint ===
if exist "..\_peek" (
    echo [OK] Data directory found: ..\_peek
) else (
    echo [!] Data directory not found: %CD%\..\_peek
    echo     You can specify data via menu File - Open directory.
)

REM === 4. Run main app ===
echo.
echo === Launching main app ===
echo (Startup takes 30-90 seconds; do not close during black screen)
echo.

%PYEXE% -u main.py >> "%LOGFILE%" 2>&1
set APPEXIT=%errorlevel%

echo.
if %APPEXIT% NEQ 0 (
    echo [X] Program exited with error. Log saved to:
    echo     %LOGFILE%
    echo.
    echo === last 25 lines of log ===
    powershell -NoProfile -Command "Get-Content '%LOGFILE%' -Tail 25"
    echo.
) else (
    echo Program exited cleanly.
)
pause