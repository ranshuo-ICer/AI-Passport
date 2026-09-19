#!/usr/bin/env python3
"""display.py 的分配行为检查（不需要硬件）。

为什么单独有这个文件：
    真机上 Beats 跑着跑着炸了 —— `MemoryError: allocating 8161 bytes`，
    而当时堆里明明还有 43 KB 空闲。根因是 display.fill_rect() 的临时缓冲
    按时长/宽度折算，满宽（240px）填充一次要 line * 17 = 8160 字节的连续块，
    碎片化的堆凑不出来就失败。

    这类问题有两个特点，所以必须专门测：
      1. 纯 CPython 跑测试完全看不出来（电脑上内存管够）；
      2. 它不在某个 app 里，而在驱动里，影响所有满宽填充的小程序。

    这里把 SPI 换成桩，量出每一次 write() 的字节数，断言上界。

    python tools/test_display.py
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))

PASS, FAIL = [], []

# 单次 write 允许的上限。fill_rect 现在按 2 KB 一块折算，加一点余量。
MAX_WRITE = 2600


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


# ------------------------------------------------------------------ stubs
class FakePWM:
    def __init__(self, *a, **kw):
        pass

    def duty(self, v):
        pass


class FakePin:
    OUT = 1

    def __init__(self, n, *a, **kw):
        self.n = n

    def __call__(self, v=None):
        return 0


class FakeSPI:
    """Records the size of every buffer handed to write()."""
    def __init__(self, *a, **kw):
        self.writes = []

    def write(self, data):
        self.writes.append(len(data))
        return len(data)


def install_stubs():
    mach = types.ModuleType("machine")
    mach.Pin = FakePin
    mach.SPI = FakeSPI
    mach.PWM = FakePWM
    sys.modules["machine"] = mach
    # framebuf 在 CPython 里没有；display 用 try/except 兜住了 ImportError，
    # 所以文字路径会自动退化，这里正好只关心 fill_rect 的分配。
    sys.modules.pop("framebuf", None)
    return mach


def main():
    install_stubs()
    from passport import config as C
    import passport.display as D

    d = D.Display.__new__(D.Display)          # 跳过 __init__（里面会真的初始化面板）
    d.w = C.LCD_W
    d.h = C.LCD_H
    d._fb_cache = {}
    d.spi = FakeSPI()
    d.cs = lambda v=None: None
    d.dc = lambda v=None: None
    d._cmd = lambda *a, **kw: None            # 面板命令与本次检查无关

    print("[1] 满宽填充不再要求大块连续内存")
    d.spi.writes = []
    d.fill_rect(0, 0, 240, 21, D.NAVY)        # 表头：正是当初炸掉的那次调用
    biggest = max(d.spi.writes)
    check("240x21 单次 write <= %d" % MAX_WRITE, biggest <= MAX_WRITE,
          "实际 %d 字节" % biggest)
    check("满宽填充确实分了多块", len(d.spi.writes) > 1,
          "%d 次 write" % len(d.spi.writes))
    check("总字节数正确 (240*21*2)",
          sum(d.spi.writes) == 240 * 21 * 2, "实际 %d" % sum(d.spi.writes))

    print("\n[2] 各种几何都不能冒出大块分配")
    cases = [
        ("整屏", 0, 0, 240, 320),
        ("信息栏", 0, 166, 240, 134),
        ("页脚", 0, 300, 240, 20),
        ("细长竖条", 5, 10, 1, 300),
        ("细长横条", 0, 5, 240, 1),
        ("小方块", 10, 10, 8, 8),
        ("超出右下边界", 200, 300, 100, 100),
        ("超出左上边界", -20, -20, 60, 60),
        ("零宽", 10, 10, 0, 10),
        ("零高", 10, 10, 10, 0),
        ("完全在屏外", 300, 300, 10, 10),
    ]
    worst = 0
    for name, x, y, w, h in cases:
        d.spi.writes = []
        d.fill_rect(x, y, w, h, D.RED)
        if d.spi.writes:
            m = max(d.spi.writes)
            if m > worst:
                worst = m
        check("%s 不超限" % name, not d.spi.writes or max(d.spi.writes) <= MAX_WRITE,
              "最大 %d" % (max(d.spi.writes) if d.spi.writes else 0))
    print("      （所有用例里最大的一次 write: %d 字节）" % worst)

    print("\n[3] 边界裁剪正确（不该画的就一次都别写）")
    for name, x, y, w, h in (("零宽", 10, 10, 0, 10), ("零高", 10, 10, 10, 0),
                             ("屏外", 300, 300, 10, 10),
                             ("负尺寸", 10, 10, -5, -5)):
        d.spi.writes = []
        d.fill_rect(x, y, w, h, D.RED)
        check("%s -> 不产生写入" % name, not d.spi.writes,
              "%d 次" % len(d.spi.writes))

    print("\n[4] 裁剪后的总字节数必须等于可见面积")
    d.spi.writes = []
    d.fill_rect(200, 300, 100, 100, D.RED)     # 可见部分是 40x20
    check("越界填充按可见面积推送",
          sum(d.spi.writes) == 40 * 20 * 2, "实际 %d" % sum(d.spi.writes))
    d.spi.writes = []
    d.fill_rect(-20, -20, 60, 60, D.RED)       # 可见部分是 40x40
    check("负坐标填充按可见面积推送",
          sum(d.spi.writes) == 40 * 40 * 2, "实际 %d" % sum(d.spi.writes))

    print("\n[5] 颜色字节序（ST7789 要高位在前）")
    d.spi.writes = []
    d.spi.write = lambda data: (d.spi.writes.append(bytes(data)), len(data))[1]
    d.fill_rect(0, 0, 2, 1, 0xF800)            # 纯红
    check("红色先发高字节 0xF8", d.spi.writes and d.spi.writes[0][:2] == b"\xf8\x00",
          d.spi.writes[0][:2].hex() if d.spi.writes else "无写入")

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - " + f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
