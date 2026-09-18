"""Reaction - how fast are you?

OK    start a round / after a result, go again
      press as soon as the screen turns GREEN
      press too early and the round is void

Best time is kept across power loss. Whole-screen colour is the stimulus, so
it is readable out of the corner of your eye.

ASCII-only and small on purpose: see docs/KNOWN_ISSUES.md #1 - a BLE upload
over 10s gets the phone heartbeat injected into app.py and loses its tail, and
a cut inside a UTF-8 char turns into UnicodeError / "LOAD FAILED".
"""

import random
import time

TITLE = "Reaction"

NAVY = 0x0010
DARK = 0x18E3
RED = 0xF800
DRED = 0x7800
GRN = 0x07E0
AMB = 0xFD20
CYAN = 0x07FF
DIM = 0x8410
WHT = 0xFFFF

WAIT_MIN = 900
WAIT_MAX = 2600

BIG_Y = 118


def _paint(ctx, bg, big, sub, bigcol=WHT):
    l = ctx.lcd
    l.fill(bg)
    l.fill_rect(0, 0, ctx.w, 21, DARK)
    l.text2x("REACTION", 8, 3, CYAN, DARK)
    l.fill_rect(0, 21, ctx.w, 1, DIM)
    l.text_center(big[:8], BIG_Y, bigcol, bg, 4)
    if sub:
        l.text_center(sub[:29], BIG_Y + 44, WHT, bg, 1)
    best = "best %d ms" % ctx.best if ctx.best else "best --"
    l.fill_rect(0, ctx.h - 20, ctx.w, 20, DARK)
    l.text(best, 6, ctx.h - 15, DIM, DARK)
    l.text("OK = go", ctx.w - 8 * 8 - 6, ctx.h - 15, DIM, DARK)


def _beep(ctx, f, ms):
    a = ctx.audio
    if a is None or not a.ok:
        return
    try:
        a.tone(f, ms)
    except Exception:
        pass


def setup(ctx):
    ctx.state = "idle"
    ctx.best = int(ctx.kv_get("best", 0))
    ctx.t_go = 0
    ctx.wait = 0
    ctx.result = 0
    _paint(ctx, NAVY, "READY", "press OK when you are set")


def _arm(ctx):
    ctx.state = "wait"
    ctx.wait = random.randint(WAIT_MIN, WAIT_MAX)
    ctx.t0 = time.ticks_ms()
    _paint(ctx, DRED, "WAIT", "not yet ...", AMB)


def _go(ctx):
    ctx.state = "go"
    ctx.t_go = time.ticks_ms()
    _paint(ctx, GRN, "GO!", "press OK NOW", 0x0000)
    _beep(ctx, 1600, 40)


def _result(ctx, early):
    ctx.state = "result"
    if early:
        _paint(ctx, RED, "EARLY", "too soon - OK to retry", WHT)
        _beep(ctx, 300, 150)
        return
    ms = ctx.result
    mark = ""
    if not ctx.best or ms < ctx.best:
        ctx.best = ms
        ctx.kv_set("best", ms)
        ctx.kv_flush()
        mark = "  NEW BEST"
    _paint(ctx, NAVY, "%d ms" % ms, "OK to go again" + mark)
    _beep(ctx, 1200 if ms < 300 else 700, 60)


def on_key(ctx, key):
    if key != "ok":
        return
    if ctx.state in ("idle", "result"):
        _arm(ctx)
    elif ctx.state == "wait":
        _result(ctx, True)
    elif ctx.state == "go":
        ctx.result = time.ticks_diff(time.ticks_ms(), ctx.t_go)
        if ctx.result < 1:
            ctx.result = 1
        _result(ctx, False)


def loop(ctx):
    if ctx.state == "wait" and time.ticks_diff(time.ticks_ms(), ctx.t0) >= ctx.wait:
        _go(ctx)


def teardown(ctx):
    ctx.kv_set("best", ctx.best)
    ctx.kv_flush()
