"""ES8311 音频输出（I2S 播放）。

寄存器序列**逐条照抄**官方 `espressif/esp_codec_dev` v1.6.2 的
`device/es8311/es8311.c`（原厂固件用的就是这个版本），源码副本在
`docs/reference/es8311/`。顺序不能随意改：open() → set_fs() → start()
是有依赖的，跳步会静音或出噪声。

硬件（来自官方 BSP bsp_pins.h）：
    I2S MCLK=GPIO6  BCLK=GPIO5  WS=GPIO3  DOUT=GPIO2(播放)  DIN=GPIO4(录音)
    ES8311 在 I2C0，7 位地址 0x18，**Slave 模式**（时钟由 ESP32-C3 主出）
    PA 功放使能脚未接 MCU（常通），所以不需要 GPIO 控制

本模块只做**播放**。录音（麦克风）需要第二个 I2S 实例共享同一组时钟，
MicroPython 的 machine.I2S 不能同时开两个实例，所以留待后续。
"""

import array
import math
import time

from machine import I2C, I2S, Pin

from . import config as C

# ---------------------------------------------------------------- 寄存器地址
REG00_RESET = 0x00
REG01_CLK = 0x01
REG02_CLK = 0x02
REG03_CLK = 0x03
REG04_CLK = 0x04
REG05_CLK = 0x05
REG06_CLK = 0x06
REG07_CLK = 0x07
REG08_CLK = 0x08
REG09_SDPIN = 0x09
REG0A_SDPOUT = 0x0A
REG0B = 0x0B
REG0C = 0x0C
REG0D = 0x0D
REG0E = 0x0E
REG10 = 0x10
REG11 = 0x11
REG12 = 0x12
REG13 = 0x13
REG14 = 0x14
REG15 = 0x15
REG16 = 0x16
REG17 = 0x17
REG1B = 0x1B
REG1C = 0x1C
REG31_DAC_MUTE = 0x31
REG32_DAC_VOL = 0x32
REG37 = 0x37
REG44_GPIO = 0x44
REG45 = 0x45

# 时钟系数表：mclk == 256 * rate 的那些行（mclk_div 默认 256）
#   key: rate
#   value: (pre_div, pre_multi, adc_div, dac_div, fs_mode, lrck_h, lrck_l,
#           bclk_div, adc_osr, dac_osr)
# 直接摘自 coeff_div[]，不重新推导。
COEFF = {
    8000:  (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
    11025: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
    12000: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
    16000: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x20),
    22050: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
    24000: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
    32000: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
    44100: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
    48000: (0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0xFF, 0x04, 0x10, 0x10),
}

# DAC 音量寄存器：0x00 = -95.5dB，0xFF = +32dB（线性 dB）
VOL_REG_MIN = 0x00
VOL_REG_MAX = 0xFF
VOL_REG_0DB = 0xBF          # 0 dB

# 1024 点正弦表，避免播放时做浮点运算
_TABLE_BITS = 10
_TABLE_SIZE = 1 << _TABLE_BITS
_SINE = array.array("h", bytes(2 * _TABLE_SIZE))
for _i in range(_TABLE_SIZE):
    _SINE[_i] = int(28000 * math.sin(2 * math.pi * _i / _TABLE_SIZE))


