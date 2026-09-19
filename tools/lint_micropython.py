#!/usr/bin/env python3
"""MicroPython 兼容性静态检查。

为什么需要这个：
    本项目在电脑上用 CPython 跑单元测试，真机上才炸了一次：
        AttributeError: 'array' object has no attribute 'byteswap'
    array.byteswap() 是 **CPython 有、MicroPython 没有** 的方法，
    静态测试完全发现不了。这个脚本把这类"只在真机上才会暴露"的 API 找出来。

扫描范围：os/ 下所有 .py（设备端代码）。
tools/ 下的宿主脚本不扫，它们跑在电脑上。

用法：
    python tools/lint_micropython.py
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCAN_DIR = os.path.join(ROOT, "os")

# (正则, 说明)
RULES = [
    (r"\.byteswap\s*\(", "array 没有 byteswap()（CPython 才有）—— 手写字节翻转"),
    (r"\bimport\s+shutil\b|\bshutil\.", "MicroPython 无 shutil"),
    (r"\bimport\s+pathlib\b|\bpathlib\.", "MicroPython 无 pathlib"),
    (r"\bimport\s+subprocess\b", "MicroPython 无 subprocess"),
    (r"\bimport\s+threading\b", "MicroPython 用 _thread，没有 threading"),
    (r"\bimport\s+typing\b", "MicroPython 无 typing"),
    (r"\bimport\s+dataclasses\b", "MicroPython 无 dataclasses"),
    (r"\bimport\s+enum\b", "MicroPython 无 enum"),
    (r"\bimport\s+abc\b", "MicroPython 无 abc"),
    (r"\bimport\s+collections\b", "MicroPython 无 collections（用 dict/list）"),
    (r"\bimport\s+itertools\b", "MicroPython 无 itertools"),
    (r"\bimport\s+functools\b", "MicroPython 无 functools"),
    (r"\bimport\s+argparse\b", "MicroPython 无 argparse"),
    (r"\bos\.path\.", "MicroPython 的 os 没有 path 子模块"),
    (r"\.casefold\s*\(", "MicroPython 字符串无 casefold()"),
    (r"\braise\s+\w+\s+from\b", "MicroPython 不支持 raise ... from"),
    (r"\byield\s+from\b", "MicroPython 不支持 yield from"),
    (r"f['\"][^'\"]*\{[^}]*=\}", "f-string 的 = 调试写法 MicroPython 可能不支持"),
    (r"\.removeprefix\s*\(|\.removesuffix\s*\(", "MicroPython 无 removeprefix/removesuffix"),
    (r"\bstr\.isascii\b|\.isascii\s*\(", "MicroPython 无 isascii()"),
]

COMPILED = [(re.compile(p), msg) for p, msg in RULES]


def device_files():
    """要扫的设备端 .py 列表。

    不只是 os/ —— `tools/hw_selftest.py` 和 `miniapps/` 下的游戏都是用
    mpremote 直接送到设备上跑的，同样受 MicroPython 的 API 限制。
    `miniapps/_verify.py` 是电脑上的检查器，`_` 开头的都排除。
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(SCAN_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in sorted(filenames):
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))

    extra = [os.path.join(ROOT, "tools", "hw_selftest.py")]
    miniapps = os.path.join(ROOT, "miniapps")
    if os.path.isdir(miniapps):
        for fn in sorted(os.listdir(miniapps)):
            if not fn.endswith(".py") or fn.startswith("_"):
                continue
            p = os.path.join(miniapps, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    head = f.read(2000)
            except OSError:
                continue
            # 只收真正的小程序（带设备端钩子），把宿主脚本排除
            if "def setup(ctx)" in head or "def loop(ctx)" in head:
                extra.append(p)

    for p in extra:
        if os.path.isfile(p):
            out.append(p)
    return sorted(set(out))


def main():
    problems = []
    scanned = 0
    for path in device_files():
        rel = os.path.relpath(path, ROOT)
        scanned += 1
        in_doc = False
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                stripped = line.strip()
                # 粗略跟踪三引号 docstring，避免把说明文字当成违规。
                # 同一行里出现两个三引号（单行 docstring）也要跳过，否则
                # `"""... byteswap ..."""` 这种会误报。
                quotes = stripped.count('"""') + stripped.count("'''")
                opens = quotes % 2 == 1
                was_in_doc = in_doc
                if opens:
                    in_doc = not in_doc
                if was_in_doc or opens or quotes >= 2 \
                        or stripped.startswith("#"):
                    continue
                for rx, msg in COMPILED:
                    if rx.search(line):
                        problems.append((rel, lineno, stripped[:70], msg))

    print("扫描 %d 个设备端文件" % scanned)
    if not problems:
        print("没有发现 MicroPython 不支持的 API 用法 ✓")
        return 0

    print("\n发现 %d 处可疑用法：" % len(problems))
    for rel, lineno, line, msg in problems:
        print("  %s:%d" % (rel, lineno))
        print("      %s" % line)
        print("      -> %s" % msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
