"""系统信息 + 背光调节 —— PassportOS 小程序示例。

上半屏是只读信息（运行时长 / 堆 / 电量 / 蓝牙 / 帧号），下半屏是背光亮度。
UP/DOWN 调亮度并**立刻**生效，OK 手动回收一次内存（示例：按了才 gc）。

背光会写进全局设置 `/settings.json`，所以关掉再开还是你调的那个值 ——
它是硬件状态，不属于任何一个小程序（见 passport/settings.py 的说明）。
"""

import gc
import time

from passport import config as C
from passport import settings

TITLE = "System"

BG = 0x0000
ACCENT = 0x07FF
FG = 0xFFFF
DIM = 0x8410
SEP = 0x18E3
TRACK = 0x2104

STEP = 10                   # 每次按调整多少个百分点

INFO_Y = 26                 # 信息区第一行的 y
INFO_H = 22
INFO_GAP = 24

BL_LABEL_Y = 158
BL_BAR_Y = 176
BL_BAR_H = 12


def draw_brightness(ctx):
    """只重画背光这一块 —— 调亮度时会连续按，别整屏重画。"""
    lcd = ctx.lcd
    lcd.fill_rect(0, BL_LABEL_Y, ctx.w, 40, BG)
    lcd.text("brightness", 8, BL_LABEL_Y, DIM, BG)
    val = "%3d%%" % ctx.bl
    lcd.text(val, ctx.w - 8 * len(val) - 8, BL_LABEL_Y, ACCENT, BG)
    lcd.progress(8, BL_BAR_Y, ctx.w - 16, BL_BAR_H, ctx.bl, ACCENT, TRACK)
    # 下限画一根竖线：让人看得出"最暗只能到这里"，省得一路按到底
    floor_x = 8 + (C.LCD_BL_MIN * (ctx.w - 16)) // 100
    lcd.vline(floor_x, BL_BAR_Y - 3, BL_BAR_H + 6, SEP)


def draw_info(ctx):
    lcd = ctx.lcd
    up = time.ticks_ms() // 1000
    lines = (
        ("uptime", "%d:%02d:%02d" % (up // 3600, (up // 60) % 60, up % 60)),
        ("heap", "%d K free" % (gc.mem_free() // 1024)),
        ("battery", ctx.battery.label()),
        ("ble", "connected" if ctx.shell.link.connected else "advertising"),
        ("frame", str(ctx.frame)),
    )
    y = INFO_Y
    for name, val in lines:
        lcd.fill_rect(0, y, ctx.w, INFO_H, BG)
        lcd.text(name, 8, y + 6, DIM, BG)
        val = str(val)
        lcd.text(val, ctx.w - 8 * len(val) - 8, y + 6, FG, BG)
        y += INFO_GAP


def draw_chrome(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.text("PASSPORT OS", 8, 6, ACCENT, BG)
    lcd.fill_rect(0, 18, ctx.w, 1, SEP)
    lcd.fill_rect(0, 150, ctx.w, 1, SEP)
    lcd.text("UP/DN brightness", 8, ctx.h - 34, SEP, BG)
    lcd.text("OK: gc   long OK: back", 8, ctx.h - 18, SEP, BG)


def draw_duty(ctx):
    """背光的实际 PWM 占空比 + 落盘位置。

    看着像凑数的，其实是这个页面的重点：亮度百分比只是输入，真正决定屏幕多亮
    的是 LEDC 的 duty；而且它写在哪、重启后还在不在，是排查"我调了怎么又回去了"
    的第一现场。
    """
    lcd = ctx.lcd
    lcd.fill_rect(0, 196, ctx.w, 40, BG)
    lcd.text("pwm duty", 8, 196, DIM, BG)
    val = "%d/1023" % (ctx.bl * 1023 // 100)
    lcd.text(val, ctx.w - 8 * len(val) - 8, 196, FG, BG)
    lcd.text("stored in /settings.json", 8, 214, DIM, BG)


def apply(ctx, bl):
    """夹到合法范围、写进全局设置、立刻改屏幕。"""
    if bl < C.LCD_BL_MIN:
        bl = C.LCD_BL_MIN
    elif bl > 100:
        bl = 100
    if bl == ctx.bl:
        return False
    ctx.bl = bl
    ctx.lcd.backlight(bl)           # 立刻生效，画完条之前就能看到
    settings.set("bl", bl)
    settings.flush()
    draw_brightness(ctx)
    draw_duty(ctx)
    return True


def setup(ctx):
    # 开机时 Shell 已经按这个值设过背光了，这里读回来只是为了显示一致
    try:
        bl = int(settings.get("bl", C.LCD_BL_DEFAULT))
    except (TypeError, ValueError):
        bl = C.LCD_BL_DEFAULT
    if bl < C.LCD_BL_MIN:
        bl = C.LCD_BL_MIN
    elif bl > 100:
        bl = 100
    ctx.bl = bl
    ctx.lcd.backlight(bl)
    draw_chrome(ctx)
    draw_info(ctx)
    draw_brightness(ctx)
    draw_duty(ctx)


def loop(ctx):
    # 每 20 帧（约 0.4 秒）刷一次，别把时间耗在刷屏上。
    # 只刷信息区：背光那块由按键驱动，没必要跟着刷。
    if ctx.frame % 20 != 0:
        return
    draw_info(ctx)


def on_key(ctx, key):
    if key == "up":
        apply(ctx, ctx.bl + STEP)
    elif key == "down":
        apply(ctx, ctx.bl - STEP)
    elif key == "ok":
        gc.collect()
