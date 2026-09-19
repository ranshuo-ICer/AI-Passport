#!/usr/bin/env python3
"""Offline checker for PassportOS mini-programs.

NOT part of the OS project - this lives alongside the .py files you push over
BLE, and only exists so a mini-program can be sanity-checked before uploading.
Uploading is slow and a broken push costs a round trip to the device.

What it does, using stubs (no hardware, no project changes):
  * bounds-checks EVERY draw call against the 240x320 panel
  * models a fragmented GC heap so allocation fallbacks actually get exercised
  * runs setup(), a scripted key sequence, loop() ticks, and teardown()
  * reports any exception with the frame it came from
  * flags non-ASCII bytes in the source (the device font is ASCII-only, and
    ASCII also keeps a corrupted upload from becoming a UnicodeError)

Usage:
    python3 _verify.py snake.py
    python3 _verify.py snake.py --keys ok,ok,up,down,ok --ticks 400
    python3 _verify.py snake.py --heap 40000 --frag 12000
"""

import argparse
import os
import sys
import types

W, H = 240, 320

# ---------------------------------------------------------------- stub panel
class LCD:
    w, h = W, H

    def __init__(self):
        self.oob = []
        self.bad = []
        self.calls = 0
        self._fb_cache = {}

    def _c(self, x, y, w, h, what):
        self.calls += 1
        if w <= 0 or h <= 0:
            self.bad.append("%s size %r" % (what, (w, h)))
        if x < 0 or y < 0 or x + w > W or y + h > H:
            self.oob.append("%s x=%d y=%d w=%d h=%d -> (%d,%d)"
                            % (what, x, y, w, h, x + w, y + h))

    def _color(self, what, *vals):
        """颜色必须是整数。

        真机上把非整数传给 fill_rect 会在 Display 里 `color >> 8` 直接 TypeError，
        而桩件原来照单全收 —— stardex.py 就把一个同名数据集元组覆盖到了颜色常量
        上，`_verify.py` 一路绿灯，直到按显示列表重放才炸出来。
        """
        for v in vals:
            if v is not None and not isinstance(v, int):
                self.bad.append("%s 颜色不是整数: %r" % (what, v))

    def fill(self, c):
        self._color("fill", c)

    def fill_rect(self, x, y, w, h, c):
        self._c(x, y, w, h, "fill_rect")
        self._color("fill_rect", c)

    def rect(self, x, y, w, h, c):
        self._c(x, y, w, h, "rect")
        self._color("rect", c)

    def hline(self, x, y, w, c):
        self._c(x, y, w, 1, "hline")
        self._color("hline", c)

    def vline(self, x, y, h, c):
        self._c(x, y, 1, h, "vline")
        self._color("vline", c)

    def text(self, s, x, y, c, bg=None):
        self._c(x, y, len(str(s)) * 8, 8, "text(%r)" % s)
        self._color("text(%r)" % s, c, bg)

    def text_scale(self, s, x, y, c, bg=None, scale=1):
        self._c(x, y, len(str(s)) * 8 * scale, 8 * scale, "text_scale(%r)" % s)
        self._color("text_scale(%r)" % s, c, bg)

    def text2x(self, s, x, y, c, bg=None):
        self.text_scale(s, x, y, c, bg, 2)

    def text_center(self, s, y, c, bg=None, scale=1):
        x = (W - len(str(s)) * 8 * scale) // 2
        self.text_scale(s, x, y, c, bg, scale)

    def progress(self, x, y, w, h, pct, fg=None, bg=None):
        self._c(x, y, w, h, "progress")

    def backlight(self, pct):
        pass


# ------------------------------------------------------- stub heap + machine
class Heap:
    def __init__(self, total, frag):
        self.total = total
        self.frag = frag          # largest single block the allocator will give
        self.used = 0
        self.peak = 0

    def free(self):
        return self.total - self.used


def install_fs_sandbox():
    """Redirect any "/apps..." path into a temp dir.

    A mini-program that installs other mini-programs writes to /apps/<name>/,
    which on the phone is the real device tree. Never let a test touch that:
    remap the prefix instead, so the write path is still exercised for real.
    """
    import builtins
    import os as _os
    import tempfile

    root = tempfile.mkdtemp(prefix="miniapp-fs-")

    def map_path(p):
        if isinstance(p, str) and (p == "/apps" or p.startswith("/apps/")):
            return root + p
        return p

    real_open = builtins.open
    real_mkdir = _os.mkdir
    # /apps already exists on the device (boot.py creates it), so mirror that:
    # without it every os.mkdir("/apps/<name>") would fail on a missing parent.
    real_mkdir(root + "/apps")
    real_listdir = _os.listdir
    real_remove = _os.remove

    builtins.open = lambda f, *a, **k: real_open(map_path(f), *a, **k)
    _os.mkdir = lambda p, *a, **k: real_mkdir(map_path(p), *a, **k)
    _os.listdir = lambda p=".": real_listdir(map_path(p))
    _os.remove = lambda p: real_remove(map_path(p))
    return root


