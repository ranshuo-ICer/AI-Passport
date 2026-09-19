"""PixelPet - a small blob that lives on your Passport.

Inspiration only: the official firmware's ui_pixel.c has a mascot that jumps
when you press a key. The character, the room, the animation and the energy
model here are our own design (the official one is an LVGL object tree).

The pet gets hungry in real time, not in frames, so the energy is stored with a
wall-clock timestamp and decays while the device is off. Feeding it costs
nothing but a key press.

Keys:  UP    cheer it up (it hops)
       DOWN  scold it (it squats and frowns)
       OK    feed it

If the battery drops below 20% it gets sleepy and shows Zzz.
Long-press OK returns to the menu.
"""

import time

TITLE = "PixelPet"

# ------------------------------------------------------------------ palette
BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
SEP = 0x39E7

SKY = 0x0A48
FLOOR = 0x2A28
FLOOR_TOP = 0x3CEA
BODY = 0x35E8
BODY_HI = 0x5F0B
EYE = 0x0861
MOUTH = 0x18C3
CHEEK = 0xFB2C

# ------------------------------------------------------------------ geometry
# The sprite is six stacked bars with a rounded profile. Every bar has a
# different width, which is exactly the pattern fill_rect's colour+width block
# cache is good at once the block height is capped at the rect height.
PROFILE = (3, 5, 6, 6, 5, 3)     # half-width per bar, in PX units
PX = 8
ROWS = len(PROFILE)
CX = 120

BOX_X = 56
BOX_Y = 80
BOX_W = 128
BOX_H = 88                       # 80..168: everything above the floor line
GROUND = 168
FOOT_H = 6

EYE_W = 8
EYE_H = 8
EYE_DX = 16
MOUTH_W = 16
MOUTH_H = 4

# ------------------------------------------------------------------ behaviour
ANIM_DIV = 5                     # 50 Hz loop -> 10 animation frames per second
BOB = (0, -1, -2, -1)            # gentle breathing
BLINK_EVERY = 20                 # in animation frames
DECAY_MS = 60000                 # one energy point per minute
FEED_GAIN = 25
LOW_BATTERY = 20                 # same threshold the official battery page uses

POSE_IDLE = 0
POSE_HAPPY = 1
POSE_SAD = 2
POSE_EAT = 3
POSE_SLEEP = 4


def clock_now():
    """Wall-clock seconds if the RTC has been set, else None."""
    try:
        t = int(time.time())
    except Exception:                                     # noqa: BLE001
        return None
    if t < 1600000000:               # 2020-09-13; anything lower means "unset"
        return None
    return t


# ------------------------------------------------------------------- drawing
def clear_box(ctx):
    ctx.lcd.fill_rect(BOX_X, BOX_Y, BOX_W, BOX_H, SKY)


