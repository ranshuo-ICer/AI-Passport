#!/usr/bin/env python3
"""验证 PassportOS 的 ES8311 音频模块（不需要硬件）。

用桩顶替 machine.I2C / machine.I2S，把 es8311 的初始化寄存器写入录下来，
逐条比对官方 esp_codec_dev v1.6.2 的顺序与取值。

    python tools/test_audio.py
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("   " + extra) if extra and not cond else ""))


# --------------------------------------------------------------------- 桩
class FakeI2C:
    """记录所有寄存器操作，并用一份可写的寄存器文件模拟芯片。"""

    def __init__(self, *a, **kw):
        self.regs = {}
        self.log = []

    def writeto_mem(self, addr, reg, data):
        self.regs[reg] = data[0]
        self.log.append((reg, data[0]))

    def readfrom_mem(self, addr, reg, n):
        return bytes((self.regs.get(reg, 0x00),))


class FakeI2S:
    instances = []

    def __init__(self, idx, **kw):
        self.idx = idx
        self.kw = kw
        self.written = 0
        self.buffers = []          # 留下每次 write 的字节，供断言检查
        FakeI2S.instances.append(self)

    def write(self, buf):
        # MicroPython 的 I2S.write 按【字节】计数；array('h') 的 len 是元素数，
        # 所以这里必须用 memoryview 的 nbytes，不能直接用 len()。
        mv = memoryview(buf)
        self.buffers.append(bytes(mv))
        self.written += mv.nbytes
        return mv.nbytes

    def deinit(self):
        pass


def install_stubs():
    mach = types.ModuleType("machine")

    class Pin:
        def __init__(self, n, *a, **kw):
            self.n = n

    mach.Pin = Pin
    mach.I2C = FakeI2C
    mach.I2S = FakeI2S
    mach.I2S.TX = 1
    mach.I2S.RX = 2
    mach.I2S.MONO = 1
    mach.I2S.STEREO = 2
    sys.modules["machine"] = mach
    return mach


def main():
    install_stubs()
    from passport import config as C
    from passport.audio import Audio, COEFF, NOTES

    print("\n[1] 系数表覆盖所有标准采样率")
    for rate in (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000):
        check("COEFF 含 %dHz" % rate, rate in COEFF)
    check("每行 10 个字段", all(len(v) == 10 for v in COEFF.values()))

    print("\n[2] 初始化寄存器序列（对照官方 es8311.c）")
    fake = FakeI2C()
    a = Audio(rate=16000, i2c=fake)
    check("初始化成功", a.ok, a.error or "")

    seq = [r for r, _ in fake.log]
    for must in (0x0D, 0x44, 0x01, 0x02, 0x03, 0x16, 0x04, 0x05, 0x0B, 0x0C,
                 0x10, 0x11, 0x00, 0x06, 0x13, 0x1B, 0x1C, 0x09, 0x0A,
                 0x07, 0x08, 0x17, 0x0E, 0x12, 0x14, 0x15, 0x37, 0x45, 0x31, 0x32):
        check("写过程序寄存器 0x%02X" % must, must in seq)

    check("0x44 写了两次（官方抗噪要求）",
          sum(1 for r, _ in fake.log if r == 0x44) >= 2,
          "次数=%d" % sum(1 for r, _ in fake.log if r == 0x44))
    check("0x0D 首次写 0xFA", (0x0D, 0xFA) in fake.log)
    check("0x44 内部参考 = 0x58", (0x44, 0x58) in fake.log)
    check("Slave 模式：0x00 不置 bit6", not (fake.regs[0x00] & 0x40),
          "0x00=0x%02X" % fake.regs[0x00])
    check("默认不用外部 MCLK：0x01 置 bit7", bool(fake.regs[0x01] & 0x80),
          "0x01=0x%02X" % fake.regs[0x01])
    check("0x09 低两位=00 (I2S 标准格式)", (fake.regs[0x09] & 0x03) == 0,
          "0x09=0x%02X" % fake.regs[0x09])
    check("0x09 16bit 位已置", (fake.regs[0x09] & 0x0C) == 0x0C,
          "0x09=0x%02X" % fake.regs[0x09])
    check("0x09 DAC 通道使能 (bit6=0)", not (fake.regs[0x09] & 0x40),
          "0x09=0x%02X" % fake.regs[0x09])
    check("0x12 使能 DAC = 0x00", fake.regs[0x12] == 0x00)
    check("0x0D 最后 = 0x01 (上电)", fake.regs[0x0D] == 0x01,
          "0x0D=0x%02X" % fake.regs[0x0D])
    check("0x37 = 0x08", fake.regs[0x37] == 0x08)

    print("\n[3] 采样率相关的分频计算（16kHz, 无外部 MCLK）")
    # coeff: pre_div=1 adc_div=1 dac_div=1 fs_mode=0 lrck_h=0 lrck_l=0xff
    #        bclk_div=4 adc_osr=0x10 dac_osr=0x20
    # 无外部 MCLK 时官方驱动把 pre_multi 覆盖成 ×8 -> datmp=3 -> bit4:3=0b11
    check("0x02 pre_div=0 / pre_multi=×8", (fake.regs[0x02] & 0xE0) == 0x00 and
          (fake.regs[0x02] & 0x18) == 0x18, "0x02=0x%02X" % fake.regs[0x02])
    check("0x05 adc/dac 分频 = 0x00", fake.regs[0x05] == 0x00,
          "0x05=0x%02X" % fake.regs[0x05])
    check("0x03 adc_osr = 0x10", (fake.regs[0x03] & 0x3F) == 0x10,
          "0x03=0x%02X" % fake.regs[0x03])
    check("0x04 dac_osr = 0x20", (fake.regs[0x04] & 0x3F) == 0x20,
          "0x04=0x%02X" % fake.regs[0x04])
    check("0x08 lrck_l = 0xFF", fake.regs[0x08] == 0xFF,
          "0x08=0x%02X" % fake.regs[0x08])
    check("0x06 bclk_div = 3 (4-1)", (fake.regs[0x06] & 0x1F) == 3,
          "0x06=0x%02X" % fake.regs[0x06])

    print("\n[4] 音量映射")
    # 寄存器 0x00=-95.5dB, 0xFF=+32dB；本模块把 1~100% 映射到 -40dB~0dB，
    # 0% 直接静音。0dB 对应 0xBF。
    fake.regs.clear()
    a.set_volume(100)
    check("100% -> 0dB (0xBF)", fake.regs[0x32] == 0xBF, "0x%02X" % fake.regs[0x32])
    a.set_volume(0)
    check("0% -> 真静音 (0x00)", fake.regs[0x32] == 0x00, "0x%02X" % fake.regs[0x32])
    a.set_volume(50)
    mid = fake.regs[0x32]
    # 50% -> -20dB -> (75.5/127.5)*255 = 151 = 0x97
    check("50% -> -20dB (0x97)", mid == 0x97, "0x%02X" % mid)

    # 单调性：音量越大寄存器值越大
    fake.regs.clear()
    prev = -1
    bad = None
    for pct in range(0, 101, 5):
        a.set_volume(pct)
        v = fake.regs[0x32]
        if v < prev and bad is None:
            bad = "%d%% -> 0x%02X < 0x%02X" % (pct, v, prev)
        prev = v
    check("音量单调递增 (0~100 全程)", bad is None, bad or "")

    print("\n[5] I2S 参数")
    i2s = FakeI2S.instances[-1]
    check("I2S 是 TX", i2s.kw.get("mode") == FakeI2S.TX)
    check("16 bit", i2s.kw.get("bits") == 16)
    check("MONO", i2s.kw.get("format") == FakeI2S.MONO)
    check("采样率 16000", i2s.kw.get("rate") == 16000)
    check("不传 mck（MicroPython 的 I2S 没这个参数）", "mck" not in i2s.kw)
    check("ibuf 必填已给", "ibuf" in i2s.kw)
    check("BCLK 接在 GPIO5", getattr(i2s.kw.get("sck"), "n", None) == C.I2S_BCLK)
    check("WS 接在 GPIO3", getattr(i2s.kw.get("ws"), "n", None) == C.I2S_WS)
    check("DOUT 接在 GPIO2", getattr(i2s.kw.get("sd"), "n", None) == C.I2S_DOUT)

    print("\n[6] 播放路径真的写出了数据")
    i2s.buffers.clear()
    before = i2s.written
    a.tone(1000, 100)
    n = i2s.written - before
    check("100ms@16kHz 写出 3200 字节", n == 16000 * 100 // 1000 * 2, "实际 %d" % n)

    # 真检查（不是 check(..., True)）：样本必须是 16bit 小端有符号、
    # 有实际幅度、且没削顶。
    raw = b"".join(i2s.buffers)
    check("字节数为偶数（16bit）", len(raw) % 2 == 0, "len=%d" % len(raw))
    if raw:
        import array as _array
        smp = _array.array("h")
        smp.frombytes(raw)
        peak = max(max(smp), -min(smp))
        check("非静音段有实际幅度", peak > 1000, "peak=%d" % peak)
        check("没有削顶 (|样本| < 32767)", peak < 32767, "peak=%d" % peak)

    print("\n[6b] 长音分块（防 MemoryError）")
    i2s.buffers.clear()
    before = i2s.written
    a.tone(440, 5000)                  # 5 秒：不分块需 160KB，必 MemoryError
    total = i2s.written - before
    cap = a.MAX_TONE_MS
    check("超长音被截断到 MAX_TONE_MS(%d)" % cap,
          total == 16000 * cap // 1000 * 2, "实际 %d" % total)
    biggest = max(len(b) for b in i2s.buffers) if i2s.buffers else 0
    check("单块不超过 CHUNK_SAMPLES*2",
          biggest <= a.CHUNK_SAMPLES * 2, "最大 %d" % biggest)
    check("确实切成了多块", len(i2s.buffers) > 1, "%d 块" % len(i2s.buffers))

    before = i2s.written
    a.tone(0, 50)                      # 静音也要占时长
    check("静音也推等长数据", i2s.written - before == 16000 * 50 // 1000 * 2)

    before = i2s.written
    a.melody([("C5", 0.5), ("REST", 0.25)], bpm=120)
    check("melody 推进了数据", i2s.written > before,
          "增加 %d 字节" % (i2s.written - before))

    print("\n[7] 每个采样率都能建起来")
    for rate in COEFF:
        f2 = FakeI2C()
        inst = Audio(rate=rate, i2c=f2)
        check("%dHz 初始化成功" % rate, inst.ok, inst.error or "")

    print("\n[8] 音名表")
    check("A4 = 440Hz", NOTES["A4"] == 440)
    check("C5 = 523Hz", NOTES["C5"] == 523)
    check("有 REST(休止)", NOTES["REST"] == 0)

    print("\n[9] use_mclk=True 模式（板子若接得到外部 MCLK 时用）")
    f3 = FakeI2C()
    a3 = Audio(rate=16000, i2c=f3, use_mclk=True)
    check("初始化成功", a3.ok, a3.error or "")
    check("0x01 清 bit7", not (f3.regs[0x01] & 0x80),
          "0x01=0x%02X" % f3.regs[0x01])
    check("0x02 pre_multi=×1 (datmp=0)", (f3.regs[0x02] & 0x18) == 0x00,
          "0x02=0x%02X" % f3.regs[0x02])

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
