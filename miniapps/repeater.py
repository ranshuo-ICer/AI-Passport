"""Repeater - press OK to record, press again to stop and loop-play.

ASCII-only on purpose: the firmware's built-in font is 8x8 ASCII only, so any
non-ASCII byte just renders as garbage. (The old "uploads over 10s get the
heartbeat injected and the tail lost" caveat is gone - fixed in known-issues.md #1.)

Hard-won on the real device:
  1. Recording works - the owner handoff below does capture from the ES8311.
  2. One big bytearray fails: the GC heap is fragmented, so a single 64 kB
     block is impossible even with plenty free in pieces. Hence a LIST OF
     SMALL PARTS.
  3. Filling the heap completely makes everything crawl (every UI redraw
     allocates). Hence RESERVE.
  4. Parts are allocated at setup, not on the OK press, so the press is instant
     and the UI never sits on READY while the mic is already live.
  5. No _big() redraw while recording: most expensive draw, text never changed.
  6. Playback must be fed in small chunks - see _play().

Keys:  OK  idle->record, recording->stop+play, playing->stop
       UP  cancel recording / stop playing
"""

import gc
import time

TITLE = "Repeater"

# REC_RATE = bytes-per-second knob. PLAY_RATE need not equal it:
#   8000/8000 -> normal speed, best quality
#   4000/8000 -> 2x capture time in the same RAM, played 2x fast (+1 octave)
# The saving comes from REC_RATE, not from playing faster. Audio() silently
# falls back to 16000 for unknown rates, so PLAY_RATE must be one of
# 8000/11025/12000/16000/22050/24000/32000/44100/48000.
REC_RATE = 8000
PLAY_RATE = 8000
RATE = REC_RATE         # kept for the capture-side calls below
TARGET = RATE * 8       # aim for 4.0 s of audio at 8 kHz
PART = 8000             # bytes per allocation unit (small => fits fragments)
PART_MIN = 2000         # do not bother with parts smaller than this
RESERVE = 10000         # heap kept free for the UI / BLE / I2S DMA
MIN = RATE // 2         # shorter than 250ms => treat as a mis-tap
IBUFS = (2048, 1024, 512)   # I2S DMA buffer fallback steps
CHUNK = 2048
PLAY_CHUNK = 512        # bytes fed to I2S per loop() pass (32ms @ 8k)

BG = 0x0000
BAR = 0x18E3
SEP = 0x39E7
ACC = 0x07FF
GRN = 0x07E0
RED = 0xF800
DIM = 0x8410
WHT = 0xFFFF

SY = 26
PY = 52
BY = 120


def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, ctx.w, 21, BAR)
    l.text2x("REPEATER", 8, 3, ACC, BAR)
    l.fill_rect(0, 21, ctx.w, 1, SEP)
    l.fill_rect(0, ctx.h - 20, ctx.w, 20, BAR)
    l.text("OK rec / stop", 6, ctx.h - 15, DIM, BAR)
    l.text("UP cancel", ctx.w - 9 * 8 - 6, ctx.h - 15, DIM, BAR)


def _status(ctx):
    l = ctx.lcd
    l.fill_rect(0, SY, ctx.w, 14, BG)
    if ctx.state == "play":
        col = GRN
    elif ctx.state in ("rec", "err"):
        col = RED
    else:
        col = DIM
    l.text(ctx.state.upper(), 6, SY + 3, col, BG)
    info = ""
    if ctx.state == "rec":
        info = "%.2fs" % _sec(ctx.got)
    elif ctx.state == "play":
        info = "%.2fs x%d" % (_sec(ctx.got), ctx.loops)
    if info:
        l.text(info, ctx.w - 8 * len(info) - 6, SY + 3, WHT, BG)


def _big(ctx, main, sub=""):
    l = ctx.lcd
    l.fill_rect(0, BY, ctx.w, 60, BG)
    l.text_center(main[:10], BY + 8, WHT, BG, 3)
    if sub:
        l.text_center(sub[:29], BY + 42, DIM, BG, 1)


