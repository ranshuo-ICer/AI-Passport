#!/usr/bin/env python3
"""Offline DSP check for miniapps/beats.py.

_verify.py already answers "does it run without crashing". This answers the
next question: does the synthesis actually produce the right waveform, and does
the mixer behave when several voices land on the same step?

No hardware and no project changes: the module is exec'd with stubbed ctx and a
controllable clock, then the voices are inspected sample by sample.

    python miniapps/_verify_beats.py
"""

import array
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "beats.py")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


# ------------------------------------------------------------------ stubs
class Clock:
    """Controllable ticks_ms, so the sequencer can be stepped deterministically."""
    now = 0

    @classmethod
    def ticks_ms(cls):
        return cls.now & 0x3FFFFFFF

    @classmethod
    def ticks_diff(cls, a, b):
        d = (a - b) & 0x3FFFFFFF
        return d - 0x40000000 if d >= 0x20000000 else d

    @classmethod
    def ticks_add(cls, a, b):
        return (a + b) & 0x3FFFFFFF

    @classmethod
    def advance(cls, ms):
        cls.now += ms


class FakeLCD:
    def __init__(self):
        self.ops = 0

    def _n(self, *a):
        self.ops += 1

    fill = fill_rect = rect = hline = vline = _n
    text = text2x = text_scale = text_center = progress = backlight = _n


class FakeAudio:
    ok = True
    rate = 16000

    def __init__(self):
        self.played = []
        self.tones = []

    def set_volume(self, p):
        pass

    def mute(self, on):
        pass

    def tone(self, f, ms):
        self.tones.append((f, ms))

    def play_raw(self, buf):
        # Copy immediately: the app reuses buffers.
        self.played.append(bytes(memoryview(buf)))


class FakeButtons:
    def __init__(self):
        self.key = None

    def current(self):
        return self.key


class FakeShell:
    def __init__(self):
        self.buttons = FakeButtons()


class FakeCtx:
    def __init__(self, store=None):
        self.lcd = FakeLCD()
        self.audio = FakeAudio()
        self.shell = FakeShell()
        self.w = 240
        self.h = 320
        self.kv = store if store is not None else {}
        self.logs = []

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v

    def kv_flush(self):
        pass

    def log(self, m):
        self.logs.append(m)

    def exit(self):
        pass


def load_beats():
    import time as _t
    _t.ticks_ms = Clock.ticks_ms
    _t.ticks_diff = Clock.ticks_diff
    _t.ticks_add = Clock.ticks_add
    _t.sleep_ms = lambda ms: Clock.advance(ms)
    mod = {"__name__": "beats_test"}
    with open(SRC, encoding="utf-8") as f:
        exec(compile(f.read(), SRC, "exec"), mod)
    return mod


# ------------------------------------------------------------------ helpers
def as_samples(raw):
    """Accept either an array('h') (what _mix returns) or raw bytes."""
    if isinstance(raw, array.array):
        return raw
    a = array.array("h")
    a.frombytes(bytes(raw))
    return a


def zero_crossings(samples):
    n = 0
    for i in range(1, len(samples)):
        if (samples[i - 1] < 0) != (samples[i] < 0):
            n += 1
    return n


def zc_gaps(samples):
    """Sample distance between consecutive zero crossings.

    A pitch sweep is much easier to see in the GAPS than in a raw crossing
    count: counting crossings inside a short window is quantised to 1-2 and
    tells you almost nothing.
    """
    pos = [i for i in range(1, len(samples))
           if (samples[i - 1] < 0) != (samples[i] < 0)]
    return [pos[i] - pos[i - 1] for i in range(1, len(pos))]


def clipped_fraction(samples):
    """Fraction of samples pinned at the int16 rails."""
    if not samples:
        return 0.0
    n = 0
    for s in samples:
        if s >= 32767 or s <= -32768:
            n += 1
    return n / len(samples)


def peak(samples):
    hi = 0
    lo = 0
    for s in samples:
        if s > hi:
            hi = s
        elif s < lo:
            lo = s
    return max(hi, -lo)


