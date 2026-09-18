# AI Passport 工具箱 —— TRAE × FoloToy 联名胸牌

你手上的这块牌子是 **FoloToy AI Passport（TRAE 联名版）**：一块基于 **ESP32-C3** 的
开源可穿戴 AI 开发板。这个文件夹里有两套完整方案 + 全部离线固件，**不需要 VPN**。

---

## 先选路线

| 路线 | 得到什么 | 难度 | 入口 |
| --- | --- | --- | --- |
| **A. 小智语音助手** | 说「你好小智」就能对话的 AI 助手，连 Wi-Fi 用 | ★ 很轻松 | [`README-刷机指南.md`](README-刷机指南.md) |
| **B. PassportOS + 手机 App** | 自制操作系统 + 用手机蓝牙推送「小程序」 | ★★ 需两步 | 本文往下看 |

两条路都从「备份原厂固件」开始 —— **先做备份，随时可退回原厂**。

---

## 目录

```
AI-Passport/
├── README.md                     ← 本文件
├── README-刷机指南.md            ← 路线 A：刷小智语音助手（含备份/排错）
├── docs/PROTOCOL.md              ← 路线 B：BLE 协议 + 小程序 API 参考
├── firmware/
│   ├── folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin        (路线 A)
│   └── ESP32_GENERIC_C3-20260824-v1.29.0.bin             (路线 B)
├── windows/                      ← Windows 一键脚本（1~6）
├── os/                           ← 路线 B：PassportOS 设备端源码
│   ├── boot.py  main.py
│   ├── passport/                 ← 显示/按键/电池/音频/BLE/外壳
│   └── builtin/                  ← 5 个示例小程序
├── pwa/                          ← 路线 B：手机端 App（Web Bluetooth）
├── tools/                        ← 部署/托管/测试/诊断脚本（15 个）
└── upstream/                     ← 官方开发仓库源码（ESP-IDF + LVGL）
```

---

## 路线 B 是什么

```
┌────────────────────┐   蓝牙推送小程序    ┌─────────────────────────┐
│  手机 Chrome       │  ───────────────▶  │  AI Passport            │
│  「Passport 助手」 │                    │  PassportOS (MicroPython)│
│  连接/编辑/运行/删除│  ◀───────────────  │  240x320 彩屏 + 三键     │
└────────────────────┘   日志/按键/状态    └─────────────────────────┘
```

- **PassportOS**：跑在板子上的系统。开机进主菜单，列出已装的小程序，
  三个物理键上下选择、OK 启动；运行中长按 OK 返回菜单。
- **Passport 助手**：手机上的 App。用 Web Bluetooth 连接设备，
  在线写代码 → 一键推送 → 设备上立刻能跑。**代码离线存在板子上。**

**为什么手机 App 不用装 APK**：Android Chrome **支持 Web Bluetooth**，
而 `http://127.0.0.1` 被浏览器视为安全上下文。所以 App 只要在本地起个静态服务就能跑，
可以「添加到主屏幕」当独立应用用，**不需要 Android SDK、不需要签名、不需要装任何东西**。

> 已验证：这台手机的 Chrome 成功加载了本地托管的 App，并报告 `Web Bluetooth 支持 ✅`。

---

## 路线 B 的完整步骤

### 第 1 步（电脑，一次性）：备份原厂固件
双击 `windows\2-备份原厂固件.bat`。**这一步能救命，别跳过。**

### 第 2 步（电脑，一次性）：烧录 PassportOS
双击 `windows\5-烧录PassportOS.bat`。它会自动：
1. 装 `esptool` / `pyserial`
2. 擦除 Flash，写入 MicroPython v1.29.0
3. 把 `os/` 下的系统文件上传到设备（`tools/deploy.py`）
4. 装上 5 个示例小程序，复位开机

看到设备屏幕出现 **Passport** 主菜单就成了。

### 第 3 步（手机，日常使用）：打开 App
- **方式一**：把 `pwa/` 拷到电脑，双击 `windows\6-启动Passport助手App.bat`，
  浏览器会自动打开 `http://127.0.0.1:8790/`。
- **方式二**：手机自己托管（就是你现在用的这套）——在手机上跑
  `python3 tools/serve.py 8790`，然后浏览器访问 `http://127.0.0.1:8790/`。

打开后建议点浏览器菜单里的「**添加到主屏幕**」，以后就是个独立 App。

### 第 4 步：推送第一个小程序
1. App 右上角点「**连接**」→ 选择 `PassportOS`
2. 切到「**编辑器**」页，点「载入模板」挑一个（比如「按键计数器」）
3. 回「应用」页填名称（如 `counter`）和标题
4. 点「**推送并运行**」——设备屏幕上立刻就跑起来了

---

## 写自己的小程序

最小的小程序长这样：

```python
TITLE = "Hello"

def setup(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text_center("HELLO", 140, 0x07FF, 0x0000, 2)

def on_key(ctx, key):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text_center(key.upper(), 140, 0xFFE0, 0x0000, 3)
```

