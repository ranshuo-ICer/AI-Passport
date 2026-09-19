#!/usr/bin/env python3
"""Offline state-machine checks for the three utility mini-apps.

_verify.py answers "does it draw inside the screen without crashing". This
answers the next question for btnlab / battlog / pet: are the mappings, the
ring buffers and the persisted state actually right?

Each module is exec'd with lightweight stubs; no hardware, no project changes.

    python miniapps/_verify_apps.py
"""

import os
import sys
import time as real_time

HERE = os.path.dirname(os.path.abspath(__file__))


class Clock:
    """MicroPython's time module has ticks_*; the host's does not. battlog and
    pet both read the clock, so patch it before exec'ing anything."""

    now = 0

    @classmethod
    def ticks_ms(cls):
        return cls.now & 0x3FFFFFFF

    @classmethod
    def ticks_add(cls, a, b):
        return (a + b) & 0x3FFFFFFF

    @classmethod
    def ticks_diff(cls, a, b):
        d = (a - b) & 0x3FFFFFFF
        return d - 0x40000000 if d >= 0x20000000 else d


real_time.ticks_ms = Clock.ticks_ms
real_time.ticks_add = Clock.ticks_add
real_time.ticks_diff = Clock.ticks_diff
real_time.sleep_ms = lambda ms: setattr(Clock, "now", Clock.now + ms)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


class FakeLCD:
    def __init__(self):
        self.calls = 0
        self.rects = []

    def _n(self, *a):
        self.calls += 1

    def fill(self, *a):
        self.calls += 1

    def fill_rect(self, x, y, w, h, c):
        self.calls += 1
        self.rects.append((x, y, w, h))

    rect = hline = vline = text = text2x = text_scale = text_center = _n
    progress = backlight = _n


class FakeButtons:
    """Mirrors os/passport/buttons.py: check() returns (mV, classified key).

    Classifying here matters - btnlab counts keys from what check() reports, and
    a stub that always returned None would silently make every counter stay 0
    (which is exactly what happened the first time this verifier was written).
    """

    WINDOWS = (("up", 0, 150), ("down", 150, 447), ("ok", 447, 1900))

    def __init__(self):
        self.mv = 2900
        self.key = None

    def _classify(self, mv):
        for name, lo, hi in self.WINDOWS:
            if lo <= mv < hi:
                return name
        return None

    def check(self):
        return (self.mv, self.key or self._classify(self.mv))

    def voltage(self):
        return self.mv

    def current(self):
        return self.key


class FakeBattery:
    def __init__(self):
        self.ok = True
        self.percent = 85
        self.millivolts = 4085

    def label(self):
        return "%d%%" % self.percent


class FakeShell:
    def __init__(self):
        self.buttons = FakeButtons()


class FakeCtx:
    def __init__(self, store=None):
        self.lcd = FakeLCD()
        self.audio = None
        self.shell = FakeShell()
        self.buttons = self.shell.buttons
        self.battery = FakeBattery()
        self.w = 240
        self.h = 320
        self.frame = 0
        self.name = "test"
        self.kv = store if store is not None else {}
        self.logs = []
        self.exited = False

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v

    def kv_flush(self):
        pass

    def log(self, m):
        self.logs.append(m)

    def exit(self):
        self.exited = True


def load(name):
    path = os.path.join(HERE, name + ".py")
    mod = {"__name": name + "_test"}
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), mod)
    return mod


# ===================================================================== btnlab
print("=" * 62)
print("btnlab.py —— 窗口映射与计数")
print("=" * 62)

B = load("btnlab")

print("\n[1] band_of：每个窗口的边界")
for name, lo, hi, _c in B["BANDS"]:
    check("%s 下边界 %dmV -> %s" % (name, lo, name), B["band_of"](lo)[0] == name)
    check("%s 上边界 %dmV -> %s" % (name, hi - 1, name),
          B["band_of"](hi - 1)[0] == name)
check("150mV 属于 DOWN 而不是 UP", B["band_of"](150)[0] == "DOWN")
check("447mV 属于 OK", B["band_of"](447)[0] == "OK")
check("1900mV 视为松开", B["band_of"](1900)[0] == "--")
check("超出量程也算松开", B["band_of"](4000)[0] == "--")

