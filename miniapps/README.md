# 小程序合集

这是给 **PassportOS**（路线 B）用的小程序集合，加上两个开发工具。

小程序就是 `/apps/<名字>/app.py` —— 一段普通 Python，通过手机 App 用蓝牙推送上去，
设备菜单里就会出现它。运行时环境见 [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md)。

---

## 1. 有什么

| 文件 | 设备菜单名 | 玩法 | 体积 |
| --- | --- | --- | --- |
| [`timer.py`](timer.py) | **Timer** | 秒表 + 倒计时。UP 调整/归零，DOWN 切模式，OK 起停；倒计时到点会响 | 5.4 KB |
| [`reaction.py`](reaction.py) | **Reaction** | 反应速度测试。整屏变色当刺激信号，早按判「抢跑」，最快成绩掉电保留 | 2.9 KB |
| [`snake.py`](snake.py) | **Snake** | 贪吃蛇。只有两个方向键，所以 **UP/DOWN 是相对转向**；OK 暂停/重开 | 5.0 KB |
| [`metronome.py`](metronome.py) | **Metronome** | 节拍器 40–240 BPM，四分音符重音，**跑着也能调速度** | 3.6 KB |
| [`memory.py`](memory.py) | **Memory** | 记忆序列。屏幕亮格 + 发对应音高，逐关加长 | 4.6 KB |
| [`repeater.py`](repeater.py) | **Repeater** | 复读机：按一下录、再按一下循环放。**实验性**，见下方说明 | 13.4 KB |

> 「设备菜单名」就是源码里的 `TITLE` 常量 —— 推送时 App 会让你填标题，
> 填什么就显示什么；用内置方式安装时则以 `meta.json` 里的 `title` 为准。

`repeater.py` 单独说明：PassportOS 的固件**没有录音 API**，所以它是自己做了
I2S 交接（把播放实例 `deinit` 掉 → 用同一组 BCLK/WS 建 RX → 录完再重建播放）。
这条路**在真机上验证过可以录音**，但录音长度受 RAM 限制（约 2 秒 @8kHz），
且音质取决于 ES8311 的 ADC 通路是否被完整初始化。详见
[`../docs/KNOWN_ISSUES.md`](../docs/KNOWN_ISSUES.md)。

---

## 2. 怎么装

### 方式一：手机 App 单个推（推荐）

打开「Passport 助手」→ 连接 → 应用页 → 内容来源选「选择 .py 文件」→ 选一个 →
点「推送并运行」。

> ⚠ **推之前先断开再重连**，然后立刻推。原因：手机端每 10 秒发一次心跳，
> 而上传超过 10 秒心跳会被写进 `app.py` 并截断文件 —— 见
> [`../docs/KNOWN_ISSUES.md`](../docs/KNOWN_ISSUES.md) #1。重连后计时归零，
> 只要「连接 → 传完」在 10 秒内就安全。

### 方式二：打包成一个安装包

手机 App 一次只能选一个文件，所以想一次装多个就打成包：

```sh
python3 _bundle.py --name "Bundle A" --out bundle_a.py reaction metronome memory
python3 _bundle.py --list        # 看看有哪些可选
```

生成的 `bundle_a.py` 推上去运行一次，里面几个小程序就全装好了
（还会自动刷新菜单，不用重启）。打包器会拒绝超过设备 32 KB 上限的组合。

### 方式三：电脑上批量推

```sh
cd ..
for f in miniapps/timer.py miniapps/snake.py; do
  n=$(basename "$f" .py)
  python tools/ble_client.py push "$f" --name "$n" --title "$n"
done
```

### 方式四：让它随系统一起装

把某个 `.py` 连同 `meta.json` 放进 [`../os/builtin/`](../os/builtin/) 下的一个目录，
再跑 `python tools/deploy.py`，它就会作为内置小程序出现在设备上。

> 设备最多 12 个小程序（`config.MAX_APPS`）。

---

## 3. 写新小程序前必须知道的约束

这些都是真机上踩出来的，不是理论：

| 约束 | 原因 |
| --- | --- |
| **源码只用 ASCII** | 固件自带的 8×8 点阵字体只有 ASCII，中文会显示成方块；而且上传被截断时，多字节字符会变成 `UnicodeError` 而不是普通语法错误 |
| **文件尽量小** | 上传越大、越可能跨过 10 秒心跳窗口（见 #1）。几十个分片的文件最安全 |
| **别占用长按 OK** | 按住 OK 超过 1.2 秒，系统会强制退出小程序（`ui.py` 的 `LONG_PRESS_MS`），小程序改不了 |
| **只有按下事件，没有松开** | `on_key` 只在按下时触发。要判断「按住/松开」只能每帧问 `ctx.shell.buttons.current()` |
| **音频会阻塞** | `tone()` / `melody()` 播放期间主循环冻结。单次音长别超过约 1 秒，`ms` 超过约 3000 会 `MemoryError` |
| **内存只有约 40 KB** | 而且**碎片化**：一个大 `bytearray` 往往失败，一串小块反而能拿到更多。别把堆吃光，留一两万字节给界面和 BLE |
| **界面按时间节流** | 用 `ctx.frame % N` 节流，会在循环变慢时退化成几秒一次。用 `time.ticks_ms()` 更稳 |

现成的例子都在本目录：`snake.py` 看增量重绘和游戏循环，`repeater.py` 看
I2S 交接与分片缓冲，`metronome.py` 看定时调度。

---

## 4. 两个工具

### `_verify.py` —— 推之前先离线验一遍

```sh
python3 _verify.py snake.py --ticks 900 --keys up,up,down,ok
```

用桩件空跑，**不需要硬件**：逐笔检查每一次绘制是否越界、模拟碎片化堆
（所以退让逻辑真的会被走到）、注入按键序列、报告异常行号、数发声次数。
它还会把 `/apps` 重定向到临时目录，所以测安装器不会碰真实文件系统。

**它抓到过三类只有上板才会暴露的 bug**：引用了不存在的模块级 `ctx`、
`setup()` 里先画界面后初始化状态、少传函数参数 —— 这些 `py_compile` 全都通过。

### `_bundle.py` —— 生成安装包

见上方「方式二」。

---

## 5. 相关文档

- [`../docs/PROTOCOL.md`](../docs/PROTOCOL.md) —— 小程序 API（`ctx` 全部成员、绘图接口）
- [`../docs/KNOWN_ISSUES.md`](../docs/KNOWN_ISSUES.md) —— 已知缺陷，**推之前值得读一遍**
- [`../README.md`](../README.md) —— 项目总入口
