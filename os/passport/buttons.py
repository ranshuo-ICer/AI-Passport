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
    def _read_mv(self):
        try:
            return self.adc.read_uv() // 1000
        except AttributeError:
            return self.adc.read() * 3300 // 4095

    def raw_mv(self):
        """多次采样取中位数，滤掉 ADC 抖动。

        真机实测：**单次 ADC 读取就要 61 µs**，是这条路径的绝对大头（`read()`
        原始值也一样慢，52 µs），所以采样次数才是主要成本；原来的
        `list + append + sort()` 另外还要花约 55 µs，而且每 20 ms 就在热路径上
        分配一个 list —— 这块板没有 PSRAM，没必要。

        默认 4 采样走排序网络，**语义与原来完全一致**（等价于 sort 后取索引 2），
        只是不再分配。
        """
        if self.samples == 4:
            a = self._read_mv()
            b = self._read_mv()
            c = self._read_mv()
            d = self._read_mv()
            # 4 元素排序网络（5 次比较交换）：排完 c 就是"排序后索引 2"
            if a > b:
                a, b = b, a
            if c > d:
                c, d = d, c
            if a > c:
                a, c = c, a
            if b > d:
                b, d = d, b
            if b > c:
                b, c = c, b
            return c
        vals = []
        for _ in range(self.samples):
            vals.append(self._read_mv())
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
