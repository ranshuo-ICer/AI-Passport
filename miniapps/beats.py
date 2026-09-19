"""Beats - a 4-track 16-step drum machine that keeps running while you edit it.

  KICK / SNARE / HAT / BASS   - one row per track, 16 steps, loops forever.

  UP / DOWN   move the edit cursor (row-major: 16 steps per row, wraps)
  OK          toggle the cell under the cursor
  hold UP     tempo +4 BPM
  hold DOWN   tempo -4 BPM

  Long-press OK (the system's own gesture) exits back to the launcher; the
  pattern is written to /apps/<name>/kv.json on exit, so it survives power loss.
  There is no stop button on purpose - it is an instrument, not a player.

WHY IT IS BUILT THIS WAY (three things that were not obvious):

  1. The four sounds are SYNTHESISED, not sampled. A mini-program has no way to
     ship an audio asset, so the kick is a pitch-swept sine (140 Hz -> 50 Hz),
     the snare is noise plus a 200 Hz body tone, the hat is differentiated
     noise, and the bass is a table sine at the step's note. All four write
     straight into a mix buffer, so they can simply be added together.

  2. Every track that fires on the same step is MIXED INTO ONE BUFFER and sent
     with a single play_raw(). audio.tone() and friends block until the I2S
     transfer is queued, so triggering three tracks separately would block for
     3x as long and make the sequencer stutter. One mix per step keeps the
     blocking cost constant no matter how dense the pattern is.

  3. That blocking cost sets the tempo ceiling. A mix is MIX_MS long, so the
     step interval has to stay above it: at BPM_MAX the 16th-note interval is
     about 107 ms against a 60 ms mix. Steps with nothing active skip the audio
     entirely, which is why sparse patterns feel snappier than dense ones.

ASCII-only on purpose: the firmware's built-in font is 8x8 ASCII only, so any
non-ASCII byte just renders as garbage.
"""

import array
import math
import time

TITLE = "Beats"

# ------------------------------------------------------------------ layout
W = 240
H = 320
BAR = 0x18E3
SEP = 0x39E7
BG = 0x0000
DIM = 0x8410
WHT = 0xFFFF
ACC = 0x07FF
HOT = 0xFD20

TRACKS = 4
STEPS = 16
CELLS = TRACKS * STEPS

GRID_X = 22
CELL_W = 13
CELL_H = 30
ROW_H = 34
GRID_Y = 28
PH_Y = 22                      # playhead marker strip
PH_H = 5
INFO_Y = 166
FOOT_Y = H - 20

TRACK_NAME = ("KICK", "SNARE", "HAT", "BASS")
TRACK_SHORT = ("KC", "SN", "HH", "BS")
TRACK_COL = (0xF800, 0xFD20, 0x07FF, 0x07E0)
TRACK_BEEP = (330, 220, 880, 165)

# Per-step bass pitch as a semitone offset. The melody is fixed and editing
# only turns steps on or off - that is what keeps three buttons enough.
# Transposing would need a second edit layer, which is not worth it here.
BASS_SEMI = (0, 0, 12, 0, 7, 0, 12, 3, 0, 0, 12, 0, 5, 0, 7, 10)
BASS_ROOT = 110                # A2

# ------------------------------------------------------------------ audio
MIX_MS = 60                    # mix length; the step interval must exceed it
BPM_MIN = 60
BPM_MAX = 140
BPM_DEF = 100
BPM_STEP = 4
STEPS_PER_BEAT = 4             # sixteenth notes

KICK_MS = 55
SNARE_MS = 45
HAT_MS = 22
BASS_MS = 58

# Per-voice gain, as a /256 fraction. These exist because the four raw peaks
# sum to roughly 86000 - far past the int16 rail - so the downbeat (where the
# kick, snare and hat can all land on one step) used to clip on 10% of samples,
# which is plainly audible. The numbers below keep the worst-case sum near 28000
# and leave the rest of the headroom to the output volume.
GAIN_KICK = 110
GAIN_SNARE = 75
GAIN_HAT = 55
GAIN_BASS = 80

SINE = None                    # 1024-entry table, built once in setup()
_rnd = [0x2BAD1DEA]            # LCG state (a list so helpers can mutate it)


def _note(semi):
    """Semitone offset -> frequency in Hz."""
    if semi == 0:
        return BASS_ROOT
    return int(BASS_ROOT * (2.0 ** (semi / 12.0)))


