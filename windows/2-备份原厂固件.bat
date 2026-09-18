@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [2] 备份原厂固件（8MB）
cd /d "%~dp0"

echo ============================================================
echo   步骤 2 / 4  ——  完整备份原厂固件
echo ============================================================
echo.
echo   把设备用 Type-C 数据线插到电脑，按电源键开机（屏幕亮），然后回车。
echo   备份约 8MB，需要一分钟左右，期间绝对不要拔线。
echo.
pause

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

if not exist "..\backup" mkdir "..\backup"
set "OUT=..\backup\passport_original_8MB.bin"

if "%~1"=="" (
    echo 未指定串口，esptool 会自动侦测。
    echo 如果有多个 COM 口导致选错，改用：  %~nx0 COM3
    set "PORTARG="
) else (
    echo 使用指定串口: %~1
    set "PORTARG=--port %~1"
)

echo.
echo 开始读取 Flash（8MB @ 460800）...
echo 目标文件: %OUT%
echo.
%PY% "%~dp0..\tools\esp.py" --chip esp32c3 %PORTARG% --baud 460800 read_flash 0 0x800000 "%OUT%"
if errorlevel 1 (
    echo.
    echo [X] 备份失败。常见原因：
    echo     - 线是纯充电线  :  换一根能传数据的线
    echo     - 设备关机了    :  按一下电源键开机
    echo     - 串口被占用    :  关掉串口助手 / Arduino IDE / 其它烧录软件
    echo     - 有多个 COM 口 :  用  %~nx0 COM3  指定
    echo     - 卡在 Connecting :  按住 UP 键不放，插 USB，再松开，重跑
    pause
    exit /b 1
)

echo.
echo 校验备份文件：
certutil -hashfile "%OUT%" SHA256
echo.
echo ============================================================
echo   备份完成，已保存到:
echo     %~dp0%OUT%
echo.
echo   请把这个文件再复制一份到别的地方（U盘/网盘），然后继续下一步。
echo ============================================================
pause
