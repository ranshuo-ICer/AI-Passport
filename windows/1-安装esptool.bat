@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [1] 安装 esptool
cd /d "%~dp0"

echo ============================================================
echo   步骤 1 / 4  ——  安装烧录工具
echo ============================================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] 没找到 Python。
    echo.
    echo     请到 https://www.python.org/downloads/ 下载安装，
    echo     安装时务必勾选 "Add python.exe to PATH"，装完重开本窗口。
    echo.
    pause
    exit /b 1
)

echo [OK] Python:
%PY% --version
echo.
echo 正在安装 / 更新 esptool、pyserial、mpremote ...
echo.
echo   如果下载慢，可先换国内镜像（只需一次）：
echo       %PY% -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
echo.
%PY% -m pip install --upgrade pip
%PY% -m pip install --upgrade esptool pyserial mpremote
if errorlevel 1 (
    echo.
    echo [X] 安装失败。如果提示 pip 不存在，先试：
    echo       %PY% -m ensurepip --upgrade
    echo     如果提示网络问题，先按上面换镜像再重试。
    pause
    exit /b 1
)

echo.
echo 版本确认：
%PY% -m esptool version
%PY% -c "import serial, mpremote; print('pyserial', serial.__version__, '/ mpremote OK')"
echo.
echo ============================================================
echo   环境就绪，接着双击  2-备份原厂固件.bat
echo ============================================================
pause