def make_stubs(heap_total, heap_frag, clock):
    heap = Heap(heap_total, heap_frag)

    class BA(bytearray):
        def __new__(cls, n):
            if n > heap.frag or heap.used + n > heap.total:
                raise MemoryError()
            b = bytearray.__new__(cls, n)
            heap.used += n
            if heap.used > heap.peak:
                heap.peak = heap.used
            return b

        def __del__(self):
            try:
                heap.used -= len(self)
            except Exception:
                pass

    fake_gc = types.ModuleType("gc")
    fake_gc.collect = lambda: None
    fake_gc.mem_free = heap.free
    # must be installed BEFORE the mini-program runs its `import gc`
    sys.modules["gc"] = fake_gc

    i2s_state = {"live": 0, "need": 0}

    class I2S:
        RX = "RX"
        TX = "TX"
        MONO = "MONO"

        def __init__(self, *a, **kw):
            if i2s_state["need"] and heap.free() < i2s_state["need"]:
                raise OSError("ESP_ERR_NO_MEM")
            if i2s_state["live"]:
                raise OSError("I2S in use")
            i2s_state["live"] += 1
            self.rate = kw.get("rate")
            self.ibuf = kw.get("ibuf")

        def readinto(self, buf):
            return len(buf)

        def deinit(self):
            i2s_state["live"] = 0

    class Audio:
        def __init__(self, rate=16000, **kw):
            self.ok = True
            self.error = None
            self.rate = rate
            self.played = []
            # 模拟 ES8311 REG31 的 DAC 静音位：它会一直 latch 住，而所有小程序
            # 共用这一个实例 —— 退出时留下静音会把后面每个程序一起弄哑。
            # 见 docs/known-issues.md #24（真机实测退出 Beats 后 REG31=0x60）。
            self.muted = False

        def set_volume(self, v):
            # 真机上 pct>0 会顺带解除静音（audio.py 第 2 层防线）
            if v > 0:
                self.muted = False

        def tone(self, f, ms=200):
            self.played.append(("tone", f, ms))

        def melody(self, notes, bpm=120):
            self.played.append(("melody", len(notes)))

        def play_raw(self, d):
            self.played.append(("raw", len(d)))

        def mute(self, on=True):
            self.muted = bool(on)

        def deinit(self):
            self.ok = False

    machine = types.ModuleType("machine")
    machine.I2S = I2S
    machine.Pin = lambda *a, **k: object()
    machine.ADC = lambda *a, **k: object()
    machine.RTC = lambda *a, **k: object()
    sys.modules["machine"] = machine

    pkg = types.ModuleType("passport.audio")
    pkg.Audio = Audio
    import passport
    sys.modules["passport.audio"] = pkg
    setattr(passport, "audio", pkg)

    # controllable clock
    import time as _t
    _t.ticks_ms = lambda: clock["t"]
    _t.ticks_add = lambda a, b: a + b
    _t.ticks_diff = lambda a, b: a - b
    _t.sleep_ms = lambda ms: clock.__setitem__("t", clock["t"] + ms)

    return heap, BA, i2s_state, Audio


# ------------------------------------------------------------------ context
def snap(ctx):
    """Short human-readable state for the trace.

    Mini-programs do not have to call it `state` - fall back to other common
    names so the trace is useful for every app.
    """
    for a in ("state", "mode"):
        v = getattr(ctx, a, None)
        if isinstance(v, str):
            return v
    r = getattr(ctx, "run", None)
    if isinstance(r, bool):
        return "RUN" if r else "IDLE"
    return "?"
class Ctx:
    def __init__(self):
        self.lcd = LCD()
        self.w, self.h = W, H
        self.frame = 0
        self.battery = Battery()
        self.audio = None
        self.shell = Shell()
        self.buttons = self.shell.buttons
        self.name = "test"
        self.kv = {}
        self.flushes = 0

    def log(self, m):
        pass

    def exit(self):
        pass

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v

    def kv_flush(self):
        self.flushes += 1


class Battery:
    def label(self):
        return "85%"

    ok = True
    percent = 85
    millivolts = 4085


class Shell:
    def __init__(self):
        self.link = Link()
        self.audio = None
        self.buttons = Buttons()


class Link:
    connected = False


