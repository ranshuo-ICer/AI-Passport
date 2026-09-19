"""复现「退出 Beats 后全系统没声音」—— 在真机上跑，读 ES8311 真实寄存器。

用法：
    python -m mpremote connect COM3 run tools/hw_audio_mute_repro.py

背景
----
`Ctx.audio` 就是 `shell.audio` 这个共享实例（见 os/passport/ui.py），所有小程序
共用同一个 ES8311。Beats 的 teardown() 里调了 `ctx.audio.mute(True)`，而整个系统
没有任何地方调 `mute(False)`（只有 `Audio.__init__` 会）—— 于是「退出 Beats」等于
把 codec 的 DAC 永久静音，此后所有小程序都哑，直到重启。

`set_volume()` 只写 REG32，不碰 REG31，所以就算重新进 Beats 也救不回来。

本脚本按用户报告的顺序走一遍**真实**代码路径，每一步都读 REG31(0x31) 判定：

    1. 新建 Audio            ≈ 开机
    2. 载入真实 beats.py
    3. 调真实 teardown()     ≈ 长按 OK 退出 Beats
    4. set_volume()+tone()   ≈ 随后打开别的小程序并发声

跑完设备停在 REPL —— 必须按 Ctrl-D 或复位才能回到 PassportOS。
"""

import gc

REG31_DAC_MUTE = 0x31
MUTE_MASK = 0x60                     # _set_mute(True) 置的两bit

FAILS = []


def check(name, ok, detail=""):
    print("  [%s] %-30s %s" % ("OK  " if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)
    return ok


def reg31(a):
    v = a._rd(REG31_DAC_MUTE)
    return v, bool(v & MUTE_MASK)


print("=" * 62)
print("复现：退出 Beats 后全系统没声音")
print("=" * 62)

# ------------------------------------------------------------------ 1. 开机
from passport.audio import Audio

audio = None

print("\n[1] 新建 Audio 实例（= 开机时 Shell 做的事）")
try:
    audio = Audio(rate=16000)
except Exception as exc:                                  # noqa: BLE001
    import sys
    sys.print_exception(exc)
if audio is None or not audio.ok:
    print("  [FAIL] Audio 起不来，无法继续: %s" % (audio.error if audio else "?"))
    raise SystemExit(1)

raw, muted = reg31(audio)
check("开机后应能发声", not muted, "REG31=0x%02X muted=%s" % (raw, muted))

# ------------------------------------------------------------------ 2. 载入 beats
print("\n[2] 载入真实的 /apps/beats/app.py")
import passport.apps as apps

mod = apps.load_module("beats")
check("beats.teardown 存在", callable(mod.get("teardown")))

# ------------------------------------------------------------------ 3. 退出
print("\n[3] 调真实 teardown()（= 长按 OK 退出 Beats）")


class _Ctx:
    """只补 teardown() 真正会碰的字段。dirty=False 跳过 kv 写入。"""

    def __init__(self, aud):
        self.audio = aud
        self.dirty = False

    def kv_set(self, key, val):
        pass

    def kv_flush(self):
        pass


mod["teardown"](_Ctx(audio))
gc.collect()
raw, muted = reg31(audio)
print("      REG31=0x%02X  mute位=%s" % (raw, muted))
check("退出 Beats 后不应静音", not muted,
      "BUG：teardown 把共享 codec 静音了" if muted else "正常")

# ------------------------------------------------------------------ 4. 下一个小程序
print("\n[4] 模拟随后打开别的小程序（set_volume + tone）")
audio.set_volume(70)
audio.tone(880, 150)
raw, muted = reg31(audio)
print("      REG31=0x%02X  mute位=%s" % (raw, muted))
check("别的程序应能发声", not muted,
      "BUG：set_volume/tone 救不回 mute" if muted else "正常")

# ------------------------------------------------------------------ 5. 外壳兜底
print("\n[5] 外壳兜底：模拟一个乱来的程序留下静音，再跑 launch() 会做的 reset_state()")
audio.mute(True)                      # 假设某个小程序的 teardown 干了这事
raw, muted = reg31(audio)
print("      故意静音后  REG31=0x%02X  mute位=%s" % (raw, muted))
check("故意静音应生效", muted, "（前置条件）")

audio.reset_state()                   # = ui.py launch() 里调的那一句
raw, muted = reg31(audio)
print("      reset_state 后 REG31=0x%02X  mute位=%s" % (raw, muted))
check("reset_state 应救回声音", not muted and audio.volume == audio.DEFAULT_VOLUME,
      "volume=%d" % audio.volume)

print("\n" + "=" * 62)
if FAILS:
    print("复现成功，%d 项失败：" % len(FAILS))
    for f in FAILS:
        print("   - " + f)
    print("\n结论：Beats 退出时静音了共享 codec，且无人恢复 —— 全系统哑到重启。")
else:
    print("全部通过：退出 Beats 不再影响其他程序发声。")
print("=" * 62)
print("设备停在 REPL，按 Ctrl-D 或复位恢复 PassportOS。")
