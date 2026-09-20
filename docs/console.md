# BLE 终端：能做什么、有哪些 API

连上设备后可以直接跑 Python —— 不插 USB、不开 mpremote，手机上就能查状态、
摸硬件、试一行 API。协议见 [`ble-protocol.md`](ble-protocol.md) 的 4.1 节，
设备端实现在 `os/passport/console.py`。

**怎么打开**：

- 手机 / 电脑浏览器打开 [Passport 助手](pwa.md)，连上设备 → 「终端」标签页
- 或者命令行：`python tools/ble_client.py py 某个文件.py`（`-` 表示从标准输入读）

交互规则：**回车执行**（手机上按不出 Ctrl）、**Shift+回车换行**、上下键翻历史。
变量在设备端保留，`x = 41` 之后下一条 `x + 1` 就是 `42`。

> ⚠ 本文里所有输出都是**真机跑出来的**（MicroPython v1.29.0 / ESP32-C3），
> 不是照签名手写的。

---

## 1. 已经注入好的名字

不用 `import`，进终端就能用：

| 名字 | 类型 | 是什么 |
| --- | --- | --- |
| `lcd` | `Display` | 屏幕。绘图、背光、文字 |
| `audio` | `Audio` | ES8311 扬声器 / 麦克风 |
| `battery` | `Battery` | CW2017 电量计 |
| `buttons` | `Buttons` | 三个按键（ADC 分压） |
| `apps` | module | 小程序的增删查（`/apps` 目录） |
| `shell` | `Shell` | 系统外壳本体：当前小程序、菜单、启动/停止 |
| `link` | `AppLink` | 这条 BLE 链路本身 |
| `display` | module | 颜色常量与 `rgb()` |
| `settings` | module | 读写 `/settings.json` |
| `gc` / `time` / `sys` | module | MicroPython 标准模块 |
| `print` | 函数 | 被换成"写进终端输出"的版本 |

其余模块要自己 import，例如 `from passport import config as C`。

设备上 `/passport/` 里可用的模块：

```
__init__.py  apps.py  audio.py  battery.py  blepush.py  buttons.py
config.py    console.py  display.py  settings.py  ui.py
```

---

## 2. API 速查

### `lcd` —— 屏幕（240×320，ST7789P3）

| 调用 | 说明 |
| --- | --- |
| `lcd.fill(color)` | 整屏填充 |
| `lcd.fill_rect(x, y, w, h, color)` | 实心矩形 |
| `lcd.rect(x, y, w, h, color)` | 空心矩形 |
| `lcd.hline(x, y, w, color)` / `lcd.vline(x, y, h, color)` | 横线 / 竖线 |
| `lcd.text(s, x, y, color, bg=BLACK)` | 8×8 点阵文字 |
| `lcd.text_scale(s, x, y, color, bg=BLACK, scale=2)` | n 倍放大 |
| `lcd.text2x(...)` | `text_scale` 的 2 倍简写 |
| `lcd.text_center(s, y, color, bg=BLACK, scale=1)` | 水平居中 |
| `lcd.progress(x, y, w, h, pct, fg=GREEN, bg=DARK)` | 进度条 |
| `lcd.backlight(pct)` | 背光 0~100 |
| `lcd.w` / `lcd.h` | 宽 / 高（240 / 320） |
| `lcd.blit(buf, x, y, w, h, swap=True)` | 贴 framebuf |
| `lcd.row_fb(h)` / `lcd.blit_row(fb, y, h)` | 逐行离屏合成（省内存的写法） |
| `lcd.splash(title, sub="")` | 画一张启动画面 |

颜色常量在 `display` 模块里（**都不是** `lcd.` 上的）：

```python
>>> print(display.BLACK, display.RED, display.GREEN, display.BLUE, display.WHITE)
0 63488 2016 31 65535
>>> print("WxH", lcd.w, lcd.h)
WxH 240 320
```

可选：`BLACK` `RED` `SILVER` `GREY` `NAVY` `DARK` `CYAN` `TEAL` `WHITE`
`YELLOW` `GREEN` `BLUE` `MAGENTA` `ORANGE`，以及 `display.rgb(r, g, b)`。

