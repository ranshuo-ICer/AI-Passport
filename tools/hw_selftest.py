"""设备端硬件自检 —— 在真机上跑，往屏幕画测试画面并打印读数。

用法：
    python -m mpremote connect COM3 run tools/hw_selftest.py

它会：
    1. 初始化屏幕，画出色块/文字/进度条（肉眼可验证）
    2. 读三键 ADC 电压（松开应约 3300mV）
    3. 读 CW2017 电量
    4. 初始化 ES8311 + I2S 并放两个音（能听到就说明音频通）
    5. 打印剩余堆

注意：它会打断正在运行的 PassportOS；跑完按 Ctrl-D 或复位即可恢复。
"""

import gc
import time

FAILS = []


def step(name, fn):
    try:
        result = fn()
        print("  [OK]   %-14s %s" % (name, result if result is not None else ""))
        return result
    except Exception as exc:                              # noqa: BLE001
        import sys
        FAILS.append(name)
        print("  [FAIL] %-14s %s: %s" % (name, type(exc).__name__, exc))
        sys.print_exception(exc)
        return None


print("=" * 54)
print("PassportOS 硬件自检")
print("=" * 54)

# ------------------------------------------------------------------ 屏幕
from passport import display as D
from passport.display import Display

lcd = None


def t_display():
    global lcd
    lcd = Display(backlight=80)
    lcd.fill(D.NAVY)
    lcd.fill_rect(0, 0, 240, 28, D.DARK)
    lcd.text_scale("DISPLAY OK", 4, 6, D.CYAN, D.DARK, 2)
    lcd.text("8x8 single line", 8, 40, D.WHITE, D.NAVY)
    lcd.text_center("CENTERED", 60, D.YELLOW, D.NAVY, 2)
    for i, c in enumerate((D.RED, D.GREEN, D.BLUE, D.YELLOW, D.CYAN,
                           D.MAGENTA, D.WHITE, D.GREY)):
        lcd.fill_rect(8 + i * 28, 90, 24, 24, c)
    lcd.progress(8, 130, 224, 16, 66)
    lcd.text("progress 66%", 8, 152, D.SILVER, D.NAVY)
    lcd.rect(8, 176, 224, 60, D.TEAL)
    lcd.text_center("If you can read", 186, D.WHITE, D.NAVY, 1)
    lcd.text_center("this, screen is OK", 200, D.WHITE, D.NAVY, 1)
    return "240x320 ST7789P3"


step("display", t_display)

# ------------------------------------------------------------------ 按键
from passport.buttons import Buttons

btn = None


def t_buttons():
    global btn, btnmv
    btn = Buttons()
    mv, key = btn.check()
    btnmv = mv
    return "%d mV -> %s" % (mv, key)


btnmv = None
step("buttons", t_buttons)

# ------------------------------------------------------------------ 电池
from passport.battery import Battery

batt = None


def t_battery():
    global batt
    batt = Battery()
    batt.poll(force=True)
    if not batt.ok:
        raise OSError("CW2017 无应答")
    return "ver=0x%02X %s %d mV" % (batt.version, batt.label(), batt.millivolts)


step("battery", t_battery)

# ------------------------------------------------------------------ 音频
from passport.audio import Audio

audio = None


def t_audio():
    global audio
    audio = Audio(rate=16000)
    if not audio.ok:
        raise OSError(audio.error or "未知错误")
    audio.set_volume(70)
    return "ES8311 @16kHz"


if step("audio init", t_audio) is not None:
    def t_play():
        audio.tone(880, 150)
        time.sleep_ms(60)
        audio.tone(1319, 200)
        return "已播放 880Hz + 1319Hz（听到了吗？）"
    step("audio play", t_play)

# ------------------------------------------------------------------ 收尾
print("-" * 54)
if lcd is not None:
    lcd.text("SELFTEST DONE", 8, 244, D.GREEN, D.NAVY)
    lcd.text("free %dK" % (gc.mem_free() // 1024), 8, 258, D.SILVER, D.NAVY)
    if btnmv is not None:
        lcd.text("BTN %dmV" % btnmv, 8, 272, D.SILVER, D.NAVY)
    if batt is not None and batt.ok:
        lcd.text("BAT %s" % batt.label(), 8, 286, D.SILVER, D.NAVY)

print("heap free: %d bytes" % gc.mem_free())
if FAILS:
    print("失败项: %s" % ", ".join(FAILS))
else:
    print("全部通过")
print("按 Ctrl-D 或复位可回到 PassportOS")
