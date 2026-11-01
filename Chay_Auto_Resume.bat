@echo off
chcp 65001 >nul
title AUTO RESUME FLOW PROFILES (DA CAI DAT)
cd /d "%~dp0"
echo ======================================================
echo   DANG CHAY AUTOMATION TU DONG RESUME PROFILE CHO FLOW
echo   (Tu dong mo lai profile bi dung - KHONG mo captcha)
echo ======================================================
".venv\Scripts\python.exe" auto_resume_watcher.py
pause
