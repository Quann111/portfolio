@echo off
chcp 65001 >nul
title KHOI DONG PROFILE CHROME CHO AUTOVIDEO
cd /d "%~dp0"

echo ========================================================
echo   DANG KHOI CHAY CAC PROFILE CHROME TAO ANH / KICH BAN
echo ========================================================
echo.

set "CHROME_BIN=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME_BIN%" (
    set "CHROME_BIN=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
)
if not exist "%CHROME_BIN%" (
    set "CHROME_BIN=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)
if not exist "%CHROME_BIN%" (
    echo [LOI] Khong tim thay Chrome tai duong dan mac dinh!
    pause
    exit /b 1
)

set "FLOW_NEW_URL=https://flow.google.com"
set "NOTEBOOK_URL=https://notebooklm.google.com"
set "GEMINI_URL=https://gemini.google.com"

set "EXT_FLAGS=--load-extension="%~dp0chrome_ext,%~dp0chrome_ext_notebook,%~dp0chrome_ext_gemini""

:: ----------------------------------------------------------
:: 1. Mo cac profile Flow worker tao anh & NotebookLM / Gemini
:: ----------------------------------------------------------

echo [1/9] Mo profile QUAN (Default)...
start "" "%CHROME_BIN%" --profile-directory="Default" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [2/9] Mo profile CANH (Profile 1)...
start "" "%CHROME_BIN%" --profile-directory="Profile 1" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [3/9] Mo profile QUAN2 (Profile 2)...
start "" "%CHROME_BIN%" --profile-directory="Profile 2" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [4/9] Mo profile VR (Profile 3) - Flow + NotebookLM + Gemini...
start "" "%CHROME_BIN%" --profile-directory="Profile 3" %EXT_FLAGS% "%FLOW_NEW_URL%" "%NOTEBOOK_URL%" "%GEMINI_URL%"
ping -n 2 127.0.0.1 >nul

echo [5/9] Mo profile pana (Profile 23)...
start "" "%CHROME_BIN%" --profile-directory="Profile 23" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [6/9] Mo profile trung (Profile 24)...
start "" "%CHROME_BIN%" --profile-directory="Profile 24" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [7/9] Mo profile tung (Profile 27)...
start "" "%CHROME_BIN%" --profile-directory="Profile 27" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [8/9] Mo profile QUANGBE (Profile 28)...
start "" "%CHROME_BIN%" --profile-directory="Profile 28" %EXT_FLAGS% "%FLOW_NEW_URL%"
ping -n 2 127.0.0.1 >nul

echo [9/9] Mo profile qqkun2 (Profile 33) - Flow + NotebookLM + Gemini...
start "" "%CHROME_BIN%" --profile-directory="Profile 33" %EXT_FLAGS% "%FLOW_NEW_URL%" "%NOTEBOOK_URL%" "%GEMINI_URL%"
ping -n 2 127.0.0.1 >nul

echo.
echo ========================================================
echo   DA MO THANH CONG TAT CA PROFILE VA TAB!
echo   Vui long kiem tra neu co profile bi popup/captcha.
echo ========================================================
echo.
ping -n 5 127.0.0.1 >nul
