#!/usr/bin/env python3
"""Offline layout test for the PassportOS main menu (os/passport/ui.py).

Why this exists: the menu is pure geometry, and geometry bugs (a title running
under the size column, a row drawn off-screen) are invisible in every other
test - `_verify.py` only exercises mini-apps, and there is no way to screenshot
the panel from the host. So the drawing calls are recorded and the boxes are
compared numerically.

The Shell is built via __new__ (same trick test_display.py uses for Display) so
no SPI, BLE or ADC is touched.

    python tools/test_menu.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name +
          (("   " + extra) if extra and not cond else ""))


def install_stubs():
    """ui.py -> display/battery/buttons/blepush, so the host needs stand-ins for
    the MicroPython-only modules. Same approach as test_display.py."""
    import types
    mach = types.ModuleType("machine")

    class _Pin:
        def __init__(self, *a, **kw):
            pass

    class _SPI:
        def __init__(self, *a, **kw):
            pass

        def write(self, *a, **kw):
            pass

    class _PWM:
        def __init__(self, *a, **kw):
            pass

        def freq(self, *a):
            pass

        def duty_u16(self, *a):
            pass

    class _ADC:
        def __init__(self, *a, **kw):
            pass

    class _I2C:
        def __init__(self, *a, **kw):
            pass

    class _I2S:
        def __init__(self, *a, **kw):
            pass

    mach.Pin = _Pin
    mach.SPI = _SPI
    mach.PWM = _PWM
    mach.ADC = _ADC
    mach.I2C = _I2C
    mach.I2S = _I2S
    mach.I2S.TX = 1
    mach.I2S.RX = 2
    mach.I2S.MONO = 1
    mach.I2S.STEREO = 2
    sys.modules["machine"] = mach

    ble = types.ModuleType("bluetooth")
    ble.BLE = object
    sys.modules["bluetooth"] = ble

    # display.py guards the framebuf import, so leaving it out just disables text
    sys.modules.pop("framebuf", None)


install_stubs()


# --------------------------------------------------------------------- stubs
class RecLCD:
    """Records every drawing call with its bounding box."""

    w = 240
    h = 320

    def __init__(self):
        self.calls = []          # (kind, x, y, w, h, text_or_None)

    def _rect(self, kind, x, y, w, h):
        self.calls.append((kind, x, y, w, h, None))

    def fill(self, c):
        self.calls.append(("fill", 0, 0, self.w, self.h, None))

    def fill_rect(self, x, y, w, h, c):
        self._rect("fill_rect", x, y, w, h)

    def rect(self, x, y, w, h, c):
        self._rect("rect", x, y, w, h)

    def hline(self, x, y, w, c):
        self._rect("hline", x, y, w, 1)

    def vline(self, x, y, h, c):
        self._rect("vline", x, y, 1, h)

    def progress(self, x, y, w, h, pct, fg=None, bg=None):
        self._rect("progress", x, y, w, h)

    def text(self, s, x, y, c, bg=None):
        self.calls.append(("text", x, y, len(str(s)) * 8, 8, str(s)))

    def text_scale(self, s, x, y, c, bg=None, scale=1):
        self.calls.append(("text_scale", x, y, len(str(s)) * 8 * scale,
                           8 * scale, str(s)))

    def text2x(self, s, x, y, c, bg=None):
        self.text_scale(s, x, y, c, bg, 2)

    def text_center(self, s, y, c, bg=None, scale=1):
        x = (self.w - len(str(s)) * 8 * scale) // 2
        self.text_scale(s, x, y, c, bg, scale)

    def backlight(self, pct):
        pass

    def drop_text_cache(self):
        pass

    # --- helpers for assertions ---
    def texts(self):
        return [c for c in self.calls if c[0] in ("text", "text_scale")]

    def rowwise(self):
        """Group text calls by their y coordinate."""
        out = {}
        for kind, x, y, w, h, s in self.texts():
            out.setdefault(y, []).append((x, w, s))
        for v in out.values():
            v.sort()
        return out


class FakeLink:
    uploading = False
    connected = False

    def progress_key(self):
        return 0

    def status_text(self):
        return "idle"

    def send(self, obj):
        pass

    def log_line(self, msg):
        pass


class FakeBattery:
    ok = True
    percent = 85
    millivolts = 4085

    def label(self):
        return "85%"


def make_shell(apps, sel=0, scroll=0):
    from passport import ui as U
    sh = U.Shell.__new__(U.Shell)          # skip __init__: no SPI / BLE / ADC
    sh.lcd = RecLCD()
    sh.app_list = apps
    sh.sel = sel
    sh.scroll = scroll
    sh._status = None
    sh._status_sig = None
    sh._menu_dirty = True
    sh.link = FakeLink()
    sh.battery = FakeBattery()
    # The real shell clamps the window on every selection change; skipping this
    # made "select the last of 32" show row 8 instead of 32 (the test's fault,
    # not the app's).
    sh._clamp_scroll()
    return sh


def apps(n, size=15253, title=None):
    out = []
    for i in range(n):
        out.append({"n": "app%02d" % i,
                    "title": (title % i) if title else "App %d" % i,
                    "s": size})
    return out


def bounds_ok(lcd):
    bad = []
    for kind, x, y, w, h, s in lcd.calls:
        if w <= 0 or h <= 0:
            bad.append("%s size %r" % (kind, (w, h)))
        elif x < 0 or y < 0 or x + w > lcd.w or y + h > lcd.h:
            bad.append("%s x=%d y=%d w=%d h=%d" % (kind, x, y, w, h))
    return bad


def row_boxes(lcd):
    """For every menu row, return (index_text, title, size) with x spans.

    Rows are identified by the y of their three text calls. The index is the
    first 2-char text at x=8; the size is the right-aligned text.
    Only the list area counts - the header and the footer have text too, and the
    first version of this helper happily reported the footer as a row with all
    three columns missing.
    """
    from passport import ui as U
    nrows = (lcd.h - U.LIST_Y - U.FOOTER_H) // U.ROW_H
    want = set(U.LIST_Y + r * U.ROW_H for r in range(nrows))
    out = []
    for y, items in sorted(lcd.rowwise().items()):
        # text is drawn at row_y + 6, so map back before deciding it is a row
        if (y - 6) not in want:
            continue
        idx = [t for t in items if t[0] == 8 and len(t[2]) == 2
               and t[2].strip().isdigit()]
        title = [t for t in items if t[0] == 30]
        size = [t for t in items if t[0] >= 100 and t[2].endswith("K")]
        out.append((idx[0] if idx else None,
                    title[0] if title else None,
                    size[0] if size else None))
    return out


def drawn_row_ys(lcd):
    """The y of every row background that was painted in the list area."""
    from passport import ui as U
    nrows = (lcd.h - U.LIST_Y - U.FOOTER_H) // U.ROW_H
    want = [U.LIST_Y + r * U.ROW_H for r in range(nrows)]
    got = set()
    for kind, x, y, w, h, _s in lcd.calls:
        if kind == "fill_rect" and x == 0 and w == lcd.w and y in want:
            got.add(y)
    return want, got


# ---------------------------------------------------------------------- main
print("=" * 60)
print("主菜单布局：每个绘制都在屏内、且三栏不重叠")
print("=" * 60)

print("\n[1] 序号画出来了，而且是绝对位置")
sh = make_shell(apps(18), sel=0, scroll=0)
sh.draw_menu()
want_ys, got_ys = drawn_row_ys(sh.lcd)
check("所有可见行的背景都画了", set(want_ys) <= got_ys,
      "缺 %s" % sorted(set(want_ys) - got_ys))
rows = row_boxes(sh.lcd)
check("找到的行数 = 可见行数", len(rows) == len(want_ys),
      "%d vs %d" % (len(rows), len(want_ys)))
check("每行都有序号", all(r[0] for r in rows),
      "缺序号的行: %s" % [r for r in rows if not r[0]][:2])
check("第一行序号是 1", rows[0][0] and rows[0][0][2].strip() == "1",
      rows[0][0][2] if rows[0][0] else None)
check("序号逐行递增",
      [int(r[0][2]) for r in rows] == list(range(1, len(rows) + 1)),
      [r[0][2] for r in rows])
check("每行都有标题和尺寸", all(r[1] and r[2] for r in rows))

print("\n[2] 滚动之后序号跟着走（取绝对位置，不是行号）")
sh = make_shell(apps(18), sel=9, scroll=5)
sh.draw_menu()
rows = row_boxes(sh.lcd)
check("滚到第 6 个时首行显示 6", int(rows[0][0][2]) == 6, rows[0][0][2])

print("\n[3] 三栏不重叠")
sh = make_shell(apps(18), sel=3)
sh.draw_menu()
worst = None
for i, (idx, title, size) in enumerate(row_boxes(sh.lcd)):
    if not (idx and title and size):
        check("第 %d 行三栏齐全" % (i + 1), False, "%s" % ((idx, title, size),))
        continue
    gap_idx_title = title[0] - (idx[0] + idx[1])
    gap_title_size = size[0] - (title[0] + title[1])
    if worst is None or gap_title_size < worst[0]:
        worst = (gap_title_size, gap_idx_title, i + 1)
    if gap_idx_title < 0 or gap_title_size < 0:
        check("第 %d 行不重叠" % (i + 1), False,
              "序号->标题 %d, 标题->尺寸 %d" % (gap_idx_title, gap_title_size))
check("所有行都不重叠", worst is None or worst[0] >= 0,
      "最窄的标题->尺寸间距 %s" % (worst,))
print("      （最小的 标题->尺寸 间距 = %d px，序号->标题 = %d px）"
      % (worst[0], worst[1]) if worst else "")

print("\n[4] 长标题被截断，不会顶到尺寸栏")
sh = make_shell(apps(4, title="This Is A Very Long Mini App Title %d"))
sh.draw_menu()
ok_all = True
for i, (idx, title, size) in enumerate(row_boxes(sh.lcd)):
    if title[0] + title[1] > size[0]:
        ok_all = False
        check("长标题第 %d 行不越界" % (i + 1), False,
              "标题右边缘 %d > 尺寸左边缘 %d" % (title[0] + title[1], size[0]))
check("长标题全部被截断到安全宽度", ok_all)
check("截断长度是 20 个字符（160px）",
      all(t[1] == 160 for _i, t, _s in row_boxes(sh.lcd)),
      [t[1] for _i, t, _s in row_boxes(sh.lcd)][:3])

print("\n[5] 尺寸栏最宽的情况（9999K）也不重叠")
sh = make_shell(apps(3, size=9999 * 1024))
sh.draw_menu()
ok_all = True
for i, (idx, title, size) in enumerate(row_boxes(sh.lcd)):
    if title[0] + title[1] > size[0]:
        ok_all = False
        check("宽尺寸第 %d 行不重叠" % (i + 1), False,
              "标题右 %d vs 尺寸左 %d" % (title[0] + title[1], size[0]))
check("尺寸栏最宽时仍不重叠", ok_all)

print("\n[6] 边界情况")
for n, label in ((0, "空列表"), (1, "只有一个"), (32, "满额 32 个")):
    sh = make_shell(apps(n), sel=0, scroll=0)
    sh.draw_menu()
    bad = bounds_ok(sh.lcd)
    check("%s 每笔绘制都在屏内" % label, not bad, "; ".join(bad[:2]))
    if n:
        r = row_boxes(sh.lcd)
        check("%s 首行序号是 1" % label, r and int(r[0][0][2]) == 1)

sh = make_shell(apps(32), sel=31)
sh.draw_menu()
r = row_boxes(sh.lcd)
check("选中最后一个时序号正确", r and int(r[-1][0][2]) == 32,
      r[-1][0][2] if r else None)

print("\n[7] 反例：旧的几何确实会重叠（证明这个测试不是空的）")
# 旧代码：标题 26 字符、从 x=10 开始 -> 10+208 = 218，而尺寸栏最窄从 196 起
old_title_right = 10 + 26 * 8
size_left_wide = 240 - 8 * 5 - 4
check("旧的标题几何会撞上宽尺寸栏（所以这个测试测的是真东西）",
      old_title_right > size_left_wide,
      "旧右边缘 %d <= 尺寸左边缘 %d" % (old_title_right, size_left_wide))

print("\n" + "=" * 60)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
