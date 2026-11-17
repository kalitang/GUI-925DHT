@echo off
chcp 65001 >nul
title PowerCollector

cd /d "%~dp0"

REM English-named alias launcher. Forwards to 启动.bat.
if exist "启动.bat" (
    call "启动.bat"
) else (
    echo [X] 启动.bat missing in %CD%
    pause
)