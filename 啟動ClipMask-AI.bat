@echo off
chcp 65001 >nul
title ClipMask-AI 智慧影音去識別化工作站
cd /d "%~dp0"
set PYTHONPATH=.
echo 正在啟動 ClipMask-AI...
python run.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ========================================================
    echo 程式異常結束 (結束代碼: %ERRORLEVEL%)
    echo 請參考上方錯誤訊息。
    echo ========================================================
    pause
)
