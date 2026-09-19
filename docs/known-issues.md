# 已知缺陷清单

> 来源：2026-09-19 对全仓的一次系统性代码评审（**只读评审，逐条对过源码**）。
> 每条都注明**触发条件**与**影响面**，按严重程度排列。
>
> **状态（2026-09-19 更新）：本清单已全部处理完毕。**
> 3 条 P0、8 条 P1、12 条 P2 全部修复，并补了对应的回归测试。
> 唯一一条被判定为**误诊**的是 #11（`mck`）—— 已用
> `ports/esp32/machine_i2s.c`（v1.29.0 全文无 `mck`）+ 真机 `mck=None` 复测
> 两项证据定论：ESP32 端口确实没实现这个参数。
>
> 修复后新增/强化的守卫：
> - `tools/repro_ping_corruption.py` —— #1 的可执行复现，修好前红、修好后绿
> - `tools/test_audio.py` 新增"长音分块"用例；删掉两条永真断言（#18）
> - `tools/lint_micropython.py` 扫描范围扩到 `hw_selftest.py` 与 6 个小程序（#17）
> - `tools/check_pwa.py` 重写：路径无关、任一失败都让退出码非 0（#14）
---

## P0 — 会静默损坏数据 / 卡死

### #1 上传小程序期间，手机端心跳被当成源码写进 `app.py` — `FIXED`

| | |
| --- | --- |
| 位置 | `pwa/app.js:30-38,208,343-397` × `os/passport/blepush.py:382,384-407` |
| 触发 | **推送耗时超过 10 秒**（32 KB 应用 = 205 个分片，慢链路/大文件必中） |
| 影响 | 源码被污染 + 末尾字节丢失，**推送仍显示"成功"**，程序跑不起来 |

**机理**：手机每 10 秒发一次心跳 `{"t":"ping"}`（`startHeartbeat()` 在连接后启动，
上传期间**没有停**）。设备在数据模式下只把 `abort` / `end` / `put` / `stop`
识别为控制命令（`_CTRL_DURING_UPLOAD`），`ping` 不在其中，于是这 12 字节走了
`_handle_data()` 的"追加写文件"分支：

1. `app.py` 里混进一行 `{"t":"ping"}` → `SyntaxError`
2. 这 12 字节同时计入 `got`，设备**提前 12 字节**认为收满 → 每次 ping 丢掉 12 字节真实源码

**同一根因的第二个入口**：`awaitMsg()` 只有**一个等待槽**，新命令会 reject 掉旧的
（`app.js:91`）。所以上传途中点「停止」「刷新」「对时」「删除」中任意一个，都会把
推送的 ack 等待顶掉 → 走 catch → 发 `abort` → 设备删除半截应用
（`blepush.py:440-454`）。这些按钮在上传期间并未禁用。

**复现（无需硬件）**：

```sh
python tools/repro_ping_corruption.py     # 修复前：exit 1（红）
                                          # 修复后：exit 0（绿）
```

该脚本用**生产代码本身**（`passport.blepush.AppLink`）走真实的 `_irq()` → `poll()`
路径，只把 `bluetooth` / `machine` 换成桩。当前的输出是：

```
设备回复序列: ['put', 'ack', 'ack', 'done']      ← 设备认为自己成功了
落盘大小: 432 字节 (声明 432)                     ← 连长度校验也发现不了
心跳被写进了源码，位置第 216 字节
  上下文: b'adding padding\nx ={"t":"ping"} 1  # padding padd'
语法检查: SyntaxError: invalid syntax (第 8 行)
```

心跳被插进了**一行代码的中间**，末尾 12 字节真实源码被丢弃，而设备照常回 `done`。

> 该脚本只检验**设备端**是否免疫。App 侧同样要修（上传期间 `stopHeartbeat()`），
> 两者是互补的双层防御 —— 只修 App 的话这个脚本仍会红，这是刻意的：
> 设备端不该依赖客户端守规矩。

**建议修法**：`pushApp` 期间 `stopHeartbeat()` 并把其余命令按钮全部 `disabled`，
`finally` 里恢复；设备端把 `ping` 补进 `_CTRL_DURING_UPLOAD`。

---

### #2 连接失败不回滚，UI 卡在"已连接"且断开无效 — `FIXED`

