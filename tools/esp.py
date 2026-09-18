#!/usr/bin/env python3
"""esptool 版本兼容层。

esptool v5 把子命令和参数从下划线改成了连字符：

    v4                      v5
    ----------------------  ----------------------
    write_flash             write-flash
    read_flash              read-flash
    erase_flash             erase-flash
    flash_id                flash-id
    --flash_mode dio        --flash-mode dio
    --flash_freq 80m        --flash-freq 80m
    --flash_size 8MB        --flash-size 8MB

本脚本按已安装的 esptool 版本自动翻译参数，所以调用方（.bat / 文档）
统一写下划线形式即可，两种版本都能跑。

用法完全等同 esptool：
    python tools/esp.py --chip esp32c3 --port COM3 write_flash -z 0x0 fw.bin
"""

import os
import subprocess
import sys

# v4 的子命令名 -> v5 的名字
V5_COMMANDS = {
    "write_flash": "write-flash",
    "read_flash": "read-flash",
    "erase_flash": "erase-flash",
    "erase_region": "erase-region",
    "read_mac": "read-mac",
    "flash_id": "flash-id",
    "image_info": "image-info",
    "merge_bin": "merge-bin",
    "verify_flash": "verify-flash",
    "load_ram": "load-ram",
    "dump_mem": "dump-mem",
    "read_mem": "read-mem",
    "write_mem": "write-mem",
    "read_flash_status": "read-flash-status",
    "write_flash_status": "write-flash-status",
    "read_flash_sfdp": "read-flash-sfdp",
    "get_security_info": "get-security-info",
    "chip_id": "chip-id",
}


# 少数【选项取值】在 v5 里也改了写法。这些是 --before / --after 的取值，
# 它们不带 '-' 前缀，所以不能靠前缀规则识别，只能列白名单。
V5_VALUES = {
    "no_reset": "no-reset",
    "hard_reset": "hard-reset",
    "soft_reset": "soft-reset",
    "default_reset": "default-reset",
    "usb_reset": "usb-reset",
    "no_reset_no_sync": "no-reset-no-sync",
}


def detect_major():
    """返回 esptool 主版本号；探测不到就假定 5。"""
    try:
        import importlib.metadata as md
        return int(md.version("esptool").split(".")[0])
    except Exception:
        pass
    try:
        out = subprocess.run(
            [sys.executable, "-m", "esptool", "version"],
            capture_output=True, text=True, timeout=30,
        ).stdout
        for tok in out.replace("\n", " ").split():
            if tok[:1].isdigit() and "." in tok:
                return int(tok.split(".")[0])
    except Exception:
        pass
    return 5


def translate(argv, major):
    if major < 5:
        return argv
    out = []
    for a in argv:
        if a in V5_COMMANDS:
            out.append(V5_COMMANDS[a])       # 子命令名
        elif a in V5_VALUES:
            out.append(V5_VALUES[a])         # 选项取值
        elif a.startswith("-"):
            # 选项名：文件名不会以 '-' 开头，所以替换下划线是安全的
            out.append(a.replace("_", "-"))
        else:
            out.append(a)
    return out


def main():
    major = detect_major()
    argv = translate(sys.argv[1:], major)
    if os.environ.get("ESP_COMPAT_VERBOSE"):
        print("[esp.py] esptool v%d -> %s" % (major, " ".join(argv)),
              file=sys.stderr)
    return subprocess.call([sys.executable, "-m", "esptool"] + argv)


if __name__ == "__main__":
    sys.exit(main())
