"""ButtonLab - live ADC readout for the three-button resistor ladder.

Why this exists: the official firmware's `main/demo_button.c` says it out loud -
the live millivolt readout is the tool you use to re-calibrate the voltage table
when the divider or pull-up changes. This is that tool, for our board, plus the
min/max the official page does not keep.

Everything on screen comes from `ctx.buttons.check()`, which reads the ADC
without touching the shell's own debounce state.

Keys:  UP    reset min/max
       DOWN  toggle HOLD (freeze the live numbers)
       OK    clear counters and the event log

Long-press OK returns to the menu (system behaviour).
"""

TITLE = "ButtonLab"

BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
SEP = 0x39E7
WARN = 0xF800
HOLD_C = 0xFFE0

# Band colours. The bands are deliberately drawn to scale, so you can see how
# narrow UP really is (about 9 px out of 208) - that is the whole point.
C_UP = 0xF800
C_DOWN = 0xFD20
C_OK = 0x07E0
C_REL = 0x2104

BANDS = (
    ("UP", 0, 150, C_UP),
    ("DOWN", 150, 447, C_DOWN),
    ("OK", 447, 1900, C_OK),
    ("--", 1900, 3300, C_REL),
)
FULL_MV = 3300

BAR_X = 16
BAR_Y = 84
BAR_W = 208
BAR_H = 26
MARK_W = 3

LOG_LINES = 4


def band_of(mv):
    """The label and colour of the window `mv` falls into."""
    for name, lo, hi, col in BANDS:
        if lo <= mv < hi:
            return name, col
    return ("--", C_REL)


def x_of_mv(mv):
    if mv < 0:
        mv = 0
    elif mv > FULL_MV:
        mv = FULL_MV
    return BAR_X + mv * (BAR_W - MARK_W) // FULL_MV


def color_at_x(x):
    mv = (x - BAR_X) * FULL_MV // (BAR_W - MARK_W)
    return band_of(mv)[1]


# ------------------------------------------------------------------- drawing
def draw_bar(ctx, mv):
    lcd = ctx.lcd
    lcd.fill_rect(BAR_X, BAR_Y, BAR_W, BAR_H, BG)
    for _name, lo, hi, col in BANDS:
        x0 = x_of_mv(lo)
        x1 = x_of_mv(hi)
        w = x1 - x0
        if w > 0:
            lcd.fill_rect(x0, BAR_Y, w, BAR_H, col)
    # marker last so it sits on top of the bands
    mx = x_of_mv(mv)
    lcd.fill_rect(mx, BAR_Y, MARK_W, BAR_H, INK)


def draw_static(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("BUTTON LAB", 6, 5, ACCENT, BAR)
    lcd.text("UP", 16, 114, C_UP, BG)
    lcd.text("DOWN", 38, 114, C_DOWN, BG)
    lcd.text("OK", 96, 114, C_OK, BG)
    lcd.text("released", 150, 114, DIM, BG)
    lcd.text("0", 16, 126, DIM, BG)
    lcd.text("3300 mV", 168, 126, DIM, BG)
    lcd.fill_rect(8, 196, 224, 1, SEP)
    lcd.text("EVENTS", 8, 202, DIM, BG)
    lcd.text("UP: clr min/max", 8, 286, SEP, BG)
    lcd.text("DN: hold   OK: clr cnt", 8, 298, SEP, BG)
    lcd.text("long OK = back", 8, 310, BAR, BG)


def draw_live(ctx, mv, key, mn, mx):
    lcd = ctx.lcd
    lcd.text_scale("%d" % mv, 8, 26, INK, BG, 3)
    lcd.text("mV", 152, 38, DIM, BG)

    name, _col = band_of(mv)
    lcd.text("KEY: %-8s" % (key.upper() if key else name), 8, 56,
             WARN if key else DIM, BG)

    draw_bar(ctx, mv)

    lcd.text("min %-4d  max %-4d" % (mn, mx), 8, 140, DIM, BG)
    lcd.text("min->%-5s max->%-5s" % (band_of(mn)[0], band_of(mx)[0]),
             8, 154, ACCENT, BG)

    lcd.text("UP %-4d DN %-4d OK %-4d"
             % (ctx.cnt[0] % 10000, ctx.cnt[1] % 10000, ctx.cnt[2] % 10000),
             8, 174, INK, BG)


def draw_log(ctx):
    lcd = ctx.lcd
    for i in range(LOG_LINES):
        y = 216 + i * 14
        if i < len(ctx.log_lines):
            lcd.text(ctx.log_lines[i], 8, y, DIM, BG)
        else:
            lcd.text(" " * 12, 8, y, DIM, BG)


def draw_hold(ctx):
    lcd = ctx.lcd
    lcd.text("HOLD" if ctx.hold else "    ", 8, 268, HOLD_C, BG)


def push_log(ctx, key, mv):
    ctx.log_lines.insert(0, "%s @ %d mV" % (key.upper(), mv))
    del ctx.log_lines[LOG_LINES:]


# --------------------------------------------------------------------- hooks
def setup(ctx):
    ctx.hold = False
    ctx.cnt = [0, 0, 0]
    ctx.log_lines = []
    ctx.mn = 99999
    ctx.mx = -1
    mv, key = read(ctx)
    draw_static(ctx)
    draw_live(ctx, mv, key, mv if ctx.mn > 90000 else ctx.mn, mv)
    draw_log(ctx)
    draw_hold(ctx)
    ctx.log("buttonlab ready")


IDX = {"up": 0, "down": 1, "ok": 2}


def read(ctx):
    try:
        return ctx.buttons.check()
    except Exception as exc:                              # noqa: BLE001
        ctx.log("adc: %s" % exc)
        return (ctx.mn if ctx.mn < 90000 else 0, None)


def loop(ctx):
    # ~12 Hz is plenty for a voltmeter and keeps the redraw off the CPU.
    if ctx.frame % 4:
        return
    if ctx.hold:
        return
    mv, key = read(ctx)
    if mv < ctx.mn:
        ctx.mn = mv
    if mv > ctx.mx:
        ctx.mx = mv
    draw_live(ctx, mv, key, ctx.mn, ctx.mx)


def on_key(ctx, key):
    # Control action FIRST, then record this press.
    #
    # Order matters and both alternatives are wrong:
    #   * returning early while toggling HOLD (the first version) meant those
    #     presses were never counted at all;
    #   * recording first and clearing after (the second version) meant OK
    #     wiped its own count, so the OK counter was permanently 0.
    # Clearing first leaves the clearing press itself in the log, which is the
    # honest answer to "what just happened".
    if key == "up":
        ctx.mn = 99999
        ctx.mx = -1
    elif key == "down":
        ctx.hold = not ctx.hold
        draw_hold(ctx)
    elif key == "ok":
        ctx.cnt = [0, 0, 0]
        ctx.log_lines = []

    mv, k = read(ctx)
    if mv < ctx.mn:
        ctx.mn = mv
    if mv > ctx.mx:
        ctx.mx = mv
    if k and k in IDX:
        ctx.cnt[IDX[k]] += 1
        push_log(ctx, k, mv)
    draw_log(ctx)
    draw_live(ctx, mv, k, ctx.mn, ctx.mx)


def teardown(ctx):
    # Nothing shared to release - the shell owns the ADC. Deliberately empty:
    # see docs/pitfalls.md 5.5 about latching shared peripherals on exit.
    pass
