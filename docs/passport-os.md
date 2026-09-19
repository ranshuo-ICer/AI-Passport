# PassportOS —— 架构与代码地图

路线 B 的设备端系统，用 MicroPython 写成。本文是**代码级参考**：模块划分、
启动流程、每个文件负责什么、模块之间怎么依赖。

- 想**用**它（刷机、推小程序）→ [`flashing.md`](flashing.md) 和
  [`../miniapps/README.md`](../miniapps/README.md)
- 想给**小程序**写代码 → [`protocol.md`](ble-protocol.md) 里的小程序 API
- 想知道**为什么这么设计** → [`pitfalls.md`](pitfalls.md)

---

## 1. 启动流程

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

## 2. 核心模块

### 2.1 `config.py` —— 硬件事实唯一来源

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
| 存储 | `APPS_DIR="/apps"`, `MAX_APP_SIZE=32KB`, `MAX_APPS=32` | 小程序限制 |

### 2.2 `display.py` —— ST7789P3 显示驱动

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

### 2.3 `buttons.py` —— 三键电阻梯

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

### 2.4 `battery.py` —— CW2017 电量计

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

### 2.5 `apps.py` —— 小程序存储与加载

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

### 2.6 `blepush.py` —— BLE 小程序推送服务

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

### 2.7 `audio.py` —— ES8311 播放驱动

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
[`known-issues.md`](known-issues.md) #11。

**已知限制**：`tone()`/`melody()` 阻塞且纯 Python 生成波形；缓冲区按时长一次性分配，
`ms` 超过约 3000 会 OOM（[`known-issues.md`](known-issues.md) #10）。
录音未实现（第二个 I2S 实例无法共享时钟）。

## 3. 系统外壳与运行时

文件：[os/passport/ui.py](../os/passport/ui.py)

### 3.1 `Ctx` 类 —— 小程序运行时上下文

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

### 3.2 `Shell` 类 —— 系统外壳

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

## 4. 内置小程序
| 小程序 | 文件 | 功能 | 演示要点 |
| --- | --- | --- | --- |
| Clock | [os/builtin/clock/app.py](../os/builtin/clock/app.py) | 数字时钟 | RTC 未对时退化为开机计时；`time.localtime()` |
| Dice | [os/builtin/dice/app.py](../os/builtin/dice/app.py) | 骰子 | 按键交互 + 图形绘制（点位图 LAYOUT） |
| Sound | [os/builtin/sound/app.py](../os/builtin/sound/app.py) | 发声玩具 | `ctx.audio` 判空、`tone()` 与 `melody()`、五声音阶 |
| Mu Yu | [os/builtin/muyu/app.py](../os/builtin/muyu/app.py) | 木鱼计数器 | 渐变圆绘制、局部重画做敲击反馈、`kv_*` 节流落盘、跨天归零 |
| System | [os/builtin/sysinfo/app.py](../os/builtin/sysinfo/app.py) | 系统信息 | 读 `gc.mem_free()` / 电量 / 运行时长 / BLE 状态 |

每个小程序目录含 `app.py` 和 `meta.json`（`{"title": "..."}`）。

---

---

## 5. PassportOS 模块依赖

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

## 6. 外部依赖

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

## 7. 数据流（路线 B）

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

## 8. 性能：热点在哪、用什么加速

数据来自真机（160 MHz ESP32-C3），跑 [`tools/hw_bench.py`](../tools/hw_bench.py) 复现。

### 8.1 瓶颈是 Python 逐字节/逐像素的循环，不是 SPI

原始 SPI 能跑到 **38.4 Mbit/s**（32 KB 单次写），总线完全不是瓶颈。
问题在每帧的 Python 层工作。

| 项目 | 优化前 | 优化后 | 加速 |
| --- | --- | --- | --- |
| `_to_be` 1280 字节（字节序翻转） | 6 536 µs | **646 µs** | **10×** |
| `_to_be` 3840 字节 | 19 418 µs | **1 248 µs** | **16×** |
| `blit`（含翻转，10 字符文字） | 8 964 µs | **1 496 µs** | **6.0×** |
| `text` 10 字符 | 10 142 µs | **1 928 µs** | **5.3×** |
| `text` 28 字符 | 21 956 µs | **3 271 µs** | **6.7×** |
| `text_scale` ×3 | 17 111 µs | **6 110 µs** | 2.8× |
| `fill` 整屏 | 69 512 µs | **36 531 µs** | 1.9× |