def _sine_table():
    """Build the 1024-entry sine table. Runs once, in setup()."""
    t = array.array("h", bytes(2048))
    for i in range(1024):
        t[i] = int(27000 * math.sin(6.283185307179586 * i / 1024.0))
    return t


def _noise():
    """LCG noise in -16384..16383. Cheaper than random and allocates nothing."""
    s = (_rnd[0] * 1103515245 + 12345) & 0x7FFFFFFF
    _rnd[0] = s
    return (s >> 15) - 16384


def _put(buf, i, v):
    """Add v onto buf[i] with saturation. The single mixing write point."""
    s = buf[i] + v
    if s > 32767:
        s = 32767
    elif s < -32768:
        s = -32768
    buf[i] = s


# ------------------------------------------------------------- synthesis
# Every voice ADDS into buf, which is what lets the tracks be mixed by simply
# calling several of them on the same buffer.
def _kick(buf, n, rate):
    ph = 0
    for i in range(n):
        f = 140 - (90 * i) // n                      # 140 Hz down to 50 Hz
        ph += (f * 4194304) // rate
        s = SINE[(ph >> 12) & 1023]
        _put(buf, i, ((s * (n - i)) // n * GAIN_KICK) >> 8)   # linear decay


def _snare(buf, n, rate):
    ph = 0
    for i in range(n):
        ph += (200 * 4194304) // rate
        body = SINE[(ph >> 12) & 1023] // 3
        # The noise decays faster than the body, which reads as a "crack".
        nz = (_noise() * (n - i)) // n
        _put(buf, i, ((body + nz) * GAIN_SNARE) >> 8)


def _hat(buf, n, rate):
    prev = 0
    for i in range(n):
        nz = _noise()
        d = nz - prev                                # 1st difference = high-pass
        prev = nz
        _put(buf, i, (((d * (n - i)) // (n * 2)) * GAIN_HAT) >> 8)


def _bass(buf, n, rate, semi):
    f = _note(semi)
    ph = 0
    edge = n // 8
    for i in range(n):
        ph += (f * 4194304) // rate
        s = SINE[(ph >> 12) & 1023]
        # Ramp the first and last 1/8, otherwise every step clicks.
        if edge:
            if i < edge:
                s = (s * i) // edge
            elif i >= n - edge:
                s = (s * (n - i)) // edge
        _put(buf, i, ((s * 2) // 3 * GAIN_BASS) >> 8)


def _mix(ctx, step):
    """Mix every track that fires on this step. Returns (buffer|None, active)."""
    p = ctx.pat
    active = (p[step], p[STEPS + step], p[STEPS * 2 + step], p[STEPS * 3 + step])
    if not (active[0] or active[1] or active[2] or active[3]):
        return None, active

    rate = ctx.rate
    n = (rate * MIX_MS) // 1000
    buf = array.array("h", bytes(n * 2))
    if active[0]:
        _kick(buf, (rate * KICK_MS) // 1000, rate)
    if active[1]:
        _snare(buf, (rate * SNARE_MS) // 1000, rate)
    if active[2]:
        _hat(buf, (rate * HAT_MS) // 1000, rate)
    if active[3]:
        _bass(buf, (rate * BASS_MS) // 1000, rate, BASS_SEMI[step])
    return buf, active


# ---------------------------------------------------------------- drawing
def _frame(ctx):
    l = ctx.lcd
    l.fill(BG)
    l.fill_rect(0, 0, W, 21, BAR)
    l.text2x("BEATS", 8, 3, ACC, BAR)
    l.text("BPM", W - 62, 6, DIM, BAR)
    _bpm(ctx)
    l.fill_rect(0, 21, W, 1, SEP)
    for t in range(TRACKS):
        y = GRID_Y + t * ROW_H
        l.text(TRACK_SHORT[t], 3, y + 11, TRACK_COL[t], BG)
        l.fill_rect(0, y + CELL_H, W, 1, 0x1082)
    for s in range(STEPS):
        if s % 4 == 0:                                # beat separator
            l.vline(GRID_X + s * CELL_W, GRID_Y, CELL_H * TRACKS, 0x2945)
    l.fill_rect(0, INFO_Y - 2, W, 1, SEP)
    l.fill_rect(0, FOOT_Y, W, 20, BAR)
    l.text("OK on/off", 6, FOOT_Y + 5, DIM, BAR)
    l.text("hold UP/DN = bpm", 78, FOOT_Y + 5, DIM, BAR)
    for i in range(CELLS):
        _cell(ctx, i)
    _cursor(ctx, ctx.cur)
    _info(ctx)


def _cell(ctx, idx):
    l = ctx.lcd
    t = idx // STEPS
    s = idx % STEPS
    x = GRID_X + s * CELL_W
    y = GRID_Y + t * ROW_H
    # The playhead column gets a lighter background so "where are we" reads at
    # a glance without having to look at the marker strip.
    bg = 0x18E3 if s == ctx.step else BG
    l.fill_rect(x, y, CELL_W - 1, CELL_H, bg)
    if ctx.pat[idx]:
        l.fill_rect(x + 1, y + 2, CELL_W - 3, CELL_H - 4, TRACK_COL[t])
    else:
        l.fill_rect(x + 1, y + CELL_H // 2 - 1, CELL_W - 3, 2, DIM)


def _cursor(ctx, idx):
    """The cursor is a white outline OUTSIDE the cell, so it never hides the
    on/off fill it is sitting on."""
    l = ctx.lcd
    t = idx // STEPS
    s = idx % STEPS
    x = GRID_X + s * CELL_W
    y = GRID_Y + t * ROW_H
    l.rect(x - 1, y - 1, CELL_W + 1, CELL_H + 2, WHT)


def _playhead(ctx, step):
    l = ctx.lcd
    x = GRID_X + step * CELL_W
    l.fill_rect(x + 1, PH_Y, CELL_W - 2, PH_H, HOT)


def _bpm(ctx):
    l = ctx.lcd
    l.fill_rect(W - 46, 3, 44, 16, BAR)
    l.text("%3d" % ctx.bpm, W - 44, 6, WHT, BAR)


def _info(ctx):
    l = ctx.lcd
    t = ctx.cur // STEPS
    l.fill_rect(0, INFO_Y, W, FOOT_Y - INFO_Y, BG)
    l.text2x(TRACK_NAME[t], 8, INFO_Y + 6, TRACK_COL[t], BG)
    l.text("STEP %02d" % ((ctx.cur % STEPS) + 1), 8, INFO_Y + 30, DIM, BG)
    l.text("BPM %d" % ctx.bpm, 8, INFO_Y + 44, DIM, BG)
    # Four activity lamps: did this track fire on the step we just played?
    for i in range(TRACKS):
        x = 150 + i * 20
        col = TRACK_COL[i] if ctx.hit[i] else 0x2104
        l.fill_rect(x, INFO_Y + 8, 16, 16, col)
        l.text(TRACK_SHORT[i], x, INFO_Y + 28, DIM, BG)
    n = 0
    for s in range(STEPS):
        n += ctx.pat[t * STEPS + s]
    l.text("ON  %02d/%d" % (n, STEPS), 8, INFO_Y + 60, DIM, BG)
    l.progress(8, INFO_Y + 74, W - 16, 10, (n * 100) // STEPS,
               TRACK_COL[t], 0x2104)


# -------------------------------------------------------------- sequencer
def _hold(ctx, now):
    """Hold UP/DOWN to change tempo: wait to confirm it is a hold, then repeat
    every 150 ms. The initial press already moved the cursor - that is fine and
    keeps the single-press behaviour instant."""
    key = ctx.shell.buttons.current()
    if key not in ("up", "down"):
        ctx.held = None
        return
    if ctx.held != key:
        ctx.held = key
        ctx.held_at = now
        return
    if time.ticks_diff(now, ctx.held_at) < 550:
        return
    if time.ticks_diff(now, ctx.last_rep) < 150:
        return
    ctx.last_rep = now
    _bump_bpm(ctx, BPM_STEP if key == "up" else -BPM_STEP)


def _bump_bpm(ctx, delta):
    v = ctx.bpm + delta
    if v < BPM_MIN:
        v = BPM_MIN
    elif v > BPM_MAX:
        v = BPM_MAX
    if v != ctx.bpm:
        ctx.bpm = v
        _bpm(ctx)
        _info(ctx)
        ctx.dirty = True


def _advance(ctx):
    old = ctx.step
    ctx.step = (ctx.step + 1) % STEPS

    # Draw the playhead BEFORE synthesising: mixing blocks, and the eye should
    # not have to wait for the audio to be queued.
    x = GRID_X + old * CELL_W
    ctx.lcd.fill_rect(x, GRID_Y, CELL_W - 1, CELL_H * TRACKS, BG)
    for i in range(TRACKS):
        _cell(ctx, i * STEPS + old)
    _playhead(ctx, ctx.step)
    for i in range(TRACKS):
        _cell(ctx, i * STEPS + ctx.step)

    buf, active = _mix(ctx, ctx.step)
    ctx.hit = active
    if buf is not None and ctx.audio and ctx.audio.ok:
        ctx.audio.play_raw(buf)
    _info(ctx)


# ------------------------------------------------------------------ hooks
def setup(ctx):
    global SINE
    l = ctx.lcd
    l.fill(BG)
    l.text_center("BEATS", 130, ACC, BG, 3)
    l.text_center("building sine table", 180, DIM, BG, 1)

    SINE = _sine_table()

    # One character per cell ("0"/"1"), so 64 chars total. The writer in
    # teardown() must use exactly this format - an earlier version wrote 0/1
    # chars here but decoded them as hex on load, so the pattern silently never
    # came back and every boot fell through to the factory pattern.
    ctx.pat = bytearray(CELLS)
    saved = ctx.kv_get("pat", "")
    ok = isinstance(saved, str) and len(saved) == CELLS
    if ok:
        for i in range(CELLS):
            if saved[i] not in ("0", "1"):
                ok = False
                break
    if ok:
        for i in range(CELLS):
            ctx.pat[i] = 1 if saved[i] == "1" else 0
    else:
        # Factory pattern: four-on-the-floor kick, backbeat snare, eighth-note
        # hats, bass on the downbeats. Makes noise the moment it boots.
        for s in (0, 4, 8, 12):
            ctx.pat[s] = 1
        for s in (4, 12):
            ctx.pat[STEPS + s] = 1
        for s in range(0, STEPS, 2):
            ctx.pat[STEPS * 2 + s] = 1
        for s in (0, 8):
            ctx.pat[STEPS * 3 + s] = 1

    bpm = ctx.kv_get("bpm", BPM_DEF)
    if not isinstance(bpm, int) or bpm < BPM_MIN or bpm > BPM_MAX:
        bpm = BPM_DEF
    ctx.bpm = bpm

    # The mix is generated at the audio device's rate, not a hard-coded 16 kHz.
    ctx.rate = ctx.audio.rate if (ctx.audio and ctx.audio.ok) else 16000
    ctx.cur = 0
    ctx.step = STEPS - 1          # the first _advance lands on step 0
    ctx.hit = (0, 0, 0, 0)
    ctx.held = None
    ctx.held_at = 0
    ctx.last_rep = 0
    ctx.dirty = False
    ctx.next_tick = time.ticks_ms()

    if ctx.audio and ctx.audio.ok:
        # A little hotter than the other apps: the per-voice gains above are
        # deliberately conservative so a four-track downbeat cannot clip, which
        # leaves 9 dB of headroom to make up at the codec.
        ctx.audio.set_volume(84)

    _frame(ctx)
    _playhead(ctx, 0)
    ctx.log("Beats ready, %d BPM" % ctx.bpm)


def on_key(ctx, key):
    if key == "up":
        old = ctx.cur
        ctx.cur = (ctx.cur - 1) % CELLS
        _cursor(ctx, old)
        _cursor(ctx, ctx.cur)
        _info(ctx)
    elif key == "down":
        old = ctx.cur
        ctx.cur = (ctx.cur + 1) % CELLS
        _cursor(ctx, old)
        _cursor(ctx, ctx.cur)
        _info(ctx)
    elif key == "ok":
        i = ctx.cur
        ctx.pat[i] = 0 if ctx.pat[i] else 1
        _cell(ctx, i)
        _info(ctx)
        ctx.dirty = True
        if ctx.audio and ctx.audio.ok:
            # Instant audible feedback for the row being edited.
            ctx.audio.tone(TRACK_BEEP[i // STEPS], 40)


def loop(ctx):
    now = time.ticks_ms()
    _hold(ctx, now)

    step_ms = 60000 // (ctx.bpm * STEPS_PER_BEAT)
    if time.ticks_diff(now, ctx.next_tick) >= 0:
        _advance(ctx)
        # If mixing pushed us past a whole step, re-align instead of trying to
        # catch up - catching up would fire several steps back to back.
        if time.ticks_diff(now, ctx.next_tick) >= step_ms:
            ctx.next_tick = time.ticks_add(now, step_ms)
        else:
            ctx.next_tick = time.ticks_add(ctx.next_tick, step_ms)


def teardown(ctx):
    if ctx.dirty:
        ctx.kv_set("pat", "".join("1" if b else "0" for b in ctx.pat))
        ctx.kv_set("bpm", ctx.bpm)
        ctx.kv_flush()
    if ctx.audio and ctx.audio.ok:
        ctx.audio.mute(True)
