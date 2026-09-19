# 硬件事实

本文是这块板子的硬件参考。**所有数值来自官方 BSP**（`components/bsp/include/bsp_pins.h`
与 `components/bsp/src/bsp_display.c`），已在真机上核对过；标「实测」的是真机读数。

---

## 1. 规格

| 项目 | 规格 |
| --- | --- |
| 主控 | ESP32-C3，RISC-V 单核 160 MHz |
| 存储 | 8 MB Flash（XMC），**无 PSRAM** |
| 无线 | Wi-Fi 2.4 GHz + BLE 5.0（**无蓝牙经典**，只能走 GATT） |
| 屏幕 | 240×320 TFT，**ST7789P3**，SPI |
| 音频 | ES8311 codec（麦克风 + 喇叭），I²S 全双工 |
| 按键 | 3 个物理键 UP / DOWN / OK，共用 GPIO0 ADC 电阻梯 |
| 电池 | CW2017 电量计 + 520 mAh 锂电池，**独立硬件电源键** |
| NFC | NTAG213 **被动标签**，不连 MCU |
| USB | Type-C，**原生 USB Serial/JTAG**（GPIO18/19，非 CH340） |

实测芯片信息：

```
Chip type : ESP32-C3 (QFN32) rev v1.1
Features  : Wi-Fi, BT 5 (LE), Single Core, 160MHz, Embedded Flash 8MB (XMC)
USB mode  : USB-Serial/JTAG
Flash     : 8MB (Manufacturer 0x20, Device 0x4017)
```

---

## 2. GPIO 引脚表

| GPIO | 用途 | GPIO | 用途 |
| --- | --- | --- | --- |
| 0 | 三键 ADC（**兼 BOOT 脚**） | 8 | LCD SCLK |
| 1 | LCD CS | 9 | LCD MOSI（**无 MISO，屏不可读**） |
| 2 | I²S DOUT（MCU → codec，放音） | 10 | I²C SDA |
| 3 | I²S WS | 18 / 19 | USB Serial/JTAG |
| 4 | I²S DIN（codec → MCU，录音） | 20 | LCD DC |
| 5 | I²S BCLK | 21 | 背光 PWM |
| 6 | I²S MCLK | 7 | I²C SCL（ES8311=0x18, CW2017=0x63） |

**几乎全部 GPIO 都被占用**，剩下能自由用的非常有限。两个要注意的点：

- **GPIO0 身兼三职**：boot 选择脚、三键 ADC 采样脚、且按下时电压被拉低。
  不能当普通 IO 用，下载/复位时序里按键状态会影响启动行为。
- **GPIO21 与 UART0 TX 冲突**：背光 PWM 默认在 GPIO21，想用默认串口打印日志时会打架。

---

## 3. 三键 ADC 电阻梯

```
3.3V ── 外部 10k 上拉 ──┬── ADC 节点（GPIO0 / ADC1_CH0）
                        └── 按键 ── 分压电阻 ── GND
```

| 按键 | 分压电阻 | 典型电压 | 判定窗口 |
| --- | --- | --- | --- |
| UP | 0 Ω | 0 mV | 0 ~ 150 mV |
| DOWN | 1 kΩ | ~300 mV | 150 ~ 447 mV |
| OK | 2.2 kΩ | ~595 mV | 447 ~ 1900 mV |
| 松开 | 无通路 | ~3300 mV | > 1900 mV |

- 实测松开态：**2897 mV**
- ⚠ **绝不能改用内部上拉**（约 45 kΩ 且精度差），会把三档全挤进 0~154 mV 并随温漂重叠
- 换了分压/上拉阻值后：进设备的主菜单按着键看串口，用
  `from passport.buttons import Buttons; b=Buttons(); b.check()` 打印实际 mV，
  再按相邻两档的中点改窗口

---

## 4. 显示（ST7789P3）

| 项 | 值 |
| --- | --- |
| 分辨率 | 240×320 竖屏，RGB565 |
| 接口 | SPI2，40 MHz，mode 0 |
| 复位脚 | **未接 MCU**（硬接 3.3V），只能走 `SWRESET` 软复位 |
| 背光 | GPIO21，LEDC PWM 5 kHz / 10 bit（满值 1023） |
| 反色 | **出厂即需反色**（无条件发 `0x21 INVON`） |

⚠ **这是最容易踩的坑**：这块屏需要**面板厂给的专属初始化序列**
（PORCTRL / GCTRL / VCOMS / LCMCTRL / PWCTRL / 伽马，共 13 条命令）。
普通 ST7789 的通用序列**点不亮或显示异常**。序列原文在
[`reference/es8311/`](reference/es8311/) 之外的 `upstream/components/bsp/src/bsp_display.c`，
本仓库的 MicroPython 实现见 `os/passport/display.py` 的 `VENDOR_INIT`。

---

## 5. 音频（ES8311）

| 项 | 值 |
| --- | --- |
| I²C 地址 | `0x18`（7 位） |
| I²S 端口 | I2S0，**Slave 模式**（时钟由 ESP32-C3 主出） |
| PA 功放使能 | **未接 MCU**（常通），不需要 GPIO 控制 |
| 原厂自检配置 | STD 格式，16 bit，MONO，slot_mask 0x1，`mclk_multiple = 256` |

原厂固件的自检走**回环**：TX 发 1000 Hz 正弦，RX 收回来量幅度
（`tone_mag=3422`，静音基线 mag=1），说明音频通路完好。

> **MCLK 的争议**：`audio.py` 目前走 BCLK 倍频（`use_mclk=False`），
> 理由写的是「MicroPython 的 `machine.I2S` 不接受 `mck` 参数」。
> 但 v1.29.0 官方文档明确有 `mck=None`（v1.24 起就有），原厂日志也是
> `mclk_multiple: 256`。这条结论**很可能误诊**，见
> [`known-issues.md`](known-issues.md) #11。

---

## 6. 电池（CW2017）

| 项 | 值 |
| --- | --- |
| I²C 地址 | `0x63`（7 位） |
| VERSION | 实测 `0x0F` |
| 电压换算 | `V(uV) = raw × 312.5`（14 bit 寄存器） |
| 出厂状态 | profile `4.2v_520mah` **已烧进芯片**，`UPDATE_FLAG` 已置位 |
| 实测 | 85% / 4085 mV（与原厂固件报的 85% / 4026 mV 吻合） |

原厂固件空闲 5 分钟进深睡（`standby_time=300`）。

---

## 7. NFC

NTAG213，**被动标签，不连 MCU**：固件既不读也不模拟它。

⚠ **它不是防伪凭证** —— NDEF 内容任何 NFC 写入器都能改，UID 也能被
magic tag 克隆。只能用来「碰一碰跳个链接」。

---

## 8. 内存实况

| 项 | 值 |
| --- | --- |
| 启动时可用堆 | 约 188 KB |
| 原厂固件跑起来后 | 约 66 KB |
| PassportOS 自检后 | 约 110 KB |
| 实际能用来存音频 | **约 40 KB**（还要留 RESERVE） |

**没有 PSRAM 是这块板子所有内存问题的根源**，也是后面一堆设计取舍的原因 ——
参见 [`pitfalls.md`](pitfalls.md) 的内存相关条目。
