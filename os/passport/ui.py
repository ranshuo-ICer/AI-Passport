"""PassportOS 系统外壳：启动器 UI + 小程序运行时。

交互（只有三个键，别设计得太复杂）：
    菜单： UP/DOWN 移动光标，OK 启动
    运行中：按键原样交给小程序；长按 OK 超过 1.2 秒退回主菜单
"""

import gc
import json
import time

from . import apps
from . import display as disp
from . import config as C
from .audio import Audio
from .battery import Battery
from .blepush import AppLink
from .buttons import Buttons
from .display import Display

HEADER_H = 30
STATUS_Y = 30
STATUS_H = 16
LIST_Y = 48
ROW_H = 28
FOOTER_H = 22
LONG_PRESS_MS = 1200


class Ctx:
    """交给小程序的运行时上下文。"""

    def __init__(self, shell, name):
        self.shell = shell
        self.lcd = shell.lcd
        self.battery = shell.battery
        self.audio = shell.audio
        # 按键对象。给诊断类小程序用（读原始 ADC 毫伏，用于重标定
        # config.BTN_WINDOWS）—— 官方 C 固件的 demo_button 就是干这个的。
        self.buttons = shell.buttons
        self.w = shell.lcd.w
        self.h = shell.lcd.h
        self.name = name
        self.frame = 0
        self._exit = False
        self._kv = None
        self._kv_dirty = False

    # --- 控制 ---
    def log(self, msg):
        self.shell.link.log_line("[%s] %s" % (self.name, msg))

    def exit(self):
        self._exit = True

    @property
    def wants_exit(self):
        return self._exit

    # --- 掉电保持的小存储 ---
    def _kv_load(self):
        if self._kv is None:
            try:
                with open(apps.app_dir(self.name) + "/kv.json") as f:
                    self._kv = json.load(f)
            except (OSError, ValueError):
                self._kv = {}
        return self._kv

    def kv_get(self, key, default=None):
        return self._kv_load().get(key, default)

    def kv_set(self, key, value):
        self._kv_load()[key] = value
        self._kv_dirty = True

    def kv_flush(self):
        if self._kv is not None and self._kv_dirty:
            try:
                with open(apps.app_dir(self.name) + "/kv.json", "w") as f:
                    json.dump(self._kv, f)
            except OSError:
                pass
            self._kv_dirty = False


class _Canvas:
    """把 Display 的 fill_rect/text 转到一块 framebuf 上，签名保持一致。

    有了它，`_paint_row` 不用关心自己是在画屏幕还是在画离屏缓冲 ——
    同一份绘制代码两边都能跑，也就不会出现"合成版和直接版画得不一样"。
    """

    def __init__(self, fb):
        self.fb = fb

    def fill_rect(self, x, y, w, h, c):
        self.fb.fill_rect(x, y, w, h, c)

    def text(self, s, x, y, c, bg=0):
        if not s:
            return
        # Display.text 会用 bg 填背景，framebuf.text 不会 —— 这里补上
        self.fb.fill_rect(x, y, len(s) * 8, 8, 0 if bg is None else bg)
        self.fb.text(s, x, y, c)


