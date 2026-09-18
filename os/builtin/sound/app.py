"""发声玩具 —— PassportOS 音频示例。

UP/DOWN 换音，OK 播放；长按 OK 退回主菜单（系统行为）。
同时演示 ctx.audio 的 tone() 和 melody() 两个接口。
"""

TITLE = "Sound"

BG = 0x0000
ACCENT = 0x07FF
FG = 0xFFFF
DIM = 0x8410

# 五声音阶，随便按都好听
SCALE = ("C4", "D4", "E4", "G4", "A4", "C5", "D5", "E5", "G5", "A5")

MELODY = (("C5", 0.5), ("E5", 0.5), ("G5", 0.5), ("C6", 1.0),
          ("REST", 0.25), ("G5", 0.5), ("E5", 0.5), ("C5", 1.0))


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.text("SOUND", 8, 8, ACCENT, BG)
    lcd.fill_rect(0, 20, ctx.w, 1, 0x18E3)

    if ctx.audio is None or not ctx.audio.ok:
        lcd.text("audio not", 12, 70, 0xF800, BG)
        lcd.text("available", 12, 86, 0xF800, BG)
        lcd.text(ctx.audio.error[:28] if ctx.audio else "no driver",
                 8, 110, DIM, BG)
        lcd.text("long OK: back", 8, ctx.h - 18, 0x18E3, BG)
        return

    note = SCALE[ctx.idx]
    lcd.text_center(note, 60, FG, BG, 4)

    # 十格音阶指示
    total = len(SCALE)
    y = 150
    for i in range(total):
        on = (i == ctx.idx)
        x = 12 + i * 22
        h = 20 + i * 6
        color = ACCENT if on else (0x18E3 if i < ctx.idx else 0x39E7)
        lcd.fill_rect(x, y + (80 - h), 16, h, color)

    lcd.text("BAT %s" % ctx.battery.label(), 8, ctx.h - 34, DIM, BG)
    lcd.text("OK play  DN mel", 8, ctx.h - 18, 0x18E3, BG)


def setup(ctx):
    ctx.idx = 4
    ctx.melody_mode = False
    if ctx.audio and ctx.audio.ok:
        ctx.audio.set_volume(75)
    draw(ctx)


def on_key(ctx, key):
    if ctx.audio is None or not ctx.audio.ok:
        return
    if key == "up":
        ctx.idx = (ctx.idx + 1) % len(SCALE)
        ctx.melody_mode = False
        ctx.audio.tone(SCALE_FREQ(ctx.idx), 160)
        draw(ctx)
    elif key == "down":
        ctx.idx = (ctx.idx - 1) % len(SCALE)
        ctx.melody_mode = False
        ctx.audio.tone(SCALE_FREQ(ctx.idx), 160)
        draw(ctx)
    elif key == "ok":
        if ctx.melody_mode:
            ctx.audio.tone(SCALE_FREQ(ctx.idx), 160)
        else:
            ctx.melody_mode = True
            lcd = ctx.lcd
            lcd.fill_rect(0, 40, ctx.w, 100, BG)
            lcd.text_center("MELODY", 70, 0xFFE0, BG, 2)
            ctx.audio.melody(MELODY, bpm=180)
            ctx.melody_mode = False
            draw(ctx)


def SCALE_FREQ(i):
    from passport.audio import NOTES
    return NOTES[SCALE[i]]
