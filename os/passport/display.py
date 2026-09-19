"""ST7789P3 显示驱动（240x320，SPI）。

初始化序列取自官方 BSP `components/bsp/src/bsp_display.c` 的厂商参数表
（PORCTRL / GCTRL / VCOMS / LCMCTRL / PWCTRL / 伽马）。这不是 ST7789 通用默认值，
照抄通用序列屏幕不会正常显示 —— 这是本板最容易踩的坑。

设计取舍：ESP32-C3 没有 PSRAM，MicroPython 可用堆只有约 150KB，
而 240x320 RGB565 整屏缓冲就要 153,600 字节 —— 放不下。
所以这里不使用整屏 framebuf，而是：
  * 纯色块直接按行写 SPI（只占一条扫描线的内存）
  * 文字先用小块 framebuf 渲染，再整体推送
"""

import time
import array

try:
    import framebuf
except ImportError:          # 理论上 ESP32 固件自带，兜底避免 import 崩
    framebuf = None

from machine import Pin, SPI, PWM

from . import config as C

# ---------------------------------------------------------------- 颜色 (RGB565)
BLACK = 0x0000
WHITE = 0xFFFF
RED = 0xF800
GREEN = 0x07E0
BLUE = 0x001F
YELLOW = 0xFFE0
CYAN = 0x07FF
MAGENTA = 0xF81F
GREY = 0x8410
SILVER = 0xC618
DARK = 0x18E3
NAVY = 0x0010
ORANGE = 0xFD20
TEAL = 0x0410


def rgb(r, g, b):
    """24bit RGB → 16bit RGB565。"""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# 文字帧缓冲缓存的总预算（字节）。见 _text_fb() 的说明。
# 4 KB 足够覆盖几个常见宽度，同时不会把堆切碎。
TEXT_FB_BUDGET = 4096

# fill_rect 每次推给 SPI 的分块大小。压到 2 KB 是为了让碎片化的堆也凑得出
# 连续内存（历史事故：8160 字节的块直接 MemoryError）。这块缓冲现在是常驻的，
# 所以不用再为分配代价留余量。
FILL_CHUNK = 2048

# ===========================================================================
# 热点加速：viper
#
# 实测（真机 hw_bench.py，160MHz ESP32-C3）：
#     _to_be 纯 Python      5.1 us/字节   → 1280 字节的文字要 6.5 ms
#     一次 text(10 字符)   10.1 ms，其中 _to_be 占 65%
#     fill 整屏            69 ms
# 界面刷新慢的根子就在这里。
#
# 为什么用 viper 而不是 C：
#   · viper 是"用 Python 写、编译成机器码"，量级上接近 C（音频那边实测 26x）；
#   · **不需要重编固件** —— 官方预编译的 MicroPython 直接就能跑；
#   · 写成 C 模块就得自己维护一份固件，代价远大于那点额外收益。
# 编译不出机器码是可能的（DRAM 不可执行等），所以整块包在 try 里，
# 失败就退回纯 Python，两条路结果逐位相同。
# ===========================================================================
try:
    import micropython
except ImportError:                                       # noqa: BLE001
    micropython = None

_to_be_fast = None
_scale_row_fast = None

if micropython is not None:
    # 两个函数分开 try：一个编译不出来不该连累另一个。
    try:
        @micropython.viper
        def _to_be_fast(src, dst, n: int):
            s = ptr8(src)
            d = ptr8(dst)
            i = 0
            while i < n:
                d[i] = s[i + 1]
                d[i + 1] = s[i]
                i += 2
    except Exception:                                     # noqa: BLE001
        _to_be_fast = None

    try:
        @micropython.viper
        def _scale_row_fast(src, dst, base: int, w: int, scale: int):
            s = ptr8(src)
            d = ptr8(dst)
            i = 0
            while i < w:
                lo = s[base + i * 2]
                hi = s[base + i * 2 + 1]
                b = i * scale * 2
                k = 0
                while k < scale:
                    d[b] = hi
                    d[b + 1] = lo
                    b += 2
                    k += 1
                i += 1
    except Exception:                                     # noqa: BLE001
        _scale_row_fast = None


