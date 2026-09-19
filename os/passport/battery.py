"""CW2017 电量计（I2C 0x63）。

寄存器与 80 字节电池 profile 均取自官方 BSP `components/bsp/src/bsp_battery.c`。
没有 profile 的话 CW2017 算不出 SOC，所以首次使用必须写一次 profile 并触发更新。
"""

import time

from machine import I2C, Pin

from . import config as C

# 官方 BSP 的电池 profile，必须正好 80 字节
PROFILE = bytes((
    0x64, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0xAD, 0xC7, 0xC8, 0xCA, 0xBD, 0xB1, 0xC1, 0x94,
    0x88, 0xD1, 0xBD, 0x97, 0x88, 0x66, 0x56, 0x4A,
    0x3F, 0x33, 0x26, 0x5C, 0x37, 0xD1, 0x27, 0xD8,
    0xCC, 0xB7, 0xCF, 0xB3, 0xB2, 0xAE, 0xA6, 0x9E,
    0x99, 0x97, 0x9B, 0x86, 0x47, 0x1E, 0x17, 0x26,
    0x49, 0x96, 0xD9, 0xE1, 0xDD, 0xDC, 0xD4, 0x59,
    0x00, 0x00, 0x90, 0x02, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x64, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x5C,
))

# 寄存器地址一律从 config.py 取，这里【不再重复定义】。
# config.py 是硬件事实的单一来源；两处各写一份迟早会漂移。
REG_VERSION = C.CW_REG_VERSION
REG_VCELL_H = C.CW_REG_VCELL_H
REG_SOC_H = C.CW_REG_SOC_H
REG_CONFIG = C.CW_REG_CONFIG
REG_SOC_ALERT = C.CW_REG_SOC_ALERT
REG_PROFILE = C.CW_REG_PROFILE

CFG_ACTIVE = C.CW_CONFIG_ACTIVE
UPDATE_FLAG = C.CW_UPDATE_FLAG


class Battery:
    def __init__(self, i2c=None):
        self.i2c = i2c if i2c is not None else I2C(
            0, sda=Pin(C.I2C_SDA), scl=Pin(C.I2C_SCL), freq=C.I2C_FREQ)
        self.ok = False
        self.version = 0
        self._soc = -1
        self._mv = 0
        self._next = 0
        self._probe()

    # ------------------------------------------------------------------ 底层
    def _read(self, reg, n):
        return self.i2c.readfrom_mem(C.ADDR_CW2017, reg, n)

    def _write(self, reg, val):
        self.i2c.writeto_mem(C.ADDR_CW2017, reg, bytes((val,)))

    def _probe(self):
        try:
            self.version = self._read(REG_VERSION, 1)[0]
            self.ok = True
        except OSError:
            self.ok = False

    def init_profile(self):
        """写电池 profile 并触发 SOC 重算。只需一次。"""
        if not self.ok:
            return False
        try:
            self.i2c.writeto_mem(C.ADDR_CW2017, REG_PROFILE, PROFILE)
            self._write(REG_CONFIG, CFG_ACTIVE)
            val = self._read(REG_SOC_ALERT, 1)[0]
            self._write(REG_SOC_ALERT, val | UPDATE_FLAG)
            return True
        except OSError:
            return False

    # ------------------------------------------------------------------ 读数
    def poll(self, force=False):
        """节流刷新（默认 5 秒一次）。"""
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self._next) < 0:
            return
        self._next = time.ticks_add(now, 5000)
        if not self.ok:
            self._probe()
            if not self.ok:
                return
        try:
            soc = self._read(REG_SOC_H, 1)[0]
            if soc <= 100:
                self._soc = soc
            b = self._read(REG_VCELL_H, 2)
            raw = ((b[0] << 8) | b[1]) & 0x3FFF
            self._mv = raw * 3125 // 10000       # V(uV)=raw*312.5 → mV
        except OSError:
            self.ok = False

    @property
    def percent(self):
        return self._soc

    @property
    def millivolts(self):
        return self._mv

    def label(self):
        if not self.ok:
            return "--"
        if self._soc < 0:
            return "%dmV" % self._mv
        return "%d%%" % self._soc