class Buttons:
    """ADC stub. `check()` reports the released voltage so diagnostics apps have
    a sane reading; `current()` stays None because no key is physically held."""

    mv = 2900

    def current(self):
        return None

    def voltage(self):
        return self.mv

    def check(self):
        return (self.mv, None)

    def update(self):
        return None


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--keys", default="ok,up,ok,down,ok,up,down,ok,ok")
    ap.add_argument("--ticks", type=int, default=300)
    ap.add_argument("--heap", type=int, default=42000)
    ap.add_argument("--frag", type=int, default=12000)
    ap.add_argument("--check-written", action="store_true",
                    help="把写出的 /apps/<name>/app.py 与本地 <name>.py 逐字节比对")
    args = ap.parse_args()

    src_bytes = open(args.path, "rb").read()
    non_ascii = sum(1 for b in src_bytes if b > 127)
    src = src_bytes.decode("utf-8", "replace")

    clock = {"t": 0}
    fs_root = install_fs_sandbox()
    heap, BA, i2s_state, Audio = make_stubs(args.heap, args.frag, clock)

    ctx = Ctx()
    ctx.audio = Audio()
    ctx.shell.audio = ctx.audio

    g = {"__name__": "miniapp", "bytearray": BA}
    problems = []
    try:
        exec(compile(src, os.path.basename(args.path), "exec"), g)
    except Exception as e:
        print("  [FAIL] exec: %s: %s" % (type(e).__name__, e))
        return 1

    # setup / on_key / teardown 是必需的（loop 可选）。原来只是 [warn] 一句，
    # 然后 step() 里照样 g["on_key"](...) —— 缺钩子的小程序会在这里抛 KeyError，
    # 报出来的是桩件的行号，而不是"你少写了 on_key"。现在直接判失败。
    missing = [fn for fn in ("setup", "on_key", "teardown") if fn not in g]
    if missing:
        print("  [FAIL] 缺少必需钩子: %s" % ", ".join("%s()" % m for m in missing))
        return 1

    keys = [k for k in args.keys.split(",") if k]
    trace = []

    def step(n=1, key=None):
        for _ in range(n):
            if key is not None:
                g["on_key"](ctx, key)
                key = None
            ctx.frame += 1
            clock["t"] += 20
            if "loop" in g:
                g["loop"](ctx)

    try:
        g["setup"](ctx)
        trace.append("setup -> %s" % snap(ctx))
        # interleave key presses with idle ticks
        per = max(1, args.ticks // (len(keys) + 1))
        step(per)
        for k in keys:
            before = ctx.lcd.calls
            step(per, k)
            trace.append("%s -> %s%s" % (k, snap(ctx),
                                         "" if ctx.lcd.calls > before else " [无重绘]"))
        step(per)
        g["teardown"](ctx)
    except Exception as e:
        import traceback
        tb = traceback.extract_tb(sys.exc_info()[2])[-1]
        print("  [FAIL] %s: %s  (at %s:%d)"
              % (type(e).__name__, e, os.path.basename(tb.filename), tb.lineno))
        print("         trace: %s" % " | ".join(trace[-6:]))
        return 1

    ok = True
    if ctx.lcd.oob:
        ok = False
        print("  [FAIL] %d 处绘制越界:" % len(ctx.lcd.oob))
        for m in ctx.lcd.oob[:6]:
            print("         " + m)
    if ctx.lcd.bad:
        ok = False
        print("  [FAIL] %d 处非法参数（尺寸/颜色类型等）:" % len(ctx.lcd.bad))
        for m in ctx.lcd.bad[:6]:
            print("         " + m)
    if non_ascii:
        ok = False
        print("  [FAIL] 源码含 %d 个非 ASCII 字节（设备字体只有 ASCII，"
              "且被截断时会变成 UnicodeError）" % non_ascii)
    if getattr(ctx.audio, "muted", False):
        ok = False
        print("  [FAIL] teardown 之后共享 codec 仍是静音（ctx.audio.muted=True）")
        print("         小程序共用 shell.audio 这一个实例，而 REG31 的静音位会 latch：")
        print("         留下的静音会传染给后面每个程序，只有重启能救。")
        print("         别在 teardown 里 mute —— 见 docs/known-issues.md #24。")

    written = []
    for dirpath, _dn, fns in os.walk(fs_root):
        for fn in fns:
            fp = os.path.join(dirpath, fn)
            written.append((fp[len(fs_root):], os.path.getsize(fp)))

    audio_n = len(getattr(ctx.audio, "played", []))
    print("  文件 %s：%d 字节, %d 次绘制, %d 次发声, 堆峰值 %d/%d, 结束状态 %s"
          % (os.path.basename(args.path), len(src_bytes), ctx.lcd.calls,
             audio_n, heap.peak, heap.total, snap(ctx)))
    if trace:
        print("  状态轨迹: %s" % " | ".join(trace[:12]))
    if written:
        print("  写出文件 %d 个:" % len(written))
        for rel, sz in written[:12]:
            print("      %-28s %6d 字节" % (rel, sz))
    if args.check_written:
        here = os.path.dirname(os.path.abspath(args.path))
        for rel, _sz in written:
            parts = rel.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "apps" and parts[2] == "app.py":
                local = os.path.join(here, parts[1] + ".py")
                if not os.path.exists(local):
                    continue
                want = open(local, "rb").read()
                got = open(fs_root + rel, "rb").read()
                if want == got:
                    print("      %-14s 逐字节一致 ✓" % parts[1])
                else:
                    ok = False
                    print("      %-14s 不一致 ❌ 本地 %d / 设备 %d"
                          % (parts[1], len(want), len(got)))
    print("  -> %s" % ("通过 ✓" if ok else "有问题 ❌"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, "/sdcard/Download/AI-Passport/os")
    sys.exit(main())
