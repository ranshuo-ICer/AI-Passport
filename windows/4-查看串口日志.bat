@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [4] 查看串口日志
cd /d "%~dp0"

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
echo   步骤 4 / 4  ——  查看设备串口日志
echo ============================================================
echo.

if "%~1"=="" (
    echo 当前可见的串口：
    %PY% -m serial.tools.list_ports
    echo.
    set /p PORT=请输入串口号（例如 COM3）:
) else (
    set "PORT=%~1"
)

if "%PORT%"=="" (
    echo 没输入串口，退出。
    pause
    exit /b 1
)

echo.
echo 正在连接 %PORT% @115200 ...
echo 退出请按  Ctrl + ]
echo.
%PY% -m serial.tools.miniterm %PORT% 115200
if errorlevel 1 (
    echo.
    echo [X] 打不开串口。检查：
    echo     - 设备是否开机（按一下电源键）
    echo     - 串口是否被别的软件占用
    echo     - 串口号是否变了（插拔后 Windows 会重新分配）
)
pause
