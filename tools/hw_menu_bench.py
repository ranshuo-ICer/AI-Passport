"""真机测量：主菜单一次更新到底写了多少像素、花了多少时间。

闪烁的根因不是"画得慢"，而是**同一片像素在一次更新里被经过好几次**：
整屏清成 NAVY（一块纯色）→ 再画标题栏 → 再画状态栏 → 再逐行画 → 最后页脚。
中间那些状态都会真的出现在屏上，SPI 又只有几十 Mbit/s，于是肉眼看到闪。

所以这里量的不是"总耗时"一个数，而是：
  * 一次更新往屏幕写了多少字节（决定"亮多久"）
  * 其中有多少是**重复写同一片区域**（决定"闪几下"）
  * 整屏 fill 占了多大比例（决定"白屏多久"）

    python -m mpremote connect COM3 run tools/hw_menu_bench.py
"""

import time

from passport import apps as A
from passport.display import Display
from passport.ui import ROW_H


class CountingSPI:
    """包一层真 SPI，只统计写了多少字节。"""

    def __init__(self, real):
        self.real = real
        self.bytes = 0
        self.writes = 0

    def write(self, buf):
        n = len(buf)
        self.bytes += n
        self.writes += 1
        return self.real.write(buf)


def make_shell(lcd):
    from passport.ui import Shell
    sh = Shell()
    sh.lcd = lcd
    sh.app_list = A.list_apps()
    sh.sel = 0
    sh.scroll = 0
    sh._clamp_scroll()
    sh._status = None
    sh._status_sig = None
    return sh


N = 5


def timed(fn, n=N):
    best = None
    for _ in range(n):
        t0 = time.ticks_us()
        fn()
        dt = time.ticks_diff(time.ticks_us(), t0)
        if best is None or dt < best:
            best = dt
    return best


class RowCap:
    """录下 y 在 [0, h) 这一带的像素，用来比较两条绘制路径。"""

    def __init__(self, real, w, h):
        self.real = real
        self.w = w
        self.h = h
        self.buf = bytearray(w * h * 2)
        self.dc_state = 0
        self.cmd = 0
        self.pix = False
        self.pend = bytearray(4)
        self.pn = 0
        self.x0 = 0
        self.x1 = 0
        self.cx = 0
        self.cy = 0

    def cs(self, v):
        pass

    def dc(self, v):
        self.dc_state = v

    def write(self, b):
        if self.dc_state == 0:
            self.cmd = b[0] if len(b) else 0
            self.pn = 0
            self.pix = (self.cmd == 0x2C)
            return
        if self.pix:
            n = len(b) >> 1
            i = 0
            while i < n:
                left = self.x1 - self.cx + 1
                take = n - i
                if take > left:
                    take = left
                if 0 <= self.cy < self.h:
                    off = (self.cy * self.w + self.cx) * 2
                    self.buf[off:off + take * 2] = b[i * 2:(i + take) * 2]
                i += take
                self.cx += take
                if self.cx > self.x1:
                    self.cx = self.x0
                    self.cy += 1
            return
        if self.cmd == 0x2A or self.cmd == 0x2B:
            n = len(b)
            self.pend[self.pn:self.pn + n] = b
            self.pn += n
            if self.pn >= 4:
                a = (self.pend[0] << 8) | self.pend[1]
                z = (self.pend[2] << 8) | self.pend[3]
                if self.cmd == 0x2A:
                    self.x0, self.x1, self.cx = a, z, a
                else:
                    self.cx, self.cy = self.x0, a
                self.pn = 0


def capture_row(sh, lcd, real_spi, direct):
    """按指定的绘制路径画屏幕第 0 行，返回这一带的像素（面板字节序）。"""
    old_spi, old_dc, old_cs = lcd.spi, lcd.dc, lcd.cs
    old_direct = sh._direct_draw
    cap = RowCap(real_spi, lcd.w, ROW_H)
    lcd.spi = cap
    lcd.dc = cap.dc
    lcd.cs = cap.cs
    lcd._fill_cache.clear()
    lcd._fill_bytes = 0
    sh._direct_draw = direct
    try:
        sh._draw_row(0, 3)                 # 固定画第 4 项，两条路比同一行
    finally:
        sh._direct_draw = old_direct
        lcd.spi, lcd.dc, lcd.cs = old_spi, old_dc, old_cs
    return bytes(cap.buf)