### `audio` —— 声音

| 调用 | 说明 |
| --- | --- |
| `audio.tone(freq, ms=200)` | 纯音；`freq=0` 是静音（用来凑节奏），超长会被截断 |
| `audio.melody(notes, bpm=120)` | `notes = [(音名或频率, 拍数), ...]` |
| `audio.beep()` | 1000 Hz 120 ms |
| `audio.play_raw(data)` | 直接写 16bit 单声道 PCM |
| `audio.set_volume(pct)` | 0~100；**0 是静音，>0 会顺便解除静音** |
| `audio.mute(on=True)` / `audio.suspend()` / `audio.reset_state()` | 静音控制 |
| `audio.ok` / `audio.rate` / `audio.volume` / `audio.error` | 状态 |

音名表在 `passport.audio.NOTES`（37 个：`C3`~`A6` 加 `REST`）。

### `battery` —— 电量

| 调用 | 说明 |
| --- | --- |
| `battery.label()` | 方法。`"89%"`，读不到时 `"--"` |
| `battery.percent` | **property（不加括号）** 0~100，-1 表示未知 |
| `battery.millivolts` | **property（不加括号）** 电压 mV |
| `battery.poll(force=False)` | 重新读一次 |
| `battery.ok` / `battery.version` / `battery.i2c` | 状态 / 芯片版本 / I²C 对象 |

> ⚠ **`percent` 和 `millivolts` 是 property，写 `battery.percent()` 会得到
> `TypeError: 'int' object isn't callable`。** `label()` 才是方法。
> 这个坑我自己在写终端片段按钮时就踩了一次。

### `buttons` —— 三个按键

| 调用 | 说明 |
| --- | --- |
| `buttons.check()` | 诊断用：返回 `(电压mV, 识别到的键)`，**不阻塞、不改状态** |
| `buttons.update()` | 刷新并做消抖，返回**本次新按下**的键名，没有则 `None` |
| `buttons.current()` | 当前按住且已消抖的键名，松开是 `None` |
| `buttons.voltage()` | 当前 ADC 电压 mV |
| `buttons.raw_mv()` | 直接读一次 ADC |

按键窗口见 [`hardware.md`](hardware.md)：`up` 0~150 mV、`down` 150~447、
`ok` 447~1900、松开 ≥1900。

`buttons.update()` 需要**连续两次**读数一致才算数，所以自己写循环时要按帧率调用
（每 20 ms 一次）。

### `apps` —— 小程序

| 调用 | 说明 |
| --- | --- |
| `apps.list_apps()` | `[{"n": 名字, "title": 标题, "s": 字节数}, ...]` |
| `apps.read_source(name)` | 读 `app.py` 源码（字符串） |
| `apps.free_space()` | 文件系统剩余字节 |
| `apps.valid_name(name)` | 名字是否合法（`a-z0-9_-`，≤16） |
| `apps.make_app(name, title="")` | 返回 `(path, 已打开的文件对象)` |
| `apps.write_meta(name, title, size)` | 写 meta |
| `apps.delete_app(name)` | 删除 |
| `apps.load_module(name)` | 真正 import 成模块对象 |

### `settings` —— 全局设置（`/settings.json`）

| 调用 | 说明 |
| --- | --- |
| `settings.get(key, default=None)` | 读 |
| `settings.set(key, value)` | 写（内存里，**要 flush 才落盘**） |
| `settings.flush()` | 落盘 |
| `settings.load(force=False)` / `settings.reset_cache()` | 重载 / 丢弃缓存 |

目前只有一个键：`bl`（背光 0~100）。

### `shell` —— 系统外壳

| 调用 | 说明 |
| --- | --- |
| `shell.current_app()` | 正在跑的小程序名，没有则 `None` |
| `shell.launch(name)` / `shell.stop_app()` | 启动 / 停止小程序 |
| `shell.refresh_apps()` | 重新扫描 `/apps` |
| `shell.draw_menu()` / `shell.draw_status(force=False)` | 强制重画菜单 / 状态栏 |
| `shell.bl_pct` | 当前背光百分比 |
| `shell.app_list` / `shell.sel` | 菜单列表 / 当前选中项 |
| `shell.lcd` `shell.audio` `shell.battery` `shell.link` `shell.buttons` | 同一批对象 |