class Shell:
    # 设成 True 就完全绕开整行离屏合成、直接画屏幕。
    # 用类属性而不是实例属性：测试和截图工具会用 `__new__` 造 Shell（跳过 __init__），
    # 类属性保证那种构造方式也能读到它。
    _direct_draw = False

    def __init__(self):
        self.lcd = Display(backlight=75)
        self.lcd.splash("PassportOS", "booting...")
        self.buttons = Buttons()
        self.battery = Battery()
        self.audio = None        # 在 run() 里初始化；失败也不影响系统启动
        self.link = AppLink(self)
        self.app_list = []
        self.sel = 0
        self.scroll = 0
        self._app = None          # {"name":..,"mod":..,"ctx":..}
        self._ok_since = None
        self._status = None
        self._status_sig = None   # draw_status 的廉价指纹，避免每 tick 拼串
        self._menu_dirty = True
        self._frames = 0
        self._last_key = None

    # ================================================================ 应用管理
    def refresh_apps(self):
        self.app_list = apps.list_apps()
        if self.sel >= len(self.app_list):
            self.sel = max(0, len(self.app_list) - 1)
        self._clamp_scroll()
        self._menu_dirty = True

    def _visible_rows(self):
        return (self.lcd.h - LIST_Y - FOOTER_H) // ROW_H

    def _clamp_scroll(self):
        rows = self._visible_rows()
        if self.sel < self.scroll:
            self.scroll = self.sel
        elif self.sel >= self.scroll + rows:
            self.scroll = self.sel - rows + 1
        if self.scroll < 0:
            self.scroll = 0

    def current_app(self):
        return self._app["name"] if self._app else None

    def launch(self, name):
        """启动小程序。成功返回 True。"""
        if not apps.valid_name(name):
            return False
        if self._app is not None:
            self.stop_app()
        # 载入前先把自己的缓存清空、再回收一次：菜单刚画完，文字帧缓冲缓存里
        # 全是中等大小的块，正好把堆切碎，让 read_source() 要不到一整块连续内存。
        # 实测不清这一步，启动器里载入 32 KB 程序会 MemoryError。
        self.lcd.drop_text_cache()
        gc.collect()
        try:
            mod = apps.load_module(name)
        except Exception as e:                            # noqa: BLE001
            self.lcd.fill(disp.BLACK)
            self.lcd.text("LOAD FAILED", 8, 8, disp.RED)
            msg = "%s: %s" % (type(e).__name__, e)
            for i in range(0, min(len(msg), 260), 26):
                self.lcd.text(msg[i:i + 26], 8, 30 + (i // 26) * 12,
                              disp.SILVER)
            self.lcd.text("long OK = back", 8, self.lcd.h - 16, disp.GREY)
            self.link.log_line("启动 %s 失败: %s" % (name, msg))
            self._app = {"name": name, "mod": None, "ctx": None, "error": True}
            return False

        ctx = Ctx(self, name)
        self._app = {"name": name, "mod": mod, "ctx": ctx}
        # 音频是外壳的共享单例（Ctx.audio = shell.audio），小程序只是借用。
        # 上一个程序在 teardown 里留下的静音位/0 音量必须在这里清掉，否则会一直
        # 传染给后面每个程序 —— 真机实测退出 Beats 后 REG31=0x60，全系统哑到重启。
        if self.audio and self.audio.ok:
            self.audio.reset_state()
        self.lcd.fill(disp.BLACK)
        self._call("setup", ctx)
        self.link.log_line("已启动 %s" % name)
        if self.link.connected:
            self.link.send({"t": "state", "app": name})
        return True

    def stop_app(self):
        app = self._app
        self._app = None
        if app and app.get("mod"):
            ctx = app["ctx"]
            try:
                fn = app["mod"].get("teardown")
                if fn:
                    fn(ctx)
            except Exception as e:                        # noqa: BLE001
                self.link.log_line("teardown 出错: %s" % e)
            try:
                ctx.kv_flush()
            except Exception:                             # noqa: BLE001
                pass
        self.link.log_line("已停止")
        if self.link.connected:
            self.link.send({"t": "state", "app": None})
        self._menu_dirty = True
        self._status = None

    def _call(self, name, *args):
        if not self._app or not self._app.get("mod"):
            return None
        fn = self._app["mod"].get(name)
        if fn is None:
            return None
        try:
            return fn(*args)
        except Exception as e:                            # noqa: BLE001
            self.link.log_line("%s() 出错: %s: %s"
                               % (name, type(e).__name__, e))
            return None

    # ================================================================ 界面
    def draw_menu(self):
        lcd = self.lcd
        # ⚠ 这里原来是一句整屏 `fill(disp.NAVY)`：153,600 字节、实测 42.7 ms，
        # 而这 42.7 ms 里屏幕上**就是一片纯色** —— 这正是"更新时闪"的主因。
        # 其实后面每一块都会铺满自己的区域，没人覆盖的只有三处窄带：
        # 状态栏与列表之间的 2 px、列表末尾到页脚之间、以及 app_list 为空时的列表区。
        # （每行底部的行间缝由 _draw_row 自己清，见那里。）
        lcd.fill_rect(0, STATUS_Y + STATUS_H, lcd.w,
                      LIST_Y - STATUS_Y - STATUS_H, disp.NAVY)
        rows = self._visible_rows()
        end_y = LIST_Y + rows * ROW_H
        tail_y = lcd.h - FOOTER_H
        if end_y < tail_y:
            lcd.fill_rect(0, end_y, lcd.w, tail_y - end_y, disp.NAVY)

        lcd.fill_rect(0, 0, lcd.w, HEADER_H, disp.DARK)
        lcd.text2x("Passport", 6, 7, disp.CYAN, disp.DARK)
        lcd.fill_rect(0, HEADER_H - 2, lcd.w, 2, disp.CYAN)

        lcd.fill_rect(0, STATUS_Y, lcd.w, STATUS_H, disp.BLACK)
        self._status = None
        self.draw_status(force=True)

        if not self.app_list:
            # 没有 _draw_row 去铺这个区域，这里必须自己清
            lcd.fill_rect(0, LIST_Y, lcd.w, end_y - LIST_Y, disp.NAVY)
            lcd.text("no mini-app yet", 12, LIST_Y + 20, disp.SILVER, disp.NAVY)
            lcd.text("push one over BLE", 12, LIST_Y + 34, disp.GREY, disp.NAVY)
        else:
            for i in range(rows):
                self._draw_row(i, self.scroll + i)

        lcd.fill_rect(0, lcd.h - FOOTER_H, lcd.w, FOOTER_H, disp.DARK)
        lcd.text("UP/DN move   OK run", 6, lcd.h - 15, disp.SILVER, disp.DARK)
        self._menu_dirty = False

    def _paint_row_for(self, idx):
        """把某个绝对索引画到它当前应在的那一行（不在可见范围内就什么都不做）。"""
        row = idx - self.scroll
        if 0 <= row < self._visible_rows() and idx < len(self.app_list):
            self._draw_row(row, idx)

    def _draw_row(self, row, idx):
        """画一行。优先**整行离屏合成后一次 blit**，拿不到缓冲才直接画屏幕。

        直接画屏幕时这一行是分几十次 SPI 写上去的（底色 → 行间缝 → 选中条 →
        序号 → 标题 → 尺寸），每一步都真的出现在屏上，肉眼就是"先空一块再长出
        字"。合成到一行缓冲里再一次推上去，这一行就是一瞬间换掉的。
        """
        lcd = self.lcd
        y = LIST_Y + row * ROW_H
        fb = None
        if not self._direct_draw:
            fb = lcd.row_fb(ROW_H)
        if fb is None:
            self._paint_row(lcd, y, idx, ROW_H)
            return
        fb.fill(disp.NAVY)                  # 行间缝的颜色，铺满整行
        self._paint_row(_Canvas(fb), 0, idx, ROW_H)
        lcd.blit_row(fb, y, ROW_H)

    def _paint_row(self, t, y, idx, row_h):
        """把第 idx 项画到目标 `t` 上，行顶为 `y`。t 可以是 Display 也可以是 _Canvas。"""
        w = self.lcd.w
        if idx >= len(self.app_list):
            t.fill_rect(0, y, w, row_h, disp.NAVY)
            return
        app = self.app_list[idx]
        selected = (idx == self.sel)
        bg = disp.TEAL if selected else disp.NAVY
        fg = disp.WHITE if selected else disp.SILVER
        t.fill_rect(0, y, w, row_h - 2, bg)
        if selected:
            t.fill_rect(0, y, 4, row_h - 2, disp.YELLOW)
        # 序号取的是**绝对位置** idx+1，不是屏幕行号 row+1 —— 列表滚动之后
        # 行号会从 1 重新开始，那样这个数字就没有意义了。
        # 用 %2d 右对齐，个位数和两位数的小数位能对齐，看起来是一列。
        t.text("%2d" % (idx + 1), 8, y + 6,
               disp.YELLOW if selected else disp.GREY, bg)
        title = app.get("title") or app["n"]
        # 标题区从 30 开始：左边让给 4px 选中条 + 16px 序号（8..24）。
        # 右边被尺寸文字占掉（最宽 "9999K" = 40px，从 x=196 起），
        # 所以标题最多 20 个字符（160px）—— 留一点余量，别贴上去。
        t.text(title[:20], 30, y + 6, fg, bg)
        size = "%dK" % max(1, app["s"] // 1024)
        t.text(size, w - 8 * len(size) - 4, y + 6,
               disp.YELLOW if selected else disp.GREY, bg)

    def draw_status(self, force=False):
        # 每 tick（50 Hz）都会被调一次，而绝大多数时候什么都没变。所以先比一个
        # 廉价指纹（几个属性 + link.progress_key()），只有它变了才去拼字符串 ——
        # 拼一次实测 157 µs，纯浪费。
        # ⚠ 指纹必须覆盖下面读到的每个字段；漏一个的状态栏就会卡住不刷新。
        # 用 `_status is not None` 做前置条件，是为了让"把 _status 置 None 来强制
        # 重绘"的既有写法继续生效（否则会卡在指纹相等上，永远不重绘）。
        sig = (self.link.uploading, self.link.connected, self.battery.ok,
               self.battery.percent, self.battery.millivolts,
               self.link.progress_key())
        if not force and self._status is not None and sig == self._status_sig:
            return
        self._status_sig = sig

        text = ""
        if self.link.uploading:
            text = self.link.status_text()
        elif self.link.connected:
            text = "BLE connected"
        else:
            text = "BLE: " + C.BLE_NAME
        text = text + "   BAT " + self.battery.label()
        if not force and text == self._status:
            return
        self._status = text
        self.lcd.fill_rect(0, STATUS_Y, self.lcd.w, STATUS_H, disp.BLACK)
        color = disp.GREEN if self.link.connected else disp.SILVER
        self.lcd.text(text[:29], 4, STATUS_Y + 4, color, disp.BLACK)

    # ================================================================ 主循环
    def tick(self):
        self._frames += 1
        self.link.poll()
        self.battery.poll()

        key = None
        if self._frames % C.BTN_POLL_DIV == 0:
            # 按键 ADC 是每 tick 最贵的一项（update() 实测约 300 µs，其中单次
            # 读取就 61 µs × 4 次采样），而消抖只要求"连续两次一致" —— 所以
            # 隔 tick 读一次完全够用，采样率仍有 25 Hz。
            # 跳过的 tick 里 key 保持 None（= 没有新按下），held 继续用上一次
            # 消抖结果，长按计时不受影响。
            key = self.buttons.update()
        held = self.buttons.current()

        # 长按 OK 计时
        if held == "ok":
            if self._ok_since is None:
                self._ok_since = time.ticks_ms()
        else:
            self._ok_since = None

        if self._app is not None:
            self._tick_app(key, held)
        else:
            self._tick_menu(key)

        if self.link.connected and key is not None:
            self.link.send({"t": "key", "k": key})

        # 上传进度需要实时刷新状态条
        if self._app is None:
            self.draw_status()

        if self._frames % 300 == 0:
            gc.collect()

    def _tick_menu(self, key):
        if key in ("up", "down") and self.app_list:
            old_sel = self.sel
            old_scroll = self.scroll
            if key == "up":
                self.sel = (self.sel - 1) % len(self.app_list)
            else:
                self.sel = (self.sel + 1) % len(self.app_list)
            self._clamp_scroll()
            if self.scroll == old_scroll:
                # 只重画受影响的两行。
                # 原来这里走整屏 draw_menu()：实测写 316 KB（两屏的像素）、
                # 412 次 SPI 写、189 ms，其中头 153 KB 还是纯色空白 —— 屏上就是
                # "全屏闪一下再重画"。移动选择其实只改了两行的底色和文字。
                self._paint_row_for(old_sel)
                self._paint_row_for(self.sel)
                self._menu_dirty = False
                return
            # 滚动发生了 → 整个列表的内容都换了位置，只能整屏重画
            self._menu_dirty = True
        elif key == "ok" and self.app_list:
            self.launch(self.app_list[self.sel]["n"])
            return
        if self._menu_dirty:
            self.draw_menu()

    def _tick_app(self, key, held):
        # 长按 OK 退出
        if held == "ok" and self._ok_since is not None:
            if time.ticks_diff(time.ticks_ms(), self._ok_since) > LONG_PRESS_MS:
                self.stop_app()
                self.draw_menu()
                return

        app = self._app
        if app is None or app.get("error"):
            if key is not None:                 # 报错页：任意键返回
                self.stop_app()
                self.draw_menu()
            return

        ctx = app["ctx"]
        if key is not None:
            self._call("on_key", ctx, key)
        ctx.frame += 1
        self._call("loop", ctx)
        if ctx.wants_exit:
            self.stop_app()
            self.draw_menu()

    # ================================================================ 启动
    def run(self):
        self.battery.init_profile()
        try:
            self.link.start()
        except Exception as e:                            # noqa: BLE001
            self.link.log_line("BLE 启动失败: %s" % e)
        self._init_audio()
        self.refresh_apps()
        self.lcd.fill(disp.NAVY)
        self.draw_menu()
        self.link.log_line("PassportOS 就绪")
        while True:
            self.tick()
            time.sleep_ms(20)

    def _init_audio(self):
        """音频是可选能力：初始化失败不能让整个系统起不来。"""
        try:
            self.audio = Audio()
        except Exception as e:                            # noqa: BLE001
            self.audio = None
            self.link.log_line("音频不可用: %s" % e)
            return
        if self.audio.ok:
            self.link.log_line("音频就绪 ES8311 @%dHz" % self.audio.rate)
            try:
                self.audio.set_volume(70)
                self.audio.tone(880, 90)
                time.sleep_ms(60)
                self.audio.tone(1320, 110)
            except Exception as e:                        # noqa: BLE001
                self.link.log_line("开机提示音失败: %s" % e)
        else:
            self.link.log_line("音频初始化失败: %s" % self.audio.error)
