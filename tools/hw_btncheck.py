"""Button ADC stability check — are phantom key presses possible?

Reads GPIO0 fast for a few seconds and reports the distribution. A phantom
press needs two consecutive samples landing inside a window (see
config.BTN_WINDOWS), so this also counts how often that would have happened.

    python -m mpremote connect COM3 run tools/hw_btncheck.py
"""

import time

from passport.buttons import Buttons
from passport import config as C

N = 4000
b = Buttons()

print("BTN_WINDOWS = %r" % (C.BTN_WINDOWS,))
print("sampling %d times ..." % N)

buckets = {}
lows = []
prev_key = None
phantom = []
t0 = time.ticks_us()
for i in range(N):
    mv = b.raw_mv()
    bucket = (mv // 200) * 200
    buckets[bucket] = buckets.get(bucket, 0) + 1
    if mv < C.BTN_RELEASED_MV:
        lows.append(mv)
        # 连续两次落入同一个窗口才可能被 Buttons.update() 认定为按下
        key = b._classify(mv)
        if key is not None and key == prev_key:
            phantom.append(mv)
        prev_key = key
    else:
        prev_key = None
dt = time.ticks_diff(time.ticks_us(), t0)

print("elapsed %d us (%.1f us/sample)" % (dt, dt / N))
print("\ndistribution (200 mV buckets):")
for k in sorted(buckets):
    print("  %5d-%5d mV : %5d" % (k, k + 199, buckets[k]))

print("\nbelow release threshold (%d mV): %d samples" % (C.BTN_RELEASED_MV, len(lows)))
if lows:
    print("  min=%d max=%d" % (min(lows), max(lows)))
print("consecutive same-window pairs (would register as a press): %d" % len(phantom))
if phantom:
    print("  values: %r" % phantom[:20])

print("\nVERDICT: %s" % ("NO phantom presses" if not phantom
                         else "PHANTOM PRESSES POSSIBLE"))
