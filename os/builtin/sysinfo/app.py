"""系统信息 —— PassportOS 小程序示例，演示读取内存 / 电量 / 运行时长。"""

import gc
import time

TITLE = "System"

BG = 0x0000
ACCENT = 0x07FF
FG = 0xFFFF
DIM = 0x8410


def setup(ctx):
    ctx.lcd.fill(BG)
    ctx.lcd.text("PASSPORT OS", 8, 6, ACCENT, BG)
    ctx.lcd.fill_rect(0, 18, ctx.w, 1, 0x18E3)
    ctx.rows = []


def loop(ctx):
    # 每 20 帧（约 0.4 秒）刷一次，别把时间耗在刷屏上
    if ctx.frame % 20 != 0:
        return
    lcd = ctx.lcd
    up = time.ticks_ms() // 1000
    lines = (
        ("uptime", "%d:%02d:%02d" % (up // 3600, (up // 60) % 60, up % 60)),
        ("heap", "%d K free" % (gc.mem_free() // 1024)),
        ("battery", ctx.battery.label()),
        ("ble", "connected" if ctx.shell.link.connected else "advertising"),
        ("frame", str(ctx.frame)),
    )
    y = 30
    for name, val in lines:
        lcd.fill_rect(0, y, ctx.w, 22, BG)
        lcd.text(name, 8, y + 6, DIM, BG)
        val = str(val)
        lcd.text(val, ctx.w - 8 * len(val) - 8, y + 6, FG, BG)
        y += 24
    lcd.text("long OK: back", 8, ctx.h - 18, 0x18E3, BG)


def on_key(ctx, key):
    if key == "ok":
        gc.collect()
