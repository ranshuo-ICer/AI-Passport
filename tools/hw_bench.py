"""Device-side micro-benchmark for PassportOS hot paths.

Run on the real board:
    python -m mpremote connect COM3 run tools/hw_bench.py

Answers "what is actually slow, and how much of it is Python-level work that C
or viper could remove" with numbers instead of intuition.
"""

import array
import gc
import time

import framebuf

from passport import display as D
from passport.display import Display, _to_be

REPEAT = 20


def bench(name, fn, n=REPEAT):
    gc.collect()
    fn()                                  # warm up (caches, first-call setup)
    gc.collect()
    t0 = time.ticks_us()
    for _ in range(n):
        fn()
    dt = time.ticks_diff(time.ticks_us(), t0)
    per = dt / n
    print("  %-34s %9.0f us" % (name, per))
    return per


def spi_bytes(n_px):
    """How long the raw SPI transfer alone should take at 40 MHz."""
    return n_px * 2 / 40.0          # 40 MHz -> 0.025 us/byte -> ns/1000 = us


print("=" * 64)
print("PassportOS hot-path profile (real hardware)")
print("=" * 64)
gc.collect()
print("heap free at start: %d" % gc.mem_free())

lcd = Display(backlight=50)

print("\n[display]")
bench("fill full screen (240x320)", lambda: lcd.fill(D.NAVY), 10)
bench("fill_rect header (240x21)", lambda: lcd.fill_rect(0, 0, 240, 21, D.DARK), 50)
bench("text short (10 chars)", lambda: lcd.text("HELLO WORLD", 8, 8, D.WHITE, D.NAVY), 50)
bench("text long (28 chars)", lambda: lcd.text("A" * 28, 8, 40, D.WHITE, D.NAVY), 50)
bench("text_scale x2 (10 chars)", lambda: lcd.text_scale("HELLO", 8, 60, D.WHITE, D.NAVY, 2), 30)
bench("text_scale x3 (10 chars)", lambda: lcd.text_scale("HELLO", 8, 80, D.WHITE, D.NAVY, 3), 30)

print("\n[breakdown of one 10-char text()]  80x8 px, SPI alone ~%.2f us"
      % spi_bytes(80 * 8))
fb = lcd._text_fb(80)


def render_only():
    fb.fill(D.NAVY)
    fb.text("HELLO WORLD", 0, 0, D.WHITE)


bench("  a) render into framebuf", render_only, 50)
bench("  b) lcd.blit(fb)  (含 _to_be)", lambda: lcd.blit(fb, 8, 8, 80, 8), 50)
bench("  c) _to_be alone", lambda: _to_be(fb), 50)

print("\n[byte-order swap: correctness first, then speed]")
# viper 版本必须和纯 Python 版本逐字节一致，否则颜色会错。离线测试跑不到
# viper（CPython 没有 micropython 模块），所以只能在真机上验。
import passport.display as _D
print("  viper _to_be_fast available: %s" % (_D._to_be_fast is not None))
print("  viper _scale_row_fast available: %s" % (_D._scale_row_fast is not None))

_ref = bytearray(240 * 8 * 2)
for i in range(len(_ref)):
    _ref[i] = (i * 7 + 3) & 0xFF
_expect = bytearray(len(_ref))
for i in range(0, len(_ref), 2):
    _expect[i] = _ref[i + 1]
    _expect[i + 1] = _ref[i]
_got = _to_be(_ref)
print("  _to_be matches reference: %s" % (_got == _expect))

# text_scale 的行缩放也照同一份参考实现比一遍
_row_expect = bytearray(20 * 3 * 2)
_row_got = bytearray(20 * 3 * 2)
_src = bytearray(20 * 8 * 2)
for i in range(len(_src)):
    _src[i] = (i * 11 + 5) & 0xFF
for i in range(20):
    lo = _src[i * 2]
    hi = _src[i * 2 + 1]
    b = i * 3 * 2
    for _k in range(3):
        _row_expect[b] = hi
        _row_expect[b + 1] = lo
        b += 2
_D._scale_row(memoryview(_src), _row_got, 0, 20, 3)
print("  _scale_row matches reference: %s" % (_row_got == _row_expect))

for px in (80, 240):
    buf = bytearray(px * 8 * 2)
    for i in range(0, len(buf), 4):
        buf[i] = 0x12
        buf[i + 1] = 0x34
    bench("  _to_be %d px (%d bytes)" % (px, len(buf)), lambda b=buf: _to_be(b), 50)

print("\n[SPI raw throughput — is fill() limited by the bus or by Python?]")
# fill() 不走 _to_be，所以 viper 帮不到它。这里直接量 SPI 本身，
# 把"总线带宽"和"Python/分配开销"分开。
for kb in (2, 8, 32):
    n = kb * 1024
    chunk = bytes(n)
    gc.collect()
    t0 = time.ticks_us()
    for _ in range(10):
        lcd.dc(1)
        lcd.cs(0)
        lcd.spi.write(chunk)
        lcd.cs(1)
    dt = time.ticks_diff(time.ticks_us(), t0) / 10
    mbps = n * 8 / dt                      # bytes*8 / us = Mbit/s
    print("  write %2d KB: %8.0f us  -> %5.1f Mbit/s" % (kb, dt, mbps))
print("  (SPI is configured for 40 MHz; much less than that means the bus is")
print("   not the limit - per-call overhead and allocation are.)")

print("\n[audio]")
from passport.audio import Audio
aud = Audio(rate=16000)
if aud.ok:
    aud.set_volume(60)
    bench("tone 40ms (640 samples)", lambda: aud.tone(880, 40), 20)
    n = 16000 * 60 // 1000
    mix = array.array("h", bytes(n * 2))
    for i in range(n):
        mix[i] = (i * 37) % 2000 - 1000
    bench("play_raw 60ms (960 samples)", lambda: aud.play_raw(mix), 20)
    print("  (audio synthesis uses @micropython.viper already)")
else:
    print("  (audio unavailable: %s)" % aud.error)

print("\n[BLE JSON]")
import json
cmd = {"t": "put", "n": "beats", "s": 14685, "title": "Beats"}
bench("json.dumps command (~50 B)", lambda: json.dumps(cmd), 50)
s = json.dumps(cmd)
bench("json.loads command", lambda: json.loads(s), 50)
big_list = {"t": "ls", "apps": [{"n": "app%02d" % i, "title": "T" * 16, "s": 1234}
                               for i in range(12)]}
bs = json.dumps(big_list)
print("  (ls response is %d bytes)" % len(bs))
bench("json.dumps ls response", lambda: json.dumps(big_list), 50)
bench("json.loads ls response", lambda: json.loads(bs), 50)

print("\n" + "=" * 64)
gc.collect()
print("heap free at end: %d" % gc.mem_free())
