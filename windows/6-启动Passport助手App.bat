@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [6] 启动 Passport 助手 App
cd /d "%~dp0.."

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] 没找到 Python，请先跑 1-安装esptool.bat
    pause
    exit /b 1
)

echo ============================================================
echo   Passport 助手 —— 本地托管
echo ============================================================
echo.
echo   浏览器会自动打开 http://127.0.0.1:8790/
echo   必须先点右上角「连接」连上设备，再推送小程序。
echo.
echo   提示：可以在浏览器里「安装应用 / 添加到主屏幕」，
echo         装完就像个独立 App，断网也能开。
echo.
echo   保持这个黑窗口开着，关掉 App 就没了。
echo.
echo   注意：Web Bluetooth 只在 localhost 或 https 下可用，
echo         所以必须用这个脚本起服务，不能直接双击 index.html。
echo.
%PY% tools\serve.py 8790
pause
