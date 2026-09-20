"""设备端截图：把屏幕内容录下来，存成 FAP_SCREENSHOT_V1 格式的文件。

为什么这么绕：PassportOS 的 Display 是**直接写 SPI 的，没有帧缓冲**，而这块板
（无 PSRAM、可用堆约 110 KB）根本放不下 240x320x2 = 153,600 字节的整屏缓冲 ——
社区归档里 y2lin 的经验条目记着同一件事：220 KB 空闲时仍然拿不到一块连续的
153,600 字节。

做法分两步：

1. **录显示列表**。把 ctx.lcd 换成一个只记录不绘制的 CaptureLCD，正常跑一遍
   小程序的 setup/loop/on_key，于是拿到"最后一帧"要画的所有调用。
   遇到 `fill()`（整屏填充）就把列表清空重来 —— 各家的 draw 都以它开头，
   所以列表里留下的正好是最后那个完整帧，不会带上之前几十帧的历史。
2. **按带重放**。把真实 Display 的 spi/dc/cs 换成录制器，40 行一条带反复重放
   显示列表，每条带只要 240*40*2 = 19,200 字节。录制器解析 set_window
   （0x2A/0x2B/0x2C）算出窗口，再把后续像素落到带缓冲里。

产出文件格式与社区发布流程要求的 `FAP_SCREENSHOT_V1` 一致：
    FAP_SCREENSHOT_V1 240 320 RGB565LE 153600\\n
后接 153,600 字节小端 RGB565。

    python -m mpremote connect COM3 run tools/hw_screenshot.py
    python tools/screenshot.py COM3 --dir shots     # 电脑侧：拉回来 + 转 PNG
"""

import gc
import os

SCREEN_W = 240
SCREEN_H = 320
BAND_ROWS = 40
BAND_BYTES = SCREEN_W * BAND_ROWS * 2

_CMD_X = 0x2A
_CMD_Y = 0x2B
_CMD_RAMWR = 0x2C

# 头部宽度固定，最后回填长度；编码写成 RGB565LE-RLE。
# 为什么压：`mpremote fs cp` 实测只有 **2.7 KB/s**（2 号那条经验：把 payload 切成
# 小块走 REPL，来回太多），一张 153,642 字节的整屏图要 **57 秒**，20 张就是 19 分钟。
# 而屏幕大片是纯色，RLE 之后通常只剩几 KB —— 快一两个数量级。
# 编码名带 -RLE 是**显式声明**，不是偷偷改协议；主机侧两种都能解。
HDR = b"FAP_SCREENSHOT_V1 %d %d RGB565LE-RLE %010d\n"
RLE_BUF = 768


class RleWriter:
    """把 (计数, 像素) 对攒进一个小缓冲，满了就写文件。

    不按最坏情况（1.5 字节/像素）预留大块内存 —— 这块板没有 PSRAM，
    而这种图实际压完只有几 KB。
    """

    def __init__(self, f):
        self.f = f
        self.buf = bytearray(RLE_BUF)
        self.n = 0
        self.total = 0

    def _flush(self):
        if self.n:
            self.f.write(memoryview(self.buf)[:self.n])
            self.total += self.n
            self.n = 0

    def pair(self, cnt, v):
        if self.n + 3 > RLE_BUF:
            self._flush()
        b = self.buf
        b[self.n] = cnt
        b[self.n + 1] = v & 0xFF
        b[self.n + 2] = v >> 8
        self.n += 3

    def close(self):
        self._flush()
        return self.total


def rle_band(buf, n, w):
    """把带缓冲（小端 RGB565）按游程编码喂给 w。"""
    i = 0
    last = -1
    cnt = 0
    while i < n:
        v = buf[i] | (buf[i + 1] << 8)
        if v == last and cnt < 255:
            cnt += 1
        else:
            if cnt:
                w.pair(cnt, last)
            last = v
            cnt = 1
        i += 2
    if cnt:
        w.pair(cnt, last)

# 显示列表的安全上限：正常一帧只有几十条，超过说明这个 draw 不以 fill() 开头
OPS_CAP = 2000

DRAW_METHODS = ("fill", "fill_rect", "hline", "vline", "rect", "text",
                "text_scale", "text2x", "text_center", "progress", "blit")