可用的钩子：`setup(ctx)` / `loop(ctx)` / `on_key(ctx, key)` / `teardown(ctx)`。
完整的 `ctx` 和绘图 API 见 **[`docs/PROTOCOL.md`](docs/PROTOCOL.md)**。

---

## 硬件事实（来自官方 BSP，已核对）

| 项目 | 规格 |
| --- | --- |
| 主控 | ESP32-C3，RISC-V 单核 160MHz，**8MB Flash，无 PSRAM** |
| 屏幕 | 240×320 ST7789**P3**（厂商专属初始化序列，≠ 普通 ST7789） |
| 音频 | ES8311，I²S（**播放已实现**；录音未实现，见下） |
| 按键 | UP/DOWN/OK 共用 GPIO0 ADC 电阻梯：0mV / ~300mV / ~595mV，松开 ~3300mV |
| NFC | NTAG213 **被动标签，不连 MCU**；固件不读也不模拟它。⚠ 它不是防篡改凭证——NDEF 内容任何 NFC 写入器都能改，UID 也能被 magic tag 克隆 |
| USB | Type-C，原生 USB Serial/JTAG（GPIO18/19），**不是 CH340** |
| 电池 | CW2017 + 520mAh；**有独立硬件电源键**（关机后 USB 不枚举） |

| GPIO | 用途 | | GPIO | 用途 |
| --- | --- | --- | --- | --- |
| 0 | 三键 ADC（兼 BOOT） | | 8 | LCD SCLK |
| 1 | LCD CS | | 9 | LCD MOSI |
| 2 | I²S DOUT | | 10 | I²C SDA |
| 3 | I²S WS | | 18/19 | USB Serial/JTAG |
| 4 | I²S DIN | | 20 | LCD DC |
| 5 | I²S BCLK | | 21 | 背光 PWM（与 UART0 TX 冲突） |
| 6 | I²S MCLK | | 7 | I²C SCL（ES8311=0x18, CW2017=0x63） |

---

## 验证状态（重要，请如实看待）

### 电脑侧（无需硬件）

| 项目 | 状态 |
| --- | --- |
| BLE 协议逻辑（上传/流控/分片/命令/错误处理） | ✅ **28 项通过** —— `tools/test_protocol.py` |
| ES8311 寄存器序列 / 分频 / 音量 / I2S 参数 | ✅ **90 项通过** —— `tools/test_audio.py` |
| MicroPython 兼容性静态检查 | ✅ 干净 —— `tools/lint_micropython.py` |
| 前端 JS 语法 + DOM 元素 ID 一致性 | ✅ 已校验 |
| App 在手机 Chrome 加载 + Service Worker | ✅ 已实测 |
| Web Bluetooth 可用性 | ✅ 已实测 |

### 真机（COM3，已完成）

| 项目 | 结果 |
| --- | --- |
| 芯片识别 / 8MB Flash / MAC | ✅ ESP32-C3 rev v1.1，USB-Serial/JTAG |
| 原厂固件备份 + 回读校验 | ✅ 8,388,608 字节，哈希一致，另有 2 份异地副本 |
| **刷入 MicroPython v1.29.0** | ✅ 写入哈希校验通过 |
| **PassportOS 部署** | ✅ 19 个文件，逐文件回读大小校验一致 |
| **屏幕（ST7789P3 厂商序列）** | ✅ 点亮，自检画面正常 |
| **按键 ADC** | ✅ 2897 mV（松开态），识别正确 |
| **电池 CW2017** | ✅ ver=0x0F，85% / 4085 mV（与原厂固件 85%/4026mV 吻合） |
| **音频 ES8311 + I2S** | ✅ 初始化成功并出声（880Hz / 1319Hz） |
| 运行内存 | ✅ 自检后剩余 110 KB；分区可用 5,944 KB / 6,144 KB |
| **手机 App 通过 BLE 实际推送小程序** | ⏳ 待你实测（这需要手机在手边） |

### 真机上才发现的三个坑（已修）

1. **`array.byteswap()` 在 MicroPython 里不存在** —— CPython 有，所以电脑上
   跑测试完全正常，一烧到真机就
   `AttributeError: 'array' object has no attribute 'byteswap'`。
   已改为手写字节翻转（`display._to_be`），并加了 `tools/lint_micropython.py`
   静态拦截这类"CPython 有、MicroPython 没有"的 API。

2. **`machine.I2S` 不接受 `mck` 参数** —— 实测 `TypeError: extra keyword
   arguments given`，而且 `ibuf` 是必填的。所以 GPIO6 上的 MCLK 我们驱动不了，
   音频改用 ES8311 的 **BCLK 倍频**方案（`use_mclk=False`，官方驱动里本就有的分支）。
   若以后要用外部 MCLK，需要改用 ESP-IDF 而非 MicroPython。

3. **GATT 特征值默认只有 20 字节，超长写入被静默截断** —— 这是最坑的一个，
   表现为 App 报 **"命令不是合法 JSON"**。实测：客户端写 69 字节，
   设备只收到 20 字节（= 默认 ATT MTU 23 − 3），半截 JSON 自然解析失败。
   而 `put` 命令约 48 字节，必然中招 —— 所以连得上、握得了手，就是一推送就报错。
   修复：`gatts_set_buffer(cmd_handle, 2048)`（见 `config.BLE_ATTR_MAX_LEN`）。
   MicroPython 不会给你任何提示，必须自己显式设置。

