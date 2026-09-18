"""生成 PWA 图标（192 / 512 PNG）。纯标准库，不依赖 PIL。

设计：青→蓝对角渐变底 + 深色 "P" 字形。
"P" 用四块矩形拼出来，中间的洞自然留出背景色。
"""

import struct
import zlib

BG_DARK = (0x0b, 0x10, 0x20)
C0 = (0x37, 0xe6, 0xc8)      # 青
C1 = (0x4c, 0x8d, 0xff)      # 蓝

# 以 512 为基准的比例，保证任意尺寸下字形一致
P_STEM = (140 / 512, 110 / 512, 205 / 512, 400 / 512)
P_TOP = (140 / 512, 110 / 512, 365 / 512, 165 / 512)
P_RIGHT = (300 / 512, 110 / 512, 365 / 512, 265 / 512)
P_BOTTOM = (140 / 512, 210 / 512, 300 / 512, 265 / 512)


def render(size):
    px = bytearray(size * size * 4)
    denom = 2 * size - 2

    for y in range(size):
        row = y * size * 4
        for x in range(size):
            t = (x + y) / denom
            i = row + x * 4
            px[i] = int(C0[0] + (C1[0] - C0[0]) * t)
            px[i + 1] = int(C0[1] + (C1[1] - C0[1]) * t)
            px[i + 2] = int(C0[2] + (C1[2] - C0[2]) * t)
            px[i + 3] = 255

    def rects():
        for (fx0, fy0, fx1, fy1) in (P_STEM, P_TOP, P_RIGHT, P_BOTTOM):
            x0, y0 = int(fx0 * size), int(fy0 * size)
            x1, y1 = int(fx1 * size), int(fy1 * size)
            for y in range(y0, y1):
                row = y * size * 4
                for x in range(x0, x1):
                    i = row + x * 4
                    px[i], px[i + 1], px[i + 2] = BG_DARK
    rects()
    return bytes(px)


def png_bytes(size, rgba):
    raw = bytearray()
    stride = size * 4
    for y in range(size):
        raw.append(0)                       # filter type 0
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    out = b"\x89PNG\r\n\x1a\n"
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    out += chunk(b"IEND", b"")
    return out


if __name__ == "__main__":
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    for size in (192, 512):
        data = png_bytes(size, render(size))
        path = os.path.join(here, "icon-%d.png" % size)
        with open(path, "wb") as f:
            f.write(data)
        print("wrote %s (%d bytes)" % (path, len(data)))