def draw_body(ctx, top, wscale, hscale):
    """Six stacked bars, bottom bar last so the seam is hidden."""
    lcd = ctx.lcd
    y = top
    for r in range(ROWS):
        half = PROFILE[r] * PX * wscale // 10
        x = CX - half
        lcd.fill_rect(x, y, half * 2, hscale, BODY)
        # a lighter cap on the top bar reads as a highlight
        if r == 0:
            lcd.fill_rect(CX - half // 2, y, half, hscale // 2, BODY_HI)
        y += hscale


def draw_face(ctx, top, pose, blink):
    lcd = ctx.lcd
    ey = top + 14
    if pose == POSE_SAD:
        ey = top + 16
    for sx in (-1, 1):
        ex = CX + sx * EYE_DX - EYE_W // 2
        if pose == POSE_HAPPY:
            # two short bars make a ^ shape
            lcd.fill_rect(ex, ey + 3, EYE_W, 3, EYE)
            lcd.fill_rect(ex + 2, ey, EYE_W - 4, 3, EYE)
        elif blink or pose == POSE_SLEEP:
            lcd.fill_rect(ex, ey + 3, EYE_W, 2, EYE)
        else:
            h = EYE_H - 3 if pose == POSE_SAD else EYE_H
            lcd.fill_rect(ex, ey, EYE_W, h, EYE)
            if pose == POSE_IDLE:
                lcd.fill_rect(ex + 1, ey + 1, 3, 3, INK)

    mx = CX - MOUTH_W // 2
    my = top + 30
    if pose == POSE_HAPPY:
        lcd.fill_rect(mx - 3, my, MOUTH_W + 6, MOUTH_H, MOUTH)
    elif pose == POSE_EAT:
        lcd.fill_rect(mx, my - 2, MOUTH_W, MOUTH_H + 5, MOUTH)
    elif pose == POSE_SAD:
        lcd.fill_rect(mx, my + 2, MOUTH_W, 2, MOUTH)
    else:
        lcd.fill_rect(mx, my, MOUTH_W, MOUTH_H, MOUTH)

    # cheeks, only when it is in a good mood
    if pose in (POSE_IDLE, POSE_HAPPY):
        for sx in (-1, 1):
            lcd.fill_rect(CX + sx * 30 - 4, top + 26, 8, 4, CHEEK)


def draw_feet(ctx, bottom):
    lcd = ctx.lcd
    for sx in (-1, 1):
        lcd.fill_rect(CX + sx * 26 - 10, bottom - FOOT_H, 20, FOOT_H, BODY)


def draw_pet(ctx, pose, phase):
    """Redraw just the sprite box: nothing outside it is touched."""
    clear_box(ctx)

    wscale, hscale = 10, PX
    dy = BOB[phase % len(BOB)]
    blink = (phase % BLINK_EVERY) == 0

    if pose == POSE_HAPPY:
        hop = (0, -16, -12, -5)[phase % 4]
        dy += hop
        wscale, hscale = 8, PX + 2          # stretched while airborne
    elif pose == POSE_SAD:
        wscale, hscale = 12, PX - 2         # squashed
    elif pose == POSE_SLEEP:
        wscale, hscale = 11, PX - 1

    body_bottom = GROUND + dy - FOOT_H
    top = body_bottom - ROWS * hscale

    draw_feet(ctx, GROUND + dy)
    draw_body(ctx, top, wscale, hscale)
    draw_face(ctx, top, pose, blink)

    if pose == POSE_SLEEP:
        ctx.lcd.text("z", 172, top - 6, ACCENT, SKY)
        ctx.lcd.text("z", 182, top - 16, ACCENT, SKY)


def mood_of(ctx):
    if ctx.pose == POSE_SLEEP:
        return "SLEEPY"
    if ctx.energy >= 70:
        return "HAPPY"
    if ctx.energy >= 40:
        return "OK"
    if ctx.energy >= 20:
        return "HUNGRY"
    return "STARVING"


def draw_hud(ctx):
    lcd = ctx.lcd
    lcd.fill_rect(0, 196, ctx.w, 1, SEP)
    lcd.text("ENERGY", 8, 202, DIM, BG)
    lcd.progress(64, 202, 168, 12, ctx.energy, 0x07E0 if ctx.energy >= 40
                 else (0xFFE0 if ctx.energy >= 20 else 0xF800), 0x2104)
    lcd.text("MOOD %-8s" % mood_of(ctx), 8, 224, INK, BG)
    lcd.text("feeds %-3d  %d/100" % (ctx.feeds % 1000, ctx.energy),
             8, 240, DIM, BG)
    if ctx.battery.ok and 0 <= ctx.battery.percent < LOW_BATTERY:
        lcd.text("battery low - pet is sleepy", 8, 256, 0xF800, BG)
    else:
        lcd.text(" ", 8, 256, DIM, BG)
    lcd.text("OK feed  UP cheer", 8, 292, SEP, BG)
    lcd.text("DN scold  long OK back", 8, 304, SEP, BG)


def draw_scene(ctx):
    lcd = ctx.lcd
    lcd.fill_rect(6, 26, 228, 164, SKY)          # 26..190
    lcd.fill_rect(6, GROUND, 228, 22, FLOOR)     # 168..190
    lcd.hline(6, GROUND, 228, FLOOR_TOP)


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("PIXEL PET", 6, 5, ACCENT, BAR)
    draw_scene(ctx)
    draw_pet(ctx, ctx.pose, ctx.phase)
    draw_hud(ctx)


# --------------------------------------------------------------------- hooks
def setup(ctx):
    ctx.energy = int(ctx.kv_get("e", 70))
    if ctx.energy < 0:
        ctx.energy = 0
    elif ctx.energy > 100:
        ctx.energy = 100
    ctx.feeds = int(ctx.kv_get("n", 0))
    ctx.dirty = False
    ctx.phase = 0
    ctx.pose = POSE_IDLE
    ctx.pose_left = 0

    # decay for the time the device was off
    now = clock_now()
    last = ctx.kv_get("t", None)
    if now is not None and isinstance(last, int) and 0 < last <= now:
        lost = (now - last) * 1000 // DECAY_MS
        if lost > 0:
            ctx.energy -= lost
            if ctx.energy < 0:
                ctx.energy = 0
            ctx.dirty = True
            ctx.log("decayed %d while off" % lost)

    if ctx.battery.ok and 0 <= ctx.battery.percent < LOW_BATTERY:
        set_pose(ctx, POSE_SLEEP, 0)
    draw(ctx)
    ctx.log("pet energy=%d feeds=%d" % (ctx.energy, ctx.feeds))


def set_pose(ctx, pose, frames):
    ctx.pose = pose
    ctx.pose_left = frames


def loop(ctx):
    if ctx.frame % ANIM_DIV:
        return
    ctx.phase += 1
    if ctx.pose_left > 0:
        ctx.pose_left -= 1
    else:
        # Decide the resting pose every frame, not only when a timed pose ends.
        # The first version only re-evaluated inside `elif pose != IDLE`, so a
        # pet that was sitting idle when the battery crossed 20% never got
        # sleepy at all.
        low = ctx.battery.ok and 0 <= ctx.battery.percent < LOW_BATTERY
        want = POSE_SLEEP if low else POSE_IDLE
        if ctx.pose != want:
            ctx.pose = want
    draw_pet(ctx, ctx.pose, ctx.phase)
    # the energy bar only needs a refresh when the number changed
    if ctx.phase % 200 == 0:
        draw_hud(ctx)


def on_key(ctx, key):
    if key == "up":
        set_pose(ctx, POSE_HAPPY, 8)
        ctx.energy += 2
        ctx.phase = 0
    elif key == "down":
        set_pose(ctx, POSE_SAD, 10)
        ctx.energy -= 3
        ctx.phase = 0
    elif key == "ok":
        set_pose(ctx, POSE_EAT, 8)
        ctx.energy += FEED_GAIN
        ctx.feeds += 1
        ctx.phase = 0
    if ctx.energy > 100:
        ctx.energy = 100
    elif ctx.energy < 0:
        ctx.energy = 0
    ctx.dirty = True
    draw_pet(ctx, ctx.pose, ctx.phase)
    draw_hud(ctx)


def teardown(ctx):
    if not ctx.dirty:
        return
    ctx.kv_set("e", ctx.energy)
    ctx.kv_set("n", ctx.feeds)
    now = clock_now()
    if now is not None:
        ctx.kv_set("t", now)
    ctx.kv_flush()
