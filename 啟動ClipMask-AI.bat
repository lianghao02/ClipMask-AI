@echo off
chcp 65001 >nul
title ClipMask-AI 智慧影音去識別化工作站
cd /d "%~dp0"
set PYTHONPATH=.
python run.py
pause
