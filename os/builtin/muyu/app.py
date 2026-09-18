"""木鱼 —— 敲一下，功德 +1。

按键：
    OK    敲一下
    UP    切换 累计 / 今日
    DOWN  静音开关
    （长按 OK 退出回主菜单，这是系统行为）

数据用 ctx.kv_* 掉电保持：
    total  累计次数 —— 永不重置
    today  今日次数 —— 跨天自动归零（需要先在 App 里对过时）
    day    上次记账的日期
    view   上次看的是累计还是今日
    muted  静音开关

设计说明：
  * 屏幕只有 8x8 ASCII 点阵字体，**显示不了中文**，所以界面文案用英文。
  * 木鱼本体是一个"逐行填充 + 竖向渐变"的圆：一共 2r+1 次 fill_rect，
    一次性画好不再重画。敲击反馈只重画计数器那一小块和三条冲击线，
    保证按下去是"跟手"的（每次敲击只产生个位数次 SPI 事务）。
  * 音效只用 ctx.audio.tone()：两声短促的音，模拟"笃"的一下。
    真木鱼的木质瞬态还原不了，但节奏感在。
  * 每次敲击都写盘太费 Flash，所以每 FLUSH_EVERY 次落一次盘；
    退出时 teardown() 会强制写一次，断电最多丢 4 下。
"""

import math
import time

TITLE = "Mu Yu"

# ---------------------------------------------------------------- 配色 RGB565
BG = 0x0000
BAR = 0x18E3          # 顶栏 / 底栏
SEP = 0x39E7          # 分隔线
SLIT = 0x18C3         # 鱼口
GOLD = 0xFE60         # 计数（常态）
GOLD_HI = 0xFFF0      # 计数（敲击瞬间）
ACCENT = 0x07FF
DIM = 0x8410
WARN = 0xF800