def _scale_row(src, dst, base, w, scale):
    """把 src 里从 base 开始的一行（w 个像素）横向放大 scale 倍写进 dst（大端）。"""
    if _scale_row_fast is not None:
        try:
            _scale_row_fast(src, dst, base, w, scale)
            return
        except Exception:                                 # noqa: BLE001
            pass
    for i in range(w):
        lo = src[base + i * 2]
        hi = src[base + i * 2 + 1]
        b = i * scale * 2
        for _k in range(scale):
            dst[b] = hi
            dst[b + 1] = lo
            b += 2


# 常命令字节，避免热路径上每次 bytes((cmd,)) 分配
_B_2A = b"\x2a"
_B_2B = b"\x2b"
_B_2C = b"\x2c"


def _to_be_into(src, dst, n):
    """把 src 的 n 个字节按 16bit 翻转写进 dst。viper 优先，失败退回纯 Python。"""
    if _to_be_fast is not None:
        try:
            _to_be_fast(src, dst, n)
            return
        except Exception:                                 # noqa: BLE001
            pass
    j = 1
    for i in range(0, n - 1, 2):
        dst[i] = src[j]
        dst[j] = src[i]
        j += 2


def _to_be(buf):
    """RGB565 小端字节序 → ST7789 需要的高位在前（返回新缓冲）。

    热路径（Display.blit）请走 _to_be_into()，复用常驻缓冲、不再每次分配。
    """
    mv = memoryview(buf)
    n = len(mv) & ~1                     # 只处理成对的字节
    out = bytearray(n)
    _to_be_into(mv, out, n)
    return out


# --------------------------------------------------------------- 纯色填充图案
# fill_rect 的历史包袱：每次调用都 `line = bytes(...)*w` 再 `block = line*rows`。
# w=240 时那是 480 + 1920 字节两次分配，实测整段准备约 537 µs，而 fill_rect 的
# 小矩形本来只花约 1.9 ms —— 光准备就占了近三成。
#
# 真机实测的三种铺法（目标 1920 字节，w=240）：
#     bytes(2) * 960            571.7 µs   （一步，大 count）
#     bytes(2)*240 再 *4        537.0 µs   （旧写法两步）
#     bytearray(1920)           172.8 µs   （纯分配，但是零填充）
#   结论：**`bytes` 重复约 0.3 µs/字节，是这段开销的本质**，怎么拆都一样；
#   bytearray 分配快 3 倍但它填的是 0，还得再铺一遍图案。
#   还试过两种"更聪明"的铺法，都更慢，别改回去：
#     - viper 逐字节循环填 1920 字节：交替两色时比旧实现慢 15%；
#     - 写 2 字节后 `buf[a:b] = buf[0:n]` 对折复制：慢 214%（1.0 ms -> 3.1 ms）。
#
# 所以唯一的出路是**别重复铺**：按 (颜色, 宽度) 缓存整块图案。
# UI 里最常见的模式恰恰是"同一个矩形交替两种颜色"（进度条的底+前景、
# Beats 脏矩形的开/关），两格缓存就能把这两种情况都变成零开销。
FILL_FB_BUDGET = 6144        # 纯色块缓存预算（字节）；满宽一块 1920 B，够放 3 色


# ST7789P3 厂商专属初始化序列：(命令, 参数, 延时ms)
# 注意 0xD0 连发两次，第二次覆盖第一次 —— 这是参考例程的原样，不要"优化"掉。
VENDOR_INIT = (
    (0xB2, (0x05, 0x05, 0x00, 0x33, 0x33), 0),      # PORCTRL 帧率 porch
    (0xB7, (0x35,), 0),                              # GCTRL 栅极
    (0xBB, (0x21,), 0),                              # VCOMS
    (0xC0, (0x2C,), 0),                              # LCMCTRL
    (0xC2, (0x01,), 0),                              # VDVVRHEN
    (0xC3, (0x0B,), 0),                              # VRHS
    (0xC4, (0x20,), 0),                              # VDVSET
    (0xC6, (0x0F,), 0),                              # FRCTRL2 60Hz 点反转
    (0xD0, (0xA7, 0xA1), 0),                         # PWCTRL1
    (0xD0, (0xA4, 0xA1), 0),                         # PWCTRL1 重发覆盖
    (0xD6, (0xA1,), 0),                              # 
    (0xE0, (0xD0, 0x04, 0x08, 0x0A, 0x09, 0x05, 0x2D, 0x43,
            0x49, 0x09, 0x16, 0x15, 0x26, 0x2B), 0),  # PVGAMCTRL 正伽马
    (0xE1, (0xD0, 0x03, 0x09, 0x0A, 0x0A, 0x06, 0x2E, 0x44,
            0x40, 0x3A, 0x15, 0x15, 0x26, 0x2A), 10), # NVGAMCTRL 负伽马
)


