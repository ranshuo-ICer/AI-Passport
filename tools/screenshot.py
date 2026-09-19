#!/usr/bin/env python3
"""电脑侧截图：跑设备端录制、把 /shot_*.fap 拉回来、转成 PNG。

设备端见 [tools/hw_screenshot.py](../tools/hw_screenshot.py)。文件格式对齐社区
发布流程要求的 `FAP_SCREENSHOT_V1`：

    FAP_SCREENSHOT_V1 <w> <h> RGB565LE <bytes>\\n + <bytes> 字节小端 RGB565

用法：
    python tools/screenshot.py COM3                 # 录一轮 + 拉回来 + 转 PNG
    python tools/screenshot.py COM3 --scale 2       # 放大 2 倍（看文字用）
    python tools/screenshot.py COM3 --only menu,pet # 只要名字里含这些的
    python tools/screenshot.py COM3 --pull-only     # 不重录，只拉设备上已有的

为什么不在设备上直接出 PNG：MicroPython 没有 zlib 的压缩接口，而 153,600 字节
的整屏缓冲这块板根本分配不出来（见 hw_screenshot.py 的说明）。
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HDR_RE = re.compile(rb"^FAP_SCREENSHOT_V1 (\d+) (\d+) RGB565LE (\d+)\n")


def run(args, timeout=300):
    return subprocess.run([sys.executable, "-m", "mpremote", "connect", args.port] + args.rest,
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def list_shots(port):
    p = subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", port, "exec",
         "import os;print('SHOTS:'+','.join(sorted(f for f in os.listdir('/') "
         "if f.startswith('shot_') and f.endswith('.fap'))))"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120)
    out = (p.stdout or "") + (p.stderr or "")
    m = re.search(r"SHOTS:([^\r\n]*)", out)
    if not m:
        raise SystemExit("拿不到设备上的截图清单：\n" + out[-400:])
    names = [x for x in m.group(1).split(",") if x]
    return names


def pull(port, remote, local):
    p = subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", port, "fs", "cp",
         ":" + remote, local],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=180)
    if p.returncode != 0 or not os.path.exists(local):
        raise SystemExit("拉取 %s 失败：\n%s\n%s" % (remote, p.stdout, p.stderr))


def parse_fap(path):
    raw = open(path, "rb").read()
    m = HDR_RE.match(raw[:64])
    if not m:
        raise SystemExit("%s 不是 FAP_SCREENSHOT_V1：头部是 %r" % (path, raw[:32]))
    w, h, n = int(m.group(1)), int(m.group(2)), int(m.group(3))
    body = raw[m.end():]
    if len(body) != n:
        raise SystemExit("%s 像素数不对：头里写 %d，实际 %d" % (path, n, len(body)))
    if n != w * h * 2:
        raise SystemExit("%s 尺寸与像素数不符：%dx%d -> %d != %d" % (path, w, h, n, w * h * 2))
    return w, h, body


def rgb565le_to_rgb(w, h, body, scale=1):
    """转成 RGB888 的扫描线字节（含每行前面的 filter 字节 0）。"""
    rows = []
    for y in range(h):
        base = y * w * 2
        line = bytearray(1 + w * scale * 3)
        for x in range(w):
            lo = body[base + x * 2]
            hi = body[base + x * 2 + 1]
            v = lo | (hi << 8)
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x3F) * 255 // 63
            b = (v & 0x1F) * 255 // 31
            for k in range(scale):
                o = 1 + (x * scale + k) * 3
                line[o] = r
                line[o + 1] = g
                line[o + 2] = b
        for _ in range(scale):
            rows.append(bytes(line))
    return b"".join(rows), w * scale, h * scale


def chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))


def write_png(path, w, h, scanlines):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(scanlines, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--dir", default=os.path.join(ROOT, "shots"))
    ap.add_argument("--scale", type=int, default=2,
                    help="最近邻放大倍数（默认 2，方便看 8x8 字）")
    ap.add_argument("--only", default="", help="只处理名字含这些子串的，逗号分隔")
    ap.add_argument("--pull-only", action="store_true", help="不重录，只拉已有的")
    ap.add_argument("--keep", action="store_true", help="拉完不删设备上的文件")
    args = ap.parse_args()

    if not args.pull_only:
        print("设备端录制中（约 20 秒）…")
        p = subprocess.run(
            [sys.executable, "-m", "mpremote", "connect", args.port, "run",
             os.path.join(HERE, "hw_screenshot.py")],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600)
        tail = [ln for ln in ((p.stdout or "") + (p.stderr or "")).splitlines()
                if ln.strip()]
        for ln in tail[-6:]:
            print("  " + ln)
        if p.returncode != 0:
            raise SystemExit("设备端录制失败")

    names = list_shots(args.port)
    if not names:
        raise SystemExit("设备上没有 /shot_*.fap")
    if args.only:
        want = [s.strip() for s in args.only.split(",") if s.strip()]
        names = [n for n in names if any(w in n for w in want)]
    os.makedirs(args.dir, exist_ok=True)

    print("拉回 %d 张，放大 x%d -> %s" % (len(names), args.scale, args.dir))
    done = []
    for remote in names:
        stem = remote[len("shot_"):-len(".fap")]
        local = os.path.join(args.dir, "_" + stem + ".fap")
        pull(args.port, remote, local)
        w, h, body = parse_fap(local)
        scan, ow, oh = rgb565le_to_rgb(w, h, body, args.scale)
        out = os.path.join(args.dir, stem + ".png")
        size = write_png(out, ow, oh, scan)
        os.remove(local)
        done.append((stem, ow, oh, size))
        print("  %-16s %dx%d  %6d B" % (stem, ow, oh, size))

    if not args.keep:
        subprocess.run(
            [sys.executable, "-m", "mpremote", "connect", args.port, "exec",
             "import os\nfor f in os.listdir('/'):\n"
             "    f.startswith('shot_') and os.remove('/'+f)\nprint('cleaned')"],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120)
    print("完成：%d 张" % len(done))


if __name__ == "__main__":
    sys.exit(main())
