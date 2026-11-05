@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 请先运行 安装依赖.ps1
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m xiaomao gui