print("\n[2] x_of_mv / color_at_x 往返一致")
xs = {}
for name, lo, hi, col in B["BANDS"]:
    mid = (lo + hi) // 2
    x = B["x_of_mv"](mid)
    xs[name] = x
    check("%s 中值 %dmV -> x=%d 且颜色对得上" % (name, mid, x),
          B["color_at_x"](x) == col)
check("四个窗口的 x 单调递增",
      xs["UP"] < xs["DOWN"] < xs["OK"] < xs["--"],
      "%s" % xs)
check("x 落在 bar 内", all(B["BAR_X"] <= x <= B["BAR_X"] + B["BAR_W"]
                           for x in xs.values()))
check("负电压被夹到 bar 左端", B["x_of_mv"](-500) == B["BAR_X"])
check("超量程被夹到 bar 右端",
      B["x_of_mv"](9999) == B["BAR_X"] + B["BAR_W"] - B["MARK_W"])

print("\n[3] 运行时：计数 / 日志 / HOLD")
ctx = FakeCtx()
B["setup"](ctx)
ctx.buttons.mv = 600            # OK 窗口
B["on_key"](ctx, "ok")
check("OK 计入 cnt[2]（清空发生在记录之前，所以它自己那次留下）",
      ctx.cnt[2] == 1, "cnt=%s" % ctx.cnt)
ctx.buttons.mv = 80             # UP 窗口
B["on_key"](ctx, "up")
check("UP 计入 cnt[0]", ctx.cnt[0] == 1, "cnt=%s" % ctx.cnt)
ctx.buttons.mv = 300            # DOWN 窗口
for _ in range(6):
    B["on_key"](ctx, "down")
check("DOWN 每次都计数（含切换 HOLD 的那几次）",
      ctx.cnt == [1, 6, 1], "cnt=%s" % ctx.cnt)
check("日志最多留 %d 行" % B["LOG_LINES"], len(ctx.log_lines) == B["LOG_LINES"],
      "实际 %d" % len(ctx.log_lines))
check("日志最新一条在最前", ctx.log_lines[0].startswith("DOWN"),
      ctx.log_lines[0])

B["setup"](ctx)
ctx.buttons.mv = 10
for i in range(30):
    ctx.frame = i * 4
    B["loop"](ctx)
check("loop 记录最小值", ctx.mn == 10, "mn=%s" % ctx.mn)
ctx.buttons.mv = 2500
for i in range(30, 60):
    ctx.frame = i * 4
    B["loop"](ctx)
check("loop 记录最大值", ctx.mx == 2500, "mx=%s" % ctx.mx)

ctx.buttons.mv = 1200
B["on_key"](ctx, "up")
check("UP 从当前读数重新开始 min/max（不显示哨兵值）",
      ctx.mn == 1200 and ctx.mx == 1200, "%s %s" % (ctx.mn, ctx.mx))

ctx.buttons.mv = 100
B["on_key"](ctx, "down")        # 打开 HOLD
check("DOWN 进入 HOLD", ctx.hold)
ctx.mn = 5000                   # 放一个哨兵，看 loop 会不会动它
for i in range(200, 260):
    ctx.frame = i * 4
    B["loop"](ctx)
check("HOLD 期间 loop 不改数字", ctx.mn == 5000, "mn=%s" % ctx.mn)
B["on_key"](ctx, "down")
check("再按 DOWN 解除 HOLD", not ctx.hold)

ctx.buttons.mv = 600            # 让它被分类成 OK，而不是落到 UP 窗口
B["on_key"](ctx, "ok")
check("OK 清空计数与日志，只留下执行清空的这一次",
      ctx.cnt == [0, 0, 1] and len(ctx.log_lines) == 1
      and ctx.log_lines[0].startswith("OK"),
      "cnt=%s log=%s" % (ctx.cnt, ctx.log_lines))

# ==================================================================== battlog
print("\n" + "=" * 62)
print("battlog.py —— 历史环形缓冲与量程")
print("=" * 62)

L = load("battlog")
HIST_N = L["HIST_N"]

print("\n[1] 落盘往返")
store = {}
ctx = FakeCtx(store)
ctx.battery.percent = 77
L["setup"](ctx)
check("setup 记下第一个样本", ctx.hist == [77], "hist=%s" % ctx.hist)
ctx.battery.percent = 70
L["sample"](ctx)
ctx.battery.percent = 65
L["sample"](ctx)
L["teardown"](ctx)
check("teardown 写了 h", "h" in store, "store=%s" % store)
check("h 是逗号分隔", store["h"] == "77,70,65", store.get("h"))