> 调 `shell.launch("beats")` 会**真的切进那个小程序**，之后你敲的命令要等它退出
> 才轮得到。想回来：`shell.stop_app()`。

### `link` —— 这条 BLE 链路

| 调用 | 说明 |
| --- | --- |
| `link.send(obj)` | 把一个 JSON 消息推给手机（手机端当 `log`/`state` 之类处理） |
| `link.log_line(msg)` | 同时写串口和手机 |
| `link.connected` / `link.mtu` / `link.uploading` | 状态 |
| `link.status_text()` / `link.progress_key()` | 状态栏文字 / 上传进度键 |

---

## 3. 配方（都真机跑过）

### 看内存

```python
>>> import gc
>>> gc.collect()
>>> print("free", gc.mem_free(), "alloc", gc.mem_alloc())
free 73296 alloc 78192
```

### 看电池

```python
>>> battery.poll(force=True)
>>> print(battery.label(), "|", battery.percent, "%", "|", battery.millivolts, "mV")
89% | 89 % | 4058 mV
```

### 按键：一次性读，不阻塞

```python
>>> print(buttons.check())
(2897, None)
>>> print("current", buttons.current(), "voltage", buttons.voltage())
current None voltage 2897
```

### 按键：等一个键（会阻塞，务必带超时）

```python
>>> import time
>>> t0 = time.ticks_ms()
>>> hit = None
>>> while time.ticks_diff(time.ticks_ms(), t0) < 3000:
...     k = buttons.update()
...     if k:
...         hit = k
...         break
...     time.sleep_ms(20)
>>> print("key:", hit, "after", time.ticks_diff(time.ticks_ms(), t0), "ms")
key: None after 3014 ms
```

### 在屏幕上画画

```python
>>> lcd.progress(20, 200, 200, 14, 65, display.GREEN, display.DARK)
>>> lcd.text_center("HELLO FROM BLE", 224, display.WHITE, display.NAVY, 2)
>>> print("drawn")
drawn
```

### 放一段音阶

```python
>>> audio.set_volume(70)
>>> audio.melody([("C5", 0.25), ("E5", 0.25), ("G5", 0.5)], bpm=180)
>>> print("played, volume", audio.volume, "ok", audio.ok)
played, volume 70 ok True
```

### 调背光（顺带持久化）

```python
>>> settings.set("bl", 40)
>>> settings.flush()
>>> lcd.backlight(40)
>>> print("bl now", settings.get("bl"))
bl now 40
```

### 列小程序 / 看占了多少空间

```python
>>> for a in apps.list_apps()[:3]:
...     print(a["n"], a["title"], a["s"])
battlog BatteryLog 6126
beats Beats 15253
btnlab ButtonLab 5955
>>> print("count", len(apps.list_apps()), "free", apps.free_space())
count 19 free 5627904
```

### 把一条消息推给手机

```python
>>> link.send({"t": "log", "m": "hello from the console"})
>>> print("sent via link")
sent via link
```

手机上（`tools/ble_client.py` 的监听里）会收到：

```
[设备日志] hello from the console
```

### 看文件系统

```python
>>> import os
>>> print("root:", sorted(os.listdir("/")))
root: ['apps', 'boot.py', 'main.py', 'passport', 'settings.json', 'shot_spec.txt']
>>> print("apps:", sorted(os.listdir("/apps"))[:6])
apps: ['battlog', 'beats', 'btnlab', 'clock', 'dice', 'memory']
```

### 扫 I²C 总线 / 读芯片寄存器

```python
>>> print([hex(a) for a in battery.i2c.scan()])
['0x18', '0x63']
>>> from passport import config as C
>>> print("ES8311 ver", hex(audio._i2c.readfrom_mem(C.ADDR_ES8311, 0x01, 1)[0]))
ES8311 ver 0xbf
>>> print("CW2017 ver", hex(battery.i2c.readfrom_mem(C.ADDR_CW2017, C.CW_REG_VERSION, 1)[0]))
CW2017 ver 0xf
```

