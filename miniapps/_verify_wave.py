#!/usr/bin/env python3
"""Offline DSP check for miniapps/wave.py.

_verify.py answers "does it run without crashing". This answers the next
question: is the synthesis actually correct? Shape, peak, length, the fade at
both ends, and the pitch that the phase accumulator really produces.

No hardware and no project changes: the module is exec'd with a stubbed ctx,
then every rendered buffer is inspected sample by sample.

    python miniapps/_verify_wave.py
"""

import array
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "wave.py")

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


# ------------------------------------------------------------------ stubs
class FakeLCD:
    def __init__(self):
        self.calls = 0

    def _n(self, *a):
        self.calls += 1

    fill = fill_rect = rect = hline = vline = _n
    text = text2x = text_scale = text_center = progress = backlight = _n


class FakeAudio:
    ok = True
    rate = 16000

    def __init__(self):
        self.played = []

    def set_volume(self, p):
        pass

    def play_raw(self, buf):
        # Copy at once: the app reuses nothing, but be safe about it.
        self.played.append(array.array("h", buf))


class FakeButtons:
    def check(self):
        return (2900, None)

    def voltage(self):
        return 2900

    def current(self):
        return None


class FakeBattery:
    ok = True
    percent = 85
    millivolts = 4085

    def label(self):
        return "85%"


class FakeShell:
    def __init__(self):
        self.buttons = FakeButtons()


class FakeCtx:
    def __init__(self, store=None):
        self.lcd = FakeLCD()
        self.audio = FakeAudio()
        self.shell = FakeShell()
        self.buttons = self.shell.buttons
        self.battery = FakeBattery()
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


def load_wave():
    mod = {"__name__": "wave_test"}
    with open(SRC, encoding="utf-8") as f:
        exec(compile(f.read(), SRC, "exec"), mod)
    return mod


def new_ctx():
    ctx = FakeCtx()
    ctx.tbl = None
    return ctx


W = load_wave()
TBL_N = W["TBL_N"]
PEAK = W["PEAK"]
WAVES = W["WAVES"]
STEPS = W["STEPS"]

print("=" * 62)
print("wave.py 离线 DSP 校验")
print("=" * 62)

# ---------------------------------------------------------------- 1. 波形表
print("\n[1] 波形表形状")
tables = {}
for i, name in enumerate(WAVES):
    tables[name] = list(W["make_table"](i))

for name, t in tables.items():
    check("%s 表长 = %d" % (name, TBL_N), len(t) == TBL_N)
    check("%s 峰值 = %d（不削顶）" % (name, PEAK),
          max(abs(v) for v in t) == PEAK,
          "实际 %d" % max(abs(v) for v in t))
    check("%s 落在 ±PEAK 内" % name, max(t) <= 32767 and min(t) >= -32768)

sine = tables["SINE"]
square = tables["SQUARE"]
tri = tables["TRIANGLE"]
saw = tables["SAW"]

# 方波只能有两个电平
levels = sorted(set(square))
check("SQUARE 只有两个电平", len(levels) == 2, "实际 %s" % levels[:6])

# 正弦：相邻差应有正负、且没有平坦段
diffs = [sine[i + 1] - sine[i] for i in range(TBL_N - 1)]
check("SINE 单调段连续（无跳变）", max(diffs) < PEAK // 4,
      "最大相邻差 %d" % max(diffs))

# 三角/锯齿：线性 —— 相邻差绝对值恒定
tri_d = sorted(set(abs(tri[i + 1] - tri[i]) for i in range(TBL_N - 1)))
check("TRIANGLE 是线性的（相邻差恒定）", len(tri_d) <= 2,
      "差值集合 %s" % tri_d[:6])
saw_d = sorted(set(abs(saw[i + 1] - saw[i]) for i in range(TBL_N - 1)))
check("SAW 是线性的（相邻差恒定）", len(saw_d) <= 2, "差值集合 %s" % saw_d[:6])

# 每个波形都应当基本零均值
for name, t in tables.items():
    mean = sum(t) / len(t)
    check("%s 近似零均值" % name, abs(mean) < PEAK // 20,
          "均值 %.1f" % mean)

