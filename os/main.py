"""PassportOS 入口。

开机自动运行（MicroPython 会先跑 boot.py，再跑 main.py）。
想回到 REPL：连 USB 串口，按 Ctrl-C 打断。
"""

import gc
import sys

sys.path.append("/")


def main():
    gc.collect()
    try:
        from passport.ui import Shell
    except ImportError as e:
        print("PassportOS 缺少 passport 包:", e)
        print("请把 os/ 下的 boot.py / main.py / passport/ 全部上传到设备根目录")
        return
    shell = Shell()
    shell.run()


try:
    main()
except KeyboardInterrupt:
    print("\n[PassportOS] 已中断，回到 REPL")
except Exception as exc:                                  # noqa: BLE001
    import sys as _sys
    _sys.print_exception(exc)
    print("[PassportOS] 启动失败，已回到 REPL（修复后按 Ctrl-D 软重启）")