def _bar(ctx):
    l = ctx.lcd
    if ctx.state == "rec" and ctx.cap:
        l.progress(16, PY, 208, 14, ctx.got * 100 // ctx.cap, RED, BG)
    elif ctx.state == "play":
        l.progress(16, PY, 208, 14, 100, GRN, BG)
    else:
        l.fill_rect(16, PY, 208, 14, BG)
        l.rect(16, PY, 208, 14, SEP)


def _set(ctx, st, main, sub=""):
    ctx.state = st
    _status(ctx)
    _bar(ctx)
    _big(ctx, main, sub)
    ctx.nextui = time.ticks_add(time.ticks_ms(), 100)


def _ui(ctx):
    """Throttle the live readouts by TIME, not by frame count.

    Frame-count throttling misbehaves exactly when the loop is already slow:
    the rarer modulo then fires seconds apart - which is why the bar kept
    updating while the text under it did not.
    """
    now = time.ticks_ms()
    if time.ticks_diff(now, ctx.nextui) < 0:
        return
    ctx.nextui = time.ticks_add(now, 100)
    _bar(ctx)
    _status(ctx)


def _sec(n):
    return n / (RATE * 2.0)


def _idle(ctx):
    return "press OK  (up to %.1fs)" % _sec(ctx.cap)


def _fail(ctx, msg):
    """Show the failure on screen.

    There is no wired-up debug channel from here, so this screen is the only
    way the error gets back to the developer - keep it short and printable.
    """
    _endrx(ctx)
    ctx.state = "err"
    ctx.err = msg
    ctx.lcd.fill_rect(0, 74, ctx.w, 190, BG)
    ctx.lcd.text("FAILED", 6, 78, RED, BG)
    for i in range(0, min(len(msg), 232), 29):
        ctx.lcd.text(msg[i:i + 29], 6, 96 + (i // 29) * 12, WHT, BG)
    ctx.lcd.text_center("OK = reset", 246, DIM, BG, 1)
    _status(ctx)


def _squeeze(ctx):
    """Free the display layer's cached framebufs before asking for memory.

    They are re-created on demand, so this is memory we can spend on audio.
    Done defensively: it is an internal field of Display.
    """
    try:
        c = getattr(ctx.lcd, "_fb_cache", None)
        if c is not None:
            c.clear()
    except Exception:
        pass
    gc.collect()


def _cpct(ctx):
    ctx.cap = 0
    for p in ctx.parts:
        ctx.cap += len(p)


def _at(ctx, off, n):
    """Locate n bytes at logical offset off across the parts list."""
    for p in ctx.parts:
        if off < len(p):
            take = len(p) - off
            if take > n:
                take = n
            return p, off, take
        off -= len(p)
    return None, 0, 0


def _release(ctx):
    """Hand the shared I2S(0) back by deinit-ing whatever currently holds it.

    Must NOT be gated on ctx.audio.ok: the first recording deinits the system
    Audio object, so its .ok stays False forever afterwards. Track ctx.owner.
    """
    o = ctx.owner
    if o is None:
        return None
    try:
        o.deinit()
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    ctx.owner = None
    ctx.moved = True
    return None


def _mk_rx(ctx):
    """Create I2S RX. Cheap - no big allocation, so pressing OK stays snappy."""
    from machine import I2S, Pin
    import passport.config as C
    for ib in IBUFS:
        gc.collect()
        try:
            ctx.rx = I2S(0, sck=Pin(C.I2S_BCLK), ws=Pin(C.I2S_WS),
                         sd=Pin(C.I2S_DIN), mode=I2S.RX, bits=16,
                         format=I2S.MONO, rate=RATE, ibuf=ib)
            return "ibuf=%d" % ib
        except Exception:
            pass
    return None


def _mk_buf(ctx):
    """Allocate the record buffer as a list of small parts.

    Small parts fit heap fragments that one big block cannot, and we stop at
    RESERVE bytes free so the UI still has room to allocate while recording.
    """
    _squeeze(ctx)
    ctx.parts = []
    size = PART
    while True:
        try:
            free = gc.mem_free()
        except Exception:
            free = 1 << 30
        if free is None:
            free = 1 << 30
        budget = free - RESERVE
        if budget < PART_MIN:
            break
        n = size if size <= budget else (budget & ~1023)
        got = None
        while n >= PART_MIN:
            try:
                got = bytearray(n)
                break
            except MemoryError:
                n //= 2
        if got is None:
            break
        ctx.parts.append(got)
        if n < size:
            size = n
        if sum(len(p) for p in ctx.parts) >= TARGET:
            break
    _cpct(ctx)
    return bool(ctx.parts)


def _beginrec(ctx):
    _endrx(ctx)
    err = _release(ctx)
    if err:
        _fail(ctx, "audio.deinit: " + err)
        return

    # I2S needs a contiguous DMA block. If it does not fit next to the parts we
    # are holding, give one part back and retry rather than giving up.
    info = None
    shed = 0
    while True:
        info = _mk_rx(ctx)
        if info:
            break
        if not ctx.parts:
            _fail(ctx, "I2S RX: ESP_ERR_NO_MEM (shed %d parts)" % shed)
            return
        ctx.parts.pop()
        shed += 1
        _cpct(ctx)
    if shed:
        ctx.lastcap = ctx.cap

    ctx.got = 0
    ctx.wi = 0
    ctx.wo = 0
    _set(ctx, "rec", "REC", "OK to stop")


def _pump(ctx):
    while ctx.wi < len(ctx.parts) and ctx.wo >= len(ctx.parts[ctx.wi]):
        ctx.wi += 1
        ctx.wo = 0
    if ctx.wi >= len(ctx.parts):
        return
    part = ctx.parts[ctx.wi]
    room = len(part) - ctx.wo
    n = CHUNK if room > CHUNK else room
    try:
        got = ctx.rx.readinto(memoryview(part)[ctx.wo:ctx.wo + n])
    except Exception as e:
        _fail(ctx, "readinto: %s: %s" % (type(e).__name__, e))
        return
    if got:
        ctx.wo += got
        ctx.got += got
    # No _big() here on purpose: the text never changes while recording and
    # that 240x60 redraw was the most expensive thing in the loop.
    _ui(ctx)


def _endrx(ctx):
    try:
        if ctx.rx:
            ctx.rx.deinit()
    except Exception:
        pass
    ctx.rx = None


def _beginplay(ctx):
    if ctx.got < MIN:
        _set(ctx, "idle", "TOO SHORT", _idle(ctx))
        return
    gc.collect()
    try:
        from passport.audio import Audio
        p = Audio(rate=PLAY_RATE)
    except Exception as e:
        _fail(ctx, "Audio: %s: %s" % (type(e).__name__, e))
        return
    if not p.ok:
        _fail(ctx, "Audio(): %s" % (p.error or "init failed"))
        return
    try:
        p.set_volume(80)
    except Exception:
        pass
    ctx.player = p
    ctx.owner = p
    ctx.loops = 0
    ctx.playpos = 0
    _set(ctx, "play", "PLAY", "OK to stop")


def _play(ctx):
    """Feed ONE small chunk per call.

    play_raw() blocks until the DMA drains the whole buffer, so writing the
    entire recording at once froze the main loop for the whole playback -
    buttons were never sampled (presses just lost). 32ms at a time keeps the
    DMA fed seamlessly while letting tick() run in between.
    """
    if ctx.player is None:
        return
    left = ctx.got - ctx.playpos
    if left <= 0:                       # one full pass finished
        ctx.playpos = 0
        ctx.loops += 1
        _ui(ctx)
        return
    n = PLAY_CHUNK if left > PLAY_CHUNK else left
    part, off, take = _at(ctx, ctx.playpos, n)
    if part is None:
        ctx.playpos = 0
        return
    try:
        ctx.player.play_raw(memoryview(part)[off:off + take])
    except Exception as e:
        _fail(ctx, "play_raw: %s: %s" % (type(e).__name__, e))
        return
    ctx.playpos += take
    _ui(ctx)


def _idlego(ctx):
    err = _release(ctx)
    ctx.player = None
    ctx.got = 0
    ctx.wi = 0
    ctx.wo = 0
    ctx.playpos = 0
    gc.collect()
    if err:
        _fail(ctx, "stop: " + err)
        return
    _set(ctx, "idle", "READY", _idle(ctx))


def setup(ctx):
    ctx.owner = getattr(ctx, "owner", None)
    ctx.moved = getattr(ctx, "moved", False)
    ctx.state = "idle"
    ctx.rx = None
    ctx.player = None
    ctx.err = ""
    ctx.parts = getattr(ctx, "parts", None) or []
    ctx.cap = 0
    ctx.got = 0
    ctx.wi = 0
    ctx.wo = 0
    ctx.loops = 0
    ctx.playpos = 0
    ctx.nextui = 0
    _frame(ctx)
    if ctx.owner is None:
        if ctx.audio is not None and getattr(ctx.audio, "ok", False):
            ctx.owner = ctx.audio
        elif not ctx.moved:
            _fail(ctx, "no audio (check boot log)")
            return
    # Allocate the parts now, not on the OK press: this keeps the press snappy
    # and means the capacity shown below is the real one.
    if not ctx.parts:
        if not _mk_buf(ctx):
            _fail(ctx, "no RAM for buffer")
            return
    _cpct(ctx)
    _set(ctx, "idle", "READY", _idle(ctx))
    ctx.log("repeater rec=%d play=%d cap=%dB %.2fs parts=%d"
            % (REC_RATE, PLAY_RATE, ctx.cap, _sec(ctx.cap), len(ctx.parts)))


def on_key(ctx, key):
    if ctx.state == "err":
        if key in ("ok", "up"):
            setup(ctx)
        return
    if key == "ok":
        if ctx.state == "idle":
            _beginrec(ctx)
        elif ctx.state == "rec":
            _endrx(ctx)
            _beginplay(ctx)
        elif ctx.state == "play":
            # Stop and re-record in one press. _beginrec() releases whatever
            # holds the I2S, so it is safe to call straight from playback.
            _beginrec(ctx)
    elif key == "up":
        if ctx.state == "rec":
            _endrx(ctx)
            ctx.got = 0
            ctx.wi = 0
            ctx.wo = 0
            _set(ctx, "idle", "READY", _idle(ctx))
        elif ctx.state == "play":
            _idlego(ctx)


def loop(ctx):
    if ctx.state == "rec":
        _pump(ctx)
        if ctx.state == "rec" and ctx.got >= ctx.cap:
            _endrx(ctx)
            _beginplay(ctx)
    elif ctx.state == "play":
        _play(ctx)


def teardown(ctx):
    _endrx(ctx)
    _release(ctx)
    ctx.player = None
    if ctx.moved:
        try:
            gc.collect()
            from passport.audio import Audio
            fresh = Audio()
            if fresh.ok:
                ctx.shell.audio = fresh
        except Exception:
            pass
    ctx.parts = []
    ctx.cap = 0
    ctx.got = 0
    gc.collect()
