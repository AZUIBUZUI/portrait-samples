@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem ---- 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python，请先安装 Python 3.10+ 并勾选 "Add python.exe to PATH"
  echo         下载地址: https://www.python.org/downloads/
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('python -V 2^>^&1') do echo 检测到 Python %%v

rem ---- 创建虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
  echo 正在创建虚拟环境 .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [错误] 创建虚拟环境失败
    pause
    exit /b 1
  )
)

rem ---- 安装依赖 ----
echo 正在安装依赖 ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络后重试
  pause
  exit /b 1
)

echo.
echo 安装完成。双击 start.bat 启动，浏览器访问 http://127.0.0.1:5000
echo 需要采集小红书时，先执行: .venv\Scripts\python.exe run.py --login
pause
