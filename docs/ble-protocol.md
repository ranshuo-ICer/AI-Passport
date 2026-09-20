# PassportOS 协议与小程序 API

设备端（ESP32-C3 上的 MicroPython）与手机端（Web Bluetooth App）之间的契约。
两端实现分别位于 `os/passport/blepush.py` 和 `pwa/app.js`，改协议要同时改这两处。

---

## 1. 拓扑

```
   ┌──────────────────────┐        BLE GATT        ┌───────────────────────┐
   │  手机 Chrome         │  ───────────────────▶  │  AI Passport          │
   │  Web Bluetooth App   │   CMD 特征（写）        │  ESP32-C3             │
   │  (pwa/)              │  ◀───────────────────  │  PassportOS           │
   └──────────────────────┘   RSP 特征（通知）      └───────────────────────┘
```

BLE 只用来传小程序和少量状态；**小程序本身可以完全离线运行**，不依赖网络。

---

## 2. GATT 定义

| 项 | 值 |
| --- | --- |
| 广播名 | `PassportOS`（放在扫描响应里） |
| 服务 UUID | `7a5c0001-0000-4000-8000-70617373706f` |
| CMD 特征 | `7a5c0002-0000-4000-8000-70617373706f` — Write / WriteWithoutResponse |
| RSP 特征 | `7a5c0003-0000-4000-8000-70617373706f` — Notify |

末 12 位 `70617373706f` 是 ASCII 的 `passpo`，方便肉眼辨认。

**主广播**只放 flags + 128bit 服务 UUID（共 21 字节），**设备名放在扫描响应**里。
这样做的原因：31 字节的广播包塞不下「名字 + 128bit UUID」，而 Web Bluetooth 的
`filters:[{services:[...]}]` 依赖 UUID 出现在广播里。

MTU：设备启动时请求 247。手机侧必须容忍协商失败（退回 23），所以：

- 手机 → 设备：单次写入不要超过 160 字节；写失败就把分片减半重试（App 里已实现）。
- 设备 → 手机：单包通知上限 = `min(180, MTU-3)`，超长自动分片（见下）。

> ⚠ **已知缺陷**：MTU 未协商（保持默认 23）时，设备端仍按 180 字节发送，超过 ATT 上限
> （20 字节）。详见 [`known-issues.md`](known-issues.md) #3。

> **必须显式设置特征值缓冲长度。** MicroPython 的 GATT 特征值默认最大只有 20 字节
> （= 默认 ATT MTU 23 − 3），超过这个长度的写入会被**静默截断** —— 表现为设备收到半截
> JSON、回「命令不是合法 JSON」。而 `put` 命令约 48 字节，必然中招：连得上、握得了手，
> 就是一推送就报错。设备端因此调用
> `gatts_set_buffer(cmd_handle, BLE_ATTR_MAX_LEN)` 放大到 2048 字节。
> 这是本项目在真机上踩出来的最坑的一个问题，MicroPython 不会给任何提示。

---

## 3. 分帧

RSP 上的每条通知是一段 UTF-8 文本，按首字节区分：

| 首字节 | 含义 |
| --- | --- |
| `{` | 单包完整 JSON |
| `~` | 分片，后面还有 |
| `!` | 末片，累积到这里后解析 |

手机侧累积逻辑见 `pwa/app.js` 的 `onNotify()`。