def main():
    B = load_beats()
    ctx = FakeCtx()
    B["setup"](ctx)

    print("\n[1] setup")
    check("sine table is 1024 int16 entries", len(B["SINE"]) == 1024)
    check("sine table peak is sane",
          10000 < peak(B["SINE"]) < 32767, "peak=%d" % peak(B["SINE"]))
    check("default pattern is not empty", sum(ctx.pat) > 0,
          "sum=%d" % sum(ctx.pat))
    check("default pattern has a kick on step 0", ctx.pat[0] == 1)
    check("default pattern has a snare on step 4", ctx.pat[16 + 4] == 1)
    check("rate taken from the audio device", ctx.rate == 16000)

    print("\n[2] empty step produces no audio")
    for i in range(64):
        ctx.pat[i] = 0
    buf, active = B["_mix"](ctx, 0)
    check("empty step -> no buffer", buf is None)
    check("empty step -> all tracks inactive", active == (0, 0, 0, 0))

    print("\n[3] each voice produces sound on its own")
    names = ("kick", "snare", "hat", "bass")
    for t in range(4):
        for i in range(64):
            ctx.pat[i] = 0
        ctx.pat[t * 16 + 0] = 1
        buf, active = B["_mix"](ctx, 0)
        check("%s -> buffer produced" % names[t], buf is not None)
        if buf is None:
            continue
        s = as_samples(buf)
        want_len = 16000 * B["MIX_MS"] // 1000
        check("%s -> buffer length is MIX_MS" % names[t],
              len(s) == want_len, "%d vs %d" % (len(s), want_len))
        check("%s -> actually audible" % names[t], peak(s) > 2000,
              "peak=%d" % peak(s))
        check("%s -> within int16" % names[t], peak(s) < 32768,
              "peak=%d" % peak(s))

    print("\n[4] kick really sweeps downward")
    for i in range(64):
        ctx.pat[i] = 0
    ctx.pat[0] = 1
    buf, _ = B["_mix"](ctx, 0)
    s = as_samples(buf)
    n = 16000 * B["KICK_MS"] // 1000
    gaps = zc_gaps(s[:n])
    check("kick has enough cycles to measure", len(gaps) >= 6,
          "%d gaps" % len(gaps))
    if len(gaps) >= 6:
        k = len(gaps) // 3
        early = sum(gaps[:k]) / k
        late = sum(gaps[-k:]) / k
        # 140 Hz -> half-period 57 samples; 50 Hz -> 160 samples.
        check("kick period grows (pitch falls)", late > early * 1.6,
              "early=%.0f late=%.0f" % (early, late))
    head = s[:n // 5]
    tail = s[n - n // 5:n]
    check("kick head is louder than tail", peak(head) > peak(tail),
          "head=%d tail=%d" % (peak(head), peak(tail)))

    print("\n[5] bass hits the note it claims")
    # A 55 Hz tone over 40 ms at 16 kHz is ~2.2 periods -> 4..5 zero crossings.
    for semi, label in ((0, "A2=110Hz"), (12, "A3=220Hz"), (7, "E3=165Hz")):
        f = B["_note"](semi)
        check("_note(%d) -> %s" % (semi, label), abs(f - (110 * 2 ** (semi / 12.0)))
              < 1.0, "got %s" % f)
    check("_note(12) is an octave", B["_note"](12) == 220, B["_note"](12))

    print("\n[6] mixing several voices does not clip")
    for i in range(64):
        ctx.pat[i] = 1
    buf, active = B["_mix"](ctx, 0)
    s = as_samples(buf)
    check("all four tracks report active", active == (1, 1, 1, 1))
    # -32768 is a legal int16, so the bound is inclusive; what actually matters
    # is how much of the mix is pinned to the rails.
    check("four-voice mix within int16", peak(s) <= 32768, "peak=%d" % peak(s))
    cf = clipped_fraction(s)
    check("four-voice mix barely clips (<5%)", cf < 0.05,
          "clipped=%.2f%%" % (cf * 100))
    check("four-voice mix is louder than one voice", peak(s) > 2000)

    print("\n[7] sequencer advances and triggers playback")
    ctx.audio.played = []
    ctx.step = 15
    ctx.next_tick = Clock.ticks_ms()
    B["_advance"](ctx)
    check("step 15 advances to 0", ctx.step == 0)
    check("step 0 fires a sound", len(ctx.audio.played) == 1,
          "%d played" % len(ctx.audio.played))
    if ctx.audio.played:
        s = as_samples(ctx.audio.played[0])
        check("played buffer is non-silent", peak(s) > 2000, "peak=%d" % peak(s))

    print("\n[8] editing toggles and reports dirtiness")
    ctx.cur = 5
    before = ctx.pat[5]
    B["on_key"](ctx, "ok")
    check("OK toggles the cell", ctx.pat[5] != before)
    check("toggle marks the pattern dirty", ctx.dirty is True)
    check("toggle gives audible feedback", len(ctx.audio.tones) == 1)

    ctx.cur = 0
    B["on_key"](ctx, "up")
    check("UP wraps backwards to the last cell", ctx.cur == B["CELLS"] - 1,
          "cur=%d" % ctx.cur)
    B["on_key"](ctx, "down")
    check("DOWN wraps forwards to the first cell", ctx.cur == 0)

    print("\n[9] persistence round-trip")
    B["teardown"](ctx)
    saved = ctx.kv.get("pat")
    check("pattern written to kv (one char per cell)",
          isinstance(saved, str) and len(saved) == B["CELLS"],
          "len=%s" % (len(saved) if saved else None))
    check("bpm written to kv", ctx.kv.get("bpm") == ctx.bpm)

    ctx2 = FakeCtx(store=dict(ctx.kv))
    B["setup"](ctx2)
    check("pattern restored byte for byte",
          bytes(ctx2.pat) == bytes(ctx.pat))
    check("bpm restored", ctx2.bpm == ctx.bpm)

    print("\n" + "=" * 58)
    print("passed %d, failed %d" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  - " + f)
    print("=" * 58)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