class Recorder:
    """顶替 Display 的 spi + dc + cs，把像素录进带缓冲。"""

    def __init__(self):
        self.buf = bytearray(BAND_BYTES)
        self.zero = bytes(64)
        self.band_y0 = 0
        self.band_y1 = 0
        self.dc_state = 0
        self.cmd = 0
        self.pixmode = False
        self.pending = bytearray(4)
        self.pn = 0
        self.x0 = 0
        self.x1 = 0
        self.cx = 0
        self.cy = 0
        self.pixels = 0

    # Display 把这两个当函数调用（原来是 Pin 对象，可调用）
    def cs(self, v):
        pass

    def dc(self, v):
        self.dc_state = v

    def arm(self, y0, y1):
        self.band_y0 = y0
        self.band_y1 = y1
        i = 0
        z = self.zero
        while i < BAND_BYTES:
            self.buf[i:i + 64] = z
            i += 64
        self.pixmode = False
        self.cmd = 0
        self.pn = 0
        self.pixels = 0

    def write(self, b):
        if self.dc_state == 0:
            c = b[0] if len(b) else 0
            self.cmd = c
            self.pn = 0
            self.pixmode = (c == _CMD_RAMWR)
            return
        if self.pixmode:
            self._pixels(b)
            return
        if self.cmd == _CMD_X or self.cmd == _CMD_Y:
            n = len(b)
            self.pending[self.pn:self.pn + n] = b
            self.pn += n
            if self.pn >= 4:
                a = (self.pending[0] << 8) | self.pending[1]
                z = (self.pending[2] << 8) | self.pending[3]
                if self.cmd == _CMD_X:
                    self.x0 = a
                    self.x1 = z
                    self.cx = a
                else:
                    self.cx = self.x0
                    self.cy = a
                self.pn = 0
            return

    def _pixels(self, b):
        n = len(b) >> 1
        if n <= 0:
            return
        buf = self.buf
        by0 = self.band_y0
        by1 = self.band_y1
        x0 = self.x0
        x1 = self.x1
        cx = self.cx
        cy = self.cy
        i = 0
        while i < n:
            left = x1 - cx + 1
            take = n - i
            if take > left:
                take = left
            if by0 <= cy < by1:
                off = ((cy - by0) * SCREEN_W + cx) * 2
                buf[off:off + take * 2] = b[i * 2:(i + take) * 2]
                self.pixels += take
            i += take
            cx += take
            if cx > x1:
                cx = x0
                cy += 1
        self.cx = cx
        self.cy = cy

    def swap16(self):
        """面板要高位在前，而 FAP_SCREENSHOT_V1 要小端 —— 就地翻一次。"""
        buf = self.buf
        i = 0
        while i < BAND_BYTES:
            t = buf[i]
            buf[i] = buf[i + 1]
            buf[i + 1] = t
            i += 2


REC = Recorder()


class CaptureLCD:
    """只记录绘制调用，不碰硬件。"""

    def __init__(self):
        self.w = SCREEN_W
        self.h = SCREEN_H
        self.ops = []
        self.overflow = False

    def _add(self, name, args):
        if len(self.ops) >= OPS_CAP:
            self.overflow = True
            return
        self.ops.append((name, args))

    def fill(self, c):
        # 整屏填充 = 一帧的开头，把它之前的都丢掉，列表里就只剩最后一帧
        self.ops = [("fill", (c,))]

    def fill_rect(self, x, y, w, h, c):
        self._add("fill_rect", (x, y, w, h, c))

    def hline(self, x, y, w, c):
        self._add("hline", (x, y, w, c))

    def vline(self, x, y, h, c):
        self._add("vline", (x, y, h, c))

    def rect(self, x, y, w, h, c):
        self._add("rect", (x, y, w, h, c))

    def text(self, s, x, y, c, bg=None):
        self._add("text", (s, x, y, c, bg))

    def text_scale(self, s, x, y, c, bg=None, scale=1):
        self._add("text_scale", (s, x, y, c, bg, scale))

    def text2x(self, s, x, y, c, bg=None):
        self._add("text2x", (s, x, y, c, bg))

    def text_center(self, s, y, c, bg=None, scale=1):
        self._add("text_center", (s, y, c, bg, scale))

    def progress(self, x, y, w, h, pct, fg=None, bg=None):
        self._add("progress", (x, y, w, h, pct, fg, bg))

    def blit(self, buf, x, y, w, h, swap=True):
        self._add("blit", (buf, x, y, w, h, swap))

    # 非绘制类：直接忽略
    def backlight(self, pct):
        pass

    def drop_text_cache(self):
        pass

    def row_fb(self, height):
        # 返回 None = 走直接画屏幕那条路，于是每一笔都进显示列表。
        # 两条路共用同一个 _paint_row()，像素等价性由 hw_menu_bench.py 逐字节验证。
        return None

    def blit_row(self, fb, y, height):
        pass

    def set_window(self, *a):
        pass

    def splash(self, *a):
        pass


