@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Chua cai dat. Dang chay setup lan dau, vui long cho...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
  if not exist ".venv\Scripts\pythonw.exe" (
    echo Setup that bai, xem loi o tren.
    pause
    exit /b 1
  )
)
start "" ".venv\Scripts\pythonw.exe" main.py
