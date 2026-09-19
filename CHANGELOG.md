# 变更记录

本文件记录**面向使用者**的变化。格式参考 [Keep a Changelog](https://keepachangelog.com/)，
版本号遵循[语义化版本](https://semver.org/lang/zh-CN/)。

---

## [未发布]

### 性能 —— `audio.tone()` 的合成循环改用 viper

`tone()` 的采样生成循环与淡入淡出改用 `@micropython.viper` + `ptr16`，
编译成机器码。真机实测（小程序 `tonebench.py`，n=3200 样本，ESP32-C3）：

| 实现 | 耗时 | 每样本 | 相对 |
| --- | --- | --- | --- |
| 原来（纯 Python 字节码） | 25878 us | 8.09 us | 1.00x |
| `@micropython.native` | 16628 us | 5.20 us | 1.55x |
| **viper + `ptr16`** | **986 us** | **0.308 us** | **26.24x** |

`native` 只快 1.55x，不值得单独成一条路，所以直接用 viper。

**正确性有逐位证据**：viper 写出的 3200 个样本与改动前的实现完全相同，
指纹 `0000e85e`（与离线模型同值）。淡入淡出里的 `// 64` 换成 `>> 6` 也做过
全量比对——全部 int16 取值 × 全部 `i` 结果一致；`n < 256` 时 `fade = n//4`
不是 2 的幂，保留原除法。

**回退**：整块包在 `try` 里。编译不出机器码（ESP32 的 DRAM 不可执行，光看
`MICROPY_EMIT_RV32` 打开并不保证能分配）就 `_tone_fast = None`，`_synth()`
自动走纯 Python；运行期一旦报错也永久退回。**最坏情况等于改动前的速度。**

**还没做的**：每次调用仍会新分配一个 `array`，真机实测 1434 us —— 循环优化
之后它反而成了最大单项（比 viper 循环本身还贵）。想再快一步需要复用缓冲 +
用 `memoryview(buf)[:n]` 写出，会引入尚未在这台设备上验证过的路径，
留作单独一步。

### 变更 —— 文档重构

文档此前由两个不同的 Agent 分别生成，导致同一件事被写了好几遍、并且互相矛盾
（音频模块已实现并真机验证出声，却有三份文档写着"未实现"）。现在按
**「一件事只写一遍」**重构：

| 原来 | 现在 |
| --- | --- |
| `README.md`（283 行，混了快速开始/硬件/验证状态/分区表/排错） | 只做入口（约 200 行） |
| `README-刷机指南.md` | [`docs/flashing.md`](docs/flashing.md)（两条路线合并） |
| `docs/PROTOCOL.md` | [`docs/ble-protocol.md`](docs/ble-protocol.md) |
| `docs/CODE_WIKI.md`（947 行，大量重复） | 拆成 [`docs/passport-os.md`](docs/passport-os.md)、[`docs/pwa.md`](docs/pwa.md)、[`docs/tools.md`](docs/tools.md)、[`docs/upstream.md`](docs/upstream.md) |
| `docs/FACTORY_FIRMWARE.md` | [`docs/factory-firmware.md`](docs/factory-firmware.md)（并入原厂分区表） |
| `docs/KNOWN_ISSUES.md` | [`docs/known-issues.md`](docs/known-issues.md) |
| 硬件事实散落 4 处 | 合并到 [`docs/hardware.md`](docs/hardware.md) |
| 踩坑经验散落各处 | 新增 [`docs/pitfalls.md`](docs/pitfalls.md)（约 30 条，现象 → 根因 → 教训） |
| 无索引 | 新增 [`docs/README.md`](docs/README.md) |

**新增自动检查**：`tools/check_docs.py` 扩展到 12 项，覆盖链接、数量、常量、
协议命令、`ctx` API、固件哈希、小程序约束，以及**文档索引完整性** ——
`docs/` 下新增文档若忘了登记进索引会直接报错。

### 新增 —— 小程序合集

新增 [`miniapps/`](miniapps/README.md)：6 个可直接推送的小程序，外加两个工具。

- `timer.py` / `reaction.py` / `snake.py` / `metronome.py` / `memory.py` / `repeater.py`
- `_verify.py` —— **离线校验器**，桩件空跑，查绘制越界、模拟碎片化堆、
  注入按键序列、报告异常行号。抓到过 3 个 `py_compile` 检查不出来的真 bug。
- `_bundle.py` —— 把多个小程序打成一个安装包（手机 App 一次只能选一个文件）。

`tools/check_docs.py` 新增对 `miniapps/` 的强制检查：必须纯 ASCII、必须有
`TITLE`、必须列进 `miniapps/README.md`。

### 修复

- **`cardid` 分区大小写错**：原文档写 16 KB，与 `audio` 的起始偏移对不上；
  实际是 144 KB（其余分区行都自洽、末地址也吻合）。
- **`README-刷机指南.md` 关于 `upstream/` 的描述有误**：它说带「多个 demo 分支」，
  但那份快照是 main 分支的 tar 包，**没有 `.git`、没有其它分支**。
- **「`0x700000` 有 Recovery 分区」的错误说法**：实测那里是空白，
  官方 upstream 的 CHANGELOG 也写明该分区已被移除。**这块板没有恢复兜底。**
- **NTAG213 被描述为「不可伪造」**：它是普通被动标签，NDEF 内容任何 NFC
  写入器都能改，UID 也能被 magic tag 克隆。已改为「不能作防伪凭证」。
- **`battery.init_profile()` 被描述为幂等**：它每次都无条件重写 profile 并
  重设 `UPDATE_FLAG`（触发 SOC 重算）。安全但不是幂等。

### 已知问题

未修复的缺陷见 [`docs/known-issues.md`](docs/known-issues.md)。其中最严重的一条是
**上传小程序超过 10 秒时，手机端心跳会被写进源码并截断文件**（#1），
可用 [`tools/repro_ping_corruption.py`](tools/repro_ping_corruption.py) 复现。

---

## [1.0.0] —— 首个可用版本

### 新增

- **路线 A**：小智 AI 语音助手固件（`firmware/`，含哈希校验）+ 6 个 Windows 一键脚本
- **路线 B**：PassportOS（MicroPython）
  - 显示驱动（ST7789P3 厂商序列）、三键 ADC、CW2017 电量计、ES8311 播放、BLE 推送
  - 启动器 + 小程序运行时（`ctx` API、`kv_*` 掉电保持）
- **手机端 App**：Web Bluetooth PWA（无需 APK，`pwa/`）
- **工具链**：部署 / 托管 / 测试 / 诊断共 18 个脚本
- 5 个内置示例小程序（`os/builtin/`）：Clock、Dice、Sound、System、Mu Yu

### 真机验证

MicroPython 刷写、屏幕点亮、按键 ADC、电池读数（85% / 4085 mV）、
音频出声（880 / 1319 Hz）。
