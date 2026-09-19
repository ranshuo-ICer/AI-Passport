"""SoundSentry - a noise watcher for your desk.

Two community entries feed into this one:
  * "Sound Sentry" (the ambient sound meter whose publishing needed a screenshot)
    for the idea;
  * "Sound Meter UI: Smoothing, Anchors, and Stray Blocks" for the readout
    behaviour - the level uses an **asymmetric EMA** (snap up, drift down), so a
    short clap is visible instead of being averaged away.

Like Repeater, this app has to hand the I2S over to read the microphone: it
deinits the shared Audio object, opens I2S RX on the same pins, and rebuilds the
shared Audio on exit. Repeater proves that path works on this board.

A real constraint that shapes the UI: while the mic owns the I2S, **the app
cannot play a tone**. So the alarm is visual (the whole screen flashes red), not
audible.

Keys:  UP / DOWN  raise / lower the alarm threshold
       OK         arm / disarm the alarm
"""

import gc

TITLE = "SoundSentry"

BG = 0x0000
BAR = 0x18E3
INK = 0xFFFF
DIM = 0x8410
ACCENT = 0x07FF
QUIET = 0x07E0
MID = 0xFFE0
LOUD = 0xF800
SCALE = 0x2104
SEP = 0x39E7

RATE = 8000
CHUNK = 1024                     # samples per read (128 ms @ 8 kHz)
IBUFS = (2048, 1024, 512)

BAR_X = 24
BAR_Y = 96
BAR_W = 192
BAR_H = 40

HIST = 96                        # level history across the bar


def _release(ctx):
    """Hand the I2S over: deinit whatever currently holds it."""
    o = ctx.owner
    if o is None:
        return
    try:
        o.deinit()
    except Exception as exc:                              # noqa: BLE001
        ctx.log("release: %s" % exc)
    ctx.owner = None
    ctx.moved = True


def _mk_rx(ctx):
    from machine import I2S, Pin
    import passport.config as C
    for ib in IBUFS:
        gc.collect()
        try:
            ctx.rx = I2S(0, sck=Pin(C.I2S_BCLK), ws=Pin(C.I2S_WS),
                         sd=Pin(C.I2S_DIN), mode=I2S.RX, bits=16,
                         format=I2S.MONO, rate=RATE, ibuf=ib)
            return "ibuf=%d" % ib
        except Exception:                                 # noqa: BLE001
            ctx.rx = None
    return None


def _endrx(ctx):
    try:
        if ctx.rx:
            ctx.rx.deinit()
    except Exception:                                     # noqa: BLE001
        pass
    ctx.rx = None


def _restore_audio(ctx):
    """Put a working Audio back on ctx.shell.audio, like Repeater does."""
    if not ctx.moved:
        return
    try:
        gc.collect()
        from passport.audio import Audio
        fresh = Audio()
        if fresh.ok:
            ctx.shell.audio = fresh
    except Exception as exc:                              # noqa: BLE001
        ctx.log("audio restore failed: %s" % exc)


