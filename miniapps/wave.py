"""WaveLab - build a waveform and hear it, see its shape.

The OS audio helper only exposes a sine `tone()`. This app goes one level lower
and synthesises PCM itself, so you can switch between SINE / SQUARE / TRIANGLE /
SAW and watch the shape change while you hear the pitch move.

Reference for the capability: the official firmware's `main/demo_audio.c` plays a
1 kHz square wave. The UI here is our own (the official one is LVGL-based).

Keys:  UP / DOWN  pitch down / up (plays a short blip)
       OK         next waveform (plays it in full)

Both settings survive a power cycle. Long-press OK returns to the menu (system).
"""

import array
import math

TITLE = "WaveLab"

# ---------------------------------------------------------------- appearance
BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
ACCENT = 0x07FF
DIM = 0x8410
PLOT_BG = 0x0841
AXIS = 0x39E7
KEY_ON = 0xFD20
HINT = 0x39E7

# ------------------------------------------------------------------- the DSP
# One period is stored as a 256-entry signed table. 256 is a power of two on
# purpose: the playback loop keeps the phase as 16.16 fixed point and wraps it
# with a mask instead of a modulo, which keeps every number a small int.
TBL_N = 256
TBL_MASK = (TBL_N << 16) - 1
PEAK = 7000                  # headroom: a full-scale square at 32767 would clip

WAVES = ("SINE", "SQUARE", "TRIANGLE", "SAW")

# A ladder rather than every semitone: with two buttons, "every step is audible"
# beats "every step is a semitone". Low notes first.
STEPS = (
    ("A2", 110), ("C3", 131), ("D3", 147), ("E3", 165), ("G3", 196),
    ("A3", 220), ("C4", 262), ("D4", 294), ("E4", 330), ("G4", 392),
    ("A4", 440), ("C5", 523), ("D5", 587), ("E5", 659), ("G5", 784),
    ("A5", 880), ("C6", 1047), ("D6", 1175), ("E6", 1319), ("G6", 1568),
)
DEFAULT_STEP = 10            # A4 440 Hz


def make_table(kind):
    """One period of `kind` as signed 16-bit samples."""
    tbl = array.array("h", bytes(TBL_N * 2))
    for i in range(TBL_N):
        x = i / TBL_N
        if kind == 0:                                  # sine
            v = math.sin(2.0 * math.pi * x)
        elif kind == 1:                                # square
            v = 1.0 if x < 0.5 else -1.0
        elif kind == 2:                                # triangle
            v = (4.0 * x - 1.0) if x < 0.5 else (3.0 - 4.0 * x)
        else:                                          # saw
            v = 2.0 * x - 1.0
        tbl[i] = int(v * PEAK)
    return tbl


def synth(ctx, hz, ms):
    """Render `ms` of `hz` into an array('h'). Returns None if audio is unusable."""
    audio = ctx.audio
    if audio is None or not audio.ok:
        return None
    rate = audio.rate
    total = rate * ms // 1000
    buf = array.array("h", bytes(total * 2))
    tbl = ctx.tbl
    step = (hz * TBL_N << 16) // rate
    fade = rate // 400                       # ~2.5 ms, kills the click at both ends
    if fade < 8:
        fade = 8
    pos = 0
    tail = total - fade
    for i in range(total):
        s = tbl[pos >> 16]
        if i < fade:
            s = s * i // fade
        elif i >= tail:
            s = s * (total - i) // fade
        buf[i] = s
        pos = (pos + step) & TBL_MASK
    return buf


def play(ctx, hz, ms):
    buf = synth(ctx, hz, ms)
    if buf is None:
        return False
    try:
        ctx.audio.play_raw(buf)
    except Exception as exc:                              # noqa: BLE001
        ctx.log("play_raw failed: %s" % exc)
        return False
    return True


# ------------------------------------------------------------------- drawing
PLOT_X = 8
PLOT_Y = 76
PLOT_W = 224
PLOT_H = 80
COLS = 56                    # 4 px per column: 56 fill_rects per redraw, not 224
COL_W = PLOT_W // COLS


def draw_plot(ctx):
    """Draw two periods of the current wave. Cheap enough to do on every key."""
    lcd = ctx.lcd
    lcd.fill_rect(PLOT_X, PLOT_Y, PLOT_W, PLOT_H, PLOT_BG)
    mid = PLOT_Y + PLOT_H // 2
    lcd.hline(PLOT_X, mid, PLOT_W, AXIS)

    amp = PLOT_H // 2 - 4
    tbl = ctx.tbl
    prev = mid
    for c in range(COLS):
        # two periods across the plot width
        idx = (c * 2 * TBL_N // COLS) % TBL_N
        y = mid - (tbl[idx] * amp) // PEAK
        lo = y if y < prev else prev
        hi = prev if y < prev else y
        lcd.fill_rect(PLOT_X + c * COL_W, lo, COL_W, hi - lo + 1, ACCENT)
        prev = y


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("WAVELAB", 6, 5, ACCENT, BAR)

    name = WAVES[ctx.wf]
    lcd.text_scale(name, 8, 26, INK, BG, 3)

    label, hz = STEPS[ctx.step]
    lcd.text("%d Hz" % hz, 8, 56, ACCENT, BG)
    lcd.text(label, 96, 56, DIM, BG)

    draw_plot(ctx)

    if ctx.audio is None or not ctx.audio.ok:
        lcd.text("audio unavailable", 8, 168, 0xF800, BG)
    else:
        lcd.text("peak %d  fade 2.5ms" % PEAK, 8, 164, DIM, BG)

    # waveform selector: four boxes, the active one lit
    bx = 8
    for i in range(4):
        sel = (i == ctx.wf)
        lcd.fill_rect(bx, 188, 54, 22, KEY_ON if sel else BAR)
        lcd.text(WAVES[i][:6], bx + 3, 195, BG if sel else HINT,
                 KEY_ON if sel else BAR)
        bx += 58

    lcd.text("BAT %s" % ctx.battery.label(), 8, 274, DIM, BG)
    lcd.text("OK wave  UP/DN pitch", 8, 292, HINT, BG)
    lcd.text("long OK = back", 8, 306, BAR, BG)


# -------------------------------------------------------------------- hooks
def setup(ctx):
    ctx.wf = int(ctx.kv_get("w", 0)) % len(WAVES)
    ctx.step = int(ctx.kv_get("f", DEFAULT_STEP)) % len(STEPS)
    ctx.dirty = False
    ctx.tbl = make_table(ctx.wf)
    if ctx.audio and ctx.audio.ok:
        ctx.audio.set_volume(70)
    ctx.log("wavelab %s @%dHz" % (WAVES[ctx.wf], STEPS[ctx.step][1]))
    draw(ctx)


def on_key(ctx, key):
    if key == "up":
        ctx.step = (ctx.step + 1) % len(STEPS)
        ctx.dirty = True
        play(ctx, STEPS[ctx.step][1], 150)
    elif key == "down":
        ctx.step = (ctx.step - 1) % len(STEPS)
        ctx.dirty = True
        play(ctx, STEPS[ctx.step][1], 150)
    elif key == "ok":
        ctx.wf = (ctx.wf + 1) % len(WAVES)
        ctx.tbl = make_table(ctx.wf)
        ctx.dirty = True
        draw(ctx)
        play(ctx, STEPS[ctx.step][1], 320)
        return
    draw(ctx)


def teardown(ctx):
    if ctx.dirty:
        ctx.kv_set("w", ctx.wf)
        ctx.kv_set("f", ctx.step)
        ctx.kv_flush()
