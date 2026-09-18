# TRAE × FoloToy AI Passport —— 刷成「小智」AI 语音助手（Windows 版）

> 本工具包已包含**官方固件**，无需自己去 GitHub 下载，也无需 VPN。
> 固件 SHA-256 已核对，与官方发布完全一致。

---

## 0. 一句话结论

你这块是 **FoloToy AI Passport（TRAE 联名版）**，本质是一块 **ESP32-C3 开发板**，
官方开源、可以直接烧录自定义固件。本工具包把它刷成 **小智语音助手**：
连上 Wi-Fi 后，说「**你好小智**」就能和它对话（用板载麦克风 + 喇叭）。

**必须在 Windows 电脑上操作**（手机刷不了，原因见文末附录）。

---

## 1. 你手上的设备是什么

| 项目 | 规格 |
| --- | --- |
| 主控 | ESP32-C3，RISC-V 单核 160 MHz |
| 存储 | 8 MB Flash，**无 PSRAM**（内存吃紧，别跑大 UI） |
| 屏幕 | 240×320 TFT，ST7789**P3**（厂商专属初始化序列，≠ 普通 ST7789） |
| 音频 | ES8311 编解码器，I²S 全双工（麦克风 + 喇叭） |
| 按键 | 3 个物理键 UP / DOWN / OK，共用 GPIO0 ADC 电阻梯 |
| 电池 | CW2017 电量计 + 520 mAh 锂电池；**有独立硬件电源键** |
| NFC | **NTAG213 被动标签**——不连 MCU，固件里没有 NFC 代码，只能手机碰读 |
| USB | Type-C，走**原生 USB Serial/JTAG（GPIO18/19）**，不是 CH340/CP2102 |
| 无线 | Wi-Fi 2.4 GHz + BLE（ESP32-C3 **不支持蓝牙经典**，只有 BLE） |

### 完整引脚表（来自官方 BSP `bsp_pins.h`）

| GPIO | 功能 | | GPIO | 功能 |
| --- | --- | --- | --- | --- |
| 0 | 三键 ADC（也是 BOOT 脚） | | 8 | LCD SCLK |
| 1 | LCD CS | | 9 | LCD MOSI（无 MISO） |
| 2 | I²S DOUT（放音） | | 10 | I²C SDA |
| 3 | I²S WS | | 18/19 | USB Serial/JTAG |
| 4 | I²S DIN（录音） | | 20 | LCD DC |
| 5 | I²S BCLK | | 21 | 背光 PWM（与 UART0 TX 冲突） |
| 6 | I²S MCLK | | 7 | I²C SCL（ES8311=0x18, CW2017=0x63） |

---

## 2. 烧录前必读 ⚠️

1. **会覆盖原厂固件**。本工具包第 2 步就是完整备份 8 MB，**请务必先做**。
   备份文件里包含原厂的设备身份分区（`cardid` @ `0x356000`），刷回去就能还原。
2. **电源键是硬件开关**。关机后 USB 不会枚举，电脑上看不到串口 —— 先按电源键开机。
3. **不要在烧录中断电**。
4. 官方目前验证过的是：编译、Flash 内容写入、基础启动和配网。
   **麦克风、喇叭、按键和长期稳定性官方建议按不同批次继续测试** —— 有抽奖可能。

---

## 3. 准备工作

