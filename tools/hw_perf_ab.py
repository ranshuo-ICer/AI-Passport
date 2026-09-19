"""同场 A/B：三项性能改动，把新旧两种实现放在一次运行里对比。

为什么同场对比：跨会话的数字会被堆状态、系统负载影响，靠不住。这里把"旧写法"
内联复现出来，和新代码在同一个进程、同一段堆上轮流测。

    python -m mpremote connect COM3 run tools/hw_perf_ab.py
"""

import gc
import time

import machine
from machine import ADC, Pin

from passport import config as C
from passport.buttons import Buttons

print("=" * 66)
print("性能改动 A/B（同场对比）")
print("=" * 66)

R = []


def bench(name, n, fn):
    fn()
    gc.collect()
    t0 = time.ticks_us()
    for _ in range(n):
        fn()
    dt = time.ticks_diff(time.ticks_us(), t0) / n
    print("  %-42s %9.1f us" % (name, dt))
    return dt


def ab(label, n, old, new, note=""):
    o = bench("OLD  " + label, n, old)
    w = bench("NEW  " + label, n, new)
    ratio = (o / w) if w else 0
    print("       -> %.1f%% 更快，省 %.1f us/次%s" % ((1 - w / o) * 100, o - w,
                                                     ("  " + note) if note else ""))
    R.append((label, o, w))
    print()


# ============================================================ 1. 按键
print("[1] buttons：去掉 list/sort 分配")
btn = Buttons()
adc = btn.adc


def old_raw():
    vals = []
    for _ in range(4):
        try:
            vals.append(adc.read_uv() // 1000)
        except AttributeError:
            vals.append(adc.read() * 3300 // 4095)
    vals.sort()
    return vals[len(vals) // 2]


# 两个实现的返回值必须一致，否则比的不是同一个东西
vals_ok = all(old_raw() == btn.raw_mv() for _ in range(60))
print("      等价性：60 次采样新旧返回值全等 = %s" % vals_ok)
ab("buttons.raw_mv()", 300, old_raw, btn.raw_mv,
   "(ADC 读取占大头，砍不掉)")

# ============================================================ 2. fill_rect
print("[2] fill_rect：常驻缓冲取代每次两次分配")
from passport.display import Display, FILL_CHUNK

lcd = Display(backlight=0)


class NopSPI:
    def write(self, buf):
        return len(buf)


lcd.spi = NopSPI()
lcd.set_window = lambda *a: None
lcd.dc = lambda v: None
lcd.cs = lambda v: None


def old_fill_rect(x, y, w, h, color):
    """旧 fill_rect 的**完整**复刻：含边界裁剪与 SPI 事务外壳。

    必须连外壳一起复刻，否则比较是假的 —— 内联一个"只建块+写块"的函数去比
    完整的 fill_rect 方法，会把方法调用/裁剪/事务那部分的开销全算到新实现头上
    （第一版就是这样，得出"最坏情况慢 16%"的错误结论）。
    """
    if w <= 0 or h <= 0:
        return
    x0 = x if x > 0 else 0
    y0 = y if y > 0 else 0
    x1 = x + w
    if x1 > lcd.w:
        x1 = lcd.w
    y1 = y + h
    if y1 > lcd.h:
        y1 = lcd.h
    if x1 <= x0 or y1 <= y0:
        return
    w = x1 - x0
    h = y1 - y0
    lcd.set_window(x0, y0, x1 - 1, y1 - 1)
    line = bytes((color >> 8, color & 0xFF)) * w
    rows = FILL_CHUNK // (w * 2)
    if rows < 1:
        rows = 1
    block = line * rows
    lcd.dc(1)
    lcd.cs(0)
    done = 0
    while done < h:
        if done + rows <= h:
            lcd.spi.write(block)
            done += rows
        else:
            tail = h - done
            lcd.spi.write(line * tail)
            done += tail
    lcd.cs(1)


def new_fill():
    lcd.fill_rect(0, 0, 240, 20, 0x001F)


def old_fill_c_1f():
    old_fill_rect(0, 0, 240, 20, 0x001F)


ab("fill_rect 240x20 同色复现 (SPI 已打桩)", 400, old_fill_c_1f, new_fill,
   "命中缓存后零分配、零铺图案")

# 真实的交替场景：同一个矩形在两种颜色之间来回切（进度条底/前景、
# Beats 脏矩形的开/关）。旧实现每次都重铺；新实现两格都缓存，第二圈起免费。
def old_alt():
    old_fill_rect(0, 0, 240, 20, 0x001F)
    old_fill_rect(0, 0, 240, 20, 0xF800)


def new_alt():
    lcd.fill_rect(0, 0, 240, 20, 0x001F)
    lcd.fill_rect(0, 0, 240, 20, 0xF800)


ab("交替两色 2 次填充", 400, old_alt, new_alt,
   "新实现只有每对颜色的第一次要铺")

# 冷缓存：每次换一个没用过的颜色，逼新实现每次重建（最坏情况）
_ci = [0]


def new_cold():
    _ci[0] = (_ci[0] + 1) & 0xFF
    lcd.fill_rect(0, 0, 240, 20, (_ci[0] << 8) | _ci[0])


def old_cold():
    _ci[0] = (_ci[0] + 1) & 0xFF
    old_fill_rect(0, 0, 240, 20, (_ci[0] << 8) | _ci[0])


ab("每次都换新颜色（最坏情况）", 300, old_cold, new_cold,
   "缓存未命中，应与旧实现持平（不许倒扣）")

# ============================================================ 3. 状态栏
print("[3] draw_status：先比廉价指纹再拼串")
label = "85%"


def old_status():
    text = "BLE: " + C.BLE_NAME
    text = text + "   BAT " + label
    return text == "BLE: PassportOS   BAT 85%"


sig_state = (False, False, True, 85, 4085, 0)
cached_sig = sig_state


def new_status():
    sig = (False, False, True, 85, 4085, 0)
    if cached_sig == sig:
        return True
    text = "BLE: " + C.BLE_NAME
    text = text + "   BAT " + label
    return text == "BLE: PassportOS   BAT 85%"


ab("draw_status 未变化时", 600, old_status, new_status)

# ============================================================ 汇总
print("=" * 66)
print("按主循环 20 ms、每 tick 各调一次折算（CPU 占用）")
total_old = total_new = 0.0
for label, o, w in R:
    if "raw_mv" in label:
        total_old += o
        total_new += w
        print("  %-40s %6.2f%% -> %6.2f%%" % (label, o / 20000 * 100,
                                              w / 20000 * 100))
    elif "fill_rect 240x20" in label:
        print("  %-40s %6.2f%% -> %6.2f%%" % (label, o / 20000 * 100,
                                              w / 20000 * 100))
    elif "draw_status" in label:
        total_old += o
        total_new += w
        print("  %-40s %6.2f%% -> %6.2f%%" % (label, o / 20000 * 100,
                                              w / 20000 * 100))
print("  " + "-" * 62)
print("  每 tick 固定开销（raw_mv + 状态栏）  %6.2f%% -> %6.2f%%"
      % (total_old / 20000 * 100, total_new / 20000 * 100))
print("  再叠加 BTN_POLL_DIV=%d 的降采样：raw_mv 摊销到 %.1f us/tick"
      % (C.BTN_POLL_DIV, [w for l, o, w in R if "raw_mv" in l][0] / C.BTN_POLL_DIV))
gc.collect()
print("  heap free=%d" % gc.mem_free())
print("=" * 66)
