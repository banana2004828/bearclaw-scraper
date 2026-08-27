@echo off
chcp 65001 >nul
title 熊爪采集器 - 一键启动
echo ============================================
echo   熊爪采集器（自研）| 一键启动
echo ============================================
echo.
cd /d "%~dp0"

REM ---------- 1. 检测 Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.11+：
    echo        https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ---------- 2. 虚拟环境 ----------
if not exist ".venv" (
    echo 首次运行：创建虚拟环境...
    python -m venv .venv
)
call .venv\Scripts\activate.bat

REM ---------- 3. 依赖 ----------
python -c "import playwright" 2>nul
if errorlevel 1 (
    echo 安装依赖（playwright）...
    pip install --no-cache-dir playwright -q
    python -m playwright install chromium
)

echo.
echo ============================================
echo   环境就绪！请在下方输入采集命令：
echo.
echo   python 熊爪采集.py --keyword 儿童玩具 --limit 20
echo   python 熊爪采集.py --keyword 健身 --limit 50 --save csv
echo.
echo   提示：首次采集会在浏览器弹登录，请用【小号】扫码
echo ============================================
echo.
cmd /k