store2 = store          # same store: this is the "reopen after reboot" case
ctx2 = FakeCtx(store2)
L["setup"](ctx2)
check("重新 setup 能读回", ctx2.hist == [77, 70, 65], "hist=%s" % ctx2.hist)

print("\n[2] 环形缓冲不会无限增长")
ctx.hist = []
for i in range(HIST_N + 25):
    ctx.battery.percent = i % 101
    L["sample"](ctx)
check("样本数被截到 %d" % HIST_N, len(ctx.hist) == HIST_N,
      "实际 %d" % len(ctx.hist))

print("\n[3] 量程自动缩放 + 夹取")
ctx.hist = [80, 81, 79]
ctx.mode = L["MODE_PCT"]
lo, hi = L["graph_range"](ctx)
check("百分比模式量程含数据", lo <= 79 and hi >= 81, "%s..%s" % (lo, hi))
check("最小跨度不小于 4（小波动也看得见）", hi - lo >= 4, "跨度 %d" % (hi - lo))
check("百分比模式不会超出 0..100", lo >= 0 and hi <= 100, "%s..%s" % (lo, hi))

ctx.hist = [4010]
ctx.mode = L["MODE_MV"]
lo, hi = L["graph_range"](ctx)
check("毫伏模式最小跨度不小于 60", hi - lo >= 60, "跨度 %d" % (hi - lo))
check("单样本也能量程", lo < 4010 < hi, "%s..%s" % (lo, hi))

ctx.hist = []
lo, hi = L["graph_range"](ctx)
check("空历史返回默认量程", hi > lo, "%s..%s" % (lo, hi))

print("\n[4] y_of 落在绘图区内")
ctx.hist = [10, 50, 90]
lo, hi = L["graph_range"](ctx)
ys = [L["y_of"](v, lo, hi) for v in (lo, hi, 50)]
check("y 都在 bar 内",
      all(L["GY"] <= y <= L["GY"] + L["GH"] for y in ys), "%s" % ys)
check("值越大 y 越小（屏幕坐标）", L["y_of"](hi, lo, hi) < L["y_of"](lo, lo, hi))
check("量程退化时不除零", isinstance(L["y_of"](5, 5, 5), int))

print("\n[5] 按键：清空 / 切模式")
store3 = {}
ctx3 = FakeCtx(store3)
L["setup"](ctx3)
m0 = ctx3.mode
L["on_key"](ctx3, "down")
check("DOWN 切换模式", ctx3.mode != m0, "%s -> %s" % (m0, ctx3.mode))
L["teardown"](ctx3)
check("模式被写回 kv", store3.get("m") == ctx3.mode, "store=%s" % store3)
L["on_key"](ctx3, "up")
check("UP 清空历史", ctx3.hist == [], "hist=%s" % ctx3.hist)
L["on_key"](ctx3, "ok")
check("OK 立刻取一个样本", len(ctx3.hist) == 1, "hist=%s" % ctx3.hist)

# ======================================================================= pet
print("\n" + "=" * 62)
print("pet.py —— 能量模型与姿态")
print("=" * 62)

P = load("pet")
real_time_fn = real_time.time
NOW = [1700000000]


def fake_time():
    return NOW[0]


print("\n[1] 能量夹取与喂食")
store = {}
ctx = FakeCtx(store)
P["setup"](ctx)
check("初始能量在 0..100", 0 <= ctx.energy <= 100, "e=%d" % ctx.energy)

ctx.energy = 95
P["on_key"](ctx, "ok")
check("喂食后不超过 100", ctx.energy == 100, "e=%d" % ctx.energy)
check("喂食计数 +1", ctx.feeds == 1, "feeds=%d" % ctx.feeds)

ctx.energy = 3
P["on_key"](ctx, "down")
check("训斥后不低于 0", ctx.energy == 0, "e=%d" % ctx.energy)

store2 = {}
ctx2 = FakeCtx({"e": 999, "n": -5})
P["setup"](ctx2)
check("越界的持久化能量被夹取", 0 <= ctx2.energy <= 100, "e=%d" % ctx2.energy)

