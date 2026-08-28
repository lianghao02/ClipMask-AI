@echo off
title ClipMask-AI Video Redaction Station
cd /d "%~dp0"
set PYTHONPATH=.
python run.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Application exited with error code: %ERRORLEVEL%
    pause
)