`0x18` = ES8311（音频 codec），`0x63` = CW2017（电量计）。寄存器名都在
`passport.config` 里（`CW_REG_*`）。

### 看常量

```python
>>> from passport import config as C
>>> print(C.LCD_W, C.LCD_H, C.MAX_APPS, C.MAX_APP_SIZE, C.BLE_IDLE_TIMEOUT_MS)
240 320 32 32768 25000
>>> print(C.UUID_SERVICE)
7a5c0001-0000-4000-8000-70617373706f
```

---

## 4. 红线与限制

### 三条硬限制

1. **同步执行。** 代码在 BLE 主循环里跑完才返回。`while True:` 会把界面、按键、
   看门狗**一起卡住** —— 只能断电重开。写循环一定要带 `time.ticks_ms()` 超时。
2. **只捕获 `print`。** 异步 / 中断里产生的输出不会进终端。（`sys.stdout` 在这个
   MicroPython 构建上**存在但不可赋值**，所以捕获靠的是注入命名空间的 `print`。）
3. **权限等同系统本身。** `machine.reset()` 会重启，乱改文件会破坏系统。
   终端里按 `exit()` 或 Ctrl-C 不会带走设备（会被当成一条错误显示）。

### 数字上限

| 项 | 上限 | 超了会怎样 |
| --- | --- | --- |
| 单次执行输出 | **2048 字节**（`console.MAX_OUT`） | 截断，末尾注明"已截断" |
| 单次执行源码 | **8192 字节**（`_MAX_CONSOLE_SRC`） | 报错，不执行 |
| 单次 GATT 写 | **512 字节 JSON** | 客户端自动分片，不用你管 |
| 一次 `lcd` 全屏填充 | — | 一次写 153 KB，约 43 ms，会明显顿一下 |

### 卡死了怎么办

屏幕停在最后一帧、按键没反应、连不上 —— **断电重开**（拔 USB 再插，或按电源键）。
设备端有 25 秒空闲看门狗负责恢复广播，但它也跑在同一个循环里，被你的代码堵住时
同样不会执行。

---

## 5. 出错怎么读

终端给的是**完整 traceback + 出错那行的源码回显**：

```
Traceback (most recent call last):
  File "passport/console.py", line 239, in run
  File "<ble-console>", line 1, in <module>
TypeError: 'int' object isn't callable
      ^^^ print(battery.percent())
```

读法：

- **`File "passport/console.py", line 239, in run`** —— 这是终端自己的框架，忽略它
- **`File "<ble-console>", line 1, in <module>`** —— **这个才是你的代码**，
  行号是相对你这次提交的源码数的（不是整段历史）
- **`^^^ 那一行`** —— 出错那行的原文，省得你对着行号数

常见错误：

| 报错 | 通常是什么 |
| --- | --- |
| `NameError: name 'x' isn't defined` | 名字打错，或变量在另一次「清空变量」之后没了 |
| `TypeError: 'int' object isn't callable` | 把 property 当方法调了（`battery.percent`、`battery.millivolts`） |
| `AttributeError: 'module' object has no attribute 'xxx'` | 模块里没这个名字；用 `dir(模块)` 看真实成员 |
| `MemoryError` | 堆不够。先 `gc.collect()`；大缓冲要分块 |
| `OSError: [Errno 19] ENODEV` | 摸到了不存在的外设（比如 `audio` 初始化失败） |

想看清某个对象到底有什么，直接问它：

```python
>>> print(" ".join(m for m in dir(lcd) if not m.startswith("_")))
blit cs fill fill_rect hline rect spi text vline w h bl backlight splash drop_text_cache text2x row_fb blit_row dc set_window text_scale text_center progress
```

---

## 6. 相关文档

- 协议与命令细节：[`ble-protocol.md`](ble-protocol.md) 4.1 节
- 手机端终端界面：[`pwa.md`](pwa.md) 2.1 节
- 写小程序（`ctx` 那套 API 是另一套）：[`ble-protocol.md`](ble-protocol.md) 的「小程序 API」
- 屏幕刷新为什么会闪、怎么省字节：[`pitfalls.md`](pitfalls.md) 1.0 节
