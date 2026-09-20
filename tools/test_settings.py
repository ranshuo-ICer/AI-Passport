#!/usr/bin/env python3
"""全局设置与背光夹取的离线测试。

背光是**硬件状态**：小程序调完之后关机再开也得还在。为此新增了全局设置文件
`/settings.json`（见 os/passport/settings.py），Shell 开机时读它。这条链路有几个
必须钉住的点 —— 文件坏了不能让系统起不来、亮度不能调到看不见屏幕。

    python tools/test_settings.py
"""

import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


def install_stubs():
    """passport.ui 会一路 import 到 machine / bluetooth，主机上要顶一下。"""
    mach = types.ModuleType("machine")

    class _Any:
        def __init__(self, *a, **kw):
            pass

    class _PWM(_Any):
        def freq(self, *a):
            pass

        def duty(self, *a):
            pass

        def duty_u16(self, *a):
            pass

    mach.Pin = _Any
    mach.SPI = _Any
    mach.I2C = _Any
    mach.ADC = _Any
    mach.PWM = _PWM
    mach.I2S = _Any
    mach.I2S.TX = 1
    mach.I2S.RX = 2
    mach.I2S.MONO = 1
    mach.I2S.STEREO = 2
    sys.modules["machine"] = mach
    ble = types.ModuleType("bluetooth")
    ble.BLE = object
    sys.modules["bluetooth"] = ble
    sys.modules.pop("framebuf", None)


install_stubs()

from passport import config as C          # noqa: E402
from passport import settings             # noqa: E402
from passport.ui import read_backlight    # noqa: E402

tmpdir = tempfile.mkdtemp()
settings.PATH = os.path.join(tmpdir, "settings.json")


def fresh():
    settings.reset_cache()


print("=" * 60)
print("全局设置 /settings.json")
print("=" * 60)

print("\n[1] 读不到就是默认值，绝不影响开机")
fresh()
check("文件不存在 -> get 返回默认", settings.get("bl", 75) == 75)
check("文件不存在 -> load() 是空 dict", settings.load() == {})

with open(settings.PATH, "w") as f:
    f.write("{ this is not json")
fresh()
check("文件损坏 -> 退回默认值", settings.get("bl", 75) == 75,
      "get 返回 %r" % settings.get("bl", 75))

with open(settings.PATH, "w") as f:
    f.write("[1, 2, 3]")
fresh()
check("JSON 不是对象（是数组）-> 退回默认值", settings.get("bl", 75) == 75)

os.remove(settings.PATH)
fresh()

print("\n[2] 写盘：没改过就不写")
check("没改过 -> flush() 返回 False", settings.flush() is False)
settings.set("bl", 40)
check("有改动 -> flush() 返回 True", settings.flush() is True)
check("写完文件存在", os.path.exists(settings.PATH))
fresh()
check("重新读回来是 40", settings.get("bl") == 40, "%r" % settings.get("bl"))
check("再 flush 一次不再写", settings.flush() is False)

print("\n[3] 背光夹取（read_backlight 是 Shell 开机走的那条路）")


def backlight_with(value):
    fresh()
    if value is None:
        if os.path.exists(settings.PATH):
            os.remove(settings.PATH)
    else:
        with open(settings.PATH, "w") as f:
            f.write('{"bl": %r}' % (value,) if not isinstance(value, str)
                    else '{"bl": %s}' % value)
    return read_backlight()


check("没有设置 -> 默认 %d" % C.LCD_BL_DEFAULT,
      backlight_with(None) == C.LCD_BL_DEFAULT)
check("正常值 60 -> 60", backlight_with(60) == 60)
check("0 -> 抬到下限 %d（0 会让屏幕全黑，用户就找不回调高的路了）"
      % C.LCD_BL_MIN, backlight_with(0) == C.LCD_BL_MIN)
check("负数 -> 下限", backlight_with(-30) == C.LCD_BL_MIN)
check("超过 100 -> 100", backlight_with(160) == 100)
check("字符串数字 \"45\" -> 45", backlight_with('"45"') == 45)
check("乱七八糟的字符串 -> 默认值", backlight_with('"abc"') == C.LCD_BL_DEFAULT)
check("null -> 默认值", backlight_with("null") == C.LCD_BL_DEFAULT)
check("浮点 55.7 -> 55", backlight_with(55.7) == 55)
check("下限本身合法（不能把合法值也夹掉）",
      backlight_with(C.LCD_BL_MIN) == C.LCD_BL_MIN)

print("\n[4] System 小程序的亮度步进")


class FakeLCD:
    def __init__(self):
        self.pct = []

    def backlight(self, p):
        self.pct.append(p)

    def fill_rect(self, *a):
        pass

    def text(self, *a):
        pass

    def progress(self, *a):
        pass

    def vline(self, *a):
        pass

    def fill(self, *a):
        pass


def load_sysinfo():
    mod = {"__name__": "sysinfo_test"}
    path = os.path.join(ROOT, "os", "builtin", "sysinfo", "app.py")
    with open(path, encoding="utf-8") as f:
        exec(compile(f.read(), path, "exec"), mod)
    return mod


S = load_sysinfo()
fresh()


class Ctx:
    def __init__(self):
        self.lcd = FakeLCD()
        self.bl = C.LCD_BL_DEFAULT
        self.w = 240
        self.h = 320


ctx = Ctx()
check("调高到 100 封顶", S["apply"](ctx, 140) and ctx.bl == 100, "bl=%s" % ctx.bl)
n = len(ctx.lcd.pct)
S["apply"](ctx, 120)
check("已经在 100 时不再重复设背光", len(ctx.lcd.pct) == n)
check("一路按到底停在下限 %d（不会全黑）" % C.LCD_BL_MIN,
      (S["apply"](ctx, -50), ctx.bl == C.LCD_BL_MIN)[1], "bl=%s" % ctx.bl)
fresh()
check("两次改动都写进了全局设置", settings.get("bl") == C.LCD_BL_MIN,
      "store=%r" % settings.get("bl"))

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
