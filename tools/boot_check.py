#!/usr/bin/env python3
"""启动冒烟测试：软复位后看 PassportOS 有没有真的跑起来。

为什么需要它：主循环是在 `main.py` 顶层 try/except 里的，**任何**在 draw_menu /
tick 里抛出的异常都会把整个 OS 打回 REPL。这时候设备看起来"没死"（USB 和 BLE
都还在，BLE 甚至是崩溃前起来的，能连上），但服务层完全不响应 —— 现象很有迷惑性。

实测踩过一次：整行离屏合成给 blit 多要了一块同样大的 _swap，开机堆紧张直接
`MemoryError`，OS 就再也没起来过。当时是靠人肉读串口日志才定位的，所以把它自动化。

    python tools/boot_check.py COM3

退出码非 0 表示启动失败。
"""

import argparse
import re
import sys
import time

import serial

# 注意 log_line 会加 "[PassportOS] " 前缀，所以实际那行是
# "[PassportOS] PassportOS 就绪" —— 匹配串用后半段，别把前缀一起写进来
OK_MARK = "PassportOS 就绪"
BAD_MARKS = ("Traceback", "MemoryError", "启动失败", "缺少 passport 包")


def read_boot(port, seconds=10.0):
    p = serial.Serial(port, 115200, timeout=0.3)
    try:
        p.write(b"\x03\x03")          # Ctrl-C：确保停在 REPL
        time.sleep(0.4)
        p.reset_input_buffer()
        p.write(b"\x04")              # Ctrl-D：软复位，跑 boot.py / main.py
        t0 = time.time()
        buf = b""
        while time.time() - t0 < seconds:
            buf += p.read(4096)
            if OK_MARK.encode() in buf:
                time.sleep(0.3)       # 再多收一点，别漏掉紧随其后的异常
                buf += p.read(4096)
                break
        return buf.decode("utf-8", "replace")
    finally:
        p.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--seconds", type=float, default=10.0)
    args = ap.parse_args()

    try:
        txt = read_boot(args.port, args.seconds)
    except Exception as exc:                                  # noqa: BLE001
        print("  [FAIL] 打不开串口 %s: %s" % (args.port, exc))
        return 1

    lines = [ln.strip() for ln in txt.splitlines() if ln.strip()]
    print("  串口日志 %d 行，最后 5 行：" % len(lines))
    for ln in lines[-5:]:
        print("    " + ln[:110])

    ready = OK_MARK in txt
    bad = [m for m in BAD_MARKS if m in txt]
    # 只关心最后那次启动：先复位再读，所以日志里不该有旧的 Traceback
    fails = []
    if not ready:
        fails.append("没有看到「%s」" % OK_MARK)
    for m in bad:
        fails.append("出现「%s」" % m)
    m = re.search(r"MemoryError: memory allocation failed, allocating (\d+)", txt)
    if m:
        fails.append("启动时分配 %s 字节失败" % m.group(1))

    print("")
    if fails:
        print("  [FAIL] 启动冒烟测试不通过：")
        for f in fails:
            print("       - " + f)
        for ln in lines:
            if any(mk in ln for mk in BAD_MARKS) or "File \"" in ln:
                print("       | " + ln[:110])
        return 1
    print("  [OK] PassportOS 启动成功，日志中没有异常")
    return 0


if __name__ == "__main__":
    sys.exit(main())
