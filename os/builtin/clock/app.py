"""数字时钟 —— PassportOS 小程序示例。

如果 RTC 还没被手机对过时，会自动退化成"开机计时"模式。
在手机 App 里点一下「对时」就能显示真实时间。
"""

import time

TITLE = "Clock"

BG = 0x0000
ACCENT = 0x07FF
FG = 0xFFFF
DIM = 0x8410


def setup(ctx):
    ctx.lcd.fill(BG)
    ctx.lcd.text("CLOCK", 8, 8, ACCENT, BG)
    ctx.lcd.fill_rect(0, 20, ctx.w, 1, 0x18E3)
    ctx.last = None
    ctx.hint_shown = False


def loop(ctx):
    t = time.localtime()
    if t[0] < 2020:
        # 没有真实时间：用开机毫秒数当秒表
        secs = time.ticks_ms() // 1000
        text = "%02d:%02d:%02d" % (secs // 3600, (secs // 60) % 60, secs % 60)
        if not ctx.hint_shown:
            ctx.lcd.text("RTC not set", 8, 30, 0xF800, BG)
            ctx.lcd.text("tap Sync in app", 8, 44, DIM, BG)
            ctx.hint_shown = True
    else:
        text = "%02d:%02d:%02d" % (t[3], t[4], t[5])
        if ctx.hint_shown:
            ctx.lcd.fill_rect(0, 26, ctx.w, 24, BG)
            ctx.hint_shown = False

    if text != ctx.last:
        ctx.last = text
        ctx.lcd.text_center(text, 130, FG, BG, 3)

    # 日期 / 状态，每秒刷一次就够
    if ctx.frame % 20 == 0:
        if t[0] >= 2020:
            ctx.lcd.text_center("%04d-%02d-%02d" % (t[0], t[1], t[2]),
                                190, DIM, BG, 1)
        ctx.lcd.text("BAT %s" % ctx.battery.label(), 8, ctx.h - 20, DIM, BG)
        ctx.lcd.text("long OK: back", 120, ctx.h - 20, 0x18E3, BG)
