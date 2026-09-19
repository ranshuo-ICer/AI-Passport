"""小程序载入耗时实测（设备端）—— 启动器"按 OK 到画面出来"的主要成本。

配合 BLE 推送吞吐一起看：推一个 15 KB 程序大概几秒，而载入只要几十毫秒，
所以启动器的体感瓶颈在推送侧，载入侧不用过度优化。

    python -m mpremote connect COM3 run tools/hw_appload_bench.py
"""

import gc
import time

import passport.apps as apps

print("=" * 66)
print("小程序载入耗时（真机）")
print("=" * 66)

# 有哪些装好了的程序
try:
    names = [a["n"] for a in apps.list_apps()]
except Exception as exc:                                      # noqa: BLE001
    print("  list_apps 失败: %s" % exc)
    names = []

print("  已安装 %d 个: %s" % (len(names), ", ".join(names[:8])))
print()

rows = []
for n in names:
    gc.collect()
    try:
        src = apps.read_source(n)
        size = len(src)
        del src
    except Exception as exc:                                  # noqa: BLE001
        print("  %-12s 读源码失败: %s" % (n, exc))
        continue
    gc.collect()
    t0 = time.ticks_us()
    mod = apps.load_module(n)
    dt = time.ticks_diff(time.ticks_us(), t0)
    hooks = sum(1 for k in ("setup", "loop", "on_key", "teardown") if k in mod)
    del mod
    gc.collect()
    rows.append((n, size, dt))
    print("  %-12s %6d 字节  %6.1f ms  (%d 个钩子)"
          % (n, size, dt / 1000.0, hooks))

if rows:
    rows.sort(key=lambda r: -r[2])
    print()
    print("  最慢: %s %d 字节 %.1f ms" % (rows[0][0], rows[0][1], rows[0][2] / 1000))
    print("  最快: %s %d 字节 %.1f ms" % (rows[-1][0], rows[-1][1], rows[-1][2] / 1000))
    big = [r for r in rows if r[1] > 12000]
    if big:
        avg = sum(r[2] for r in big) / len(big)
        print("  >12 KB 的程序平均 %.1f ms（共 %d 个）" % (avg / 1000, len(big)))

# ---------------------------------------------------------------- 拆解
# 注意 stress 32 KB 反而比 beats 15 KB 快得多 —— 说明成本不在文件大小，
# 而在"要编译执行的代码量"。下面把 read / compile / exec 三段分开量。
print("\n[拆解] 读源码 / 编译 / 执行（各测 3 次取最快）")
print("  %-12s %8s %10s %10s %10s" % ("程序", "字节", "read(ms)", "compile(ms)", "exec(ms)"))
for n in ("clock", "beats", "stress", "repeater"):
    if n not in names:
        continue
    best_r = best_c = best_e = None
    size = 0
    for _ in range(3):
        gc.collect()
        t0 = time.ticks_us()
        src = apps.read_source(n)
        t1 = time.ticks_us()
        size = len(src)
        code = compile(src, apps.app_file(n), "exec")
        t2 = time.ticks_us()
        del src
        gc.collect()
        mod = {"__name__": "bench_" + n}
        exec(code, mod)
        t3 = time.ticks_us()
        del mod, code
        gc.collect()
        r = time.ticks_diff(t1, t0)
        c = time.ticks_diff(t2, t1)
        e = time.ticks_diff(t3, t2)
        best_r = r if best_r is None else min(best_r, r)
        best_c = c if best_c is None else min(best_c, c)
        best_e = e if best_e is None else min(best_e, e)
    print("  %-12s %8d %10.1f %10.1f %10.1f"
          % (n, size, best_r / 1000, best_c / 1000, best_e / 1000))

gc.collect()
print("\n  heap free=%d" % gc.mem_free())
print("=" * 66)