`fill` 优化后是 36.5 ms，而 153 600 字节在 38.4 Mbit/s 下本身就要 32 ms —— **已经到总线的 88%**。

三类手段，按收益排序：

1. **viper 重写逐字节/逐像素的循环**（`_to_be` / `_scale_row`）—— 这是最大的一块。
2. **`set_window()` 合并事务**：原来 0x2A/0x2B/0x2C 各走一遍 `_write()`，
   每次都要切 CS/DC，一次窗口设置要 15 回 Pin 调用；现在整个过程
   **CS 只拉低一次、7 回 Pin 调用**，参数用预分配的 4 字节缓冲拼，
   不再每次 `bytes((...))` 分配。
3. **消除热路径上的重复分配**：`blit` 的翻转结果写进常驻 `self._swap`；
   `fill_rect` 的块缓冲只拼一次、循环复用。

`tools/test_display.py` 第 6 节专门断言 `set_window` 的**协议序列**
（命令走 DC=0、4 字节参数走 DC=1、CS 只拉低一次）—— 这类优化最容易
悄悄改坏协议，而颜色对不对离线是看不出来的。

### 8.2 用什么加速：viper，不是 C

`_to_be()` 和 `_scale_row()` 用 **`@micropython.viper`** 重写（见
[`os/passport/display.py`](../os/passport/display.py) 顶部的 `_to_be_fast` /
`_scale_row_fast`）。选它而不是写 C 模块的理由：

- **viper 是"用 Python 写、编译成机器码"**，量级上接近 C —— 本项目音频那边
  早先实测过 26×，这次字节翻转又实测 12×；
- **不需要重编固件**。官方预编译的 MicroPython 直接就能跑；写成 C 模块就得
  维护一份自定义固件（ESP-IDF 工具链 + 以后无法再跟官方固件），
  代价远大于那点额外收益；
- 编译不出机器码是可能的（DRAM 不可执行等），所以整块包在 `try` 里，
  失败自动退回纯 Python，两条路写出的结果逐位相同。

`fill_rect()` 的加速走的是另一条路 —— **不是换语言，而是少分配**：
原来每块都 `self.spi.write(line * n)` 重新拼一个大 `bytes`，整屏要重复
80 次；改成整块只分配一次、循环里复用。SPI 调用次数没变，分配次数从 80 降到 1。

### 8.3 已经优化不动的地方

| 项目 | 耗时 | 为什么不能再快 |
| --- | --- | --- |
| `play_raw` 60 ms | 59 823 µs | I²S 必须按**实时速率**推，60 ms 的音频就得占 60 ms。要异步只能上 DMA 双缓冲，MicroPython 的 `machine.I2S` 不暴露 |
| `tone` 40 ms | 39 825 µs | 同上 —— 合成（viper）几乎不额外花时间，耗时就是音频时长本身 |
| JSON 编解码 | 0.2–3.9 ms | 已经是 C 实现的 `ujson` |

**所以"把热点改用 C 写"能拿到的额外收益已经很小**：剩下的 Python 开销主要
在 `blit()` 里的 `set_window()`（每次要发 3 条 SPI 命令、逐条切换 CS/DC 引脚），
整段合成一个 C 函数大约还能再省 2 ms/次文字 —— 但要为此维护自定义固件，
不划算。

### 8.4 真正吃内存的不是 CPU 热点

这块板没有 PSRAM，**RAM 才是紧的那一头**，而 C 并不会减少 Python 对象数量。
实测有效的三条都是分配策略，不是语言：

1. `fill_rect` 的单次写缓冲从 8160 字节压到 ~2 KB（碎片化的堆凑不出 8 KB 连续块）；
2. 文字帧缓冲缓存按**总字节数**限 4 KB（原来按条目数限 16 条，能吃掉 21 KB 碎片）；
3. `apps.load_module()` 先编译、丢掉源码、再 exec，失败回收重试。

合起来把可载入的小程序体积从 **16 KB 抬到 32 KB**，空闲堆从 78 KB 回到 99 KB。