def install(lcd):
    lcd.spi = REC
    lcd.dc = REC.dc
    lcd.cs = REC.cs
    lcd._fill_cache.clear()
    lcd._fill_bytes = 0


def replay(lcd, ops):
    for name, args in ops:
        getattr(lcd, name)(*args)


def shoot(lcd, ops, path):
    """按带重放显示列表，写成一个 FAP_SCREENSHOT_V1（RLE 载荷）文件。"""
    install(lcd)
    gc.collect()
    with open(path, "wb") as f:
        f.write(HDR % (SCREEN_W, SCREEN_H, 0))      # 长度先占位，最后回填
        w = RleWriter(f)
        y = 0
        while y < SCREEN_H:
            y1 = y + BAND_ROWS
            if y1 > SCREEN_H:
                y1 = SCREEN_H
            REC.arm(y, y1)
            replay(lcd, ops)
            REC.swap16()                            # 面板是大端，存档要小端
            rle_band(REC.buf, SCREEN_W * (y1 - y) * 2, w)
            y = y1
        total = w.close()
        f.seek(0)
        f.write(HDR % (SCREEN_W, SCREEN_H, total))
    return total


# ------------------------------------------------------------------ 视图
class _Link:
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


class ScriptedButtons:
    """替掉真按键，好把"按住/松开"也演出来。

    不少小程序的核心状态靠 `buttons.current()` 判断按住（转盘、宠物、
    长按调速），光调 on_key 是截不到那些画面的。
    """

    def __init__(self, real):
        self.real = real
        self.held = None
        self.mv = 2900

    def current(self):
        return self.held

    def voltage(self):
        return self.mv

    def check(self):
        return (self.mv, None)

    def update(self):
        return None


# 每个小程序想截的那一瞬间。条目是 ("press", 键, 帧数) 或 ("hold", 键, 帧数)。
# hold 结束后会自动松开并再跑几帧 —— 转盘就是靠"松开"才落定的。
SCRIPTS = {
    "roulette": (("hold", "up", 30), ("hold", "down", 24)),
    "pet": (("press", "ok", 6), ("hold", "up", 6)),
    "stardex": (("press", "down", 4), ("press", "down", 4)),
    "battlog": (("press", "down", 6), ("press", "ok", 6)),
    "wave": (("press", "ok", 6), ("press", "up", 6)),
    "btnlab": (("hold", "up", 8), ("press", "ok", 8)),
    "beats": (("press", "ok", 8), ("press", "up", 8)),
    "metronome": (("press", "up", 8), ("press", "up", 8)),
    "snake": (("press", "ok", 10), ("press", "up", 10)),
    "memory": (("press", "ok", 10), ("press", "ok", 10)),
    "timer": (("press", "ok", 10), ("press", "down", 10)),
    "reaction": (("press", "ok", 10), ("press", "up", 10)),
    "dice": (("press", "ok", 10), ("press", "up", 10)),
    "muyu": (("press", "ok", 10), ("press", "up", 10)),
    "muyu": (("press", "ok", 10), ("press", "up", 10)),
    "clock": (("press", "ok", 6),),
    "sound": (("press", "ok", 6), ("press", "up", 6)),
    "sysinfo": (("press", "down", 4), ("press", "down", 4)),
    "probe": (("press", "ok", 4),),
}
DEFAULT_SCRIPT = (("press", "ok", 4), ("press", "up", 10))


def make_shell():
    """用**真实的** Shell 构造：它只建对象、不起 BLE（那是 run() 的事），
    所以比手工拼一串字段更可信 —— 连 link/battery/buttons 都是真的。"""
    from passport.ui import Shell
    from passport.audio import Audio
    sh = Shell()
    try:
        sh.battery.poll(force=True)      # 不然状态栏会显示 BAT 0mV
    except Exception:                                     # noqa: BLE001
        pass
    # run() 才会做音频初始化。sentry / repeater 要把共享 Audio deinit 掉再重建，
    # 没有这个真实例，那条最危险的路径就测不到。
    try:
        sh.audio = Audio()
    except Exception:                                     # noqa: BLE001
        sh.audio = None
    return sh


