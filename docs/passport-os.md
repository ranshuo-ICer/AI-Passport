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

⚠ **`teardown()` 里不要复位/静音/deinit 任何 `ctx.*` 共享外设**（`ctx.audio`、
`ctx.lcd` 都来自外壳）。停手就够了，外壳会在下一次 `launch()` 前把状态清回
基线。芯片里的状态位（如 ES8311 的 `REG31` 静音位）**会 latch 住**，退出时
动一下会把后面所有小程序一起弄坏 —— 真机上就这么哑过一次，见
[pitfalls 5.5](pitfalls.md) 与 [KNOWN_ISSUES #24](known-issues.md)。

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
| `DEFAULT_VOLUME` | `reset_state()` 回到的音量（80） |
| `tone(freq, ms=200)` | 纯音（1024 点正弦表 + 淡入淡出防爆音），`freq=0` 为休止 |
| `melody(notes, bpm=120)` | 依 `NOTES` 音名表逐音播放 |
| `set_volume(pct)` | 0~100；0 = 静音，1~100 → −40 dB ~ 0 dB（REG32 线性 dB 映射）。**`pct>0` 会顺带解除 DAC 静音** |
| `mute(on)` | 写 `REG31` 静音位。⚠ 这个位**会 latch**，谁开谁负责关 |
| `reset_state()` | 取消静音 + 回 `DEFAULT_VOLUME`；`ui.launch()` 每次启动小程序前调用 |
| `suspend()` | 低功耗前关 DAC+ADC。⚠ **单向**：没有对应的 resume，只能重建 `Audio()` |
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
| `draw_menu()` / `_draw_row()` | 绘制主菜单（标题栏/状态栏/列表/页脚）。每行三栏：**序号**（绝对位置 `idx+1`，滚动后不重新编号）/ 标题（最多 20 字符）/ 尺寸。布局由 [`tools/test_menu.py`](../tools/test_menu.py) 离线守护 |
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
        ├─ passport.settings          ──► /settings.json（全局设置，目前只有背光）
        ├─ passport.apps              ──► passport.config
        └─ passport.ui.Ctx            ──► 持有 shell 引用
```

**`settings.py` 存在的理由**：小程序的 kv 是 `/apps/<名字>/kv.json`，是那个小程序
的**私有**数据；而背光是**硬件状态** —— 小程序退出后它还在，Shell 下次开机又得读
回来。让 Shell 去读某个小程序的私有文件是错的层次，所以单开一个全局的。
读不出来/文件坏了/类型不对一律退回默认值，绝不让一个设置文件影响开机。

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

### 8.5 空闲路径与三轮分配优化（真机同场 A/B）

主循环是 `while True: tick(); sleep_ms(20)`，也就是 **50 Hz**。所以"每 tick 一次的
固定开销"直接等于 CPU 占用。拆开实测（`tools/hw_perf_ab.py`，新旧实现放在同一次
运行里轮流测，避免跨会话漂移）：

| 项目 | 改前 | 改后 | 说明 |
| --- | --- | --- | --- |
| `buttons.raw_mv()` | 343.6 µs | 310.2 µs | 去掉 `list + sort`，改 4 元素排序网络；**语义完全一致**（60 次采样逐一同值） |
| `fill_rect` 同色复现 | 875.5 µs | 337.0 µs | 纯色块按 `(颜色, 宽度)` 缓存，命中则零分配、零铺图案 |
| `fill_rect` 交替两色 ×2 | 1686.0 µs | 763.5 µs | 两格都缓存（进度条底/前景、Beats 脏矩形就是这个模式） |
| `fill_rect` 每次新颜色 ×2 | 871.8 µs | 937.8 µs | **最坏情况慢 7.6%（66 µs）**，见下 |
| `draw_status()` 未变化时 | 184.7 µs | 9.5 µs | 先比廉价指纹再拼字符串 |

再叠加 `BTN_POLL_DIV = 2`（隔 tick 读一次按键 ADC），每 tick 固定开销从
**2.64% 降到约 0.8%**。

关于那个 7.6% 的倒扣：它只在"每次都用全新颜色"时出现，而这块 UI 的调色板是固定
的一小撮颜色，实际不会发生 —— 缓存预算 6144 字节放得下 3 个满宽块，用满之后
**既不清空也不插入**（清空会让多色场景反复重铺，实测倒扣 11%；不插入则最坏情况
退回旧实现的代价）。这一条是明确记录的下限，不是没看见。

**三个没走通的"优化"，别再试**：

1. **viper 逐字节铺 1920 字节纯色块** —— 比 `bytes` 重复慢 15%；
2. **写 2 字节后用 `buf[a:b] = buf[0:n]` 对折复制** —— 慢 **214%**（1.0 → 3.1 ms），
   MicroPython 的 bytearray 切片赋值远没有 CPython 便宜；
3. **一步大 count 的 `bytes(2) * 960`** —— 571.7 µs，和旧两步的 537.0 µs 同量级。

根因是 **`bytes` 重复本身约 0.3 µs/字节，砍不掉**（对照：`bytearray(1920)` 只要
172.8 µs，但那是零填充，还得再铺一遍图案）。所以唯一出路是**别重复铺**，即缓存。

### 8.6 小程序载入耗时：瓶颈是 `compile()`，不是 I/O

真机实测（`tools/hw_appload_bench.py`，各阶段分开计时取 3 次最快）：

| 程序 | 字节 | read | **compile** | exec |
| --- | --- | --- | --- | --- |
| clock | 1 361 | 7.8 ms | 24.7 ms | 15.5 ms |
| metronome | 3 591 | — | — | — |
| repeater | 13 428 | 16.5 ms | **192.5 ms** | 16.7 ms |
| beats | 15 253 | 19.3 ms | **200.5 ms** | 30.3 ms |
| stress | 31 967 | 37.9 ms | 24.5 ms | 15.7 ms |

要点：

- **载入耗时几乎全在 `compile()`**（beats 有 88%），`exec` 只占 16–30 ms，
  读文件按约 1.2 ms/KB 线性增长、占比不到 10%。
- 代价跟**代码量**走，不跟文件大小走：32 KB 的 `stress` 编译只要 24.5 ms，
  而 15 KB 的 `beats` 要 200.5 ms。
- 端到端"按 OK 到画面出来"因此是 **46 ms（clock）～231 ms（repeater）**。
- 想再快只有一条路：**预编译 `.mpy` 而不是在设备上编译源码**。但那要求宿主机
  跑与固件**版本严格匹配**的 `mpy-cross`（本项目固件是官方 v1.29.0 之上的定制
  构建），而且 PWA 是手机上直接推送源码的 —— 改成推 `.mpy` 会破坏"手机上改完
  就推"的用法。所以暂时**不做**，只记录在这里。

### 8.7 BLE 推送吞吐：波动极大，分片数是瓶颈

实测推 15 253 字节的 `beats.py`（`tools/ble_client.py push`）：

| 分片 | 3 次耗时 | 中位数 |
| --- | --- | --- |
| 160 字节（原来的硬编码值） | 35.0 / 59.3 / 57.8 s | 57.8 s |
| 240 字节（按协商 MTU 自动） | 12.4 / 16.2 / 31.9 s | 16.2 s |

- 协商到的 MTU 是 **247**，ATT 负载上限 244，而客户端原来写死 160 —— 白白浪费
  84 字节/包。**每个分片都是一次 ATT 往返 + 一次 ack 通知**，所以分片数就是吞吐
  的直接瓶颈。现在按 MTU 自动算，并保留 `--chunk N` 手动覆盖以便实测对比。
- ⚠ **这个数字波动极大**（同一配置 35–59 s），所以**单次测量不可信**，上面每个
  取值都是 3 次。做 BLE 性能结论时必须重复测量。
- ⚠ 取不到 MTU 时默认值**必须退回 160**，不能退回 `23-3=20` —— 我第一版就是
  这样，推送慢了 6 倍（97 s）。`BleakClient` 在 `cli.c` 里，不在 `cli` 上。

### 8.8 屏幕闪烁：写屏字节数，而不是"画得慢"

用户报"屏幕更新时闪烁比较严重"。真机实测（`tools/hw_menu_bench.py`，5 次取最快、
字节数取平均）：

| 操作 | 改前 | 改后 |
| --- | --- | --- |
| 整屏 `draw_menu()` | **316 KB / 189 ms / 412 次 SPI 写** | 172 KB / 118 ms / 147 次写 |
| 其中整屏 `fill()` 的纯色空白 | 153.6 KB / 42.7 ms | **已消除** |
| 窗口内移动一次选择 | 316 KB / 189 ms / 412 次写 | **26.9 KB / 18.9 ms / 12 次写** |
| 单行 `_draw_row()` | 18.1 ms / 42 次写 | **9.3 ms / 6 次写** |

**根因不是"画得慢"，而是同一片像素在一次更新里被经过好几次。** 一次菜单更新
写出去 316 KB —— 那是**两屏**的像素，其中头 153.6 KB 是整屏 `fill(NAVY)` 铺的
纯色（42.7 ms 的纯色空白），后面再一块块画上去。SPI 只有几十 Mbit/s，这些中间
状态都真的出现在屏上，肉眼就是"全屏闪一下再重画"。

三处修复：

1. **去掉冗余的整屏清屏**。后面每块都会铺满自己的区域，没人覆盖的只有
   「状态栏与列表之间 2 px」「列表末尾到页脚之间」「`app_list` 为空时的列表区」
   三处窄带 —— 只清这三处，省掉 153.6 KB 的纯色空白。为此 `_draw_row` 也要
   **自给自足**（自己清掉行底那 2 px 行间缝），否则单独重画一行时上一帧选中态的
   青色会留在那里。
2. **选择移动只重画受影响的两行**（`_paint_row_for(old)` + `_paint_row_for(new)`）。
   只有**跨过滚动边界**时才整屏重画 —— 那时整个列表的内容都换了位置。
3. **整行离屏合成后一次 blit**（`Display.row_fb()` + `blit_row()`，用 `_Canvas`
   把 `fill_rect`/`text` 重定向到一块 240×28 的 framebuf）。直接画时一行要分
   **42 次** SPI 写（底色 → 行间缝 → 选中条 → 序号 → 标题 → 尺寸），每一步都
   真的出现在屏上，于是"先空一块、再长出字"；合成后 **6 次写**（一次 set_window
   + 一次数据），这一行是**一瞬间**换掉的。缓冲 13.4 KB，按需分配，
   `drop_text_cache()` 会放掉；分配失败自动退回直接画。

两条绘制路径**必须像素一致**，所以 `hw_menu_bench.py` 会把"直接画"和"离屏合成"
产生的像素流录下来逐字节比较：实测 **13440/13440 字节完全一致，0 个不同像素**。
两条路共用同一个 `_paint_row()`，这个比较是它唯一可信的验证方式。

`tools/test_menu.py` 锁住了这几条不变量：窗口内移动**只能**碰旧/新两行、
**不许**出现整屏填充、滚动发生**必须**整屏重画。谁把它改回整屏重画，测试会红。


