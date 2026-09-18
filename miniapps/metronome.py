"""Metronome - 4/4, 40..240 BPM, accented downbeat.

UP / DOWN   tempo -/+ (works while running, so you can dial it in by ear)
OK          start / stop

Fits the board's original identity: the factory firmware calls itself
"audio mode = score playback", and this is the tool you would use next to it.

ASCII-only and small on purpose: see docs/KNOWN_ISSUES.md #1 - a BLE upload
over 10s gets the phone heartbeat injected into app.py and loses its tail, and
a cut inside a UTF-8 char turns into UnicodeError / "LOAD FAILED".
"""

import time

TITLE = "Metronome"

BG = 0x0000
BAR = 0x18E3
SEP = 0x39E7
ACC = 0x07FF
GRN = 0x07E0
RED = 0xF800
DIM = 0x8410
WHT = 0xFFFF

BPM_MIN = 40
BPM_MAX = 240
BPM_STEP = 2

TY = 56
DOT_Y = 168
DOT_W = 28
DOT_GAP = 20
BEATS = 4


def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, ctx.w, 21, BAR)
    l.text2x("METRONOME", 8, 3, ACC, BAR)
    l.fill_rect(0, 21, ctx.w, 1, SEP)
    l.text_center("TEMPO", 34, DIM, BG, 1)
    l.fill_rect(0, ctx.h - 20, ctx.w, 20, BAR)
    l.text("OK run", 6, ctx.h - 15, DIM, BAR)
    l.text("UP/DN tempo", ctx.w - 11 * 8 - 6, ctx.h - 15, DIM, BAR)


def _tempo(ctx):
    l = ctx.lcd
    l.fill_rect(0, TY, ctx.w, 46, BG)
    l.text_center("%d" % ctx.bpm, TY + 4, WHT, BG, 5)
    l.fill_rect(0, TY + 50, ctx.w, 14, BG)
    l.text_center("BPM", TY + 50, DIM, BG, 1)


def _dots(ctx, clear_only=False):
    l = ctx.lcd
    l.fill_rect(0, DOT_Y, ctx.w, DOT_W + 8, BG)
    if clear_only:
        return
    total = BEATS * DOT_W + (BEATS - 1) * DOT_GAP
    x = (ctx.w - total) // 2
    for i in range(BEATS):
        on = (i == ctx.beat) and ctx.run
        col = (RED if i == 0 else GRN) if on else SEP
        l.fill_rect(x, DOT_Y + 4, DOT_W, DOT_W, col)
        if not on:
            l.rect(x, DOT_Y + 4, DOT_W, DOT_W, DIM)
        x += DOT_W + DOT_GAP


def _hint(ctx, txt):
    l = ctx.lcd
    l.fill_rect(0, 250, ctx.w, 14, BG)
    l.text_center(txt[:29], 250, DIM, BG, 1)


def _click(ctx, accent):
    a = ctx.audio
    if a is None or not a.ok:
        return
    try:
        a.tone(1600 if accent else 1050, 28)
    except Exception:
        pass


def _period(ctx):
    return 60000 // ctx.bpm


def _beat(ctx):
    ctx.beat = (ctx.beat + 1) % BEATS
    _dots(ctx)
    _click(ctx, ctx.beat == 0)
    p = _period(ctx)
    ctx.t_next = time.ticks_add(ctx.t_next, p)
    # if we fell badly behind (slow loop), re-anchor instead of catching up
    if time.ticks_diff(time.ticks_ms(), ctx.t_next) > p:
        ctx.t_next = time.ticks_add(time.ticks_ms(), p)


def setup(ctx):
    ctx.bpm = int(ctx.kv_get("bpm", 100))
    if ctx.bpm < BPM_MIN or ctx.bpm > BPM_MAX:
        ctx.bpm = 100
    ctx.beat = -1
    ctx.run = False
    ctx.t_next = 0
    _frame(ctx)
    _tempo(ctx)
    _dots(ctx)
    _hint(ctx, "OK to start")


def _toggle(ctx):
    ctx.run = not ctx.run
    if ctx.run:
        ctx.beat = -1
        ctx.t_next = time.ticks_ms()
        _hint(ctx, "running")
    else:
        _dots(ctx, clear_only=True)
        _hint(ctx, "OK to start")


def on_key(ctx, key):
    if key == "ok":
        _toggle(ctx)
        return
    if key == "up":
        ctx.bpm = min(BPM_MAX, ctx.bpm + BPM_STEP)
    elif key == "down":
        ctx.bpm = max(BPM_MIN, ctx.bpm - BPM_STEP)
    else:
        return
    _tempo(ctx)
    if ctx.run:
        ctx.t_next = time.ticks_add(time.ticks_ms(), _period(ctx))


def loop(ctx):
    if not ctx.run:
        return
    if time.ticks_diff(time.ticks_ms(), ctx.t_next) >= 0:
        _beat(ctx)


def teardown(ctx):
    ctx.kv_set("bpm", ctx.bpm)
    ctx.kv_flush()
