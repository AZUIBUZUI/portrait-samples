@echo off
cd /d "%~dp0"

rem 优先用安装脚本创建的虚拟环境，没有则退回系统 python
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

rem 服务已在监听就直接打开页面
netstat -ano | findstr /c:":5000 " | findstr "LISTENING" >nul
if not errorlevel 1 (
  start "" http://127.0.0.1:5000
  exit /b
)

start "PortraitSamples" /min "%PY%" run.py
timeout /t 6 /nobreak >nul
start "" http://127.0.0.1:5000
