@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [5] 烧录 PassportOS
cd /d "%~dp0.."

echo ============================================================
echo   PassportOS 烧录  ——  FoloToy AI Passport (ESP32-C3)
echo ============================================================
echo.
echo   这一步会【完全擦除】设备上的原厂固件。
echo   如果还没备份，请关掉本窗口，先运行 windows\2-备份原厂固件.bat
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

set "FW=firmware\ESP32_GENERIC_C3-20260824-v1.29.0.bin"
if not exist "%FW%" (
    echo [X] 找不到 MicroPython 固件: %FW%
    pause
    exit /b 1
)

echo.
echo [1/4] 安装依赖 (esptool / pyserial / mpremote) ...
%PY% -m pip install --quiet --upgrade esptool pyserial mpremote
if errorlevel 1 (
    echo [X] 依赖安装失败，检查网络后重试。
    echo     下载慢可先换镜像：
    echo     %PY% -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo.
echo [2/4] 擦除 Flash ...
%PY% "%~dp0..\tools\esp.py" --chip esp32c3 --baud 460800 erase_flash
if errorlevel 1 (
    echo.
    echo [X] 擦除失败。常见原因：
    echo     - 线是纯充电线 : 换一根数据线
    echo     - 设备关机了   : 按一下电源键
    echo     - 串口被占用   : 关掉串口助手 / Arduino IDE
    echo     - 需要手动进下载模式：按住 UP 键不放，插 USB，再松开
    pause
    exit /b 1
)

echo.
echo [3/4] 写入 MicroPython v1.29.0 ...
%PY% "%~dp0..\tools\esp.py" --chip esp32c3 --baud 460800 write_flash -z 0x0 "%FW%"
if errorlevel 1 (
    echo [X] 写入失败，重试一次；仍失败请检查线材和供电。
    pause
    exit /b 1
)

echo.
echo     等设备重新启动（约 4 秒）...
timeout /t 4 /nobreak >nul

echo.
echo [4/4] 上传 PassportOS 系统文件与示例小程序 ...
%PY% tools\deploy.py --clean
if errorlevel 1 (
    echo.
    echo [X] 上传失败。如果提示找不到串口：
    echo     - esptool 硬复位后设备可能关机，按一下电源键，再重跑本脚本
    echo     - 也可以只重跑上传：  %PY% tools\deploy.py COM3 --clean
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   完成！设备屏幕应该已经显示 PassportOS 主菜单。
echo   下一步：双击 windows\6-启动Passport助手App.bat
echo ============================================================
pause