| | |
| --- | --- |
| 位置 | `pwa/app.js:149-165`, `:198-225`, `:234-244` |
| 触发 | GATT 连上了，但 `hello` 握手（6 秒超时）或 `refreshApps()` 抛错 |
| 影响 | 界面卡死，**只能刷新页面**才能恢复 |

`openGatt()` 在**握手和拉列表之前**就设 `connected = true` 并启动心跳。
若随后抛错，catch 分支只做 `device = null` + 移除监听，**没有断开 GATT、
没清 `cmdChar/rspChar/server`、没复位 `connected`、没停心跳**。
于是：界面显示已连接、按钮可用、心跳还在往一条活链路上打；点「断开」时
`device` 已是 null → 抛 TypeError 被 `catch (_) {}` 吞掉。执行还会继续往下走到
`requestDevice()`，在"已连接"状态下弹设备选择器。

---

### #3 设备端通知分片的 MTU 兜底写反了 — `FIXED`

| | |
| --- | --- |
| 位置 | `os/passport/blepush.py:171-182` |
| 触发 | 中心设备不做 MTU 协商（保持默认 23） |
| 影响 | 超过 20 字节的通知发不出去，客户端永远停在半个 `~` 分片上 |

```python
limit = _CHUNK_LIMIT              # 180
if self.mtu and self.mtu > 23:    # ← 恰好排除了 MTU=23（未协商）这一种情况
    limit = min(limit, self.mtu - 3)
```

实测这个表达式：MTU=23 时算出 **180**，而 ATT 上限只有 **20**。
代码注释与 `docs/ble-protocol.md` 都写着「MTU 没协商出来时按最小可用值发，
宁可多切几片」——**代码做的正好相反**。`_write_rsp` 遇到 OSError 只 `break`，
消息永久丢失。MTU 正常协商到 247 时不触发。

**建议修法**：`limit = 20 if self.mtu <= 23 else min(_CHUNK_LIMIT, self.mtu - 3)`。

---

## P1 — 功能/健壮性缺陷

### #4 `ble_client.py` 保活间隔等于看门狗超时 — `FIXED`

`tools/ble_client.py:303` 是 `if n % 25 == 0:`，注释却写「设备端 **90 秒**空闲会踢人」。
实际 `config.BLE_IDLE_TIMEOUT_MS = 25000`（25 秒），且 `_check_idle()` 从**连接时刻**
开始计时。所以 `console` 模式的第一次 ping 正好踩在超时边界上，容易被反复踢下线
再重新广播。`app.js` 用的是 10 秒，两个客户端不一致。

### #5 `ble_client.py` 推送不校验 ack 的权威计数 — `FIXED`

`tools/ble_client.py:196-219` 收到 ack 后无条件 `off += len(piece)`，设备回的
`m["g"]`（权威已收字节数）被忽略；最后的 `done` 也不与 `len(data)` 比对。
**设备只存了一半也会打印 `✓` 并 `return True`。**

### #6 `_abort_upload()` 在 BLE 中断上下文里做文件 I/O — `FIXED`

`blepush.py:145` 在 `_IRQ_CENTRAL_DISCONNECT` 分支里调用 `_abort_upload()`，
它会 `delete_app()`（多次 `os.remove`）并经 `_apps_changed()` 触发
`refresh_apps()`（`os.listdir` + 逐项 `os.stat` + 读 `meta.json`）。
这**违反了本文件 `_irq` 自己写的**「只做最小动作：拷贝 + 入队」，
有阻塞 BLE 事件处理的风险。

### #7 断开时不清会话状态 — `FIXED`

`pwa/app.js:234-244` 的 `onDisconnected()` 只清连接对象，**不清 `inbox` / `frag` /
`waiter`**。上一会话超时残留的 `ack` / `done` 会被下一轮推送当作第一个回应
（流控静默错位）；残留的 `ls` / `err` 会让新请求拿到旧结果；残留的 `~…` 分片会
被拼到新会话的第一帧前面导致 JSON 解析失败。

### #8 `waking` 从不清零 → 主动断开后自动重连 — `FIXED`

`pwa/app.js:157,181` 把 `waking` 置 `true` 后再没置回。`visibilitychange`
（`:628-630`）只要 `waking` 为真就调 `tryReconnect()`，异常还被静默吞掉
（`:231`）。用户按了「断开」之后，切走再切回来会**悄悄重连**。

