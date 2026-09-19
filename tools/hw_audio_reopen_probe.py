"""探测 Audio() 反复重建是否泄漏内存（repeater 每次录音都会重建一次）。

不制造碎片，只做干净的 create/deinit 循环，逐步看 gc.mem_free()。
若单调下降 => 每建一次漏一块 => repeater 录几次就把堆耗光 => 之后重建失败
=> shell.audio 停在死实例 => 全系统没声音（KNOWN_ISSUES #25）。

    python -m mpremote connect COM3 run tools/hw_audio_reopen_probe.py
"""

import gc

print("=" * 60)
print("探测：Audio() 反复 create/deinit 是否泄漏")
print("=" * 60)

from passport.audio import Audio

gc.collect()
start = gc.mem_free()
print("  起始 free=%d" % start)

series = []
a = Audio()
gc.collect()
print("  第一个实例建好后 free=%d" % gc.mem_free())

N = 20
for i in range(N):
    a.deinit()
    gc.collect()
    freed = gc.mem_free()
    a = Audio()
    gc.collect()
    after = gc.mem_free()
    series.append((i, freed, after, after - freed))
    print("  #%2d deinit 后 free=%6d -> 重建后 free=%6d  (净 %+d)"
          % (i, freed, after, after - freed))

gc.collect()
end = gc.mem_free()
print("\n" + "=" * 60)
print("起始 %d -> 结束 %d  (净 %+d)" % (start, end, end - start))
leaks = [d for (_i, _f, _a2, d) in series if d < 0]
print("重建中净减少的次数: %d/%d" % (len(leaks), N))
if leaks:
    print("平均每次净减少 %d 字节" % (sum(leaks) // len(leaks)))
    print("=> 存在泄漏，%d 次后累计 %d 字节" % (len(leaks), -sum(leaks)))
else:
    print("=> 没有观察到泄漏")
print("=" * 60)
