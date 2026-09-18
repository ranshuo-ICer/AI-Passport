"""骰子 —— PassportOS 小程序示例，演示按键交互与图形绘制。"""

import random
import time

TITLE = "Dice"

BG = 0x0000
FACE = 0xFFFF
PIP = 0x0000
ACCENT = 0xFFE0

# 3x3 点位图：每个点数对应哪些格子亮
LAYOUT = {
    1: ((1, 1),),
    2: ((0, 0), (2, 2)),
    3: ((0, 0), (1, 1), (2, 2)),
    4: ((0, 0), (2, 0), (0, 2), (2, 2)),
    5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
    6: ((0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)),
}


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.text("DICE", 8, 8, ACCENT, BG)
    lcd.fill_rect(0, 20, ctx.w, 1, 0x18E3)

    side = 150
    x0 = (ctx.w - side) // 2
    y0 = 70
    lcd.fill_rect(x0, y0, side, side, FACE)
    lcd.rect(x0 - 2, y0 - 2, side + 4, side + 4, ACCENT)

    step = side // 4
    r = step // 2 - 4
    for gx, gy in LAYOUT[ctx.value]:
        cx = x0 + step * (gx + 1)
        cy = y0 + step * (gy + 1)
        lcd.fill_rect(cx - r, cy - r, r * 2, r * 2, PIP)

    lcd.text_center(str(ctx.value), y0 + side + 14, ACCENT, BG, 2)
    lcd.text("OK roll", 8, ctx.h - 20, 0x8410, BG)
    lcd.text("rolls %d" % ctx.rolls, 140, ctx.h - 20, 0x8410, BG)


def setup(ctx):
    random.seed(time.ticks_ms())
    ctx.value = random.randint(1, 6)
    ctx.rolls = 0
    draw(ctx)


def on_key(ctx, key):
    if key == "ok":
        ctx.value = random.randint(1, 6)
        ctx.rolls += 1
        draw(ctx)
    elif key == "up":
        ctx.value = ctx.value % 6 + 1
        draw(ctx)
    elif key == "down":
        ctx.value = (ctx.value - 2) % 6 + 1
        draw(ctx)
