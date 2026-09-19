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
        self.name = kw.pop("_name", "pin")
        self.log = None

    def __call__(self, v=None):
        if self.log is not None:
            self.log.append((self.name, v))
        return 0


class FakeSPI:
    """Records every buffer handed to write(), tagged with the DC level."""
    def __init__(self, *a, **kw):
        self.writes = []          # sizes only (kept for the older assertions)
        self.frames = []          # (dc_value_at_write, bytes)
        self._dc = None

    def write(self, data):
        b = bytes(data)
        self.writes.append(len(b))
        self.frames.append((self._dc, b))
        return len(b)


class DcTrackingPin:
    """A DC pin that also tells the SPI stub what level is currently driven.

    Without this the stub cannot tell a command byte from a parameter byte.
    """
    def __init__(self, log, spi):
        self.log = log
        self.spi = spi

    def __call__(self, v=None):
        self.log.append(("dc", v))
        self.spi._dc = v
        return 0


class CsTrackingPin:
    def __init__(self, log):
        self.log = log

    def __call__(self, v=None):
        self.log.append(("cs", v))
        return 0


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
    d._fb_bytes = 0
    d._win = bytearray(4)                     # set_window 的常驻参数缓冲
    d._swap = bytearray(64)                   # blit 的常驻翻转缓冲
    d._fill_cache = {}                        # fill_rect 的纯色块缓存
    d._fill_bytes = 0
    d.spi = FakeSPI()
    gpio = []
    d.cs = CsTrackingPin(gpio)
    d.dc = DcTrackingPin(gpio, d.spi)
    # 像素数据的断言只关心"推了多少像素"，窗口设置另有专测（第 6 节）
    d.set_window = lambda *a: None

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

    print("\n[1b] 纯色块缓存：命中时零分配，交替两色也应命中")
    # fill_rect 原来每次调用都要 line = bytes(...)*w 再 block = line*rows 两次
    # 分配（真机实测准备阶段 537 µs，而 bytes 重复本身约 0.3 µs/字节砍不掉）。
    # 现在按 (颜色, 宽度) 缓存整块图案。
    d.spi.writes = []
    d.fill_rect(0, 0, 240, 8, D.NAVY)
    navy_block = d._fill_cache[(D.NAVY, 240)]
    check("图案字节就是 (hi, lo) 交替",
          all(navy_block[i] == (D.NAVY >> 8 if i % 2 == 0 else D.NAVY & 0xFF)
              for i in range(len(navy_block))),
          "前 4 字节: %s" % list(navy_block[:4]))
    check("块长 = w*2*rows（满宽 1920）", len(navy_block) == 240 * 2 * 4,
          "len=%d" % len(navy_block))

    d.fill_rect(0, 40, 240, 8, D.NAVY)
    check("同色同宽命中同一块", d._fill_cache[(D.NAVY, 240)] is navy_block)

    # UI 里最常见的模式：同一矩形交替两种颜色（进度条底/前景、Beats 脏矩形）
    d.fill_rect(0, 60, 240, 8, D.RED)
    red_block = d._fill_cache[(D.RED, 240)]
    d.fill_rect(0, 60, 240, 8, D.NAVY)
    d.fill_rect(0, 60, 240, 8, D.RED)
    check("交替两色两格都命中，互不驱逐",
          d._fill_cache.get((D.RED, 240)) is red_block
          and d._fill_cache.get((D.NAVY, 240)) is navy_block,
          "缓存键 %s" % sorted(d._fill_cache))
    check("换色后图案正确",
          red_block[0] == (D.RED >> 8) and red_block[1] == (D.RED & 0xFF),
          "前 2 字节: %s" % list(red_block[:2]))

    # 预算满了要整个丢掉重来，不能无上限涨
    for c in (D.GREEN, D.BLUE, D.YELLOW, D.CYAN, D.MAGENTA, D.WHITE, D.SILVER):
        d.fill_rect(0, 100, 240, 8, c)
    check("缓存受预算限制（%d B）" % D.FILL_FB_BUDGET,
          d._fill_bytes <= D.FILL_FB_BUDGET, "占用 %d" % d._fill_bytes)

    d.drop_text_cache()
    check("drop_text_cache() 连纯色块一起放掉",
          not d._fill_cache and d._fill_bytes == 0)

    d.spi.writes = []
    d.fill_rect(0, 0, 240, 322, D.GREEN)        # 322 > 320，必然走裁剪 + tail 分支
    check("带 tail 分支时总字节仍然精确",
          sum(d.spi.writes) == 240 * 320 * 2,   # 高度被裁到 320
          "实际 %d" % sum(d.spi.writes))

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
    d.set_window = lambda *a: None
    d.spi.frames = []
    d.fill_rect(0, 0, 2, 1, 0xF800)            # 纯红
    px = d.spi.frames[0][1] if d.spi.frames else b""
    check("红色先发高字节 0xF8", px[:2] == b"\xf8\x00",
          px[:2].hex() if px else "无写入")

    print("\n[6] set_window 的协议序列（优化后必须仍然正确）")
    # 把真实 set_window 装回去（前面为了数像素把它换掉了）
    d.set_window = D.Display.set_window.__get__(d, D.Display)
    d.spi.writes = []
    d.spi.frames = []
    gpio.clear()
    d.set_window(0x12, 0x34, 0x56, 0x78)

    # 期望：CS 拉低一次 -> (DC低,'2A') (DC高, x0hi x0lo x1hi x1lo)
    #       -> (DC低,'2B') (DC高, y0hi y0lo y1hi y1lo) -> (DC低,'2C') -> CS 拉高
    cmds = [(dc, b) for dc, b in d.spi.frames]
    check("一共 5 次 SPI 写", len(cmds) == 5, "%d 次" % len(cmds))
    check("X 列命令 0x2A + 4 字节参数",
          len(cmds) > 1 and cmds[0] == (0, b"\x2a")
          and cmds[1] == (1, b"\x00\x12\x00\x56"),
          repr(cmds[:2]))
    check("Y 行命令 0x2B + 4 字节参数",
          len(cmds) > 3 and cmds[2] == (0, b"\x2b")
          and cmds[3] == (1, b"\x00\x34\x00\x78"),
          repr(cmds[2:4]))
    check("RAMWR 0x2C", len(cmds) > 4 and cmds[4] == (0, b"\x2c"), repr(cmds[4:5]))
    cs_seq = [v for name, v in gpio if name == "cs"]
    check("整个过程 CS 只拉低一次", cs_seq == [0, 1], repr(cs_seq))
    check("命令字节走 DC=0、参数走 DC=1",
          [dc for dc, _ in cmds] == [0, 1, 0, 1, 0],
          repr([dc for dc, _ in cmds]))

    print("\n[7] blit 复用常驻缓冲，不再每次分配")
    d.set_window = lambda *a: None
    buf = bytearray(80 * 8 * 2)
    for i in range(len(buf)):
        buf[i] = (i * 3 + 1) & 0xFF
    d._swap = bytearray(64)                    # 故意给个小的，逼它按需增长
    old_id = id(d._swap)
    d.blit(buf, 0, 0, 80, 8)
    grown = len(d._swap)
    check("缓冲按需增长到 >= 请求长度", grown >= len(buf), "%d" % grown)
    d.spi.frames = []
    d.spi._dc = None
    d.blit(buf, 0, 0, 80, 8)
    first = d.spi.frames[0][1]
    check("翻转结果正确（高低字节互换）",
          first[0] == buf[1] and first[1] == buf[0],
          "%02x %02x vs %02x %02x" % (first[0], first[1], buf[0], buf[1]))
    check("缓冲被复用（没有重新分配）", len(d._swap) == grown)
    check("推给 SPI 的就是 80*8*2 字节", len(first) == 80 * 8 * 2,
          "实际 %d" % len(first))

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - " + f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