> 附带发现：**Windows 会缓存 GATT 服务表**。设备改过特征值之后，
   Windows 侧仍用旧缓存，表现为 `Characteristic ... was not found`，
   而设备日志明确显示两个特征值都注册成功。
   `bleak` 里传 `winrt=WinRTClientArgs(use_cached_services=False)` 可绕开
   （`tools/ble_client.py` / `ble_dump_gatt.py` 已默认启用）。
   浏览器端如果遇到同样症状，到「设置 → 蓝牙和其他设备」删掉设备再连。

### 还没验证的

- 手机 App 端到端的 BLE 推送（需要你拿手机点一下「连接」）
- 麦克风录音（需要第二个 I2S 实例共享时钟，MicroPython 做不到）
- 长时间稳定性

> 已知缺陷（含 1 个会静默损坏上传源码的跨端 bug）整理在
> **[`docs/KNOWN_ISSUES.md`](docs/KNOWN_ISSUES.md)**，请一并阅读。

设备端出问题时的调试入口：USB 串口按 `Ctrl-C` 中断，回到 MicroPython REPL。

---

## 真机实测记录

2026-09-19 在实体设备上取得，非推测：

```
Chip type : ESP32-C3 (QFN32) rev v1.1
Features  : Wi-Fi, BT 5 (LE), Single Core, 160MHz, Embedded Flash 8MB (XMC)
USB mode  : USB-Serial/JTAG
MAC       : 4c:11:ae:xx:xx:xx
Flash     : 8MB (Manufacturer 0x20, Device 0x4017)
```

`BT 5 (LE)` 且**没有 Bluetooth Classic** —— 与规格一致，BLE 只能走 GATT。

### 原厂固件备份（已校验）

```
backup\passport_original_8MB.bin    8,388,608 字节
SHA256 4B45E499D87AF35C15A5BE7078782E689670201876FDEDE48E014B2D1C62F6EC
```

用 `read-flash 0 0x10000` 回读前 64KB 与备份比对，**哈希完全一致**，备份忠实可信。

### 原厂真实分区表（从备份解析）

| 分区 | 类型 | 偏移 | 大小 |
| --- | --- | --- | --- |
| nvs | data/nvs | `0x009000` | 24 KB |
| phy_init | data/phy | `0x00F000` | 4 KB |
| **factory** | app | `0x010000` | **3 MB** |
| imgstore | data | `0x310000` | 128 KB |
| imgframe | data | `0x330000` | 152 KB |
| **cardid** | data/nvs | `0x356000` | 144 KB |
| audio | data | `0x37A000` | 512 KB |
| imgava | data | `0x3FA000` | 1 MB |

最后分区在 `0x4FA000` 结束。

> 表中每一行的大小都等于「下一分区偏移 − 本分区偏移」，末地址也与解析结果吻合。
> （早期版本把 `cardid` 写成 16 KB，与 `audio` 的起始偏移对不上，已更正。）

> ⚠️ **纠正一个流传的说法**：网上攻略（含 TRAE 论坛那篇）说这块板有
> 「`0x700000` 的永久 Recovery 分区，刷坏也能救回来」。
> **实测不存在** —— `0x700000` 是纯 `0xFF`，整块 8MB 有 72% 是空的。
> 官方 upstream 的 `docs/CHANGELOG.md` 也明确记载
> *"Removed the obsolete app/test partition at `0x700000`"*。
>
> 结论：**没有板载恢复兜底，你手里的备份是唯一退路。**

---

## 出问题怎么办

| 现象 | 解决 |
| --- | --- |
| 脚本说找不到串口 | 换数据线 / 按电源键开机 / 关掉占用串口的软件 |
| 一直 `Connecting...` | 按住 UP 键不放 → 插 USB → 松开，再重跑 |
| 烧完黑屏 | 断电再上电（面板 GRAM 有残留） |
| 想退回原厂固件 | 见 `README-刷机指南.md` 第 6 节 |
| App 里点连接搜不到设备 | 确认设备已开机、屏幕显示 Passport 菜单；蓝牙需在系统里开启 |
| 推送卡住 | 看 App「日志」页；设备端 `Ctrl-C` 看报错 |

---

## 来源

- [FoloToy 官方开发仓库 ai-passport](https://github.com/FoloToy/ai-passport)（MIT，本仓库 `upstream/` 即此）
- [小智固件 folo-ai-passport-xiaozhi](https://github.com/FoloToy/folo-ai-passport-xiaozhi)（MIT）
- [MicroPython ESP32_GENERIC_C3](https://micropython.org/download/ESP32_GENERIC_C3/)
- [TRAE 论坛设备全解析](https://forum.trae.cn/t/topic/180446)
- [FOLOTOY 浏览器刷机工具](https://tool.folotoy.com/)