# ------------------------------------------------------- 原生加速的合成循环
# 真机实测（小程序 tonebench.py，n=3200 样本，做法与下面逐位一致）：
#     plain  25878 us      native 16628 us (1.55x)      viper 986 us (26.24x)
# native 只快 1.55x，不值得单独成一条路；viper 26x 且写出的 3200 个样本与
# plain 逐位相同（指纹 0000e85e，离线模型同值），所以只用 viper。
#
# 为什么能快 26 倍：viper 把 ptr16 索引编译成机器码，去掉了每个样本上的
# 对象装箱和字节码分派。ESP32-C3 是 RISC-V，端口开了 MICROPY_EMIT_RV32，
# 所以装饰器在运行时可用——但**编译不出机器码是可能的**（DRAM 不可执行），
# 所以整块包在 try 里，失败就是 None，tone() 自动退回纯 Python。
try:
    import micropython

    @micropython.viper
    def _tone_fast(buf, sine, n: int, step: int, ph: int, fade: int) -> int:
        b = ptr16(buf)
        s = ptr16(sine)
        i = 0
        while i < n:
            b[i] = s[(ph >> 8) & 1023]     # 1023 == _TABLE_SIZE - 1；viper 里
            ph += step                     # 不引用模块常量，写死更稳
            i += 1
        # fade 是 min(64, n//4)。n >= 256（16kHz 下 ms >= 16，即所有实际调用）
        # 时恒为 64，于是 // 64 可以换成 >> 6：对全部 int16 取值 × 全部 i
        # 逐值比对过，结果完全一致（Python 的 >> 对负数是向下取整，与 // 同义）。
        # 小 n 时 fade = n//4 不是 2 的幂，保留原除法。
        if fade == 64:
            i = 0
            while i < 64:
                b[i] = (b[i] * i) >> 6
                b[n - 1 - i] = (b[n - 1 - i] * i) >> 6
                i += 1
        else:
            i = 0
            while i < fade:
                b[i] = (b[i] * i) // fade
                b[n - 1 - i] = (b[n - 1 - i] * i) // fade
                i += 1
        return ph & 0xFFFFFF
except Exception:
    _tone_fast = None

# 音名 → 频率（十二平均律，A4 = 440Hz）
NOTES = {
    "C4": 262, "C#4": 277, "D4": 294, "D#4": 311, "E4": 330, "F4": 349,
    "F#4": 370, "G4": 392, "G#4": 415, "A4": 440, "A#4": 466, "B4": 494,
    "C5": 523, "C#5": 554, "D5": 587, "D#5": 622, "E5": 659, "F5": 698,
    "F#5": 740, "G5": 784, "G#5": 831, "A5": 880, "A#5": 932, "B5": 988,
    "C6": 1047, "D6": 1175, "E6": 1319, "G6": 1568, "A6": 1760,
    "C3": 131, "D3": 147, "E3": 165, "F3": 175, "G3": 196, "A3": 220, "B3": 247,
    "REST": 0,
}