def audio_ok(sh):
    return bool(sh.audio is not None and sh.audio.ok)


def capture_menu(sh, sel, scroll, cap):
    from passport import apps as A
    sh.lcd = cap
    sh.app_list = A.list_apps()
    sh.sel = sel
    sh.scroll = scroll
    sh._clamp_scroll()
    sh.draw_menu()


def capture_app(sh, name, cap):
    """按脚本演一遍按住/按下，让小程序走到想截的那一瞬间。"""
    from passport import apps as A
    from passport.ui import Ctx
    sh.lcd = cap
    real_btns = sh.buttons
    sh.buttons = ScriptedButtons(real_btns)
    mod = A.load_module(name)
    ctx = Ctx(sh, name)
    ctx.frame = 0
    if mod.get("setup"):
        mod["setup"](ctx)

    def ticks(n):
        for _ in range(n):
            ctx.frame += 1
            if mod.get("loop"):
                mod["loop"](ctx)

    for item in SCRIPTS.get(name, DEFAULT_SCRIPT):
        kind, key, frames = item
        if kind == "press":
            if mod.get("on_key"):
                mod["on_key"](ctx, key)
            ticks(frames)
        else:
            sh.buttons.held = key
            ticks(frames)
            sh.buttons.held = None
            ticks(6)                      # 松开：转盘就是在这里落定的

    if mod.get("teardown"):
        mod["teardown"](ctx)
    sh.buttons = real_btns
    del mod, ctx
    gc.collect()


def read_spec():
    """读 `/shot_spec.txt` 决定只录哪些视图（空/不存在 = 全都录）。

    为什么要传 spec 而不是在主机侧筛：**录制本身才是大头** —— 20 个视图要跑
    20 遍 setup/loop/按键，约 90 秒；主机侧筛只是少拉几个文件，白跑的时间一点
    没省。所以筛选条件必须送到设备端来。
    """
    try:
        with open("/shot_spec.txt") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def wanted(spec, name):
    if spec is None:
        return True
    return any(w in name for w in spec)


def main():
    from passport import apps as A

    spec = read_spec()
    sh = make_shell()
    lcd = sh.lcd
    jobs = []

    for name, sel, scroll in (("menu-top", 0, 0), ("menu-mid", 11, 6)):
        if not wanted(spec, name):
            continue
        cap = CaptureLCD()
        capture_menu(sh, sel, scroll, cap)
        jobs.append((name, cap.ops, cap.overflow, "-"))

    names = [a["n"] for a in A.list_apps()]
    for n in names:
        if not wanted(spec, "app-" + n):
            continue
        cap = CaptureLCD()
        try:
            capture_app(sh, n, cap)
            # 顺手体检：sentry / repeater 会把共享 Audio deinit 掉再重建，
            # 重建失败就是"退出后全系统哑掉"（known-issues #25 那一类）。
            health = "audio ok" if audio_ok(sh) else "audio DEAD"
            jobs.append(("app-" + n, cap.ops, cap.overflow, health))
        except Exception as exc:                              # noqa: BLE001
            import sys
            sys.print_exception(exc)
            print("  !! %s 捕获失败: %s" % (n, exc))

    sh.lcd = lcd
    print("=" * 58)
    for name, ops, overflow, health in jobs:
        path = "/shot_%s.fap" % name
        nbytes = shoot(lcd, ops, path)
        size = os.stat(path)[6]
        print("  %-16s %4d 条绘制  %7d B(压后)  %-10s%s"
              % (name, len(ops), nbytes, health,
                 "  [列表溢出!]" if overflow else ""))
        gc.collect()
    print("=" * 58)
    print("共 %d 张，在设备根目录 /shot_*.fap" % len(jobs))
    # 机器可读的清单：主机侧据此在**一条会话里**把图拉回去，
    # 不用再单独连一次设备问"有哪些文件"
    print("SHOTS:" + ",".join("shot_%s.fap" % name for name, _o, _f, _h in jobs))

    # 跑 System 时按了 DOWN 调暗屏幕，这里把背光恢复成"设置里记的那个值"，
    # 免得截图跑完设备停在暗屏上、和 /settings.json 还对不上。
    from passport.ui import read_backlight
    bl = read_backlight()
    lcd.backlight(bl)
    print("背光已恢复为设置里的 %d%%" % bl)


main()
