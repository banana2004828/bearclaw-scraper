@echo off
chcp 65001 >nul
title 熊爪采集器 - 启动采集浏览器
echo ============================================
echo   熊爪采集器 | CDP 模式浏览器启动器
echo ============================================
echo.
echo 即将以「调试模式」打开本机 Chrome：
echo   1. 在弹出的 Chrome 中用【小号】登录小红书
echo   2. 登录完成后【保持窗口开着】，回到网页点「启动采集」
echo   3. 采集器会直接连接这个浏览器（最像真人，最安全）
echo.
echo   ⚠️ 采集期间不要关闭这个 Chrome 窗口
echo   ⚠️ 采集完请关闭窗口并退出登录
echo.
echo 正在启动 Chrome...
echo.

set CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
if not exist "%CHROME%" set CHROME=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe

start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%~dp0.chrome-profile" --disable-blink-features=AutomationControlled https://www.xiaohongshu.com

echo.
echo Chrome 已启动（端口 9222）。
echo 登录小红书小号后，切回网页点「启动采集」即可。
echo.
pause
