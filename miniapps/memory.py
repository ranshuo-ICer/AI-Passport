"""Memory - repeat the sequence, it grows every round.

The device lights a bar and plays a tone, one at a time. Repeat it back:
    UP   = left bar    OK = middle bar    DOWN = right bar
One mistake ends the run. Best level is kept across power loss.

This is the one app that uses both output channels at once (screen + speaker),
which is why it exists at all on a three-button board.

ASCII-only and small on purpose: see docs/KNOWN_ISSUES.md #1 - a BLE upload
over 10s gets the phone heartbeat injected into app.py and loses its tail, and
a cut inside a UTF-8 char turns into UnicodeError / "LOAD FAILED".
"""

import random
import time

TITLE = "Memory"

BG = 0x0000
BAR = 0x18E3
SEP = 0x39E7
ACC = 0x07FF
GRN = 0x07E0
RED = 0xF800
DIM = 0x8410
WHT = 0xFFFF

BAR_X = 20
BAR_W = 60
BAR_GAP = 10
BAR_Y = 70
BAR_H = 130

ON_COL = (0x07FF, 0xFD20, 0xF81F)      # cyan, amber, magenta
TONE = (660, 880, 1320)
KEYMAP = {"up": 0, "ok": 1, "down": 2}

SHOW_MS = 430                           # gap between sequence items
LIT_MS = 260                            # how long a bar stays lit


def _bar(ctx, i):
    l = ctx.lcd
    x = BAR_X + i * (BAR_W + BAR_GAP)
    lit = (ctx.lit == i)
    on = ON_COL[i]
    l.fill_rect(x, BAR_Y, BAR_W, BAR_H, on if lit else (on >> 1))
    l.rect(x, BAR_Y, BAR_W, BAR_H, WHT if lit else SEP)


def _bars(ctx):
    for i in range(3):
        _bar(ctx, i)


def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, ctx.w, 21, BAR)
    l.text2x("MEMORY", 8, 3, ACC, BAR)
    l.fill_rect(0, 21, ctx.w, 1, SEP)
    l.fill_rect(0, ctx.h - 20, ctx.w, 20, BAR)
    l.text("UP / OK / DN", 6, ctx.h - 15, DIM, BAR)
    l.text("repeat it", ctx.w - 9 * 8 - 6, ctx.h - 15, DIM, BAR)


def _hud(ctx):
    l = ctx.lcd
    l.fill_rect(0, 26, ctx.w, 14, BG)
    l.text("level %d" % ctx.level, 6, 26, WHT, BG)
    b = "best %d" % ctx.best
    l.text(b, ctx.w - 8 * len(b) - 6, 26, DIM, BG)
    l.fill_rect(0, 214, ctx.w, 14, BG)
    l.text_center(ctx.msg[:29], 214, ACC, BG, 1)


def _tone(ctx, i):
    a = ctx.audio
    if a is None or not a.ok:
        return
    try:
        a.tone(TONE[i], LIT_MS)
    except Exception:
        pass


def _light(ctx, i):
    ctx.lit = i
    _bar(ctx, i)
    _tone(ctx, i)


def _dark(ctx):
    if ctx.lit >= 0:
        i = ctx.lit
        ctx.lit = -1
        _bar(ctx, i)


def _next_round(ctx):
    ctx.seq.append(random.randint(0, 2))
    ctx.state = "show"
    ctx.si = 0
    ctx.at = time.ticks_ms()
    ctx.msg = "watch"


def _start(ctx):
    ctx.seq = []
    ctx.level = 1
    ctx.ii = 0
    ctx.lit = -1
    ctx.msg = "watch"
    _next_round(ctx)


def setup(ctx):
    ctx.best = int(ctx.kv_get("best", 0))
    ctx.lit = -1
    ctx.seq = []
    ctx.level = 1
    ctx.ii = 0
    ctx.si = 0
    ctx.at = 0
    ctx.msg = "watch"
    _frame(ctx)
    _start(ctx)
    _bars(ctx)
    _hud(ctx)


def _gameover(ctx):
    ctx.state = "over"
    _dark(ctx)
    if ctx.level - 1 > ctx.best:
        ctx.best = ctx.level - 1
        ctx.kv_set("best", ctx.best)
        ctx.kv_flush()
    ctx.msg = "wrong!  OK to retry"
    _hud(ctx)
    l = ctx.lcd
    l.fill_rect(30, 240, ctx.w - 60, 40, BAR)
    l.rect(30, 240, ctx.w - 60, 40, RED)
    l.text_center("GAME OVER", 248, WHT, BAR, 1)
    l.text_center("reached %d" % (ctx.level - 1), 264, ACC, BAR, 1)


def on_key(ctx, key):
    if ctx.state == "over":
        if key == "ok":
            _frame(ctx)
            _start(ctx)
            _bars(ctx)
            _hud(ctx)
        return
    if ctx.state != "input":
        return
    i = KEYMAP.get(key)
    if i is None:
        return
    _light(ctx, i)
    ctx.lit_at = time.ticks_ms()
    if i == ctx.seq[ctx.ii]:
        ctx.ii += 1
        if ctx.ii >= len(ctx.seq):
            ctx.level += 1
            ctx.ii = 0
            _next_round(ctx)
            _hud(ctx)
    else:
        _gameover(ctx)


def loop(ctx):
    now = time.ticks_ms()

    # keep a lit bar lit for LIT_MS even during input
    if ctx.lit >= 0 and ctx.state != "show":
        if time.ticks_diff(now, ctx.lit_at) >= LIT_MS:
            _dark(ctx)

    if ctx.state != "show":
        return

    if ctx.lit >= 0:
        if time.ticks_diff(now, ctx.lit_at) >= LIT_MS:
            _dark(ctx)
        return

    if time.ticks_diff(now, ctx.at) < 0:
        return

    if ctx.si >= len(ctx.seq):
        ctx.state = "input"
        ctx.ii = 0
        ctx.msg = "your turn"
        _hud(ctx)
        return

    _light(ctx, ctx.seq[ctx.si])
    ctx.lit_at = now
    ctx.si += 1
    ctx.at = time.ticks_add(now, SHOW_MS)


def teardown(ctx):
    ctx.kv_set("best", ctx.best)
    ctx.kv_flush()
