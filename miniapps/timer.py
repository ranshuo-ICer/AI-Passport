"""Timer - stopwatch + countdown in one app.

UP    stopped: stopwatch -> reset to 0 / countdown -> +10s
DOWN  stopped: switch between STOPWATCH and COUNTDOWN
OK    start / stop  (countdown beeps when it reaches zero)

ASCII-only and small on purpose: see docs/KNOWN_ISSUES.md #1 - a BLE upload
over 10s gets the phone heartbeat injected into app.py and loses its tail, and
a cut inside a UTF-8 char turns into UnicodeError / "LOAD FAILED".
"""

import time

TITLE = "Timer"

BG = 0x0000
BAR = 0x18E3
SEP = 0x39E7
ACC = 0x07FF
GRN = 0x07E0
RED = 0xF800
AMB = 0xFE60
DIM = 0x8410
WHT = 0xFFFF

SY = 26
PY = 52
TY = 104
SUB_Y = 152
HI_Y = 196

STEP = 10000            # countdown adjust step (ms)
CD_MIN = 10000
CD_MAX = 600000


def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, ctx.w, 21, BAR)
    l.text2x("TIMER", 8, 3, ACC, BAR)
    l.fill_rect(0, 21, ctx.w, 1, SEP)
    l.fill_rect(0, ctx.h - 20, ctx.w, 20, BAR)
    l.text("OK run", 6, ctx.h - 15, DIM, BAR)
    l.text("UP adj  DN mode", ctx.w - 15 * 8 - 6, ctx.h - 15, DIM, BAR)


def _status(ctx):
    l = ctx.lcd
    l.fill_rect(0, SY, ctx.w, 14, BG)
    l.text(ctx.mode, 6, SY + 3, ACC if ctx.mode == "CD" else DIM, BG)
    run = "RUN" if ctx.run else "STOP"
    l.text(run, 60, SY + 3, GRN if ctx.run else DIM, BG)
    if ctx.mode == "CD":
        info = "set %ds" % (ctx.cd / 1000)
    else:
        info = "best %s" % _fmt(ctx.best)
    l.text(info, ctx.w - 8 * len(info) - 6, SY + 3, DIM, BG)


def _bar(ctx):
    l = ctx.lcd
    if ctx.mode == "CD":
        done = ctx.cd - _left(ctx)
        pct = done * 100 // ctx.cd if ctx.cd else 0
        col = RED if pct > 80 else GRN
        l.progress(16, PY, 208, 14, pct, col, BG)
    else:
        l.fill_rect(16, PY, 208, 14, BG)
        l.rect(16, PY, 208, 14, SEP)


def _fmt(ms):
    if ms < 0:
        ms = 0
    s = ms // 1000
    return "%02d:%02d" % (s // 60, s % 60)


def _left(ctx):
    """Milliseconds shown right now (elapsed for SW, remaining for CD)."""
    if ctx.mode == "CD":
        return ctx.cd - _elapsed(ctx)
    return _elapsed(ctx)


def _elapsed(ctx):
    if ctx.run:
        return ctx.base + time.ticks_diff(time.ticks_ms(), ctx.t0)
    return ctx.base


def _draw_time(ctx):
    l = ctx.lcd
    ms = _left(ctx)
    if ms < 0:
        ms = 0
    l.fill_rect(0, TY, ctx.w, 46, BG)
    l.text_center(_fmt(ms), TY + 6, WHT, BG, 4)
    l.fill_rect(0, SUB_Y, ctx.w, 12, BG)
    l.text_center(".%02d" % ((ms % 1000) // 10), SUB_Y, ACC, BG, 1)


def _hint(ctx, main):
    l = ctx.lcd
    l.fill_rect(0, HI_Y, ctx.w, 16, BG)
    l.text_center(main[:29], HI_Y, DIM, BG, 1)


def _beep(ctx):
    a = ctx.audio
    if a is None or not a.ok:
        return
    try:
        for _ in range(2):
            a.tone(1400, 110)
            a.tone(950, 130)
    except Exception:
        pass


def setup(ctx):
    ctx.mode = ctx.kv_get("mode", "SW")
    if ctx.mode not in ("SW", "CD"):
        ctx.mode = "SW"
    ctx.cd = int(ctx.kv_get("cd", 60000))
    ctx.best = int(ctx.kv_get("best", 0))
    ctx.run = False
    ctx.base = 0
    ctx.t0 = 0
    ctx.fired = False
    ctx.nextui = 0
    _frame(ctx)
    _status(ctx)
    _bar(ctx)
    _draw_time(ctx)
    _hint(ctx, "OK to start")


def _ui(ctx):
    now = time.ticks_ms()
    if time.ticks_diff(now, ctx.nextui) < 0:
        return
    ctx.nextui = time.ticks_add(now, 50)      # 20 Hz, time-based not frame-based
    _draw_time(ctx)
    _bar(ctx)


def _start(ctx):
    if ctx.mode == "CD" and _left(ctx) <= 0:
        ctx.base = 0                        # finished: restart from the top
    ctx.t0 = time.ticks_ms()
    ctx.run = True
    ctx.fired = False
    _status(ctx)
    _hint(ctx, "OK to stop")


def _stop(ctx):
    ctx.base = _elapsed(ctx)                # freeze; _left() clamps at 0
    ctx.run = False
    if ctx.mode == "SW" and ctx.base > ctx.best:
        ctx.best = ctx.base
        ctx.kv_set("best", ctx.best)
        ctx.kv_flush()
    _status(ctx)


def on_key(ctx, key):
    if key == "ok":
        if ctx.run:
            _stop(ctx)
            _hint(ctx, "OK to start")
            if ctx.mode == "CD" and _left(ctx) <= 0:
                _hint(ctx, "TIME UP")
        else:
            _start(ctx)
        _draw_time(ctx)
        _bar(ctx)
        return

    if ctx.run:
        return                              # no adjusting while running

    if key == "down":
        ctx.mode = "CD" if ctx.mode == "SW" else "SW"
        ctx.kv_set("mode", ctx.mode)
        ctx.base = 0
        _status(ctx)
        _bar(ctx)
        _draw_time(ctx)
        _hint(ctx, "OK to start")
    elif key == "up":
        if ctx.mode == "SW":
            ctx.base = 0
            _draw_time(ctx)
        else:
            ctx.cd += STEP
            if ctx.cd > CD_MAX:
                ctx.cd = CD_MIN
            ctx.kv_set("cd", ctx.cd)
            ctx.kv_flush()
            _status(ctx)
            _bar(ctx)
            _draw_time(ctx)


def loop(ctx):
    if ctx.run and ctx.mode == "CD" and _left(ctx) <= 0 and not ctx.fired:
        ctx.fired = True
        ctx.run = False
        ctx.base = ctx.cd
        _draw_time(ctx)
        _bar(ctx)
        _status(ctx)
        _hint(ctx, "TIME UP")
        _beep(ctx)
        return
    _ui(ctx)


def teardown(ctx):
    ctx.kv_set("mode", ctx.mode)
    ctx.kv_set("cd", ctx.cd)
    ctx.kv_flush()
