# AI Passport —— Code Wiki

> 本文档是整个 `AI-Passport` 仓库的结构化代码索引，涵盖项目整体架构、模块职责、关键类与函数、依赖关系与运行方式。
> 仓库提供两条独立可用的固件路线：
> - **路线 A**：烧录「小智」语音助手固件（ESP-IDF / C）
> - **路线 B**：刷入 MicroPython 运行 **PassportOS**，再用手机 PWA 通过 BLE 推送「小程序」

---

## 目录

1. [项目总览](#1-项目总览)
2. [硬件事实](#2-硬件事实)
3. [目录结构](#3-目录结构)
4. [路线 B：PassportOS（MicroPython）](#4-路线-bpassportosmicropython)
   - 4.1 [启动流程](#41-启动流程)
   - 4.2 [核心模块](#42-核心模块)
   - 4.3 [系统外壳与运行时](#43-系统外壳与运行时)
   - 4.4 [内置小程序](#44-内置小程序)
5. [路线 B：PWA 手机端](#5-路线-bpwa-手机端)
6. [BLE 协议](#6-ble-协议)
7. [工具链](#7-工具链)
8. [路线 A：小智固件与上游 C 工程](#8-路线-a小智固件与上游-c-工程)
9. [依赖关系图](#9-依赖关系图)
10. [项目运行方式](#10-项目运行方式)
11. [限制与已知问题](#11-限制与已知问题)

---

## 1. 项目总览

### 1.1 项目定位

`AI-Passport` 是面向 **FoloToy AI Passport（TRAE 联名版）** 胸牌开发板的工具包，
硬件核心是 **ESP32-C3**（RISC-V 单核 160MHz，8MB Flash，无 PSRAM）。

仓库同时打包了：

- 官方「小智」语音助手固件（路线 A）
- 自研 **PassportOS**（MicroPython 操作系统，路线 B）
- 手机端 PWA「Passport 助手」（Web Bluetooth）
- 官方开发仓库源码 `upstream/`（ESP-IDF + LVGL BSP）
- Windows 一键刷机脚本

### 1.2 两条路线对比

| 维度 | 路线 A：小智语音助手 | 路线 B：PassportOS + PWA |
| --- | --- | --- |
| 固件 | `firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin` | `firmware/ESP32_GENERIC_C3-...bin` + `os/` |
| 语言 | C（ESP-IDF + LVGL） | MicroPython |
| 交互 | 语音「你好小智」+ 三键 | 三键菜单 + 手机推送小程序 |
| 联网 | 需 Wi-Fi + 小智服务端 | 小程序可完全离线运行 |
| 入口文档 | `README-刷机指南.md` | `README.md` + `docs/PROTOCOL.md` |

### 1.3 核心设计思想

- **PassportOS 的本质**：一个极简的「小程序运行时」。设备端只负责显示、按键、电池、BLE 推送，
  业务逻辑全部以 Python 小程序形式存在 `/apps/<name>/app.py`，可随时通过手机蓝牙热更新。
- **内存节俭**：ESP32-C3 无 PSRAM，可用堆约 150KB。240×320 RGB565 整屏缓冲需 153KB，放不下。
  因此 `display.py` 不使用整屏 framebuf，而是纯色块按扫描线推送、文字用小块 framebuf 渲染。
- **BLE 流控**：手机每发一片数据都等设备回 `ack` 再发下一片，避免设备端积压 OOM。
- **单一事实来源**：所有硬件参数集中在 `os/passport/config.py`（路线 B）和
  `upstream/components/bsp/include/bsp_pins.h`（路线 A），换板只改这一处。

---

## 2. 硬件事实

> 数值全部来自官方 BSP，是整个项目的硬件基础。

| 项目 | 规格 |
| --- | --- |
| 主控 | ESP32-C3，RISC-V 单核 160MHz |
| 存储 | 8 MB Flash，**无 PSRAM** |
| 屏幕 | 240×320 ST7789**P3**（厂商专属初始化序列） |
| 音频 | ES8311，I²S（**播放已实现**，录音未实现） |
| 按键 | UP/DOWN/OK 共用 GPIO0 ADC 电阻梯 |
| NFC | NTAG213 **被动标签，不连 MCU**（非防伪凭证） |
| USB | Type-C 原生 USB Serial/JTAG（GPIO18/19） |
| 电池 | CW2017 + 520mAh，独立硬件电源键 |

### 2.1 GPIO 引脚表

| GPIO | 用途 | | GPIO | 用途 |
| --- | --- | --- | --- | --- |
| 0 | 三键 ADC（兼 BOOT） | | 8 | LCD SCLK |
| 1 | LCD CS | | 9 | LCD MOSI |
| 2 | I²S DOUT（放音） | | 10 | I²C SDA |
| 3 | I²S WS | | 18/19 | USB Serial/JTAG |
| 4 | I²S DIN（录音） | | 20 | LCD DC |
| 5 | I²S BCLK | | 21 | 背光 PWM |
| 6 | I²S MCLK | | 7 | I²C SCL |

I²C 设备地址：ES8311 = `0x18`，CW2017 = `0x63`。

### 2.2 按键电压窗口

电路：3.3V ── 10k 外部上拉 ──┬── ADC(GPIO0) └── 按键 ── 分压 ── GND

| 按键 | 分压电阻 | 典型电压 | 判定窗口 (mV) |
| --- | --- | --- | --- |
| UP | 0Ω | 0 mV | 0 ~ 150 |
| DOWN | 1kΩ | ~300 mV | 150 ~ 447 |
| OK | 2.2kΩ | ~595 mV | 447 ~ 1900 |
| 松开 | 无 | ~3300 mV | > 1900 |

> ⚠ 绝不能改用内部上拉（约 45kΩ），会把三档挤到一起。

---

## 3. 目录结构

```
AI-Passport/
├── README.md                      ← 主说明（路线 B 为主）
├── README-刷机指南.md              ← 路线 A 刷机指南
├── LICENSE                        ← MIT
├── docs/
│   ├── PROTOCOL.md                ← BLE 协议 + 小程序 API 参考
│   ├── FACTORY_FIRMWARE.md        ← 原厂固件实测档案（AT 指令/自检/分区表）
│   ├── KNOWN_ISSUES.md            ← 已确认缺陷清单
│   ├── reference/es8311/          ← ES8311 官方驱动源码副本（寄存器序列出处）
│   └── CODE_WIKI.md               ← 本文档
├── firmware/                      ← 预编译固件
│   ├── folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin   (路线 A)
│   └── ESP32_GENERIC_C3-20260824-v1.29.0.bin        (路线 B 基础)
├── os/                            ← 路线 B：PassportOS 设备端源码
│   ├── boot.py                    ← 引导：建目录、加 sys.path
│   ├── main.py                    ← 入口：创建 Shell 并 run()
│   ├── passport/                  ← 核心系统模块（9 个）
│   │   ├── __init__.py            ← 版本号 1.0.0
│   │   ├── config.py              ← 硬件事实（引脚/电压/UUID/限制）
│   │   ├── display.py             ← ST7789P3 显示驱动 + 绘图 API
│   │   ├── buttons.py             ← 三键 ADC 读取 + 消抖
│   │   ├── battery.py             ← CW2017 电量计
│   │   ├── audio.py               ← ES8311 播放驱动（I2S + 寄存器序列）
│   │   ├── apps.py                ← 小程序存储/加载/列表
│   │   ├── blepush.py             ← BLE GATT 推送服务
│   │   └── ui.py                  ← 系统外壳 Shell + 运行时 Ctx
│   └── builtin/                   ← 5 个示例小程序
│       ├── clock/  dice/  sound/  sysinfo/  muyu/
├── pwa/                           ← 路线 B：手机端 PWA
│   ├── index.html                 ← 三标签页 UI
│   ├── app.js                     ← 主逻辑（BLE/推送/模板/持久化）
│   ├── style.css                  ← 样式
│   ├── sw.js                      ← Service Worker（离线缓存）
│   ├── manifest.webmanifest       ← PWA 清单
│   └── make_icons.py              ← 图标生成脚本（纯标准库）
├── miniapps/                      ← 小程序合集 + 打包/校验工具
│   ├── README.md                  ← 玩法、安装方式、写新程序的硬约束
│   ├── timer / reaction / snake   ← 6 个可直接推送的小程序
│   ├── metronome / memory / repeater
│   ├── _verify.py                 ← 离线校验器（边界/堆模型/异常/非 ASCII）
│   └── _bundle.py                 ← 把多个小程序打成一个安装包
├── tools/                         ← 开发/诊断工具（15 个，见 §7）
│   ├── deploy.py                  ← 经 mpremote 部署 PassportOS
│   ├── serve.py                   ← 本地托管 PWA（http://127.0.0.1:8790）
│   ├── esp.py                     ← esptool 包装（自动适配 v4/v5 命令名）
│   ├── test_protocol.py           ← BLE 协议单元测试（无需硬件）
│   └── ...
├── upstream/                      ← 官方开发仓库 main 分支快照（ESP-IDF + LVGL）
│   ├── main/                      ← demo 应用（main.c + demo_*.c）
│   ├── components/bsp/            ← 板级支持包（display/button/audio/battery/i2c）
│   ├── tests/                     ← 主机测试 + C 单元测试
│   ├── tools/                     ← 校验脚本
│   └── docs/                      ← 官方文档
└── windows/                       ← Windows 一键脚本（6 个）
```

---

## 4. 路线 B：PassportOS（MicroPython）

### 4.1 启动流程

```
上电
  │
  ├─ boot.py          建 /apps 目录，sys.path 追加 "/" 和 "/passport"，gc.collect()
  │
  └─ main.py
       │
       └─ from passport.ui import Shell
          shell = Shell()         ← 初始化 Display / Buttons / Battery / AppLink
          shell.run()             ← init_profile → link.start → _init_audio
                                     → refresh_apps → draw_menu → 主循环
               │
               └─ while True:
                    tick()          ← 20ms 一帧（~50fps）
                      ├─ link.poll()        处理 BLE 写入队列
                      ├─ battery.poll()     节流刷新电量（5s）
                      ├─ buttons.update()   读取按键 + 消抖
                      ├─ 菜单态 / 运行态分发
                      ├─ 若 BLE 连接则转发按键事件
                      └─ 每 300 帧 gc.collect()
```

启动失败时：打印异常并回到 REPL，按 `Ctrl-D` 软重启。

### 4.2 核心模块

#### 4.2.1 `config.py` —— 硬件事实唯一来源

文件：[os/passport/config.py](../os/passport/config.py)

集中定义所有硬件常量，换板只改此文件。

| 类别 | 关键常量 | 说明 |
| --- | --- | --- |
| 显示 | `LCD_W=240`, `LCD_H=320`, `LCD_BAUD=40MHz` | ST7789P3，SPI mode 0 |
| 显示 | `LCD_INVERT=True` | 本屏必须反色（INVON） |
| 按键 | `BTN_ADC_PIN=0`, `BTN_WINDOWS` | 三键电压窗口 |
| I2C | `I2C_SDA=10`, `I2C_SCL=7`, `I2C_FREQ=400k` | ES8311 + CW2017 共用 |
| I2C | `ADDR_ES8311=0x18`, `ADDR_CW2017=0x63` | 7 位地址 |
| 音频 | `I2S_MCLK/BCLK/WS/DOUT/DIN` | 播放已实现（BCLK 倍频，不用 MCLK） |
| BLE | `BLE_NAME`, `UUID_SERVICE/CMD/RSP`, `BLE_MTU=247` | GATT 服务定义 |
| BLE | `BLE_ATTR_MAX_LEN=2048`, `BLE_IDLE_TIMEOUT_MS=25000` | 特征值缓冲 / 空闲看门狗 |
| 存储 | `APPS_DIR="/apps"`, `MAX_APP_SIZE=32KB`, `MAX_APPS=12` | 小程序限制 |

#### 4.2.2 `display.py` —— ST7789P3 显示驱动

文件：[os/passport/display.py](../os/passport/display.py)

**设计取舍**：无 PSRAM，放不下 150KB 整屏 framebuf，所以：
- 纯色块按扫描线分块推送（`fill_rect`），内存占用与矩形高度无关
- 文字用小块 framebuf 渲染后整体推送（`text` / `text_scale`）

**关键常量**：
- `VENDOR_INIT`：ST7789P3 厂商专属初始化序列（PORCTRL/GCTRL/VCOMS/LCMCTRL/PWCTRL/伽马）。
  ⚠ 不是通用 ST7789 默认值，照抄通用序列屏幕不会正常显示。其中 `0xD0` 连发两次（第二次覆盖第一次）是参考例程原样。
- 颜色常量：`BLACK/WHITE/RED/GREEN/BLUE/YELLOW/CYAN/MAGENTA/GREY/SILVER/DARK/NAVY/ORANGE/TEAL`（RGB565）
- `rgb(r, g, b)`：24bit → 16bit RGB565

**`Display` 类**：

| 方法 | 说明 |
| --- | --- |
| `__init__(backlight=70)` | 初始化 SPI、CS/DC、背光 PWM，执行厂商初始化序列，点亮背光 |
| `_cmd(cmd, data)` | 发送 ST7789 命令 |
| `set_window(x0,y0,x1,y1)` | 设置 RAM 写入窗口 |
| `fill_rect(x,y,w,h,color)` | 纯色矩形（按扫描线分块） |
| `fill(color)` | 整屏填充 |
| `hline/vline/rect` | 直线 / 描边矩形 |
| `blit(buf,x,y,w,h,swap=True)` | 推送 RGB565 缓冲（默认做字节序翻转） |
| `text(s,x,y,color,bg)` | 8×8 ASCII 文字 |
| `text_scale(s,x,y,color,bg,scale)` | 整数倍放大文字（逐行构造，峰值内存仅一条扫描线） |
| `text_center(s,y,color,bg,scale)` | 水平居中文字 |
| `progress(x,y,w,h,pct,fg,bg)` | 进度条 |
| `splash(title, sub)` | 开机画面 |
| `backlight(pct)` | 背光 0~100（LEDC PWM） |

> 中文显示限制：固件自带 8×8 点阵字体只有 ASCII，中文需自备点阵字库分块送显。

#### 4.2.3 `buttons.py` —— 三键电阻梯

文件：[os/passport/buttons.py](../os/passport/buttons.py)

**`Buttons` 类**：

| 方法 | 说明 |
| --- | --- |
| `raw_mv()` | 多次采样（默认 4 次）取中位数，滤 ADC 抖动 |
| `_classify(mv)` | 按 `config.BTN_WINDOWS` 判定键名 |
| `update()` | 刷新状态，返回**本次新按下**的键名（连续 2 次一致才算数，实现消抖） |
| `current()` | 当前按住且已消抖的键名 |
| `voltage()` | 最近一次原始 mV |
| `check()` | 诊断用：返回 `(mV, 键名)` |

消抖策略：维护 `_candidate`（候选键）和 `_stable`（稳定键），候选连续 2 帧一致才升级为稳定。

#### 4.2.4 `battery.py` —— CW2017 电量计

文件：[os/passport/battery.py](../os/passport/battery.py)

**关键常量**：
- `PROFILE`：80 字节电池 profile（取自官方 BSP），必须写入才能算出 SOC
- 寄存器：`REG_VERSION=0x00`, `REG_VCELL_H=0x02`, `REG_SOC_H=0x04`, `REG_CONFIG=0x08`, `REG_PROFILE=0x10`

**`Battery` 类**：

| 方法 | 说明 |
| --- | --- |
| `_probe()` | 读 version 寄存器，探测设备是否在线 |
| `init_profile()` | 写入 80 字节 profile，置 UPDATE_FLAG 触发 SOC 重算（只需一次） |
| `poll(force=False)` | 节流刷新（默认 5s 一次），读 SOC 和电压 |
| `percent` (property) | 电量百分比 0~100 |
| `millivolts` (property) | 电池电压 mV |
| `label()` | 返回 `"87%"` 或 `"4102mV"` 或 `"--"` |

电压换算：`V(uV) = raw * 312.5` → `mV = raw * 3125 // 10000`。

#### 4.2.5 `apps.py` —— 小程序存储与加载

文件：[os/passport/apps.py](../os/passport/apps.py)

**目录结构**：
```
/apps/<name>/app.py        小程序源码
/apps/<name>/meta.json     {"title": ..., "bytes": N}
/apps/<name>/kv.json       小程序自己的持久化数据
```

**函数**：

| 函数 | 说明 |
| --- | --- |
| `valid_name(name)` | 名字规则 `[a-z0-9_-]{1,16}` |
| `app_dir(name)` / `app_file(name)` / `meta_file(name)` | 路径拼接 |
| `list_apps()` | 返回 `[{"n","title","s"}, ...]`，按名字排序 |
| `free_space()` | 剩余 Flash 字节数 |
| `delete_app(name)` | 删除 app.py / meta.json / kv.json 及目录 |
| `make_app(name, title)` | 为上传做准备，返回 `(路径, 文件对象)` |
| `write_meta(name, title, size)` | 写 meta.json |
| `read_source(name)` | 读取源码 |
| `load_module(name)` | 用 `exec(compile(...))` 在新命名空间执行 app.py，返回该命名空间 dict |

小程序契约（钩子全部可选）：
```python
TITLE = "Clock"
def setup(ctx): ...      # 启动一次
def loop(ctx): ...       # 主循环反复调用
def on_key(ctx, key): ...  # key ∈ "up"/"down"/"ok"
def teardown(ctx): ...   # 退出前调用一次
```

#### 4.2.6 `blepush.py` —— BLE 小程序推送服务

文件：[os/passport/blepush.py](../os/passport/blepush.py)

**GATT 定义**（来自 config）：
- 服务 UUID：`7a5c0001-0000-4000-8000-70617373706f`
- CMD 特征（写）：`...0002...`
- RSP 特征（通知）：`...0003...`
- 末 12 位 `70617373706f` = ASCII `passpo`

**`AppLink` 类**（host = Shell）：

| 方法/属性 | 说明 |
| --- | --- |
| `start()` | 激活 BLE、注册 GATT 服务、开始广播 |
| `_advertise()` | 主广播放 flags + 128bit UUID（21 字节），扫描响应放设备名 |
| `_irq(event, data)` | 中断回调：连接/断开/写入/MTU 交换，只做入队 |
| `poll()` | 主循环消费 `_rx` 队列，调用 `_handle` |
| `_handle(payload)` | 上传中走 `_handle_data`，否则解析 JSON 命令 |
| `_handle_cmd(cmd)` | 命令分发：hello/ls/put/end/abort/run/stop/rm/state/time/ping |
| `_handle_data(payload)` | 追加写文件，发 ack，满了自动 done |
| `_finish_upload()` / `_abort_upload()` | 收尾 / 中断（删除半截目录） |
| `_write_rsp(text)` | 通知分片：`~` 未完 / `!` 末片 / `{` 单包 |
| `send(obj)` | `json.dumps` 后通知 |
| `log_line(msg)` | 推日志给手机 |
| `connected` / `uploading` | 状态属性 |
| `status_text()` | 状态栏文本 |

**上传流程**：
1. 手机发 `{"t":"put","n","s","title"}` → 设备创建文件，回 `put`
2. 进入数据模式：CMD 写入不再解析 JSON，直接追加写文件
3. 每片手机等 `{"t":"ack","g":已收字节}` 再发下一片
4. 收满 `s` 字节自动 `done`，写 meta.json
5. 中途可识别 `abort`/`end`/`put`/`stop` 控制命令（代价：分片边界上恰好相同的短 JSON 会被误判，概率极低）

**中断与断开**：`abort` 或 BLE 断开都会删除半截应用目录，避免菜单出现僵尸条目。

#### 4.2.7 `audio.py` —— ES8311 播放驱动

文件：[os/passport/audio.py](../os/passport/audio.py)

寄存器序列**逐条照抄**官方 `espressif/esp_codec_dev` v1.6.2 的
`device/es8311/es8311.c`（原厂固件用的就是这个版本），源码副本在
`docs/reference/es8311/`。顺序不能随意改：`open() → set_fs() → start()` 有依赖，
跳步会静音或出噪声。

| 方法 | 说明 |
| --- | --- |
| `Audio(rate=16000, use_mclk=False)` | 构造即初始化；失败不抛异常，看 `.ok` / `.error` |
| `ok` / `error` / `volume` / `rate` | 状态属性 |
| `tone(freq, ms=200)` | 纯音（1024 点正弦表 + 淡入淡出防爆音），`freq=0` 为休止 |
| `melody(notes, bpm=120)` | 依 `NOTES` 音名表逐音播放 |
| `set_volume(pct)` | 0~100；0 = 静音，1~100 → −40 dB ~ 0 dB（REG32 线性 dB 映射） |
| `mute(on)` / `suspend()` | 静音 / 低功耗前关 DAC+ADC |
| `play_raw(data)` | 直接写 16bit 单声道 PCM 字节串 |
| `deinit()` | 释放 I2S |

**为什么 `use_mclk=False`（默认）**：让 ES8311 从 BCLK 倍频出内部 DIG_MCLK，
绕开 GPIO6 上的 MCLK。官方驱动的 `es8311_config_sample()` 本就有这个分支。
⚠ 代码注释与 README 把原因写成「MicroPython 的 `machine.I2S` 不接受 `mck` 参数」，
但 **v1.29.0 官方文档明确有 `mck=None`**（v1.24 起就有），原厂固件也是
`mclk_multiple: 256`。这条结论很可能误诊，值得重测 —— 见
[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) #11。

**已知限制**：`tone()`/`melody()` 阻塞且纯 Python 生成波形；缓冲区按时长一次性分配，
`ms` 超过约 3000 会 OOM（[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md) #10）。
录音未实现（第二个 I2S 实例无法共享时钟）。

### 4.3 系统外壳与运行时

文件：[os/passport/ui.py](../os/passport/ui.py)

#### 4.3.1 `Ctx` 类 —— 小程序运行时上下文

| 成员/方法 | 说明 |
| --- | --- |
| `lcd` | Display 对象 |
| `w` / `h` | 屏幕宽高 |
| `frame` | 自启动以来的帧序号 |
| `battery` | Battery 对象（`ctx.battery.label()`） |
| `audio` | Audio 对象，**可能为 `None`**（初始化失败），用前必须判空 |
| `shell` | 系统外壳（如 `ctx.shell.link.connected`） |
| `name` | 当前小程序名 |
| `log(msg)` | 推日志到手机 App |
| `exit()` | 请求退回主菜单 |
| `kv_get(k, default)` / `kv_set(k, v)` | 掉电保持存储（`/apps/<n>/kv.json`） |
| `kv_flush()` | 写回 kv.json |

#### 4.3.2 `Shell` 类 —— 系统外壳

| 方法 | 说明 |
| --- | --- |
| `__init__()` | 创建 Display（splash "PassportOS booting..."）/ Buttons / Battery / AppLink |
| `refresh_apps()` | 刷新小程序列表，夹紧选择/滚动 |
| `launch(name)` | 加载 app.py，调用 setup，通知手机 state |
| `stop_app()` | 调用 teardown，kv_flush，通知手机 |
| `_call(name, *args)` | 安全调用小程序钩子（异常记日志不崩溃） |
| `draw_menu()` / `_draw_row()` | 绘制主菜单（标题栏/状态栏/列表/页脚） |
| `draw_status(force)` | 状态栏：BLE 状态 + 电量 |
| `tick()` | 主循环单帧：poll BLE + 电池 + 按键 → 菜单/运行分发 |
| `_tick_menu(key)` | 菜单态：UP/DOWN 移动，OK 启动 |
| `_tick_app(key, held)` | 运行态：转发按键给 app，长按 OK 1.2s 退出 |
| `run()` | 主循环：`while True: tick(); sleep_ms(20)` |

**交互模型**：
- 菜单：UP/DOWN 移动光标，OK 启动
- 运行中：按键原样交给小程序；长按 OK > 1200ms 退回主菜单
- 报错页：任意键返回

### 4.4 内置小程序

| 小程序 | 文件 | 功能 | 演示要点 |
| --- | --- | --- | --- |
| Clock | [os/builtin/clock/app.py](../os/builtin/clock/app.py) | 数字时钟 | RTC 未对时退化为开机计时；`time.localtime()` |
| Dice | [os/builtin/dice/app.py](../os/builtin/dice/app.py) | 骰子 | 按键交互 + 图形绘制（点位图 LAYOUT） |
| Sound | [os/builtin/sound/app.py](../os/builtin/sound/app.py) | 发声玩具 | `ctx.audio` 判空、`tone()` 与 `melody()`、五声音阶 |
| Mu Yu | [os/builtin/muyu/app.py](../os/builtin/muyu/app.py) | 木鱼计数器 | 渐变圆绘制、局部重画做敲击反馈、`kv_*` 节流落盘、跨天归零 |
| System | [os/builtin/sysinfo/app.py](../os/builtin/sysinfo/app.py) | 系统信息 | 读 `gc.mem_free()` / 电量 / 运行时长 / BLE 状态 |

每个小程序目录含 `app.py` 和 `meta.json`（`{"title": "..."}`）。

---

## 5. 路线 B：PWA 手机端

目录：[pwa/](../pwa/)

### 5.1 架构

纯静态 PWA，通过 **Web Bluetooth** 连接设备。无需 APK/签名，本地起 HTTP 服务即可（`http://127.0.0.1` 被浏览器视为安全上下文）。

```
index.html  →  三标签页：应用 / 编辑器 / 日志
app.js      →  BLE 连接、命令收发、推送分片、模板、localStorage 持久化
style.css   →  深色主题样式
sw.js       →  Service Worker：缓存优先 + 后台更新（断网可用）
manifest.webmanifest → PWA 清单（standalone 模式）
```

### 5.2 `app.js` 关键逻辑

文件：[pwa/app.js](../pwa/app.js)

**全局状态**：
- `device/server/cmdChar/rspChar`：GATT 连接对象
- `connected`：连接标志
- `waiter`：当前等待响应的 Promise
- `inbox`：早到消息队列
- `frag`：通知分片累积缓冲

**核心函数**：

| 函数 | 说明 |
| --- | --- |
| `connect(forceChooser)` | 先 `getDevices()` 直连已授权设备；失败或 `forceChooser` 时 `requestDevice({filters:[{services:[UUID]},{namePrefix:'Passport'}]})` |
| `pickRemembered()` | 从 `navigator.bluetooth.getDevices()` 里挑 PassportOS（**仅桌面 Chrome 实现，Android 上没有**） |
| `startHeartbeat()` / `stopHeartbeat()` | 每 10 秒 ping 一次，给设备端 25 秒空闲看门狗续命 |
| `openGatt()` | 连接 GATT、取 CMD/RSP 特征、开通知、发 hello、拉列表 |
| `onNotify(event)` | 处理 `~`/`!` 分片，重组 JSON 后 `onMsg` |
| `onMsg(m)` | 分发 log/key/state/err，匹配 waiter 或入 inbox |
| `awaitMsg(types, timeout)` | 等待指定类型响应（支持超时） |
| `sendCmd(obj)` | 写 JSON 命令到 CMD 特征 |
| `pushApp(name, title, source, alsoRun)` | 推送小程序（put → 分片数据 → ack/done → end 兜底） |
| `refreshApps()` / `runApp()` / `removeApp()` / `syncTime()` / `stopApp()` | 各命令封装 |

**推送分片策略**：起始 160 字节，写失败减半重试，最小 20 字节。每片等 `ack`。

**模板**：`TEMPLATES` 对象内置 7 个模板（最小示例/按键计数器/滚动色带/时钟/骰子/设备信息/木鱼），一键填入编辑器。

**持久化**：`localStorage` 键 `passport_editor_v1` 保存编辑器内容。

### 5.3 `sw.js` Service Worker

- 缓存名 `passport-pwa-v6`（**改动 pwa/ 下任何资源后必须递增这个版本号**，否则浏览器会一直用旧缓存）
- 缓存资源：index.html / style.css / app.js / manifest / icons
- 策略：缓存优先，后台更新；断网时返回缓存

---

## 6. BLE 协议

完整文档见 [docs/PROTOCOL.md](PROTOCOL.md)。

### 6.1 分帧

RSP 通知按首字节区分：

| 首字节 | 含义 |
| --- | --- |
| `{` | 单包完整 JSON |
| `~` | 分片，后面还有 |
| `!` | 末片，累积后解析 |

### 6.2 命令一览（手机 → 设备）

| 命令 | 作用 | 设备响应 |
| --- | --- | --- |
| `{"t":"hello"}` | 握手 | `{"t":"hi","os","name","apps","free","mtu"}` |
| `{"t":"ls"}` | 列出小程序 | `{"t":"ls","apps":[{"n","title","s"}]}` |
| `{"t":"put","n","s","title"}` | 开始上传 | `{"t":"put","n","s"}`，进入数据模式 |
| `{"t":"end"}` | 结束上传（兜底） | `{"t":"done","n","s"}` |
| `{"t":"abort"}` | 放弃上传 | `{"t":"abort","n"}` |
| `{"t":"run","n"}` | 启动小程序 | `{"t":"run","n","ok"}` |
| `{"t":"stop"}` | 停止 | `{"t":"stop","ok":true}` |
| `{"t":"rm","n"}` | 删除 | `{"t":"rm","n","ok"}` |
| `{"t":"state"}` | 查询状态 | `{"t":"state","app"\|null}` |
| `{"t":"time","epoch","tz"}` | 校准 RTC | `{"t":"time","ok"}` |
| `{"t":"ping"}` | 探活（也作心跳，App 每 10 秒一次） | `{"t":"pong"}` |
| `{"t":"bye"}` | **客户端告辞：设备立刻主动断开并恢复广播** | `{"t":"bye","ok":true}` |

### 6.3 设备主动推送

| 消息 | 时机 |
| --- | --- |
| `{"t":"log","m"}` | 小程序 `ctx.log()` 或系统事件 |
| `{"t":"key","k"}` | 按键按下 |
| `{"t":"state","app"}` | 运行状态变化 |
| `{"t":"err","m"}` | 命令出错 |

### 6.4 限制

| 限制 | 值 | 定义位置 |
| --- | --- | --- |
| 单个小程序大小 | 32 KB | `config.MAX_APP_SIZE` |
| 小程序数量 | 12 | `config.MAX_APPS` |
| 名字规则 | `[a-z0-9_-]{1,16}` | `apps.valid_name()` |
| 通知单包 | ≤ MTU-3（上限 180） | `blepush._write_rsp` |
| CMD 特征值缓冲 | 2048 字节（**默认只有 20，必须显式放大**） | `config.BLE_ATTR_MAX_LEN` |
| 连接空闲看门狗 | 25 秒（App 每 10 秒 ping 续命） | `config.BLE_IDLE_TIMEOUT_MS` |
| 手机写入分片 | 起始 160，失败减半，最小 20 | `pwa/app.js` |

---

## 7. 工具链

### 7.0 工具清单

| 脚本 | 跑在哪 | 作用 |
| --- | --- | --- |
| `deploy.py` | 电脑 | 经 mpremote 部署 PassportOS 到设备（含回读校验） |
| `esp.py` | 电脑 | esptool 包装，自动适配 v4（下划线）/ v5（连字符）命令名 |
| `serve.py` | 电脑 | 本地托管 PWA（`http://127.0.0.1:8790`） |
| `serial_probe.py` | 电脑 | 读串口日志 / 触发复位，看原厂固件输出 |
| `ble_client.py` | 电脑 | 命令行 BLE 客户端：scan / console / push / run / rm / time / repro |
| `ble_scan_detail.py` | 电脑 | 扫描并打印广播详情（RSSI、服务 UUID、厂商数据） |
| `ble_dump_gatt.py` | 电脑 | 连上后 dump 完整 GATT 服务/特征表 |
| `ble_disconnect_test.py` | 电脑 | 复现"主机抓着 BLE 链路不放"的问题 |
| `hw_selftest.py` | **设备** | 上板自检：屏幕/按键/电池/音频/内存/文件系统 |
| `test_protocol.py` | 电脑 | BLE 协议状态机单测（桩模块，无需硬件） |
| `repro_ping_corruption.py` | 电脑 | **复现 KNOWN_ISSUES #1**：上传期间心跳污染 `app.py`。exit 1 = 缺陷仍在 |
| `test_audio.py` | 电脑 | ES8311 寄存器序列 / 分频 / 音量 / I2S 参数单测 |
| `lint_micropython.py` | 电脑 | 静态拦截"CPython 有、MicroPython 没有"的 API |
| `check_pwa.py` | 电脑 | 前端 JS 语法 + DOM id 一致性检查 |
| `check_docs.py` | 电脑 | **文档↔代码一致性自检**（链接 / 数量 / 常量 / 协议 / API / 固件哈希） |

依赖：`ble_*` 需要 `bleak`；`deploy.py`/`esp.py` 需要 `mpremote` + `pyserial`。

### 7.1 `tools/deploy.py` —— 部署 PassportOS

文件：[tools/deploy.py](../tools/deploy.py)

底层走官方的 **mpremote**（`mpremote connect <port> fs cp ...`）。
**不要自己拿 pyserial 手搓 raw REPL** —— 本项目第一版就是这么写的，
每传一个分片要一次往返，实测会卡住并留下截断的文件。

```
python tools/deploy.py                 # 自动找端口
python tools/deploy.py COM21           # 指定端口
python tools/deploy.py --clean         # 先递归清空 /passport 和 /apps 再传
python tools/deploy.py --apps-only     # 只更新 /apps
python tools/deploy.py --no-builtin    # 不装示例小程序
python tools/deploy.py --dry-run       # 只打印计划（注意：仍会探测串口）
```

**流程**：
1. `find_port()`：按 VID 0x303A 或 "jtag"/"espressif" 自动识别
2. `collect()`：算出需要建的远程目录与文件映射（**排除 `__pycache__` 和 `.pyc`**）
3. 可选 `--clean` 递归删除设备上的 `/passport` 与 `/apps`
4. 逐文件经 mpremote 传输
5. **回读设备上的文件大小并与本地逐一比对**
6. 软复位设备

### 7.2 `tools/serve.py` —— 本地托管 PWA

文件：[tools/serve.py](../tools/serve.py)

```
python tools/serve.py            # 默认 8790
python tools/serve.py 9000
```

用 `http.server` 托管 `pwa/` 目录，加 `Cache-Control: no-store`，自动打开浏览器。

### 7.3 `tools/test_protocol.py` —— 协议单元测试

文件：[tools/test_protocol.py](../tools/test_protocol.py)

无需硬件，用桩模块顶替 `bluetooth` / `machine`，验证：
1. 握手 hello → hi
2. 上传分片 + 流控（每片 ack、done、落盘一致、meta 生成）
3. ls / run / stop / rm
4. 对时（合法/非法 epoch）
5. 错误处理（坏 JSON、非法应用名、超限体积、未知命令）
6. 上传中断 + 大响应分片（~/! 重组）
7. 断开重连重新广播

```
python tools/test_protocol.py
```

README 标注 **28 项自测全通过**。

### 7.4 `tools/esp.py` —— esptool 命令名适配层

文件：[tools/esp.py](../tools/esp.py)

esptool v4 用下划线（`write_flash` / `erase_flash`），v5 改成连字符
（`write-flash` / `erase-flash`）。本脚本探测主版本号后重写命令与取值，
让 `windows/*.bat` 和文档里的命令在两个大版本下都能用。

```
python tools/esp.py --chip esp32c3 --baud 460800 erase_flash
python tools/esp.py --chip esp32c3 --baud 460800 write_flash -z 0x0 firmware/xxx.bin
```

### 7.5 `tools/ble_client.py` 与 BLE 诊断三件套

| 脚本 | 用途 |
| --- | --- |
| `ble_client.py` | 全功能命令行客户端（`scan` / `console` / `push` / `run` / `rm` / `time` / `info` / `repro`），用于在电脑上复现和调试协议，不依赖手机 |
| `ble_scan_detail.py` | 扫描并打印广播详情，确认名字是否落在扫描响应里 |
| `ble_dump_gatt.py` | dump 完整 GATT 表，排查"特征值找不到" |
| `ble_disconnect_test.py` | 复现"客户端断开后主机仍抓着链路"的问题 |

> **Windows 会缓存 GATT 服务表。** 设备改过特征值之后，Windows 侧可能仍用旧缓存，
> 报 `Characteristic ... was not found`，而设备日志显示两个特征值都注册成功。
> `bleak` 里传 `winrt=WinRTClientArgs(use_cached_services=False)` 可绕开
> （`ble_*` 脚本已默认启用）。浏览器端遇到同样症状，到
> 「设置 → 蓝牙和其他设备」删掉设备再连。

### 7.6 其它校验脚本

```sh
python tools/test_audio.py         # 90 项：ES8311 寄存器/分频/音量/I2S 参数
python tools/lint_micropython.py   # 扫 os/ 下 15 个设备端文件
python tools/check_pwa.py          # 前端 JS 语法 + DOM id 一致性
python tools/check_docs.py         # 文档↔代码一致性（改完文档/代码都该跑）
python tools/repro_ping_corruption.py   # 复现 KNOWN_ISSUES #1（修复前红、修复后绿）
python tools/hw_selftest.py        # 【在设备上跑】屏幕/按键/电池/音频/内存自检
python tools/serial_probe.py       # 读串口日志（原厂或 PassportOS）
```

`hw_selftest.py` 是通过 mpremote 推到设备上执行的，会画屏幕、放提示音，
**会打断正在运行的 PassportOS**。

---

## 8. 路线 A：小智固件与上游 C 工程

### 8.1 小智固件

- 文件：`firmware/folo-ai-passport-xiaozhi-v2.4.2-folo.1.bin`
- 基于 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) 2.4.2 适配
- SHA-256：`E19B35AD5EE7D8F9CFA9A22C51A69F25835EC90CE092A798D875915F23B275BA`
- 烧录地址：`0x0`（完整合并镜像）
- 烧录脚本：[windows/3-烧录小智固件.bat](../windows/3-烧录小智固件.bat)

使用：开机后屏幕显示 Wi-Fi 热点名 → 手机连接 → 配网 → 说「你好小智」唤醒。

### 8.2 上游 C 工程 `upstream/`

官方开发仓库 [FoloToy/ai-passport](https://github.com/FoloToy/ai-passport)（MIT），
基于 **ESP-IDF 5.5.3 + LVGL**。

#### 8.2.1 构建

```sh
cd upstream
idf.py set-target esp32c3
idf.py build
```

#### 8.2.2 目录结构

```
upstream/
├── main/                      ← demo 应用
│   ├── main.c                 ← app_main：初始化 + 菜单 + 按键分发
│   ├── demo.h                 ← demo_entry_t 接口定义
│   ├── demo_navigation.{c,h}  ← 导航状态机
│   ├── demo_*.c               ← 各演示页（display/button/audio/battery/wifi/ble/low_power）
│   └── ui_pixel.{c,h}         ← 像素风 UI 组件（screen/panel/mascot）
├── components/bsp/            ← 板级支持包
│   ├── include/               ← bsp_pins.h（硬件事实）/ bsp_display.h / bsp_button.h / bsp_audio.h / bsp_battery.h / bsp_i2c.h
│   └── src/                   ← 对应实现
├── tests/                     ← 主机测试（Python）+ C 单元测试
├── tools/                     ← validate.sh / check_repo.py / verify_firmware.py 等
└── docs/                      ← 官方开发文档
```

#### 8.2.3 `main.c` 架构

`app_main()` 流程：
1. `bsp_i2c_init()` + 扫描
2. `bsp_display_init()` + `bsp_lvgl_init()`（失败则退出）
3. `demo_navigation_init()`
4. 逐项初始化外设，结果存 `s_ok[]`（失败项菜单标 `[FAIL]`）
5. 建菜单，启动按键分发任务

按键分发：`bsp_button` 回调（在 esp_timer 任务）→ 入队 → `input_task` 消费 → `process_input` → `demo_navigation_handle`。

#### 8.2.4 `demo_entry_t` 接口

```c
typedef struct {
    const char *name;
    void (*enter)(void);          // 持 LVGL 锁创建页面
    void (*exit)(void);           // 停止服务后持锁删除页面
    void (*key)(btn, ev);         // 按键回调
    esp_err_t (*start)(void);     // 可选：启动慢服务（不持锁）
    esp_err_t (*stop)(void);      // 可选：停止 producer（不持锁）
} demo_entry_t;
```

7 个 demo：Display / Button / Audio / Battery / Wi-Fi / BLE / Low Power。

#### 8.2.5 BSP 模块

| 模块 | 头文件 | 职责 |
| --- | --- | --- |
| pins | `bsp_pins.h` | 所有 GPIO / 电压窗口 / I2C 地址（单一事实来源） |
| display | `bsp_display.h` | ST7789P3 SPI 初始化 + 厂商序列 + LEDC 背光 + LVGL 接入 |
| button | `bsp_button.h` | 三键 ADC 读取 + 消抖 + 长按检测 |
| audio | `bsp_audio.h` | ES8311 I2S 全双工 |
| battery | `bsp_battery.h` | CW2017 电量计 |
| i2c | `bsp_i2c.h` | I2C 总线初始化与扫描 |

#### 8.2.6 关键约束（来自 `AGENTS.md`）

- ESP32-C3，8MB Flash，无 PSRAM，ESP-IDF 5.5.3
- LVGL 非线程安全：非 LVGL 任务操作 lv_* 对象必须持 `bsp_lvgl_lock()`
- 按键回调必须非阻塞，慢操作放 worker 任务
- demo 退出前必须停止所有能访问 UI 的任务/定时器/回调
- 中文 UI 文字需自备字库（默认 Montserrat 无中文字形）

---

## 9. 依赖关系图

### 9.1 PassportOS 模块依赖

```
main.py
  └─ passport.ui.Shell
        ├─ passport.display.Display   ──► passport.config
        ├─ passport.buttons.Buttons   ──► passport.config
        ├─ passport.battery.Battery   ──► passport.config
        ├─ passport.audio.Audio       ──► passport.config
        ├─ passport.blepush.AppLink   ──► passport.config, passport.apps
        │      └─ host = Shell (launch/stop_app/current_app)
        ├─ passport.apps              ──► passport.config
        └─ passport.ui.Ctx            ──► 持有 shell 引用
```

### 9.2 外部依赖

| 组件 | 依赖 | 版本 |
| --- | --- | --- |
| PassportOS | MicroPython（ESP32-C3） | v1.29.0 |
| PassportOS | MicroPython 内置：`bluetooth`, `machine`, `framebuf`, `array`, `json`, `gc` | — |
| deploy.py / esp.py | `mpremote`, `pyserial` | pip install |
| 刷机脚本 | `esptool` | pip install（v4/v5 均可，经 `esp.py` 适配） |
| `ble_*` 诊断工具 | `bleak` | pip install |
| PWA | Web Bluetooth（Chrome/Edge；`http://127.0.0.1` 视为安全上下文） | — |
| 上游 C | ESP-IDF | 5.5.3 |
| 上游 C | LVGL | 随 IDF |
| make_icons.py | Python 标准库（struct/zlib） | — |

### 9.3 数据流（路线 B）

```
手机 PWA                    BLE GATT                 ESP32-C3 PassportOS
─────────                   ─────────                ────────────────────
editor 代码
  │
  ├─ put 命令 ──────────────► CMD 特征 ─────────────► AppLink._handle_cmd("put")
  │                                                    └─ apps.make_app() 建文件
  │
  ├─ 原始字节分片 ───────────► CMD 特征 ─────────────► AppLink._handle_data()
  │                                                    └─ 追加写 /apps/<n>/app.py
  │                                                    └─ 回 ack
  │
  ├─ 收满自动 done ◄────────── RSP 通知 ◄──────────── AppLink._finish_upload()
  │                                                    └─ apps.write_meta()
  │
  ├─ run 命令 ───────────────► CMD 特征 ─────────────► Shell.launch(name)
  │                                                    └─ apps.load_module() → exec
  │                                                    └─ 调用 setup(ctx)
  │                                                    └─ 主循环调用 loop(ctx)
  │
  └─ 日志/按键/状态 ◄───────── RSP 通知 ◄──────────── AppLink.send()
                                                         └─ Ctx.log / Shell 转发
```

---

## 10. 项目运行方式

### 10.1 路线 A：刷小智固件

按顺序双击 `windows/` 下脚本：

| 步骤 | 脚本 | 作用 |
| --- | --- | --- |
| 1 | `1-安装esptool.bat` | 安装 esptool + pyserial |
| 2 | `2-备份原厂固件.bat` | 备份 8MB 到 `backup/passport_original_8MB.bin` |
| 3 | `3-烧录小智固件.bat` | 哈希校验后烧录小智固件 |
| 4 | `4-查看串口日志.bat` | miniterm 看启动日志 |

### 10.2 路线 B：PassportOS + PWA

| 步骤 | 操作 |
| --- | --- |
| 1 | 双击 `windows/2-备份原厂固件.bat`（备份） |
| 2 | 双击 `windows/5-烧录PassportOS.bat`（擦除 → 写 MicroPython → deploy.py 上传系统文件） |
| 3 | 双击 `windows/6-启动Passport助手App.bat`（本地起 PWA，浏览器打开 `http://127.0.0.1:8790`） |
| 4 | App 点「连接」→ 选 PassportOS → 编辑器写代码 →「推送并运行」 |

手动命令等价：
```sh
# 统一用 tools/esp.py 调用 esptool —— 它会自动适配 v4(下划线) / v5(连字符) 两套命令名
python tools/esp.py --chip esp32c3 --baud 460800 erase_flash
python tools/esp.py --chip esp32c3 --baud 460800 write_flash -z 0x0 firmware/ESP32_GENERIC_C3-20260824-v1.29.0.bin

# 部署系统文件
python tools/deploy.py

# 启动 PWA
python tools/serve.py 8790
```

### 10.3 协议自测

```sh
python tools/test_protocol.py
```

### 10.4 上游 C 工程构建

```sh
cd upstream
idf.py set-target esp32c3
idf.py build
idf.py -p COMx flash monitor
```

### 10.5 开发调试

- 设备端出问题：USB 串口按 `Ctrl-C` 中断回到 MicroPython REPL
- 看串口日志：`python -m serial.tools.miniterm COMx 115200`
- 按键电压诊断：在 REPL 中 `from passport.buttons import Buttons; b=Buttons(); b.check()`

---

## 11. 验证状态与已知问题

### 11.1 电脑侧（无需硬件）

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| BLE 协议逻辑（上传/流控/分片/命令/错误处理） | ✅ 28 项通过 | `tools/test_protocol.py` |
| ES8311 寄存器序列 / 分频 / 音量 / I2S 参数 | ✅ 90 项通过 | `tools/test_audio.py` |
| MicroPython 兼容性静态检查 | ✅ 干净（15 个文件） | `tools/lint_micropython.py` |
| 前端 JS 语法 + DOM id 一致性 | ✅ 已校验 | `tools/check_pwa.py` |
| App 在手机 Chrome 加载 + Service Worker | ✅ 已实测 | — |
| Web Bluetooth 可用性 | ✅ 已实测 | — |

### 11.2 真机（COM3，已完成）

| 项目 | 结果 |
| --- | --- |
| 芯片识别 / 8MB Flash / MAC | ✅ ESP32-C3 rev v1.1，USB-Serial/JTAG |
| 原厂固件备份 + 回读校验 | ✅ 8,388,608 字节，哈希一致 |
| 刷入 MicroPython v1.29.0 | ✅ 写入哈希校验通过 |
| PassportOS 部署 | ✅ 19 个文件，逐文件回读大小一致 |
| 屏幕（ST7789P3 厂商序列） | ✅ 点亮，自检画面正常 |
| 按键 ADC | ✅ 2897 mV（松开态），识别正确 |
| 电池 CW2017 | ✅ ver=0x0F，85% / 4085 mV |
| 音频 ES8311 + I2S | ✅ 初始化成功并出声（880Hz / 1319Hz） |
| **手机 App 端到端 BLE 推送** | ⏳ **待验证**（需要手机实测） |

### 11.3 未实现 / 不支持

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| 麦克风录音 | ❌ 未实现 | 需要第二个 I2S 实例共享时钟，MicroPython 做不到 |
| 中文显示 | ❌ 不支持 | 固件 8×8 字体只有 ASCII，需自备点阵字库 |
| 长时间稳定性 | ❌ 未验证 | — |

### 11.4 已知缺陷

完整清单（含触发条件与影响面）见 **[`KNOWN_ISSUES.md`](KNOWN_ISSUES.md)**。
其中最严重的一条：**上传小程序时若超过 10 秒，手机端心跳 `{"t":"ping"}` 会被设备
当成源码写进 `app.py`，同时导致末尾字节被丢弃** —— 推送"成功"但程序跑不起来。

### 11.5 现场调试入口

1. **屏幕白屏/花屏**：ST7789P3 厂商序列若有一条不对就会这样。对照
   `os/passport/display.py` 的 `VENDOR_INIT` 与 `upstream/components/bsp/src/bsp_display.c`。
2. **按键不灵**：`os/passport/config.py` 的 `BTN_WINDOWS`。
   进主菜单按着键看串口，用 `Buttons.check()` 打印实际 mV 再调整窗口。
3. **BLE 连不上**：先在 REPL 里看 `[BLE]` 前缀的日志，确认
   `gatts_register_services` 是否返回了两个句柄。

**安全**：BLE 协议无认证无加密，蓝牙范围内谁都能读写小程序。设备上的 NTAG213
是普通被动标签，NDEF 内容可被任何 NFC 写入器修改，**不能作为防伪凭证**。

---

*文档日期：2026-09-19（与 `README.md` 的真机测试记录同步）*
*基于仓库版本：PassportOS v1.0.0 / 小智固件 v2.4.2-folo.1 / MicroPython v1.29.0*
*本文件由人工维护；改动代码后请同步更新，并跑 `python tools/check_docs.py` 自检。*