def _rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# 木色：上方受光、下方背光，8 级渐变
_TOP = (208, 158, 104)
_BOT = (72, 46, 28)
WOOD = tuple(_rgb(_TOP[0] + (_BOT[0] - _TOP[0]) * k // 7,
                  _TOP[1] + (_BOT[1] - _TOP[1]) * k // 7,
                  _TOP[2] + (_BOT[2] - _TOP[2]) * k // 7) for k in range(8))
HILITE = _rgb(232, 198, 156)

# ---------------------------------------------------------------- 版面
TITLE_H = 21
VIEW_Y = 28
FISH_CX = 120
FISH_CY = 146
FISH_R = 54
COUNT_Y = 212
COUNT_H = 44
PLUS_X = FISH_CX + FISH_R - 6
PLUS_Y = FISH_CY - FISH_R + 6
FOOT_Y = 298

FLASH_MS = 90          # 敲击高亮持续
FLUSH_EVERY = 5        # 每敲几下写一次盘


# ================================================================ 绘图工具
def _isqrt(n):
    """MicroPython 的 math 不保证有 isqrt，这里手写一个稳妥的。

    用 math.sqrt 起步再微调，避免浮点边界把结果算少 1。
    """
    if n <= 0:
        return 0
    x = int(math.sqrt(n))
    while x > 0 and x * x > n:
        x -= 1
    while (x + 1) * (x + 1) <= n:
        x += 1
    return x


def _fill_circle(lcd, cx, cy, r, shades=None, color=None):
    """逐行填充圆。shades 非空时按 y 做竖向渐变（看着像球）。"""
    n = len(shades) if shades else 1
    for dy in range(-r, r + 1):
        dx = _isqrt(r * r - dy * dy)
        c = shades[(dy + r) * n // (2 * r + 1)] if shades else color
        lcd.fill_rect(cx - dx, cy + dy, 2 * dx + 1, 1, c)


# ================================================================ 界面
def _draw_frame(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, TITLE_H, BAR)
    lcd.text2x("MU YU", 8, 3, ACCENT, BAR)
    lcd.fill_rect(0, TITLE_H, ctx.w, 1, SEP)

    lcd.fill_rect(0, ctx.h - 22, ctx.w, 22, BAR)
    lcd.text("OK hit  UP view", 6, ctx.h - 16, DIM, BAR)
    lcd.text("DN mute", ctx.w - 7 * 8 - 6, ctx.h - 16, DIM, BAR)


def _draw_view(ctx):
    """左上角那一行：当前看的是累计还是今日 + 静音状态 + 电量。"""
    lcd = ctx.lcd
    lcd.fill_rect(0, VIEW_Y - 2, ctx.w, 14, BG)

    label = "TOTAL" if ctx.view == "total" else "TODAY"
    lcd.text(label, 8, VIEW_Y, ACCENT if ctx.view != "total" else GOLD, BG)

    if ctx.muted:
        lcd.text("MUTED", 88, VIEW_Y, WARN, BG)

    bat = ctx.battery.label()
    lcd.text(bat, ctx.w - 8 * len(bat) - 8, VIEW_Y, DIM, BG)


def _draw_fish(ctx):
    """一次性画好木鱼本体，之后不再重画。"""
    lcd = ctx.lcd
    _fill_circle(lcd, FISH_CX, FISH_CY, FISH_R, WOOD)
    # 鱼口：一条横向的暗缝
    sw = FISH_R * 3 // 2
    sh = max(5, FISH_R // 7)
    lcd.fill_rect(FISH_CX - sw // 2, FISH_CY + FISH_R // 5, sw, sh, SLIT)
    # 左上高光
    _fill_circle(lcd, FISH_CX - FISH_R // 3, FISH_CY - FISH_R // 3,
                 FISH_R // 5, None, HILITE)


def _draw_counter(ctx, hot):
    lcd = ctx.lcd
    n = ctx.today if ctx.view == "today" else ctx.total
    s = "%d" % n
    # 位数多了自动缩字号，免得超出 240 宽
    scale = 5 if len(s) <= 4 else (4 if len(s) <= 6 else 3)
    h = 8 * scale
    y = COUNT_Y + (COUNT_H - h) // 2
    lcd.fill_rect(0, COUNT_Y, ctx.w, COUNT_H, BG)
    lcd.text_center(s, y, GOLD_HI if hot else GOLD, BG, scale)


def _marks(ctx, color):
    """敲击的三条冲击线（在木鱼左侧）。3 次 fill_rect，几乎不耗时。"""
    lcd = ctx.lcd
    x = FISH_CX - FISH_R - 14
    lcd.fill_rect(x, FISH_CY - 26, 10, 3, color)
    lcd.fill_rect(x + 4, FISH_CY - 10, 8, 3, color)
    lcd.fill_rect(x + 8, FISH_CY + 6, 6, 3, color)


# ================================================================ 数据
def _today_str():
    """RTC 没对过时返回 None（那时"今日"退化为本次开机累计）。"""
    t = time.localtime()
    if t[0] < 2020:
        return None
    return "%04d-%02d-%02d" % (t[0], t[1], t[2])


def _roll_day(ctx):
    """跨天就把"今日"清零。返回 True 表示确实翻了篇。"""
    d = _today_str()
    if d is None or d == ctx.day:
        return False
    ctx.day = d
    ctx.today = 0
    ctx.kv_set("day", d)
    ctx.kv_set("today", 0)
    return True


def _knock(ctx):
    """敲击音：两声短音，音高随次数轻微浮动，听起来不那么机械。"""
    a = ctx.audio
    if ctx.muted or a is None or not a.ok:
        return
    try:
        base = 1150 + (ctx.total % 5) * 18
        a.tone(base, 34)
        a.tone(base * 2 // 3, 26)
    except Exception:
        pass


def _save(ctx):
    ctx.kv_set("total", ctx.total)
    ctx.kv_set("today", ctx.today)
    ctx.kv_flush()


# ================================================================ 生命周期
def setup(ctx):
    ctx.muted = bool(ctx.kv_get("muted", 0))
    ctx.view = ctx.kv_get("view", "total")
    if ctx.view not in ("total", "today"):
        ctx.view = "total"
    ctx.total = int(ctx.kv_get("total", 0))
    ctx.today = int(ctx.kv_get("today", 0))
    ctx.day = ctx.kv_get("day", "")
    _roll_day(ctx)

    ctx.hot = False
    ctx.hot_until = 0
    ctx.unsaved = 0

    if ctx.audio and ctx.audio.ok:
        try:
            ctx.audio.set_volume(70)
        except Exception:
            pass

    _draw_frame(ctx)
    _draw_fish(ctx)
    _draw_view(ctx)
    _draw_counter(ctx, False)


def on_key(ctx, key):
    if key == "ok":
        _roll_day(ctx)
        ctx.total += 1
        ctx.today += 1
        ctx.hot_until = time.ticks_add(time.ticks_ms(), FLASH_MS)
        ctx.unsaved += 1

        # 反馈：数字变亮 + "+1" + 三条冲击线（都是小面积重画）
        _draw_counter(ctx, True)
        if not ctx.hot:
            ctx.hot = True
            ctx.lcd.text("+1", PLUS_X, PLUS_Y, GOLD_HI, BG)
            _marks(ctx, GOLD_HI)

        _knock(ctx)

        if ctx.unsaved >= FLUSH_EVERY:
            _save(ctx)
            ctx.unsaved = 0

    elif key == "up":
        ctx.view = "today" if ctx.view == "total" else "total"
        ctx.kv_set("view", ctx.view)
        _draw_view(ctx)
        _draw_counter(ctx, False)

    elif key == "down":
        ctx.muted = not ctx.muted
        ctx.kv_set("muted", 1 if ctx.muted else 0)
        _draw_view(ctx)
        if not ctx.muted:
            _knock(ctx)          # 取消静音时立刻给个听觉确认


def loop(ctx):
    # 高亮到期就收回
    if ctx.hot and time.ticks_diff(time.ticks_ms(), ctx.hot_until) >= 0:
        ctx.hot = False
        ctx.lcd.fill_rect(PLUS_X, PLUS_Y, 16, 8, BG)
        _marks(ctx, BG)
        _draw_counter(ctx, False)

    # 每 ~0.6 秒查一次是否跨天
    if ctx.frame % 30 == 0:
        if _roll_day(ctx):
            _draw_view(ctx)
            _draw_counter(ctx, False)


def teardown(ctx):
    _save(ctx)