# ------------------------------------------------------------------- reading
def read_level(ctx):
    """One chunk of mic audio -> (rms, peak) in 0..1000."""
    buf = ctx.buf
    try:
        n = ctx.rx.readinto(buf)
    except Exception as exc:                              # noqa: BLE001
        ctx.log("readinto: %s" % exc)
        return None
    if not n:
        return None
    total = 0
    peak = 0
    i = 0
    lim = (n // 2) * 2
    while i < lim:
        v = buf[i] | (buf[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        if v < 0:
            v = -v
        if v > peak:
            peak = v
        total += v
        i += 2
    samples = lim // 2
    if samples == 0:
        return None
    avg = total // samples
    # 0..1000 (avg of |x| tops out well below full scale for normal sound)
    rms = avg * 1000 // 12000
    if rms > 1000:
        rms = 1000
    pk = peak * 1000 // 24000
    if pk > 1000:
        pk = 1000
    return (rms, pk)


def smooth(ctx, level):
    """Asymmetric EMA: jump up fast, fall back slowly.

    Straight from the community's sound-meter note - a symmetric average makes
    short sounds invisible, and this is the cheapest fix that keeps the bar
    readable at 50 Hz.
    """
    if level > ctx.ema:
        ctx.ema += (level - ctx.ema) * 3 // 4
    else:
        ctx.ema += (level - ctx.ema) // 10
    return ctx.ema


# ------------------------------------------------------------------- drawing
def level_color(v):
    if v < 350:
        return QUIET
    if v < 700:
        return MID
    return LOUD


def draw_meter(ctx):
    lcd = ctx.lcd
    lcd.fill_rect(BAR_X, BAR_Y, BAR_W, BAR_H, BG)
    lcd.rect(BAR_X - 1, BAR_Y - 1, BAR_W + 2, BAR_H + 2, SEP)
    for i in range(1, 4):
        lcd.vline(BAR_X + i * BAR_W // 4, BAR_Y, BAR_H, SCALE)

    fill = ctx.ema * BAR_W // 1000
    if fill > 0:
        lcd.fill_rect(BAR_X, BAR_Y, fill, BAR_H, level_color(ctx.ema))

    # peak hold marker
    px = ctx.peak * BAR_W // 1000
    if px >= BAR_W:
        px = BAR_W - 3
    if ctx.peak > 0:
        lcd.fill_rect(BAR_X + px, BAR_Y, 3, BAR_H, INK)

    # threshold line
    tx = ctx.thr * BAR_W // 1000
    if tx >= BAR_W:
        tx = BAR_W - 2
    lcd.fill_rect(BAR_X + tx, BAR_Y, 2, BAR_H, LOUD)


def draw(ctx):
    lcd = ctx.lcd
    alarm = ctx.alarm_on and ctx.ema >= ctx.thr and ctx.armed
    lcd.fill(0x6000 if alarm else BG)

    lcd.fill_rect(0, 0, ctx.w, 18, BAR)
    lcd.text("SOUND SENTRY", 6, 5, ACCENT, BAR)
    lcd.text("ARMED" if ctx.armed else "off", 176, 5,
             QUIET if ctx.armed else DIM, BAR)

    if ctx.rx is None:
        lcd.text("microphone unavailable", 10, 40, LOUD, lcd_bg(alarm))
        lcd.text("I2S RX could not start", 10, 58, DIM, lcd_bg(alarm))
        lcd.text("long OK = back", 10, 300, SEP, lcd_bg(alarm))
        return

    lcd.text_scale("%4d" % ctx.ema, 10, 26, level_color(ctx.ema),
                   lcd_bg(alarm), 3)
    lcd.text("level", 160, 40, DIM, lcd_bg(alarm))
    lcd.text("peak %4d" % ctx.peak, 10, 70, INK, lcd_bg(alarm))
    lcd.text("thr  %4d" % ctx.thr, 130, 70, LOUD, lcd_bg(alarm))

    draw_meter(ctx)

    lcd.text("raw %4d  %s" % (ctx.last_raw, ctx.rxinfo or "-"),
             10, 150, DIM, lcd_bg(alarm))
    if alarm:
        lcd.text_center("TOO LOUD", 180, 0x0000, LOUD, 2)
    elif ctx.peak >= ctx.thr:
        lcd.text_center("over threshold", 184, LOUD, lcd_bg(alarm), 1)
    else:
        lcd.text_center("quiet", 184, QUIET, lcd_bg(alarm), 1)

    lcd.text("holds %d" % ctx.hits, 10, 214, DIM, lcd_bg(alarm))
    lcd.text("BAT %s" % ctx.battery.label(), 10, 232, DIM, lcd_bg(alarm))
    lcd.text("UP/DN threshold  OK arm", 10, 296, SEP, lcd_bg(alarm))
    lcd.text("long OK = back", 10, 308, BAR, lcd_bg(alarm))


def lcd_bg(alarm):
    return 0x6000 if alarm else BG


# --------------------------------------------------------------------- hooks
def setup(ctx):
    ctx.owner = ctx.audio
    ctx.moved = False
    ctx.ema = 0
    ctx.peak = 0
    ctx.last_raw = 0
    ctx.hits = 0
    ctx.armed = True
    ctx.alarm_on = False
    # Default 350: in a quiet room the measured raw level is single digits and the
    # peak sits around 120, so a line at 700 would essentially never fire. Absolute
    # values depend on the room and the gain, which is why raw is shown on screen
    # and UP/DN exist: calibrate the line on site.
    ctx.thr = int(ctx.kv_get("t", 350))
    if not (50 <= ctx.thr <= 1000):
        ctx.thr = 350
    ctx.dirty = False
    ctx.buf = bytearray(CHUNK * 2)

    _release(ctx)
    ctx.rxinfo = _mk_rx(ctx)
    ctx.rx = getattr(ctx, "rx", None)
    if ctx.rxinfo is None:
        ctx.log("mic RX failed; restoring audio")
        _restore_audio(ctx)
    else:
        ctx.log("sentry listening %s thr=%d" % (ctx.rxinfo, ctx.thr))
    draw(ctx)


def loop(ctx):
    if ctx.rx is None:
        return
    lvl = read_level(ctx)
    if lvl is None:
        return
    raw, pk = lvl
    ctx.last_raw = raw
    before = ctx.ema
    smooth(ctx, raw)
    if pk > ctx.peak:
        ctx.peak = pk
    else:
        # peak hold decays slowly, otherwise one clap pins the marker forever
        ctx.peak -= 2
        if ctx.peak < 0:
            ctx.peak = 0
    if ctx.armed and ctx.ema >= ctx.thr and before < ctx.thr:
        ctx.hits += 1
        ctx.dirty = True
    ctx.alarm_on = ctx.armed and ctx.ema >= ctx.thr
    # the bar is the whole point; redraw every frame (50 Hz is fine at this size)
    draw_meter(ctx)
    if ctx.frame % 10 == 0:
        draw(ctx)


def on_key(ctx, key):
    if key == "up":
        ctx.thr += 50
        if ctx.thr > 1000:
            ctx.thr = 1000
        ctx.dirty = True
    elif key == "down":
        ctx.thr -= 50
        if ctx.thr < 50:
            ctx.thr = 50
        ctx.dirty = True
    elif key == "ok":
        ctx.armed = not ctx.armed
        ctx.dirty = True
    draw(ctx)


def teardown(ctx):
    _endrx(ctx)
    _restore_audio(ctx)
    if ctx.dirty:
        ctx.kv_set("t", ctx.thr)
        ctx.kv_set("h", ctx.hits)
        ctx.kv_flush()
    ctx.buf = None
    gc.collect()