- 一台 Windows 10/11 电脑（能上网）
- 一根**能传数据**的 Type-C 线（很多充电线不传数据，这是最常见的坑）
- [Python 3](https://www.python.org/downloads/)：安装时**务必勾选 `Add python.exe to PATH`**
  - 已经有 Python 就跳过；装完**重开**命令行窗口

---

## 4. 四步操作

把整个 `AI-Passport` 文件夹拷到电脑上（保持目录结构不变），然后按顺序双击：

### 步骤 1 —— `windows\1-安装esptool.bat`
自动装好烧录工具 `esptool`。看到 `[OK] 环境就绪` 即可。

### 步骤 2 —— `windows\2-备份原厂固件.bat`
把板子用 Type-C 线插到电脑，然后双击。
- 会往 `backup\passport_original_8MB.bin` 写一份完整备份（**约 8 MB，要几分钟，别拔线**）
- 结束后会自动校验 SHA-256 并显示

> 备份没成功**不要**往下走。

### 步骤 3 —— `windows\3-烧录小智固件.bat`
先自动核对固件哈希，再烧录。看到 `Hash of data verified.` + `Hard resetting` 就成了。

### 步骤 4 —— `windows\4-查看串口日志.bat`
看它启动时打印了什么，排错必备。按 `Ctrl + ]` 退出。

---

## 5. 烧完之后怎么用

1. 板子会自动重启，屏幕显示一个 **Wi-Fi 热点名**。
2. 手机连上那个热点（配网页面一般会自动弹出）。
   **没弹出就手动用浏览器打开 `http://192.168.4.1`**。
3. 在页面里选你家 Wi-Fi、填密码，并按提示填**小智服务端地址**
   （用官方小智服务，或你自己搭的服务器）。
4. 连上后：
   - 说「**你好小智**」唤醒（本地唤醒词，不需要联网）
   - **OK 键**：开始 / 停止对话
   - **UP / DOWN 键**：音量 +10 / −10
   - **开机时按住 OK 键**：进入配网模式

---

## 6. 出问题怎么办

| 现象 | 原因 / 解决 |
| --- | --- |
| 脚本说找不到串口 | ① 线是纯充电线 → 换根数据线 ② 板子关机了 → 按电源键开机 ③ 装一下驱动（见下） |
| 设备管理器里是什么 | 应显示 **`USB JTAG/serial debug unit`** 或「USB 串行设备 (COMx)」。Win10/11 一般免驱 |
| 有**两个** COM 口 | 这是原生 USB-JTAG 的正常现象，**挨个试**；插拔后端口号会变，别背端口号 |
| 一直 `Connecting...` | 手动进下载模式：**按住 UP 键不放 → 插 USB → 再松开**，然后重跑脚本 |
| 烧完黑屏 | ① 屏幕 GRAM 残留 → 断电再上电 ② 确认烧的是 `0x0` 地址的合并镜像 |
| 刷错想还原 | 跑 `python tools\esp.py --chip esp32c3 --port COM3 --baud 460800 write_flash 0x0 backup\passport_original_8MB.bin` |

**驱动**（只有认不出设备时才需要）：ESP32-C3 原生 USB-JTAG 在 Win10/11 免驱；
若显示为未知设备，装 [Espressif USB-JTAG 驱动](https://dl.espressif.com/dl/idf-driver/idf-driver-esp32-usb-jtag-2021-07-17.zip)。

---

## 7. 另一个更省事的选择：浏览器刷机

不想装 Python，可以用官方网页刷机工具 **[tool.folotoy.com](https://tool.folotoy.com/)**：
用 **Chrome / Edge** 打开 → 选择本工具包里的
`firmware\folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin` → 它会通过 Web Serial 写入。
优点是能实时看设备日志，固件不上传服务器。
**注意：这个工具在手机浏览器上用不了**（Android Chrome 不支持 Web Serial）。

---

## 8. 进阶：自己写固件

`upstream\` 目录是官方开发仓库 [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)（MIT）
**main 分支的源码快照**：ESP-IDF + LVGL 的完整 BSP，带 `bsp_pins.h` 与 7 个 demo 页
（Display / Button / Audio / Battery / Wi-Fi / BLE / Low Power）。

> ⚠ 快照里**没有 `.git`，也没有其它分支**。官方仓库在 GitHub 上另有
> `demo/stopwatch`、`demo/cat-themed-pomodoro-timer`、`demo/tetris-game`、
> `demo/claude-buddy-port` 等分支，需要自己 `git clone` 才能拿到。

```sh
git clone https://github.com/FoloToy/ai-passport.git
cd ai-passport
# 用 ESP-IDF 5.5.3 或 6.0.2
idf.py set-target esp32c3 && idf.py build
```

想改小智固件本身：仓库 [FoloToy/folo-ai-passport-xiaozhi](https://github.com/FoloToy/folo-ai-passport-xiaozhi)

```sh
python scripts/build.py folo/ai-passport-c3 --name folo-ai-passport-c3 --language zh-CN --wake-word nihaoxiaozhi
# 产物：build/merged-binary.bin（也是从 0x0 烧）
```

---

## 附录 A：本工具包内容

```
AI-Passport/
├── README.md                       ← 主说明（两条路线总入口）
├── README-刷机指南.md              ← 本文件（路线 A）
├── LICENSE                         ← MIT
├── docs/                           ← PROTOCOL / CODE_WIKI / FACTORY_FIRMWARE / KNOWN_ISSUES
├── firmware/
│   ├── folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin   ← 路线 A 官方固件（已校验）
│   └── ESP32_GENERIC_C3-20260824-v1.29.0.bin        ← 路线 B 基础固件
├── windows/                        ← 6 个一键脚本
├── os/  pwa/  tools/               ← 路线 B：设备端 / 手机端 / 工具链
└── upstream/                       ← 官方开发仓库 main 分支快照（248 个文件）
```

固件信息（官方 release `v2.4.2-folo.1`，发布于 2026-08-23）：

- 大小：7,742,498 字节
- SHA-256：`E19B35AD5EE7D8F9CFA9A22C51A69F25835EC90CE092A798D875915F23B275BA`
- 烧录地址：`0x0`（完整合并镜像，必须从 0x0 烧）
- 基于 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 2.4.2 适配

## 附录 B：为什么手机不能刷

已实测：DSHA 所在的 Android App 沙箱**看不到 `/dev/bus/usb`**，`/dev` 目录读取被拒，
桥接接口 `/app/*` 也不提供任何 USB / 串口 / BLE 能力。
Android 应用的 USB 访问必须走 Java `UsbManager` 授权，沙箱内无法绕过。
所以**刷机这一步必须在电脑上做**。
（理论上 root + Termux 可以，但性价比极低，不推荐。）