### #9 监听器与错误处理 — `FIXED`

- `pwa/app.js:155,179,204`：`gattserverdisconnected` / `characteristicvaluechanged`
  每次重连都重复注册，`onDisconnected` 从不移除（`getDevices()` 返回同一个
  `BluetoothDevice` 对象，所以断开时会触发 N+1 次）
- `pwa/app.js:285,302`：`removeApp` 没有 `.catch()`（其他三个按钮都有），
  失败只在控制台留一条 unhandled rejection，界面无任何反馈
- `pwa/app.js:360-370`：写失败重试 5 次后 `wrote` 仍为 `false`，却继续 `awaitMsg`
  白等 10 秒才超时
- `pwa/app.js:379-384`：`end` 兜底路径若真走到，设备回 `err: 没有正在进行的上传`，
  App 会把**成功的推送报成失败**

### #10 `audio.tone()` 无长度保护且阻塞 — `FIXED`

`os/passport/audio.py:295-313`：缓冲区按时长**一次性分配**
（16 kHz × `ms` × 2 字节）。可用堆约 110 KB，所以 `tone(440, 5000)` 需要 160 KB，
直接 `MemoryError`；`melody()` 还会把多个音长累加。
另外波形是纯 Python 逐样本生成的，`tone`/`melody` 期间**界面冻结、BLE 写入排队**
（队列上限 32，超了静默丢弃 `_drop`，且该计数从不对外报告）。
真机自检时只用了几十到一百多毫秒，所以没暴露。

**建议**：`tone()` 内部分块写入，并对 `ms` 设上限（如 1000）。

### #11 「I2S 不支持 `mck`」的结论与官方文档冲突 — `已定论：ESP32 端口确实不支持`

`README.md` 与 `audio.py:102-107` 把 `use_mclk=False` 的原因写成
「**MicroPython 的 `machine.I2S` 不接受 `mck` 参数**（真机实测 TypeError）」。
但 MicroPython **v1.29.0（正是本项目烧录的版本）** 官方文档的签名是：

```
class machine.I2S(id, *, sck, ws, sd, mck=None, mode, bits, format, rate, ibuf)
```

`mck` 从 v1.24 起就有。原厂固件的自检日志也是 `mclk_multiple: 256`
（`docs/factory-firmware.md`），说明硬件上 MCLK 是通的。

所以这条结论**很可能是误诊**（真正的 TypeError 可能由别的非法参数引起）。
功能上 BCLK 倍频能用、真机也出声了，但走外部 MCLK 是更正规的方案
（时钟更准、少一层倍频抖动）。**建议在真机上重新试一次 `mck=Pin(6)`。**

### #24 退出 Beats 后共享 codec 停在静音，全系统没声音 — `FIXED`

用户报「偶尔会出现不发出声音，好像退出 beats 后就会这样」。

`Ctx.audio` 就是 `shell.audio` 这一个实例（`os/passport/ui.py:37`），所有小程序
共用同一颗 ES8311。而 `miniapps/beats.py` 的 `teardown()` 里调了
`ctx.audio.mute(True)` —— 它写的是 `REG31` 的 DAC 静音位，**这个位会一直 latch
住**，而全仓库**没有任何地方**调 `mute(False)`（只有 `Audio.__init__` 会）。

于是「长按 OK 退出 Beats」= 把整台设备的 DAC 永久静音，之后每个小程序都哑，
只有重启能救。`set_volume()` 只写 `REG32`、不碰 `REG31`，所以**重新进 Beats
也救不回来**。

真机取证（`tools/hw_audio_mute_repro.py`，读 ES8311 `REG31`）：

| 步骤 | REG31 |
| --- | --- |
| 新建 `Audio()`（= 开机） | `0x00` 有声 |
| 调真实 `beats.teardown()` | `0x60` **静音** |
| 随后 `set_volume(70)` + `tone(880, 150)` | `0x60` **仍然静音** |

之前没被发现，是因为它是"下一个程序才发作"的延迟故障：Beats 自己听着完全正常。

**修复**（三层，任何一层单独都能挡住）：

1. `miniapps/beats.py`：`teardown()` 不再碰共享 codec。退出后 `loop()` 停止写
   I2S，输出本来就是静音，没有理由去关 DAC。
2. `os/passport/audio.py` `set_volume(pct)`：`pct>0` 时顺带解除静音，让"设了
   音量却没声音"变成不可能。