print("\n[2] 按真实时间衰减（关机期间也照掉）")
real_time.time = fake_time
try:
    NOW[0] = 1700000000
    store3 = {}
    ctx3 = FakeCtx(store3)
    P["setup"](ctx3)
    ctx3.energy = 90
    ctx3.dirty = True
    P["teardown"](ctx3)
    check("teardown 存了时间戳 t", isinstance(store3.get("t"), int),
          "store=%s" % store3)

    NOW[0] += 600                       # 关机 10 分钟
    ctx4 = FakeCtx(store3)
    P["setup"](ctx4)
    check("10 分钟掉 10 点（每 60 秒 1 点）", ctx4.energy == 80,
          "e=%d" % ctx4.energy)

    NOW[0] += 60 * 60 * 24              # 再关机一整天
    ctx5 = FakeCtx(store3)
    store3["e"] = 30
    P["setup"](ctx5)
    check("长时间关机后夹到 0", ctx5.energy == 0, "e=%d" % ctx5.energy)

    # RTC 没校时（time.time() 很小）不应该乱掉
    NOW[0] = 1000
    store4 = {"e": 55, "t": 1700000000}
    ctx6 = FakeCtx(store4)
    P["setup"](ctx6)
    check("RTC 未校时时不衰减", ctx6.energy == 55, "e=%d" % ctx6.energy)
    check("RTC 未校时时不写坏 t", "t" not in store4 or store4["t"] == 1700000000,
          "store=%s" % store4)
finally:
    real_time.time = real_time_fn

print("\n[3] 姿态计时与情绪")
ctx = FakeCtx()
P["setup"](ctx)
ctx.energy = 80
check("能量充足时是 HAPPY", P["mood_of"](ctx) == "HAPPY", P["mood_of"](ctx))
ctx.energy = 50
check("中等是 OK", P["mood_of"](ctx) == "OK", P["mood_of"](ctx))
ctx.energy = 25
check("偏低是 HUNGRY", P["mood_of"](ctx) == "HUNGRY", P["mood_of"](ctx))
ctx.energy = 5
check("极低是 STARVING", P["mood_of"](ctx) == "STARVING", P["mood_of"](ctx))

ctx.dirty = False
ctx.energy = 60
ctx.pose = P["POSE_SAD"]
ctx.pose_left = 3
for i in range(1, 40):
    ctx.frame = i * P["ANIM_DIV"]
    P["loop"](ctx)
check("姿态计时结束后回到 IDLE", ctx.pose == P["POSE_IDLE"],
      "pose=%s left=%s" % (ctx.pose, ctx.pose_left))

ctx.battery.percent = 10
ctx.pose_left = 0
for i in range(40, 80):
    ctx.frame = i * P["ANIM_DIV"]
    P["loop"](ctx)
check("低电量时变成 SLEEPY", ctx.pose == P["POSE_SLEEP"], "pose=%s" % ctx.pose)

print("\n[4] 只在改过时才写 kv")
store5 = {}
ctx = FakeCtx(store5)
P["setup"](ctx)
P["teardown"](ctx)
check("没改过就不写 kv", not store5, "store=%s" % store5)
P["on_key"](ctx, "ok")
P["teardown"](ctx)
check("改过就写 e/n", "e" in store5 and "n" in store5, "store=%s" % store5)

print("\n[5] 精灵始终画在框内")
ctx = FakeCtx()
P["setup"](ctx)
boxes = ((P["BOX_X"], P["BOX_Y"], P["BOX_W"], P["BOX_H"]),)
oob = []
for pose in (P["POSE_IDLE"], P["POSE_HAPPY"], P["POSE_SAD"], P["POSE_EAT"],
             P["POSE_SLEEP"]):
    for phase in range(24):
        ctx.lcd.rects = []
        P["draw_pet"](ctx, pose, phase)
        for (x, y, w, h) in ctx.lcd.rects:
            for (bx, by, bw, bh) in boxes:
                if x >= bx and y >= by and x + w <= bx + bw and y + h <= by + bh:
                    break
            else:
                oob.append((pose, phase, x, y, w, h))
check("五种姿态 x 24 帧全部落在精灵框内", not oob,
      "越界 %d 处，例如 %s" % (len(oob), oob[:2]))

print("\n" + "=" * 62)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 62)
sys.exit(1 if FAIL else 0)