class Audio:
    """ES8311 播放。初始化失败不会抛异常打断系统，检查 .ok 即可。"""

    DEFAULT_VOLUME = 80          # 0~100，reset_state() 回到这里

    def __init__(self, rate=16000, i2c=None, use_mclk=False):
        # use_mclk=False（默认）：
        #   ES8311 从 BCLK 倍频出内部 DIG_MCLK。
        #   为什么不用外部 MCLK：**MicroPython 的 machine.I2S 不接受 mck 参数**
        #   （真机实测 TypeError: extra keyword arguments given），
        #   所以 GPIO6 上的 MCLK 我们驱动不了，只能走 BCLK 倍频这条路。
        #   官方驱动 es8311_config_sample() 里就有这个分支，是正规用法。
        self.use_mclk = bool(use_mclk)
        self.rate = rate if rate in COEFF else 16000
        self.ok = False
        self.error = None
        self.volume = self.DEFAULT_VOLUME
        self._i2s = None
        self._i2c = i2c
        self._owns_i2c = i2c is None
        self._phase = 0
        try:
            self._setup()
            self.ok = True
        except Exception as exc:                          # noqa: BLE001
            self.error = "%s: %s" % (type(exc).__name__, exc)

    # ------------------------------------------------------------------ 底层
    def _wr(self, reg, val):
        self._i2c.writeto_mem(C.ADDR_ES8311, reg, bytes((val & 0xFF,)))

    def _rd(self, reg):
        return self._i2c.readfrom_mem(C.ADDR_ES8311, reg, 1)[0]

    # ------------------------------------------------------------------ 初始化
    def _setup(self):
        if self._i2c is None:
            self._i2c = I2C(0, sda=Pin(C.I2C_SDA), scl=Pin(C.I2C_SCL),
                            freq=C.I2C_FREQ)

        # --- 1) es8311_open() ---
        if self._rd(REG0D) != 0xFA:
            self._wr(REG0D, 0xFA)
        self._wr(REG44_GPIO, 0x08)      # 提高 I2C 抗噪，官方连写两次
        self._wr(REG44_GPIO, 0x08)
        self._wr(REG01_CLK, 0x30)
        self._wr(REG02_CLK, 0x00)
        self._wr(REG03_CLK, 0x10)
        self._wr(REG16, 0x24)
        self._wr(REG04_CLK, 0x10)
        self._wr(REG05_CLK, 0x00)
        self._wr(REG0B, 0x00)
        self._wr(REG0C, 0x00)
        self._wr(REG10, 0x1F)
        self._wr(REG11, 0x7F)
        self._wr(REG00_RESET, 0x80)

        regv = self._rd(REG00_RESET) & 0xBF     # Slave 模式（清 bit6）
        self._wr(REG00_RESET, regv)
        regv = 0x3F
        if not self.use_mclk:
            regv |= 0x80                        # 不用外部 MCLK
        self._wr(REG01_CLK, regv)               # invert_mclk = False
        self._wr(REG06_CLK, self._rd(REG06_CLK) & ~0x20)   # invert_sclk = False
        self._wr(REG13, 0x10)
        self._wr(REG1B, 0x0A)
        self._wr(REG1C, 0x6A)
        self._wr(REG44_GPIO, 0x58)              # 内部参考 ADCL + DACR

        # --- 2) es8311_set_fs() ---
        self._set_bits_and_fmt()
        self._config_sample()

        # --- 3) es8311_start() ---
        self._start()

        # --- 4) 开声 + 音量 ---
        self._set_mute(False)
        self.set_volume(self.volume)

        # --- 5) I2S 发送端 ---
        # ⚠ 不传 mck：MicroPython 的 machine.I2S 没有这个参数。
        kw = dict(
            sck=Pin(C.I2S_BCLK),
            ws=Pin(C.I2S_WS),
            sd=Pin(C.I2S_DOUT),
            mode=I2S.TX,
            bits=16,
            format=I2S.MONO,
            rate=self.rate,
            ibuf=2048,          # ibuf 是必填的，缺了会报 'ibuf' argument required
        )
        self._i2s = I2S(0, **kw)

    def _set_bits_and_fmt(self):
        dac = self._rd(REG09_SDPIN) | 0x0C        # 16 bit
        adc = self._rd(REG0A_SDPOUT) | 0x0C
        dac &= 0xFC                                # ES_I2S_NORMAL
        adc &= 0xFC
        self._wr(REG09_SDPIN, dac)
        self._wr(REG0A_SDPOUT, adc)

    def _config_sample(self):
        pre_div, pre_multi, adc_div, dac_div, fs_mode, lrck_h, lrck_l, \
            bclk_div, adc_osr, dac_osr = COEFF[self.rate]

        regv = self._rd(REG02_CLK) & 0x07
        regv |= (pre_div - 1) << 5
        datmp = {1: 0, 2: 1, 4: 2, 8: 3}.get(pre_multi, 0)
        if not self.use_mclk:
            # 没有外部 MCLK：ES8311 从 BCLK 倍频。官方驱动在这里直接覆盖
            # pre_multi —— 8kHz 用 ×4（BCLK 有下限），其余用 ×8。
            datmp = 2 if self.rate == 8000 else 3
        regv |= datmp << 3
        self._wr(REG02_CLK, regv)

        self._wr(REG05_CLK, ((adc_div - 1) << 4) | (dac_div - 1))

        regv = (self._rd(REG03_CLK) & 0x80) | (fs_mode << 6) | adc_osr
        self._wr(REG03_CLK, regv)

        regv = (self._rd(REG04_CLK) & 0x80) | dac_osr
        self._wr(REG04_CLK, regv)

        regv = (self._rd(REG07_CLK) & 0xC0) | lrck_h
        self._wr(REG07_CLK, regv)
        self._wr(REG08_CLK, lrck_l)

        regv = self._rd(REG06_CLK) & 0xE0
        regv |= (bclk_div - 1) if bclk_div < 19 else bclk_div
        self._wr(REG06_CLK, regv)

    def _start(self):
        self._wr(REG00_RESET, 0x80 & 0xBF)         # Slave 模式
        regv = 0x3F
        if not self.use_mclk:
            regv |= 0x80
        self._wr(REG01_CLK, regv)

        dac = self._rd(REG09_SDPIN) & 0xBF         # DAC 通道开（清 bit6）
        adc = self._rd(REG0A_SDPOUT) & 0xBF
        self._wr(REG09_SDPIN, dac)
        self._wr(REG0A_SDPOUT, adc)

        self._wr(REG17, 0xBF)
        self._wr(REG0E, 0x02)
        self._wr(REG12, 0x00)                      # 使能 DAC
        self._wr(REG14, 0x1A)
        self._wr(REG14, self._rd(REG14) & ~0x40)   # 不用数字麦克风
        self._wr(REG0D, 0x01)
        self._wr(REG15, 0x40)
        self._wr(REG37, 0x08)
        self._wr(REG45, 0x00)

    def _set_mute(self, mute):
        regv = self._rd(REG31_DAC_MUTE) & 0x9F
        self._wr(REG31_DAC_MUTE, (regv | 0x60) if mute else regv)

    # ------------------------------------------------------------------ 控制
    def set_volume(self, pct):
        """0~100。

        0 直接写静音（-95.5dB），而不是 -40dB —— 用户把音量拧到底就是要没声音。
        1~100 映射到 -40dB ~ 0dB：再往上推容易削顶失真，板载小喇叭也不划算。

        pct>0 时顺带解除 DAC 静音：REG31 的静音位是**会 latch 住的**，而所有
        小程序共用同一个 codec 实例。把"设了音量却没声音"变成不可能，比指望
        每个程序自己记得收尾可靠。
        """
        if pct < 0:
            pct = 0
        elif pct > 100:
            pct = 100
        self.volume = pct
        if pct == 0:
            self._wr(REG32_DAC_VOL, VOL_REG_MIN)
            return
        self._set_mute(False)
        db = -40.0 + 40.0 * pct / 100.0
        reg = int(round(VOL_REG_MIN + (db + 95.5) / 127.5 *
                        (VOL_REG_MAX - VOL_REG_MIN)))
        if reg < VOL_REG_MIN:
            reg = VOL_REG_MIN
        elif reg > VOL_REG_MAX:
            reg = VOL_REG_MAX
        self._wr(REG32_DAC_VOL, reg)

    def mute(self, on=True):
        self._set_mute(on)

    def reset_state(self):
        """把 codec 恢复成"一定能发声"的基线：取消静音 + 回到默认音量。

        `Ctx.audio` 就是 `shell.audio` 这一个实例（见 ui.py），所有小程序共用
        同一颗 ES8311。任何程序在 teardown 里留下的静音位或 0 音量都会**传染**
        给后面所有程序 —— 真机实测：退出 Beats 后 REG31 停在 0x60，此后全系统
        没声音，只有重启能救。所以外壳在每次启动小程序前调这个把它清回基线。
        """
        if not self.ok:
            return
        self._set_mute(False)
        self.set_volume(self.DEFAULT_VOLUME)

    def suspend(self):
        """进低功耗前调用：DAC 静音 + 关 DAC/ADC。

        ⚠ 这是**单向**的：codec 被关掉后没有对应的 resume()，只能整个
        `Audio()` 重建才能再出声。目前全仓库没有调用者（低功耗还没做），
        真要接低功耗休眠时必须同时补一个 resume，否则唤醒后就是哑的。
        """
        for reg, val in ((REG32_DAC_VOL, 0x00), (REG17, 0x00), (REG0E, 0xFF),
                         (REG12, 0x02), (REG14, 0x00), (REG0D, 0xFA),
                         (REG15, 0x00), (REG02_CLK, 0x10),
                         (REG00_RESET, 0x00), (REG00_RESET, 0x1F),
                         (REG01_CLK, 0x30), (REG01_CLK, 0x00),
                         (REG45, 0x00)):
            self._wr(reg, val)

    # ------------------------------------------------------------------ 播放
    def _pcm(self, samples):
        return array.array("h", samples)

    # tone() 的单次时长上限与分块粒度。
    # 不分块的话缓冲是"按时长一次性分配"：16kHz × 5000ms × 2B = 160KB，
    # 而这块板可用堆只有约 110KB → 直接 MemoryError；而且整段纯 Python
    # 合成期间界面冻结、BLE 写入排队（队列满了会静默丢弃）。
    MAX_TONE_MS = 1000
    CHUNK_SAMPLES = 640          # 40ms @16kHz = 1280 字节，对堆毫无压力

    def tone(self, freq, ms=200):
        """播放一个纯音。freq=0 表示静音（用于构成节奏）。

        分块合成 + 分块写 I2S：缓冲不再随时长线性增长，`tone(440, 5000)`
        也不会 MemoryError；块与块之间还会让出时间片，界面和 BLE 不至于僵死。
        超过 MAX_TONE_MS 的时长会被截断。
        """
        if not self.ok or ms <= 0:
            return
        if ms > self.MAX_TONE_MS:
            ms = self.MAX_TONE_MS
        total = self.rate * ms // 1000
        if total <= 0:
            return

        step = ((freq << _TABLE_BITS) * 256 // self.rate) if freq > 0 else 0
        fade = min(64, total // 4)
        done = 0
        while done < total:
            n = self.CHUNK_SAMPLES
            if n > total - done:
                n = total - done
            buf = array.array("h", bytes(n * 2))
            if freq > 0:
                self._phase = self._synth(buf, n, step, self._phase, 0)
                # 淡入淡出只作用在【整段】的头尾，不能每块都做，
                # 否则块边界会出现一串突兀的音量台阶。
                if done == 0:
                    k = fade if fade < n else n
                    for i in range(k):
                        buf[i] = buf[i] * i // k
                if done + n >= total:
                    k = fade if fade < n else n
                    for i in range(k):
                        j = n - 1 - i
                        buf[j] = buf[j] * i // k
            self._i2s.write(buf)
            done += n

    def _synth(self, buf, n, step, ph, fade):
        """把 n 个样本填进 buf 并返回新相位。

        优先走 viper（真机 26x）；一旦这条设备用不了就退回纯 Python，
        两条路写出的样本逐位相同，所以回退只影响速度不影响声音。
        """
        global _tone_fast
        if _tone_fast is not None:
            try:
                return _tone_fast(buf, _SINE, n, step, ph, fade)
            except Exception:
                # 编译期失败在 import 时就已经被接住；走到这里说明是运行期
                # 才暴露的问题（例如分配不出可执行内存）。永久退回，别每
                # 一次发声都再试一遍。
                _tone_fast = None
        for i in range(n):
            buf[i] = _SINE[(ph >> 8) & (_TABLE_SIZE - 1)]
            ph += step
        # 加淡入淡出，避免爆音
        for i in range(fade):
            buf[i] = buf[i] * i // fade
            buf[n - 1 - i] = buf[n - 1 - i] * i // fade
        return ph & 0xFFFFFF

    def play_raw(self, data):
        """直接播 16bit 单声道 PCM 字节串（例如预置音效）。"""
        if self.ok:
            self._i2s.write(data)

    def melody(self, notes, bpm=120):
        """notes: [(音名或频率, 拍数), ...]，例如 [("C5", 0.5), ("REST", 0.25)]"""
        if not self.ok:
            return
        beat_ms = int(60000 / max(20, bpm))
        for note, beats in notes:
            freq = NOTES.get(note, note) if isinstance(note, str) else note
            self.tone(int(freq), max(20, int(beat_ms * beats)))

    def beep(self):
        self.tone(1000, 120)

    def deinit(self):
        try:
            if self._i2s:
                self._i2s.deinit()
        except Exception:                                 # noqa: BLE001
            pass
        self._i2s = None
        if self.ok:
            try:
                self._set_mute(True)
            except Exception:                             # noqa: BLE001
                pass
        self.ok = False