> ⚠ **设备端发多片时，两片之间必须留间隔**（`blepush._FRAG_GAP_MS`，实测 50 ms）。
> 连续调 `gatts_notify` 时 ESP-IDF 的 TX 队列会满，而栈**照样返回成功**、
> 把中间几片静默丢掉 —— 客户端重组的 JSON 头尾都对、中间缺一段。
> 这个坑随"响应超过一包"才出现，详见 [pitfalls 3.7](pitfalls.md) 与
> [KNOWN_ISSUES #29](known-issues.md)。

---

## 4. 命令一览（手机 → 设备，JSON）

| 命令 | 作用 | 设备响应 |
| --- | --- | --- |
| `{"t":"hello"}` | 握手 | `{"t":"hi","os","name","apps","free","mtu"}` |
| `{"t":"ls"}` | 列出小程序 | `{"t":"ls","apps":[{"n","title","s"}]}` |
| `{"t":"put","n","s","title"}` | 开始上传，`s` 为字节数 | `{"t":"put","n","s"}`，之后进入数据模式 |
| `{"t":"end"}` | 结束上传（正常情况不需要，见下） | `{"t":"done","n","s"}` |
| `{"t":"abort"}` | 放弃上传 | `{"t":"abort","n"}` |
| `{"t":"run","n"}` | 启动小程序 | `{"t":"run","n","ok"}` |
| `{"t":"stop"}` | 停止当前小程序 | `{"t":"stop","ok":true}` |
| `{"t":"rm","n"}` | 删除小程序 | `{"t":"rm","n","ok"}` |
| `{"t":"state"}` | 查询运行状态 | `{"t":"state","app"\|null}` |
| `{"t":"time","epoch","tz"}` | 校准 RTC | `{"t":"time","ok"}` |
| `{"t":"ping"}` | 探活（也用作心跳） | `{"t":"pong"}` |
| `{"t":"bye"}` | **客户端告辞：设备立刻主动断开并恢复广播** | `{"t":"bye","ok":true}` |

> **`bye` 为什么必须有**：实测中心设备（Windows）在客户端调用 `disconnect()`
> 之后**仍然抓着 BLE 链路不放**，设备侧一分钟都收不到断开事件 —— 期间不广播，
> 表现就是"再也扫不到设备了"，只能重启设备。由设备端发起断开是唯一可靠的办法。
> 所以客户端收尾时**先发 `bye` 再断开自己**。
> 设备端另有 25 秒空闲看门狗兜底（崩溃/关标签页来不及说再见的情况）。

设备 **主动** 推送的消息：

| 消息 | 时机 |
| --- | --- |
| `{"t":"log","m"}` | 小程序调 `ctx.log()`、或系统事件 |
| `{"t":"key","k"}` | 按下了 up/down/ok |
| `{"t":"state","app"}` | 运行状态变化 |
| `{"t":"err","m"}` | 命令出错 |

---

## 5. 上传流程（关键部分）

`put` 之后，设备进入**数据模式**：CMD 特征上收到的字节**不再当 JSON 解析**，
而是直接追加写进 `/apps/<n>/app.py`。这样避免了 hex/base64 的 2 倍膨胀 ——
对 8MB Flash 的板子来说，省下的是实打实的传输时间。

```
手机                                        设备
 │  {"t":"put","n":"clock","s":4300}  ───▶
 │                                     ◀───  {"t":"put","n":"clock","s":4300}
 │  <160 字节原始数据>                 ───▶
 │                                     ◀───  {"t":"ack","g":160}
 │  ...重复，直到发满 4300 字节...
 │  <最后一片>                         ───▶
 │                                     ◀───  {"t":"done","n":"clock","s":4300}
```

**流控**：手机每发一片都等设备回 `ack` 再发下一片。这样设备端永远不会积压 ——
ESP32-C3 只有约 150KB 可用堆，没有流控的话几 KB 的积压就可能 OOM。

**自动收尾**：设备收满 `s` 字节就自动 `done`，不需要手机再发 `end`。
`end` 保留作为兜底（比如手机不确定最后一片是否送达）。

**中断**：数据模式下，设备仍会识别少数几个短 JSON 控制命令
（`abort` / `end` / `put` / `stop`）。这是为了能中途取消 —— 否则 abort 会被
当成源码写进文件，设备就永远退不出上传状态了。
代价是：如果 app.py 正好在一个分片边界上出现一模一样的短 JSON，会被误判。
概率极低，后果也只是这次上传作废。**abort / 断开连接都会删除半截的应用目录**，
避免菜单里留下点不动的僵尸条目。

---

## 6. 小程序契约

上传的文件是 `/apps/<name>/app.py`。可选钩子：

```python
TITLE = "Clock"                    # 显示在设备菜单上的标题

def setup(ctx):    ...             # 启动时调用一次
def loop(ctx):     ...             # 主循环反复调用（约 50fps）
def on_key(ctx, key): ...          # key ∈ "up" / "down" / "ok"
def teardown(ctx): ...             # 退出前调用一次
```

`loop()` 每帧都会被调用 —— **别在里面无条件整屏刷新**，会很慢。
惯用做法是 `if ctx.frame % 20: return` 做节流。

### ctx 提供的接口

| 成员 | 说明 |
| --- | --- |
| `ctx.lcd` | 显示对象，见下 |
| `ctx.w` / `ctx.h` | 屏幕宽高（240 / 320） |
| `ctx.frame` | 自启动以来的帧序号 |
| `ctx.name` | 当前小程序名（即 `/apps/` 下的目录名） |
| `ctx.battery` | `ctx.battery.label()` → `"87%"` 或 `"4102mV"` |
| `ctx.buttons` | 按键对象。`check()` → `(毫伏, 键名)`；`voltage()` 上一次读数；`current()` 当前按住的键。**诊断用**（重标定 `config.BTN_WINDOWS`） |
| `ctx.audio` | 音频对象，**可能为 `None`**（初始化失败时），用前必须先判空，见下 |
| `ctx.log(msg)` | 打印到手机 App 的日志页 |
| `ctx.exit()` | 请求退回主菜单 |
| `ctx.kv_get(k, default)` / `ctx.kv_set(k, v)` | 掉电保持的小存储（写 `/apps/<n>/kv.json`） |
| `ctx.kv_flush()` | 立即把 kv 写回磁盘（退出时系统会自动调用） |
| `ctx.shell` | 系统外壳（如 `ctx.shell.link.connected` 查蓝牙状态） |

小程序里**必须自己 import**（`import time` / `import gc` / `import random`），
不会从系统继承命名空间。

### ctx.lcd 绘图接口

| 方法 | 说明 |
| --- | --- |
| `fill(color)` | 整屏填充 |
| `fill_rect(x, y, w, h, color)` | 实心矩形 |
| `rect(x, y, w, h, color)` | 描边矩形 |
| `hline` / `vline` | 直线 |
| `text(s, x, y, color, bg)` | 8x8 ASCII 文字 |
| `text_scale(s, x, y, color, bg, n)` | n 倍放大（标题用 2~4） |
| `text2x(s, x, y, color, bg)` | 等价于 `text_scale(..., 2)` |
| `text_center(s, y, color, bg, n)` | 水平居中 |
| `progress(x, y, w, h, pct, fg, bg)` | 进度条 |
| `backlight(pct)` | 背光 0~100 |

颜色是 **RGB565 整数**，预置常量在 `passport/display.py`：
`BLACK WHITE RED GREEN BLUE YELLOW CYAN MAGENTA GREY SILVER DARK NAVY ORANGE TEAL`，
或用 `rgb(r, g, b)` 现算。

> **中文显示**：设备端用的是 MicroPython 固件自带的 8x8 点阵字体，**只有 ASCII**。
> 想在屏幕上显示中文，需要自己准备点阵字库（240x320 RGB565 整屏位图约 150KB，
> 而无 PSRAM 时可用堆只有约 150KB，所以要分块读取分块送显）。
> 这是当前版本明确的限制，不是 bug。

### ctx.audio 音频接口

ES8311 播放驱动见 `../os/passport/audio.py`。**`ctx.audio` 可能为 `None`**
（初始化失败时系统照常启动），所以每次使用前都要判空：

```python
def setup(ctx):
    if ctx.audio and ctx.audio.ok:
        ctx.audio.set_volume(75)
        ctx.audio.tone(880, 160)
```

| 方法 | 说明 |
| --- | --- |
| `ctx.audio.ok` | 布尔，初始化是否成功 |
| `ctx.audio.error` | 失败原因字符串（`ok` 为 False 时有意义） |
| `ctx.audio.tone(freq, ms=200)` | 播放纯音，`freq=0` 表示静音休止 |
| `ctx.audio.melody(notes, bpm=120)` | 播放旋律，`notes` 形如 `[("C5", 0.5), ("REST", 0.25)]` |
| `ctx.audio.set_volume(pct)` | 0~100；0 直接静音，1~100 映射到 −40 dB ~ 0 dB |
| `ctx.audio.mute(on)` | 静音开关 |
| `ctx.audio.play_raw(data)` | 直接播 16bit 单声道 PCM 字节串 |

音名表见 `passport.audio.NOTES`（C3~A6 + `REST`）。

> ⚠ **两个使用限制**：
> 1. `tone()` / `melody()` 是**阻塞**的，且在播放前会用纯 Python 逐样本生成波形。
>    长音会同时冻结界面和 BLE 处理，**建议单次不超过 1 秒**。
> 2. 缓冲区是按时长一次性分配的（16 kHz × 秒数 × 2 字节）。可用堆约 110 KB，
>    所以 `ms` 超过约 3000 会 `MemoryError`。详见 [`known-issues.md`](known-issues.md) #10。
>
> 录音（麦克风）**未实现**：需要第二个 I2S 实例共享同一组时钟，MicroPython 的
> `machine.I2S` 做不到。

### 一个完整例子

```python
import time

TITLE = "Counter"

def setup(ctx):
    ctx.n = ctx.kv_get("n", 0)
    draw(ctx)

def draw(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text("COUNT", 8, 8, 0x07FF, 0x0000)
    ctx.lcd.text_center(str(ctx.n), 120, 0xFFFF, 0x0000, 4)

def on_key(ctx, key):
    if key == "up":
        ctx.n += 1
    elif key == "down":
        ctx.n -= 1
    else:
        ctx.n = 0
    ctx.kv_set("n", ctx.n)
    draw(ctx)
```

---

## 7. 存储布局与限制

```
/apps/<name>/app.py        小程序源码
/apps/<name>/meta.json     {"title": ..., "bytes": ...}
/apps/<name>/kv.json       小程序自己用的持久化数据
/passport/*.py             系统文件
/boot.py  /main.py         开机入口
```

| 限制 | 值 | 在哪定义 |
| --- | --- | --- |
| 单个小程序大小 | 32 KB | `config.MAX_APP_SIZE` |
| 小程序数量 | 32 | `config.MAX_APPS` |
| 名字规则 | `[a-z0-9_-]{1,16}` | `apps.valid_name()` |
| 通知单包 | ≤ MTU-3（上限 180）；**MTU=23 时会超限，见 KNOWN_ISSUES #3** | `blepush._write_rsp()` 的分片逻辑 |
| 手机写入分片 | 起始 160，失败减半，最小 20 | `pwa/app.js` |

---

## 8. 安全说明

这个协议**没有任何认证和加密**：谁在蓝牙范围内连上，谁就能读写设备上的小程序。
对于一块挂胸前的玩具开发板这是可以接受的，但请注意：

- 不要在设备上放 Wi-Fi 密码、API key 等敏感数据。
- 不要把设备上的 NFC 标签当成可信身份凭证。那颗 **NTAG213 是普通被动标签**，
  不连 MCU，固件既不读也不模拟它；但它的 NDEF 内容**任何 NFC 写入器都能改**，
  UID 也能被 magic tag 克隆。它只能用来"碰一碰跳个链接"，不能用来防伪。
- 想加一层保护，可以在 `put` 里加个预共享口令校验，两端都改一处即可。