3. `os/passport/audio.py` 新增 `reset_state()`（取消静音 + 回默认音量），
   由 `os/passport/ui.py` 的 `launch()` 在**每次启动小程序前**调用 —— 外壳是
   共享外设的owner，必须保证下一个程序拿到的是干净状态，而不是指望每个程序
   自己记得收尾。

同一族问题还有一处未验证的隐患，见 #25。

### #25 `repeater` 用「重建实例」修复共享音频，失败即全系统哑 — `待定`

`miniapps/repeater.py`（录音）必须独占 I2S(0)，所以每次录音都要先
`_release()` 把共享的 `Audio` **deinit 掉**（`.ok` 从此为 `False`），再在
`teardown()` 里 `Audio()` 造一个**新实例**塞回 `ctx.shell.audio`：

```python
if ctx.moved:
    fresh = Audio()
    if fresh.ok:
        ctx.shell.audio = fresh
```

一旦这次 `Audio()` 构造失败（这块板无 PSRAM、堆常年碎片化，`apps.py` 的
docstring 就记着"还有 78 KB 空闲却分配不出 19 KB"），`fresh.ok` 为假 →
`shell.audio` **仍是那个已死的旧实例** → 全系统没声音，同样只有重启能救。
`except` 分支还静默吞掉了异常。

本次修复的 `reset_state()` 对死实例会直接 `return`（`if not self.ok`），
所以**挡不住这条路径**。

**已用真实代码路径复现机制，但没能触发失败**（`tools/hw_repeater_probe.py`）：
直接 `load_module("repeater")` 并调用它**自己的** `_release()` / `_mk_rx()` /
`_mk_buf()` / `_endrx()`，再照 `teardown()` 的顺序重建 `Audio()`。

- 「共享 Audio 被 deinit」**每次都会发生**：18/18 轮 —— 机制是真的。
- 「重建失败」**一次都没出现**：18/18 轮成功，即使额外压 0 / 20 / 40 / 48 /
  60 / 68 KB 压舱物、把重建前的空闲堆压到 41 KB 也一样。

过程中修掉了探针自己的两个错误，都值得记下来（否则会得出相反的结论）：

1. 第一版把 `ctx.parts = []`（释放 64 KB 录音缓冲）放在重建**之前**，于是
   8/8 全成功。但 `repeater.teardown()` 里 `ctx.parts = []` 排在 `Audio()`
   重建**之后**（`repeater.py:466-475`）—— 重建时那 64 KB **还活着**。顺序错了，
   测的就不是同一条路径。
2. 裸跑时堆有 127 KB 空闲，比真机宽松得多，必须加压舱物才接近真实水位。

**结论：机制存在，但在合成压力下无法触发。** 它保持「待定」而不是关掉 ——
压舱物是均匀的 4 KB 块，而真机的碎片来自蓝牙缓冲、显示缓存、前几次小程序切换，
形状不同。没有证据说它安全，也没有证据说它会炸。

**待办**：在真机上把 repeater 完整跑一遍（录音 → 退出 → 看 `shell.audio.ok`）；
若确认会死，再把 `Audio` 改成可原地重开（拆出 `reopen()`，复用同一个实例重建
I2S + 重跑 codec 配置），让 repeater 不再需要替换外壳的实例。

---

## P2 — 其它（工具/测试/卫生）

**2026-08 复核：#12–#23 全部已修复。**

复核方式不是"看代码像修了"，而是**逐条跑那条代码路径**（完整命令与输出见下表
「验证」列，复核稿逻辑见本节的「复核方法」）。复核时有 **2 条最初被误判成
仍未修**，原因值得记下来：文本搜索被"警告不要用这个 API"的**注释/docstring
本身**骗到了（`#18` 的 `check(..., True)` 出现在一句说明里、`#19` 的
`str.isalnum()` 出现在 `_safe_name()` 的 docstring 里）。判这类问题要么
剔注释/docstring，要么用 `ast` 找真实调用 —— 见「复核方法」。

