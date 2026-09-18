@echo off
chcp 65001 >nul
rem 兜底：Python 刚装好时资源管理器可能还没刷新 PATH
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
setlocal
title [3] 烧录小智语音助手固件
cd /d "%~dp0"

echo ============================================================
echo   步骤 3 / 4  ——  烧录小智语音助手固件
echo ============================================================
echo.
echo   会【覆盖】设备上的原厂固件。
echo   还没做备份的话，现在按 Ctrl+C 退出，先跑 2-备份原厂固件.bat。
echo.

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

set "FW=..\firmware\folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin"
if not exist "%FW%" (
    echo [X] 找不到固件: %FW%
    pause
    exit /b 1
)

echo 核对固件哈希（必须与官方发布一致）...
%PY% -c "import hashlib,sys;h=hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest().upper();print(h);sys.exit(0 if h=='E19B35AD5EE7D8F9CFA9A22C51A69F25835EC90CE092A798D875915F23B275BA' else 1)" "%FW%"
if errorlevel 1 (
    echo.
    echo [X] 哈希不匹配，固件文件可能损坏，请重新获取后再试。
    pause
    exit /b 1
)
echo [OK] 哈希一致
echo.

if not exist "..\backup\passport_original_8MB.bin" (
    echo [!] 没发现备份文件 ..\backup\passport_original_8MB.bin
    echo     刷完就没法一键还原原厂固件了。
    choice /c YN /m "确定要继续吗"
    if errorlevel 2 exit /b 1
)

if "%~1"=="" (
    echo 未指定串口，esptool 会自动侦测。
    set "PORTARG="
) else (
    echo 使用指定串口: %~1
    set "PORTARG=--port %~1"
)

echo.
echo 开始烧录（从 0x0 写入完整合并镜像）...
echo.
%PY% "%~dp0..\tools\esp.py" --chip esp32c3 %PORTARG% --baud 460800 write_flash --flash_mode dio --flash_freq 80m --flash_size 8MB 0x0 "%FW%"
if errorlevel 1 (
    echo.
    echo [X] 烧录失败。可参考 2-备份原厂固件.bat 里的排错清单，
    echo     也可以按住 UP 键插 USB 进下载模式后重试。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   烧录完成，设备会自动重启。
echo.
echo   首次启动后：
echo     1. 屏幕显示一个 Wi-Fi 热点名，手机连上它
echo     2. 配网页面没自动弹出，就用浏览器打开 http://192.168.4.1
echo     3. 选 Wi-Fi、填密码，按提示填小智服务端地址
echo     4. 说「你好小智」唤醒；OK 键开始/停止对话；UP/DOWN 调音量
echo.
echo   想看启动日志：双击 4-查看串口日志.bat
echo ============================================================
pause
