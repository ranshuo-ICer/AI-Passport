# 工具链

`tools/` 下的脚本，加上 [`../miniapps/`](../miniapps/) 里的两个。
按「跑在哪」分三类：**电脑上**、**设备上**、**两端配合**。

全部依赖：`esptool` `pyserial` `mpremote`（刷机与部署）、`bleak`（BLE 诊断）。

---

## 1. 工具清单

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
| `hw_bench.py` | **设备** | **热点剖析**：逐项量显示/音频/JSON 的耗时，并验 viper 与纯 Python 逐位一致 |
| `hw_btncheck.py` | **设备** | **按键 ADC 稳定性**：快速采样，确认没有幽灵按键 |
| `hw_audio_mute_repro.py` | **设备** | **复现 KNOWN_ISSUES #24**：退出 Beats 后共享 codec 停在静音。读 `REG31` 取证 |
| `hw_audio_reopen_probe.py` | **设备** | 探 `Audio()` 反复重建是否泄漏/失败 |
| `hw_repeater_probe.py` | **设备** | **验 KNOWN_ISSUES #25**：用真实 repeater 函数跑录音→退出，扫堆水位看重 building 会不会失败 |
| `hw_perf_ab.py` | **设备** | **性能同场 A/B**：新旧实现放一次运行里对比（按键/`fill_rect`/状态栏） |
| `hw_appload_bench.py` | **设备** | 小程序载入拆解：read / compile / exec 分段计时 |
| `e2e_audio_mute.ps1` | 电脑 | **端到端验 #24**：真实 OS + BLE 推送/退出，再读 `REG31`。三阶段 A/B |
| `test_protocol.py` | 电脑 | BLE 协议状态机单测（桩模块，无需硬件） |
| `repro_ping_corruption.py` | 电脑 | **复现 KNOWN_ISSUES #1**：上传期间心跳污染 `app.py`。exit 1 = 缺陷仍在 |
| `test_audio.py` | 电脑 | ES8311 寄存器序列 / 分频 / 音量 / I2S 参数单测 |
| `test_display.py` | 电脑 | **display 的分配行为**：`fill_rect` 单次 SPI 写入不得出现大块连续分配 |
| `lint_micropython.py` | 电脑 | 静态拦截"CPython 有、MicroPython 没有"的 API |
| `check_pwa.py` | 电脑 | 前端 JS 语法 + DOM id 一致性检查 |
| `check_docs.py` | 电脑 | **文档↔代码一致性自检**（链接 / 数量 / 常量 / 协议 / API / 固件哈希） |

依赖：`ble_*` 需要 `bleak`；`deploy.py`/`esp.py` 需要 `mpremote` + `pyserial`。

## 2. `tools/deploy.py` —— 部署 PassportOS

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

## 3. `tools/serve.py` —— 本地托管 PWA

文件：[tools/serve.py](../tools/serve.py)

```
python tools/serve.py            # 默认 8790
python tools/serve.py 9000
```

用 `http.server` 托管 `pwa/` 目录，加 `Cache-Control: no-store`，自动打开浏览器。

## 4. `tools/test_protocol.py` —— 协议单元测试

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

## 5. `tools/esp.py` —— esptool 命令名适配层

文件：[tools/esp.py](../tools/esp.py)

esptool v4 用下划线（`write_flash` / `erase_flash`），v5 改成连字符
（`write-flash` / `erase-flash`）。本脚本探测主版本号后重写命令与取值，
让 `windows/*.bat` 和文档里的命令在两个大版本下都能用。

```
python tools/esp.py --chip esp32c3 --baud 460800 erase_flash
python tools/esp.py --chip esp32c3 --baud 460800 write_flash -z 0x0 firmware/xxx.bin
```

## 6. `tools/ble_client.py` 与 BLE 诊断三件套

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

## 7. 其它校验脚本

```sh
python tools/test_audio.py         # 90 项：ES8311 寄存器/分频/音量/I2S 参数
python tools/lint_micropython.py   # 扫 os/ + hw_selftest + 小程序，共 18 个设备端文件
python tools/check_pwa.py          # 前端 JS 语法 + DOM id 一致性
python tools/check_docs.py         # 文档↔代码一致性（改完文档/代码都该跑）
python tools/repro_ping_corruption.py   # 复现 KNOWN_ISSUES #1（修复前红、修复后绿）
python tools/hw_selftest.py        # 【在设备上跑】屏幕/按键/电池/音频/内存自检
python tools/serial_probe.py       # 读串口日志（原厂或 PassportOS）
```

`hw_selftest.py` 是通过 mpremote 推到设备上执行的，会画屏幕、放提示音，
**会打断正在运行的 PassportOS**。

---