def main():
    lcd = Display(backlight=0)
    real_spi = lcd.spi
    spi = CountingSPI(real_spi)
    lcd.spi = spi
    lcd.drop_text_cache()

    sh = make_shell(lcd)
    full = 240 * 320 * 2

    print("=" * 68)
    print("主菜单更新：写屏字节与耗时（真机，%d 次取最快、字节数取平均）" % N)
    print("=" * 68)

    def measure(label, fn):
        spi.bytes = 0
        spi.writes = 0
        dt = timed(fn)
        # 计数器跨 N 次累计，必须除以 N 才是"一次更新"的量
        nb = spi.bytes // N
        nw = spi.writes // N
        print("  %-28s %7.1f ms  %7d B  %6.1f%% 屏  %4d 次写"
              % (label, dt / 1000.0, nb, nb * 100.0 / full, nw))
        return dt, nb, nw

    rows = []
    rows.append(("整屏 draw_menu()",) + measure("整屏 draw_menu()",
                                                sh.draw_menu))
    rows.append(("仅整屏 fill()",) + measure(
        "仅整屏 fill()", lambda: lcd.fill(0x0010)))
    rows.append(("单行 _draw_row()",) + measure(
        "单行 _draw_row(0,0)", lambda: sh._draw_row(0, 0)))
    rows.append(("状态栏强制重画",) + measure(
        "状态栏强制重画", lambda: sh.draw_status(force=True)))

    # ---------------------------------------------------------------- 等价性
    # 现在有两种画一行的方式：直接画屏幕（一条条 SPI 写）和整行离屏合成后一次
    # blit。两者必须**像素完全一致**，否则换路径就会改变画面。这里把两条路各自
    # 产生的像素流录下来逐字节比较 —— 这是合成路径唯一可信的验证方式。
    print("")
    print("  [等价性] 直接画 vs 离屏合成（同一行、同一项）")
    row_a = capture_row(sh, lcd, real_spi, direct=True)
    row_b = capture_row(sh, lcd, real_spi, direct=False)
    same = row_a == row_b
    diff = 0
    if not same:
        for i in range(0, len(row_a), 2):
            if row_a[i:i + 2] != row_b[i:i + 2]:
                diff += 1
    print("    %s  长度 %d/%d，不同像素 %d"
          % ("一致 OK" if same else "不一致 FAIL", len(row_a), len(row_b), diff))

    # 选择移动分两种，必须分开量：
    #   · 窗口内移动：只该重画两行
    #   · 跨过滚动边界：整个列表换了位置，只能整屏重画
    def move_in_window():
        sh.sel = 3
        sh.scroll = 0
        sh._clamp_scroll()
        sh._tick_menu("down")          # 3 -> 4，窗口没动

    def move_scrolling():
        sh.sel = sh._visible_rows() + sh.scroll - 1   # 正好在窗口下边缘
        sh._tick_menu("down")                          # 触发滚动

    rows.append(("窗口内移动一次选择",) + measure("窗口内移动一次选择",
                                                  move_in_window))
    rows.append(("跨过滚动边界一次",) + measure("跨过滚动边界一次",
                                                move_scrolling))

    print("")
    print("  SPI 满速参考: 240x320x2 = %d B/整屏" % full)
    n = len(sh.app_list)
    print("  设备上共 %d 个小程序，可见 %d 行" % (n, sh._visible_rows()))
    print("=" * 68)
    print("  结论怎么看：")
    print("   · 「仅整屏 fill()」那一行就是用户看到的**纯色空白**时长")
    print("   · 整屏 draw_menu 的字节数超过 100%% 屏，说明有区域被写了不止一次")
    print("   · 移动一次选择如果接近整屏，就是每次按键都全屏重画 → 闪")


main()