| # | 位置 | 原问题 | 状态 | 验证 |
| --- | --- | --- | --- | --- |
| 12 | `tools/deploy.py` | `--dry-run` 在探测串口之后才判断 | `FIXED` | `deploy.py COM99 --dry-run`（不存在的口）→ exit 0 并打印计划 |
| 13 | `tools/deploy.py` | `--clean` 无二次确认，失败即失去可启动系统 | `FIXED` | 不带 `--yes` 跑 `--clean` → exit 1，正文要求手输 `yes` |
| 14 | `tools/check_pwa.py` | 图标缺失不影响退出码 | `FIXED` | `main()` 结尾 `return 1 if fails else 0`，缺失图标进 `fails` |
| 15 | `tools/esp.py` | esptool v4 横幅匹配不上 → 误判 v5 | `FIXED` | `_major_from_text("esptool.py v4.7.0")`→4，`("esptool v5.4.0")`→5，`("4.7.0")`→4 |
| 16 | `tools/esp.py` | v5 取值表缺 `watchdog_reset` | `FIXED` | `V5_VALUES["watchdog_reset"] == "watchdog-reset"` |
| 17 | `tools/lint_micropython.py` | 单行 docstring 误报；`hw_selftest.py` 漏扫 | `FIXED` | 临时塞一个含 `.byteswap()` 的单行 docstring → 不报；`device_files()` 含 `tools/hw_selftest.py`（共 18 个设备端文件） |
| 18 | `tools/test_audio.py` | `check(…, True)` 空洞断言 | `FIXED` | 剔注释后正则匹配 0 处（原命中那句是「真检查（不是 check(..., True)）」的说明） |
| 19 | `tools/ble_client.py` | `str.isalnum()` 对中文为真；`--name` 不归一化 | `FIXED` | `ast` 找 `.isalnum` 属性 0 处；`push 时钟.py`→`app`，`--name Dice`→`dice`，都过 `_NAME_RE` |
| 20 | `tools/ble_disconnect_test.py` | 结论被 25 秒看门狗污染 | `FIXED` | 已按"数字只有出现在看门狗触发之前才有意义"分支给结论 |
| 21 | `pwa/app.js` | `currentSource()` 是死代码 | `FIXED` | 函数体就是一行 `return $('editor').value;` |
| 22 | `os/passport/config.py` | 五个常量无人引用 | `FIXED` | 逐个扫 `os/` + `miniapps/`，五个都有引用者 |
| 23 | 全仓 | `__pycache__`/`.pyc` 进仓库、无 `.gitignore` | `FIXED` | `git ls-files` 里 0 个；`.gitignore` 存在 |

### 复核方法

这次复核踩到的教训：**用文本搜索判断"某个 API 还在不在用"是不可靠的**。
`#18`/`#19` 都因为注释或 docstring 里写了一模一样的字符串而误报成"仍未修"。
可靠的做法：

- 判断某个**调用**是否还在 → 用 `ast` 遍历找 `ast.Attribute`，而不是正则搜文本；
- 判断某段**行为**是否还在 → 真的跑那条路径（如表里的 `deploy.py COM99 --dry-run`）。

`tools/check_docs.py` 的 `check_known_issues()` 现在会校验每条的**状态标记**
（`FIXED` / `待定` / `已定论` …）与编号连续性，防止这份清单再次悄悄漂移 ——
但**它无法校验状态是不是真的**，那是人（或上面这种复核）的事。

---

## 已经做得对、不要改回去的地方

- **`gatts_set_buffer(cmd_handle, 2048)`**（`blepush.py:98`）：不显式放大特征值缓冲，
  超过 20 字节的命令会被**静默截断**，整个推送流程直接不可用。这是全项目最关键的一处真机发现。
- **`_to_be()` 手写字节翻转**（`display.py:48`）：MicroPython 的 `array` 确实没有
  `byteswap()`（已对官方文档核实），而 CPython 有 —— 电脑上单测全绿、真机才炸。
- **设备端主动断开（`bye`）+ 25 秒空闲看门狗**：Windows 确实会在客户端 `disconnect()`
  之后继续抓着 BLE 链路，设备侧收不到断开事件就不广播，表现就是"再也扫不到设备"。
- **`deploy.py` 改用 mpremote**：手搓 raw REPL 每片一次往返，会卡住并留下截断文件。
- **对「`0x700000` 有 Recovery 分区」的纠正**：已用 `upstream/docs/CHANGELOG.md`
  交叉验证 —— 官方明确写了 *Removed the obsolete app/test partition at `0x700000`*。
  **这块板没有板载恢复兜底，原厂固件备份是唯一退路。**