# 对称性：正弦与三角是奇对称
check("SINE 奇对称", all(abs(sine[i] + sine[(TBL_N // 2 + i) % TBL_N]) <= 2
                         for i in range(TBL_N)))

# ---------------------------------------------------------------- 2. 合成
# 不再直接调 synth()：wave 现在**分块**推给 I2S（一次性渲染 320ms = 10 KB 连续
# 内存在真机上 MemoryError 过）。所以这里按真实路径走 play()，把 FakeAudio 收到的
# 每一块接起来 —— 测的就是设备上真正会跑的那段代码，而不是一个测试专用函数。
def render(ctx, hz, ms):
    ctx.audio.played = []
    ctx.buf = array.array("h", bytes(W["CHUNK"] * 2))
    ctx.pos = 0
    ctx.fade = 8
    n = W["play"](ctx, hz, ms)
    if n is None:
        return None
    out = array.array("h")
    for chunk in ctx.audio.played:
        out.extend(chunk)
    return out


print("\n[2] play()：长度 / 淡入淡出 / 幅度（分块拼接后的结果）")
for wf, name in enumerate(WAVES):
    for label, hz in (("A2", 110), ("A4", 440), ("E6", 1319)):
        ctx = new_ctx()
        ctx.wf = wf
        ctx.tbl = W["make_table"](wf)
        ms = 120
        buf = render(ctx, hz, ms)
        expect = 16000 * ms // 1000
        if len(buf) != expect:
            check("%s %s 长度" % (name, label), False,
                  "%d != %d" % (len(buf), expect))
            continue
        # 长度正确（只在第一个组合上报，避免刷屏）
        if label == "A4":
            check("%s 长度 = rate*ms/1000" % name, True)

        peak = max(max(buf), -min(buf))
        check("%s %s 无削顶" % (name, label), -32768 < min(buf) and max(buf) < 32767,
              "peak %d" % peak)
        # 淡入淡出：首尾样本必须接近 0
        check("%s %s 首样本≈0" % (name, label), abs(buf[0]) < PEAK // 8,
              "buf[0]=%d" % buf[0])
        check("%s %s 末样本≈0" % (name, label), abs(buf[-1]) < PEAK // 8,
              "buf[-1]=%d" % buf[-1])
        # 中段应当接近满幅（淡入淡出只占两端 2.5ms）
        mid = buf[len(buf) // 2: len(buf) // 2 + 200]
        check("%s %s 中段有幅度" % (name, label),
              max(max(mid), -min(mid)) > PEAK // 2,
              "peak %d" % max(max(mid), -min(mid)))

# ---------------------------------------------------------------- 3. 频率
print("\n[3] 相位累加器真正产生的频率")


def measure_hz(buf, rate=16000):
    """零穿越计数 -> 基频。方波/锯齿每周期穿越两次。"""
    cross = 0
    for i in range(1, len(buf)):
        if (buf[i - 1] < 0) != (buf[i] < 0):
            cross += 1
    return cross * rate / (2.0 * len(buf))


for wf, name in enumerate(WAVES):
    for label, hz in (("A2", 110), ("A4", 440), ("A5", 880)):
        ctx = new_ctx()
        ctx.wf = wf
        ctx.tbl = W["make_table"](wf)
        # 长一点，零穿越计数才准
        buf = render(ctx, hz, 1000)
        got = measure_hz(buf)
        err = abs(got - hz) / hz
        check("%s %s(%dHz) 实测 %.1fHz 误差<2%%" % (name, label, hz, got),
              err < 0.02, "误差 %.1f%%" % (err * 100))

# ---------------------------------------------------------------- 4. 无异常
print("\n[4] 极端参数不应崩")


class DeadAudio:
    ok = False
    rate = 16000
    error = "simulated"

    def set_volume(self, p):
        pass

    def play_raw(self, buf):
        raise AssertionError("play_raw must not be called when ok is False")


ctx = new_ctx()
ctx.audio = DeadAudio()
ctx.wf = 0
ctx.tbl = W["make_table"](0)
check("音频不可用时 play 返回 None（推不出去）",
      W["play"](ctx, 440, 100) is None)

# 最高音 + 最短时长
ctx2 = new_ctx()
ctx2.wf = 3
ctx2.tbl = W["make_table"](3)
buf = render(ctx2, STEPS[-1][1], 20)
check("最低时长 20ms 也能合成", len(buf) == 320, "len=%d" % len(buf))

# 分块：320ms 的推送应当切成多块，且每块不超过 CHUNK
ctx3 = new_ctx()
ctx3.wf = 0
ctx3.tbl = W["make_table"](0)
ctx3.audio.played = []
ctx3.buf = array.array("h", bytes(W["CHUNK"] * 2))
ctx3.pos = 0
ctx3.fade = 8
W["play"](ctx3, 440, 320)
chunks = [len(c) for c in ctx3.audio.played]
check("320ms 被切成多块推（不再一次性要 10 KB 连续内存）", len(chunks) > 1,
      "%d 块" % len(chunks))
check("每块不超过 CHUNK=%d" % W["CHUNK"], max(chunks) <= W["CHUNK"],
      "最大 %d" % max(chunks))
check("块数 = ceil(总样本/CHUNK)",
      len(chunks) == -(-(16000 * 320 // 1000) // W["CHUNK"]),
      "%d 块" % len(chunks))

# ---------------------------------------------------------------- 5. 落盘
print("\n[5] teardown 只写 dirty 的 kv")
store = {}
ctx3 = FakeCtx(store)
ctx3.wf = 0
ctx3.step = W["DEFAULT_STEP"]
ctx3.dirty = False
ctx3.tbl = W["make_table"](0)
W["teardown"](ctx3)
check("未改过就不写 kv", not store, "store=%s" % store)

ctx4 = FakeCtx(store)
ctx4.wf = 2
ctx4.step = 5
ctx4.dirty = True
ctx4.tbl = W["make_table"](2)
W["teardown"](ctx4)
check("改过就写回 w/f", store.get("w") == 2 and store.get("f") == 5,
      "store=%s" % store)

# setup 应能读回并夹取越界值
ctx5 = FakeCtx({"w": 99, "f": -3})
W["setup"](ctx5)
check("setup 夹取越界的 kv 值",
      0 <= ctx5.wf < len(WAVES) and 0 <= ctx5.step < len(STEPS),
      "wf=%s step=%s" % (ctx5.wf, ctx5.step))

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
