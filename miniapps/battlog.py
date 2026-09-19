"""BatteryLog - percent, voltage, and the history the official demo does not keep.

The official firmware's `main/demo_battery.c` shows SOC and mV and turns the
number red below 20%. That is a snapshot: you cannot tell whether you are
draining fast or sitting still. This adds a sparkline of the last N samples,
persisted to kv so it survives a power cycle, plus the 20% threshold line.

Keys:  UP    clear the history
       DOWN  switch the graph between percent and millivolts
       OK    take a sample right now

A sample is taken automatically every SAMPLE_MS (the shell itself only refreshes
the gauge every 5 s, so sampling faster would just record the same number).
Long-press OK returns to the menu.
"""

import time

TITLE = "BatteryLog"

BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
GOOD = 0x07E0
WARN = 0xF800
SEP = 0x39E7
GRID = 0x2104

SAMPLE_MS = 10000
HIST_N = 60                  # 60 samples x 10 s = 10 minutes of history

GX = 8
GY = 84
GW = 224
GH = 86

LOW_PCT = 20                 # mirrors the official page's red-under-20 rule

MODE_PCT = 0
MODE_MV = 1


# ------------------------------------------------------------------- helpers
def value_of(ctx):
    if ctx.mode == MODE_MV:
        return ctx.battery.millivolts
    return ctx.battery.percent


def load_hist(ctx):
    raw = ctx.kv_get("h", "")
    out = []
    if raw:
        for part in str(raw).split(","):
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out[-HIST_N:]


def save_hist(ctx):
    ctx.kv_set("h", ",".join(str(v) for v in ctx.hist))


def graph_range(ctx):
    """Auto-range to the observed data, with a minimum span so tiny wiggles are
    still visible instead of a dead-flat line."""
    vals = ctx.hist
    if not vals:
        return (0, 100) if ctx.mode == MODE_PCT else (3000, 4300)
    lo = min(vals)
    hi = max(vals)
    span = hi - lo
    mins = 4 if ctx.mode == MODE_PCT else 60
    if span < mins:
        pad = (mins - span) // 2 + 1
        lo -= pad
        hi += pad
    else:
        pad = span // 10 + 1
        lo -= pad
        hi += pad
    if lo < 0 and ctx.mode == MODE_PCT:
        lo = 0
    if hi > 100 and ctx.mode == MODE_PCT:
        hi = 100
    if hi <= lo:
        hi = lo + 1
    return lo, hi


def y_of(v, lo, hi):
    span = hi - lo
    if span <= 0:
        span = 1
    return GY + GH - 2 - (v - lo) * (GH - 3) // span


# ------------------------------------------------------------------- drawing
def draw_graph(ctx):
    lcd = ctx.lcd
    lcd.fill_rect(GX, GY, GW, GH, BG)
    for i in range(1, 4):
        lcd.hline(GX, GY + i * GH // 4, GW, GRID)
    lcd.rect(GX - 1, GY - 1, GW + 2, GH + 2, SEP)

    if not ctx.hist:
        lcd.text("no samples yet", 14, GY + GH // 2 - 4, DIM, BG)
        return

    lo, hi = graph_range(ctx)

    # 20% threshold, the same rule the official page uses for the red number
    if ctx.mode == MODE_PCT and lo <= LOW_PCT <= hi:
        lcd.hline(GX, y_of(LOW_PCT, lo, hi), GW, WARN)

    n = len(ctx.hist)
    denom = HIST_N - 1
    prev_y = None
    for i in range(n):
        x = GX + i * (GW - 1) // denom
        y = y_of(ctx.hist[i], lo, hi)
        if prev_y is None:
            lcd.fill_rect(x, y, 2, 1, ACCENT)
        else:
            a = y if y < prev_y else prev_y
            b = prev_y if y < prev_y else y
            lcd.fill_rect(x, a, 2, b - a + 1, ACCENT)
        prev_y = y

    lcd.text(str(hi), GX + 2, GY - 10, DIM, BG)
    lcd.text(str(lo), GX + 2, GY + GH - 8, DIM, BG)


def draw(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("BATTERY LOG", 6, 5, ACCENT, BAR)

    if not ctx.battery.ok:
        lcd.text("gauge offline", 8, 40, WARN, BG)
    else:
        v = value_of(ctx)
        unit = "mV" if ctx.mode == MODE_MV else "%"
        low = (ctx.mode == MODE_PCT and 0 <= v < LOW_PCT)
        lcd.text_scale("%d%s" % (v, unit), 8, 24, WARN if low else GOOD, BG, 3)
        other = ("%d mV" % ctx.battery.millivolts) if ctx.mode == MODE_PCT \
            else ("%d %%" % ctx.battery.percent)
        lcd.text(other, 8, 58, DIM, BG)

    draw_graph(ctx)

    n = len(ctx.hist)
    if n:
        lo = min(ctx.hist)
        hi = max(ctx.hist)
        lcd.text("n=%-3d min %-4d max %-4d" % (n, lo, hi), 8, 176, INK, BG)
    else:
        lcd.text("n=0", 8, 176, INK, BG)

    lcd.text("graph: %s   every %ds" % ("mV" if ctx.mode else "%",
                                        SAMPLE_MS // 1000), 8, 190, DIM, BG)

    left = ctx.left_ms // 1000
    lcd.text("next sample in %2ds" % left, 8, 212, DIM, BG)

    lcd.text("BAT %s" % ctx.battery.label(), 8, 234, DIM, BG)
    lcd.text("OK sample  UP clear", 8, 286, SEP, BG)
    lcd.text("DN %%/mV  long OK back", 8, 298, SEP, BG)
    lcd.text("history survives reboot", 8, 310, BAR, BG)


# --------------------------------------------------------------------- hooks
def setup(ctx):
    ctx.mode = int(ctx.kv_get("m", MODE_PCT)) & 1
    ctx.hist = load_hist(ctx)
    ctx.dirty = False
    ctx.next_ms = time.ticks_add(time.ticks_ms(), SAMPLE_MS)
    ctx.left_ms = SAMPLE_MS
    if not ctx.hist:
        sample(ctx)
    draw(ctx)


def sample(ctx):
    if not ctx.battery.ok:
        return False
    ctx.hist.append(value_of(ctx))
    del ctx.hist[:-HIST_N]
    ctx.dirty = True
    return True


def loop(ctx):
    now = time.ticks_ms()
    ctx.left_ms = time.ticks_diff(ctx.next_ms, now)
    if ctx.left_ms > 0:
        # Only the countdown changes most of the time; refresh it lazily.
        if ctx.frame % 25 == 0:
            draw(ctx)
        return
    ctx.next_ms = time.ticks_add(now, SAMPLE_MS)
    ctx.left_ms = SAMPLE_MS
    sample(ctx)
    draw(ctx)


def on_key(ctx, key):
    if key == "up":
        ctx.hist = []
        ctx.dirty = True
    elif key == "down":
        ctx.mode ^= 1
        ctx.kv_set("m", ctx.mode)
        ctx.dirty = True
    elif key == "ok":
        sample(ctx)
    draw(ctx)


def teardown(ctx):
    if ctx.dirty:
        save_hist(ctx)
        ctx.kv_set("m", ctx.mode)
        ctx.kv_flush()
