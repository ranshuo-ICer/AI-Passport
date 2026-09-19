"""三键电阻梯读取。

硬件：3.3V ── 10k 外部上拉 ──┬── ADC(GPIO0 / ADC1_CH0)
                              └── 按键 ── 分压电阻 ── GND

  UP   =   0 mV     DOWN =  ~300 mV     OK  =  ~595 mV     松开 = ~3300 mV

⚠ 绝对不要改用内部上拉（约 45kΩ 且精度差），会把三档全部压进 0~154mV 并随温漂重叠。
"""

from machine import ADC, Pin

from . import config as C


class Buttons:
    def __init__(self, samples=4):
        # 三键窗口的最后一档上界就是"松开"的门槛，两者必须一致 ——
        # 这里是唯一同时用到它们的地方，顺手把漂移挡住。
        assert C.BTN_WINDOWS[-1][2] == C.BTN_RELEASED_MV, \
            "BTN_WINDOWS 上界与 BTN_RELEASED_MV 不一致"

        self.adc = ADC(Pin(C.BTN_ADC_PIN))
        try:
            self.adc.atten(ADC.ATTN_11DB)       # 量程拉到约 0~3.3V
        except (ValueError, AttributeError):
            pass
        try:
            self.adc.width(ADC.WIDTH_12BIT)
        except (ValueError, AttributeError):
            pass
        self.samples = samples
        self._raw_mv = 0
        self._stable = None
        self._candidate = None
        self._count = 0
        self._frame = 0

    # ------------------------------------------------------------------ 读取
    def raw_mv(self):
        """多次采样取中位数，滤掉 ADC 抖动。"""
        vals = []
        for _ in range(self.samples):
            try:
                vals.append(self.adc.read_uv() // 1000)
            except AttributeError:
                vals.append(self.adc.read() * 3300 // 4095)
        vals.sort()
        return vals[len(vals) // 2]

    def _classify(self, mv):
        for name, lo, hi in C.BTN_WINDOWS:
            if lo <= mv < hi:
                return name
        return None                              # 松开（>1900mV）

    def update(self):
        """刷新一次状态。返回本次【新按下】的键名，没有则返回 None。"""
        self._frame += 1
        mv = self.raw_mv()
        self._raw_mv = mv
        key = self._classify(mv)

        if key == self._candidate:
            self._count += 1
        else:
            self._candidate = key
            self._count = 1

        pressed = None
        if self._count >= 2 and key != self._stable:   # 连续两次一致才算数
            self._stable = key
            if key is not None:
                pressed = key
        return pressed

    def current(self):
        """当前按住（且已消抖）的键名，松开返回 None。"""
        return self._stable

    def voltage(self):
        return self._raw_mv

    def check(self):
        """诊断用：返回 (电压mV, 识别到的键)。"""
        mv = self.raw_mv()
        return mv, self._classify(mv)