class Display:
    def __init__(self, backlight=70):
        self.w = C.LCD_W
        self.h = C.LCD_H
        self._fb_cache = {}
        self._fb_bytes = 0

        # SPI mode 由 config 的 LCD_SPI_MODE 推导：bit0=polarity, bit1=phase。
        # 本屏是 mode 0（SCK 空闲低、上升沿采样）。
        self.spi = SPI(
            C.LCD_SPI_ID,
            baudrate=C.LCD_BAUD,
            polarity=C.LCD_SPI_MODE & 1,
            phase=(C.LCD_SPI_MODE >> 1) & 1,
            sck=Pin(C.LCD_SCLK),
            mosi=Pin(C.LCD_MOSI),
        )
        self.cs = Pin(C.LCD_CS, Pin.OUT, value=1)
        self.dc = Pin(C.LCD_DC, Pin.OUT, value=1)
        # 背光先关，初始化完再点亮，避免开机花屏
        self.bl = PWM(Pin(C.LCD_BL), freq=C.LCD_BL_FREQ, duty=0)

        # 常驻小缓冲，避免热路径上反复分配：
        #   _win  —— set_window 的 4 字节参数
        #   _swap —— blit 的字节序翻转输出（按需增长，只增不减）
        #   _fill_cache —— fill_rect 的纯色块，按 (颜色, 宽度) 缓存
        self._win = bytearray(4)
        self._swap = bytearray(64)
        self._fill_cache = {}
        self._fill_bytes = 0

        self._init_panel()
        self.backlight(backlight)

    # ------------------------------------------------------------------ 底层
    def _write(self, data, is_data):
        self.dc(1 if is_data else 0)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)

    def _cmd(self, cmd, data=None):
        self._write(bytes((cmd,)), False)
        if data:
            self._write(bytes(data), True)

    def _init_panel(self):
        if C.LCD_RST is not None and C.LCD_RST >= 0:
            rst = Pin(C.LCD_RST, Pin.OUT, value=1)
            rst(0)
            time.sleep_ms(20)
            rst(1)
            time.sleep_ms(120)
        else:
            self._cmd(0x01)                 # SWRESET 软复位
            time.sleep_ms(150)

        self._cmd(0x11)                     # SLPOUT 退出睡眠
        time.sleep_ms(120)
        self._cmd(0x3A, (0x55,))            # COLMOD: 16bit RGB565

        for cmd, data, delay in VENDOR_INIT:
            self._cmd(cmd, data)
            if delay:
                time.sleep_ms(delay)

        if C.LCD_INVERT:
            self._cmd(0x21)                 # INVON 本屏必须反色
        self._cmd(0x36, (0x00,))            # MADCTL 不镜像
        self._cmd(0x29)                     # DISPON 开显示
        time.sleep_ms(50)

    def backlight(self, pct):
        """背光 0~100。"""
        if pct < 0:
            pct = 0
        elif pct > 100:
            pct = 100
        self.bl.duty(int(pct * 1023 // 100))

    # ------------------------------------------------------------------ 绘图
    def set_window(self, x0, y0, x1, y1):
        """设定刷新窗口并进入写显存模式（0x2A / 0x2B / 0x2C）。

        这段在热路径上被调用得极频繁（每画一块就走一次），所以专门优化过：

        · **整个过程 CS 只拉低一次**。原来每条命令都走 _write()，各自切一遍
          CS/DC —— 一次 set_window 要 15 回 Pin 调用。
        · **参数用预分配的 4 字节缓冲拼**，不再每次 bytes((...)) 分配。

        实测这是 blit 里除字节翻转外最大的一块开销。
        """
        win = self._win
        self.cs(0)

        self.dc(0)
        self.spi.write(_B_2A)
        win[0] = x0 >> 8
        win[1] = x0 & 0xFF
        win[2] = x1 >> 8
        win[3] = x1 & 0xFF
        self.dc(1)
        self.spi.write(win)

        self.dc(0)
        self.spi.write(_B_2B)
        win[0] = y0 >> 8
        win[1] = y0 & 0xFF
        win[2] = y1 >> 8
        win[3] = y1 & 0xFF
        self.dc(1)
        self.spi.write(win)

        self.dc(0)
        self.spi.write(_B_2C)               # RAMWR
        self.cs(1)

    def fill_rect(self, x, y, w, h, color):
        """纯色矩形。按扫描线分块推送，内存占用与矩形高度无关。"""
        if w <= 0 or h <= 0:
            return
        x0 = x if x > 0 else 0
        y0 = y if y > 0 else 0
        x1 = x + w
        if x1 > self.w:
            x1 = self.w
        y1 = y + h
        if y1 > self.h:
            y1 = self.h
        if x1 <= x0 or y1 <= y0:
            return
        w = x1 - x0
        h = y1 - y0

        self.set_window(x0, y0, x1 - 1, y1 - 1)
        # 分块推送。临时缓冲是 w*2*rows 字节，必须把 rows 压住：
        # 原来按「8192 字节一块」折算，w=240 时 rows=17，也就是每次满宽填充都要
        # 一次性分配 8160 字节。堆里即使有 43 KB 空闲，碎片化之后也凑不出
        # 连续的 8 KB，于是直接 MemoryError —— 实测 Beats 就是这么挂的，
        # 而且它影响所有满宽填充的小程序。
        # 现在每块最多约 2 KB，碎片化的堆也扛得住。
        rows = FILL_CHUNK // (w * 2)
        if rows < 1:
            rows = 1
        need = rows * w * 2
        # 纯色块缓存：命中就整段复用，一次分配、一次铺图案都不用。
        key = (color, w)
        buf = self._fill_cache.get(key)
        if buf is None or len(buf) != need:
            line = bytes((color >> 8, color & 0xFF)) * w
            buf = line * rows
            # 预算还有余量才入缓存。满了**既不清空也不插入** ——
            # 清空会让"颜色很多"的场景反复重铺，比不缓存还慢（真机 A/B 实测 +11%）；
            # 不插入则最坏情况就退回旧实现的代价，不会倒扣。
            if self._fill_bytes + need <= FILL_FB_BUDGET:
                self._fill_cache[key] = buf
                self._fill_bytes += need
        self.dc(1)
        self.cs(0)
        done = 0
        while done < h:
            if done + rows <= h:
                self.spi.write(buf)
                done += rows
            else:
                # 最后不足一块：切一段 memoryview，不再另拼一个 bytes
                # （图案是 2 字节周期，任意偶数长度都对）
                tail = h - done
                self.spi.write(memoryview(buf)[:tail * w * 2])
                done += tail
        self.cs(1)

    def fill(self, color):
        self.fill_rect(0, 0, self.w, self.h, color)

    def hline(self, x, y, w, color):
        self.fill_rect(x, y, w, 1, color)

    def vline(self, x, y, h, color):
        self.fill_rect(x, y, 1, h, color)

    def rect(self, x, y, w, h, color):
        self.hline(x, y, w, color)
        self.hline(x, y + h - 1, w, color)
        self.vline(x, y, h, color)
        self.vline(x + w - 1, y, h, color)

    def blit(self, buf, x, y, w, h, swap=True):
        """把 RGB565 缓冲推到屏幕。

        MicroPython framebuf 的 RGB565 是小端存放，而 ST7789 要高位在前，
        所以默认做一次 16bit 字节序翻转。

        ⚠ 不要用 array.byteswap() —— **MicroPython 的 array 没有这个方法**
        （CPython 有，所以这个坑在电脑上跑测试时完全暴露不出来，只有真机才炸）。

        翻转结果写进常驻的 self._swap，不再每次新建 bytearray —— 文字每帧
        都要走这条路径，省下的就是每次 1 KB 上下的分配 + GC。
        """
        if x >= self.w or y >= self.h or w <= 0 or h <= 0:
            return
        self.set_window(x, y, x + w - 1, y + h - 1)
        if swap:
            mv = memoryview(buf)
            n = len(mv) & ~1
            if n == 0:
                return
            if len(self._swap) < n:
                self._swap = bytearray(n)
            _to_be_into(mv, self._swap, n)
            # spi.write 是阻塞的，写完才返回，所以复用缓冲是安全的
            data = memoryview(self._swap)[:n]
        else:
            data = buf
        self.dc(1)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)

    # ------------------------------------------------------------------ 文字
    # 用 MicroPython 固件自带 framebuf 的 8x8 点阵字体（只有 ASCII），
    # 免去打包字库。中文需要用 bitmap 资源，见 docs/ble-protocol.md。
    def _text_fb(self, width):
        fb = self._fb_cache.get(width)
        if fb is None:
            need = width * 8 * 2
            # 按【总字节数】而不是【条目数】设限。
            # 原来是"超过 16 条就全清"，可 16 条宽字符串轻轻松松吃掉十几 KB，
            # 而且都是几十~几千字节的中等块 —— 堆被切得七零八落之后，
            # 连"还剩 78 KB 空闲"都分配不出一个 19 KB 的连续块。
            # 实测：小程序载入上限因此被压在 16 KB（20 KB 就 MemoryError），
            # 而上传上限是 32 KB。压住这块缓存之后载入上限才提得上去。
            if self._fb_bytes + need > TEXT_FB_BUDGET:
                self._fb_cache.clear()
                self._fb_bytes = 0
            fb = framebuf.FrameBuffer(bytearray(need), width, 8,
                                      framebuf.RGB565)
            self._fb_cache[width] = fb
            self._fb_bytes += need
        return fb

    def drop_text_cache(self):
        """丢掉文字帧缓冲缓存，把连续空闲内存让给大块分配。

        载入小程序前调用：`read_source()` 要一次性要一块和文件等长的连续内存，
        而缓存里那几十个中等大小的块正好把堆切碎 —— 实测启动器画完菜单后
        载入 32 KB 程序会 MemoryError，先清掉这里就能进去。
        """
        self._fb_cache.clear()
        self._fb_bytes = 0
        # 纯色块缓存也一起放掉：它单块 1.9 KB，同样会把堆切碎
        self._fill_cache.clear()
        self._fill_bytes = 0

    def text(self, s, x, y, color, bg=BLACK):
        """8x8 等宽 ASCII 文字。bg=None 时用黑色填充。"""
        if not s or framebuf is None:
            return
        w = len(s) * 8
        fb = self._text_fb(w)
        fb.fill(BLACK if bg is None else bg)
        fb.text(s, 0, 0, color)
        self.blit(fb, x, y, w, 8)

    def text_scale(self, s, x, y, color, bg=BLACK, scale=2):
        """按整数倍放大文字。逐行构造，峰值内存只有一条放大扫描线。"""
        if not s or framebuf is None:
            return
        if bg is None:
            bg = BLACK
        if scale < 1:
            scale = 1
        if scale == 1:
            self.text(s, x, y, color, bg)
            return

        w = len(s) * 8
        fb = self._text_fb(w)
        fb.fill(bg)
        fb.text(s, 0, 0, color)

        out_w = w * scale
        # 直接按大端构造输出行，避免再做一次字节序翻转
        row = bytearray(out_w * 2)
        self.set_window(x, y, x + out_w - 1, y + 8 * scale - 1)
        mv = memoryview(fb)
        self.dc(1)
        self.cs(0)
        for r in range(8):
            base_r = r * w * 2
            _scale_row(mv, row, base_r, w, scale)
            for _ in range(scale):
                self.spi.write(row)
        self.cs(1)

    def text2x(self, s, x, y, color, bg=BLACK):
        self.text_scale(s, x, y, color, bg, 2)

    def text_center(self, s, y, color, bg=BLACK, scale=1):
        w = len(s) * 8 * scale
        x = (self.w - w) // 2
        if x < 0:
            x = 0
        self.text_scale(s, x, y, color, bg, scale)

    # ------------------------------------------------------------------ 组件
    def progress(self, x, y, w, h, pct, fg=GREEN, bg=DARK):
        """进度条，pct 0~100。"""
        self.fill_rect(x, y, w, h, bg)
        self.rect(x, y, w, h, GREY)
        inner = (w - 2) * max(0, min(100, pct)) // 100
        if inner > 0:
            self.fill_rect(x + 1, y + 1, inner, h - 2, fg)

    def splash(self, title, sub=""):
        self.fill(NAVY)
        self.text2x(title[:10], 20, 120, WHITE, NAVY)
        if sub:
            self.text(sub[:28], 12, 160, SILVER, NAVY)
