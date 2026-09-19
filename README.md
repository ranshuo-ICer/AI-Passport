# AI Passport 工具箱

面向 **FoloToy AI Passport（TRAE 联名版）** 胸牌开发板的工具包。

这块板子的核心是 **ESP32-C3**（RISC-V 单核 160 MHz，**8 MB Flash，无 PSRAM**），
自带 240×320 彩屏、麦克风+喇叭、三个按键、电池和 Wi-Fi/BLE。
完整规格见 [`docs/hardware.md`](docs/hardware.md)。

本仓库提供**两条互相独立的固件路线**，外加一套可以自己写小程序的操作系统。

---

## 两条路线

| | **路线 A：小智 AI 语音助手** | **路线 B：PassportOS + 手机 App** |
| --- | --- | --- |
| 得到什么 | 说「你好小智」就能对话 | 自制系统 + 用手机蓝牙推送小程序 |
| 固件 | ESP-IDF / C，预编译镜像 | MicroPython + Python 源码 |
| 联网 | 需要 Wi-Fi + 小智服务端 | 小程序可**完全离线** |
| 适合 | 想直接用 AI 对话 | 想自己写东西玩 |
| 入口 | [`docs/flashing.md`](docs/flashing.md) §3 | 本文往下看 |

⚠ 两条路**互斥**：一次只能烧一个固件，互相切换要重新烧。备份在手随时能回。

---

## 路线 B 快速开始

**先备份。** 这块板**没有板载恢复兜底**，你手里的 8 MB 备份是唯一退路
（网上流传的「`0x700000` 有 Recovery 分区」是错的，实测那里是空白）。

| 步骤 | 在哪 | 做什么 |
| --- | --- | --- |
| 1 | 电脑（一次性） | 双击 `windows\2-备份原厂固件.bat` |
| 2 | 电脑（一次性） | 双击 `windows\5-烧录PassportOS.bat` —— 擦除、写 MicroPython、上传系统文件 |
| 3 | 手机（日常） | 打开「Passport 助手」，点「连接」 |
| 4 | 手机 | 编辑器里写代码 → 「推送并运行」 |

看到设备屏幕出现 **Passport** 主菜单就成功了。

> **第 3 步的 App 怎么打开**：它是个 **Web Bluetooth PWA**，需要一个本地 HTTP 服务
> （Web Bluetooth 要求安全上下文，`file://` 不算）。电脑上双击
> `windows\6-启动Passport助手App.bat` 即可。
> **打开一次后点「添加到主屏幕」**，Service Worker 会把界面缓存下来，
> 之后**即使服务没开也能启动**。

---

## 玩现成的小程序

[`miniapps/`](miniapps/README.md) 里有 6 个可以直接推上去玩的：

| 名字 | 玩法 |
| --- | --- |
| **Timer** | 秒表 + 倒计时，倒计时到点会响 |
| **Reaction** | 反应速度测试，整屏变色当刺激信号 |
| **Snake** | 贪吃蛇（两键相对转向） |
| **Metronome** | 节拍器 40–240 BPM，跑着也能调速度 |
| **Memory** | 记忆序列游戏，屏幕 + 声音双通道 |
| **Repeater** | 复读机：按一下录、再按一下循环放（实验性） |

那里还有两个工具：`_verify.py`（**推之前先离线校验**，不用硬件）和
`_bundle.py`（把多个小程序打成一个安装包 —— 手机 App 一次只能选一个文件）。

---

## 自己写

| 想做的事 | 看 |
| --- | --- |
| 写一个小程序（API、限制、协议） | [`docs/ble-protocol.md`](docs/ble-protocol.md) |
| 改 PassportOS 本身 | [`docs/passport-os.md`](docs/passport-os.md) |
| 改手机端 App | [`docs/pwa.md`](docs/pwa.md) |
| 用某个具体脚本 | [`docs/tools.md`](docs/tools.md) |

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

> ⚠ **源码只用 ASCII**。固件的 8×8 点阵字体没有中文字形；而且上传被截断时，
> 多字节字符会变成难以理解的 `UnicodeError` 而不是普通语法错误。

---

## 文档

完整索引见 [`docs/README.md`](docs/README.md)。按"我想干什么"找：

| 我想… | 看 |
| --- | --- |
| 知道板子有什么硬件 | [`docs/hardware.md`](docs/hardware.md) |
| 刷机（两条路线） | [`docs/flashing.md`](docs/flashing.md) |
| **少踩坑 / 排错** | [`docs/pitfalls.md`](docs/pitfalls.md) ← **建议先扫一遍** |
| 知道还有哪些缺陷没修 | [`docs/known-issues.md`](docs/known-issues.md) |
| 看原厂固件的实测参数 | [`docs/factory-firmware.md`](docs/factory-firmware.md) |

> 文档曾经由两个不同的 Agent 分别生成，导致同一件事被写了好几遍且互相矛盾。
> 现在已经按「一件事只写一遍」重构，并且加了
> [`tools/check_docs.py`](tools/check_docs.py) 做**自动一致性检查** ——
> 改代码后跑一下就知道文档有没有跟上。

---

## 当前状态

**电脑侧（无需硬件）**：

| 检查 | 结果 |
| --- | --- |
| `tools/test_protocol.py` | 28 项通过（BLE 协议状态机） |
| `tools/test_audio.py` | 90 项通过（ES8311 寄存器 / 分频 / 音量） |
| `tools/lint_micropython.py` | 干净（拦截「CPython 有、MicroPython 没有」的 API） |
| `tools/check_docs.py` | 11 项通过（文档一致性） |
| `tools/repro_ping_corruption.py` | **exit 1 = 缺陷仍在**（这是预期的，修好后会变 0） |

**真机已验证**：MicroPython 刷写、屏幕点亮、按键 ADC、电池读数（85% / 4085 mV）、
音频出声（880 / 1319 Hz）、小程序录音。

**还没验证**：手机 App 端到端 BLE 推送、麦克风录音质量、长时间稳定性。

> 已知缺陷（含一个会**静默损坏上传源码**的跨端 bug）全部记录在
> [`docs/known-issues.md`](docs/known-issues.md)，**没有藏**。

---

## 目录结构

```
AI-Passport/
├── README.md                 本文件
├── NOTICE.md                 第三方来源与许可证
├── docs/                     全部技术文档（索引见 docs/README.md）
├── firmware/                 两个预编译固件（已校验哈希）
├── windows/                  6 个 Windows 一键脚本
├── os/                       PassportOS 设备端源码（MicroPython）
│   ├── passport/             核心系统模块
│   └── builtin/              随系统发布的示例小程序（5 个）
├── pwa/                      手机端 App（Web Bluetooth）
├── miniapps/                 可推送的小程序合集 + 开发工具
├── tools/                    部署 / 托管 / 测试 / 诊断脚本（20 个）
└── upstream/                 官方开发仓库 main 分支快照（硬件事实来源）
```

---

## 许可与来源

本仓库自身代码为 **MIT**（见 [`LICENSE`](LICENSE)）。第三方组件（官方 BSP、
小智固件、MicroPython、ES8311 驱动）的来源与许可证见 [`NOTICE.md`](NOTICE.md)。

主要参考：

- [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport) —— 官方开发仓库（硬件事实来源）
- [FoloToy/folo-ai-passport-xiaozhi](https://github.com/FoloToy/folo-ai-passport-xiaozhi) —— 路线 A 固件
- [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) —— 小智语音助手
- [MicroPython ESP32_GENERIC_C3](https://micropython.org/download/ESP32_GENERIC_C3/)
