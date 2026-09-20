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
import tempfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

HDR_RE = re.compile(rb"^FAP_SCREENSHOT_V1 (\d+) (\d+) ([A-Z0-9-]+) (\d+)\n")


def rle_decode(data, n):
    """把 (计数, 像素小端) 对还原成 n 字节的 RGB565LE。

    设备端为什么要压：`mpremote fs cp` 实测只有 2.7 KB/s，一张整屏图 57 秒。
    """
    out = bytearray(n)
    o = 0
    i = 0
    ln = len(data)
    while i + 3 <= ln and o < n:
        cnt = data[i]
        v = data[i + 1] | (data[i + 2] << 8)
        i += 3
        for _ in range(cnt):
            if o + 2 > n:
                break
            out[o] = v & 0xFF
            out[o + 1] = v >> 8
            o += 2
    if o != n:
        raise SystemExit("RLE 解出来的长度不对：%d != %d" % (o, n))
    return bytes(out)


def run(args, timeout=300):
    return subprocess.run([sys.executable, "-m", "mpremote", "connect", args.port] + args.rest,
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def norm_out(p):
    return (p.stdout or "") + (p.stderr or "")


def remote_cmd(port, *parts, timeout=300):
    """一条 mpremote 会话里跑多段命令（用 '+' 分隔）。

    ⚠ 这是这个工具的关键：**每次 mpremote 调用都要重新连一次设备**，实测一次
    连接就要 6~13 秒。原来每张图一次 `fs cp`，20 张光连接就三四分钟，还因此
    超时了两次。现在一条会话里把要拉的都拉完。
    """
    return subprocess.run(
        [sys.executable, "-m", "mpremote", "connect", port] + list(parts),
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout)


CLEAN_CMD = ("import os\n"
             "for f in list(os.listdir('/')):\n"
             "    if f.startswith('shot_') and f.endswith('.fap'):\n"
             "        os.remove('/' + f)\n"
             "print('cleaned')")


def pull_many(port, names, dirpath, cleanup=True):
    """一条会话拉完所有截图（可选顺带删掉设备上的），返回 {远端名: 本地路径}。

    删除也塞进同一条会话：每次 mpremote 调用都要重连一次设备（实测 6~13 秒），
    能合就合。
    """
    parts = []
    local_of = {}
    for remote in names:
        stem = remote[len("shot_"):-len(".fap")]
        local = os.path.join(dirpath, "_" + stem + ".fap")
        local_of[remote] = local
        if parts:
            parts.append("+")
        parts += ["fs", "cp", ":" + remote, local]
    if cleanup:
        parts += ["+", "exec", CLEAN_CMD]
    p = remote_cmd(port, *parts, timeout=600)
    missing = [r for r, l in local_of.items() if not os.path.exists(l)]
    if missing:
        raise SystemExit("拉取失败 %d 张（%s…）：\n%s"
                         % (len(missing), missing[0], norm_out(p)[-300:]))
    return local_of


def parse_fap(path):
    raw = open(path, "rb").read()
    m = HDR_RE.match(raw[:64])
    if not m:
        raise SystemExit("%s 不是 FAP_SCREENSHOT_V1：头部是 %r" % (path, raw[:32]))
    w, h, enc, n = (int(m.group(1)), int(m.group(2)), m.group(3), int(m.group(4)))
    body = raw[m.end():]
    # ⚠ 头里那个数字是**载荷字节数**，不是像素数：原始编码时它等于 w*h*2，
    # RLE 编码时它是压缩后的长度（这正是要压的原因）。别拿它跟像素数比。
    if len(body) != n:
        raise SystemExit("%s 载荷长度与头里写的对不上：头 %d，实际 %d"
                         % (path, n, len(body)))
    if enc == b"RGB565LE":
        if n != w * h * 2:
            raise SystemExit("%s 原始编码的长度应当等于 %d，实际 %d"
                             % (path, w * h * 2, n))
        return w, h, body
    if enc == b"RGB565LE-RLE":
        return w, h, rle_decode(body, w * h * 2)
    raise SystemExit("%s 不认识的编码 %r" % (path, enc))


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
    ap.add_argument("--only", default="",
                    help="只录/只拉名字含这些子串的，逗号分隔。"
                         "**筛选会传给设备端**，不需要的视图根本不会录")
    ap.add_argument("--pull-only", action="store_true",
                    help="不重录，只拉设备上已有的")
    ap.add_argument("--keep", action="store_true", help="拉完不删设备上的文件")
    args = ap.parse_args()

    os.makedirs(args.dir, exist_ok=True)
    want = [s.strip() for s in args.only.split(",") if s.strip()]

    if args.pull_only:
        # 没有录制输出可解析，只能问一次设备
        p = remote_cmd(args.port, "exec",
                       "import os;print('SHOTS:'+','.join(sorted("
                       "f for f in os.listdir('/') if f.startswith('shot_') "
                       "and f.endswith('.fap'))))", timeout=120)
        m = re.search(r"SHOTS:([^\r\n]*)", norm_out(p))
        if not m:
            raise SystemExit("拿不到设备上的截图清单：\n" + norm_out(p)[-300:])
        names = [x for x in m.group(1).split(",") if x]
    else:
        # 把筛选条件写成 spec 文件一起送过去：**设备端只录需要的**
        spec = os.path.join(tempfile.gettempdir(), "shot_spec.txt")
        with open(spec, "w", encoding="utf-8") as f:
            f.write(args.only)
        print("录制中（%s）…" % (args.only or "全部"))
        p = remote_cmd(args.port,
                       "fs", "cp", spec, ":/shot_spec.txt",
                       "+", "run", os.path.join(HERE, "hw_screenshot.py"),
                       timeout=600)
        out = norm_out(p)
        for ln in [x for x in out.splitlines() if x.strip()][-5:]:
            print("  " + ln)
        if p.returncode != 0:
            raise SystemExit("设备端录制失败：\n" + out[-400:])
        m = re.search(r"SHOTS:([^\r\n]*)", out)
        if not m:
            raise SystemExit("设备端没报截图清单：\n" + out[-400:])
        names = [x for x in m.group(1).split(",") if x]

    if not names:
        raise SystemExit("没有可处理的截图")
    if want:
        names = [n for n in names if any(w in n for w in want)]
    if not names:
        raise SystemExit("筛选后没有匹配的截图（--only %s）" % args.only)

    print("拉回 %d 张（一条会话），放大 x%d -> %s"
          % (len(names), args.scale, args.dir))
    local_of = pull_many(args.port, names, args.dir, cleanup=not args.keep)

    done = []
    for remote in names:
        stem = remote[len("shot_"):-len(".fap")]
        local = local_of[remote]
        w, h, body = parse_fap(local)
        scan, ow, oh = rgb565le_to_rgb(w, h, body, args.scale)
        out = os.path.join(args.dir, stem + ".png")
        size = write_png(out, ow, oh, scan)
        os.remove(local)
        done.append((stem, ow, oh, size))
        print("  %-16s %dx%d  %6d B" % (stem, ow, oh, size))

    print("完成：%d 张" % len(done))


if __name__ == "__main__":
    sys.exit(main())
