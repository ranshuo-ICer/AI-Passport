# 手机端 App（Passport 助手）

路线 B 的手机端是一个 **Web Bluetooth PWA**，纯静态、无需 APK、无需签名 ——
在本地起一个 HTTP 服务就能用，可以「添加到主屏幕」当独立应用。

为什么要本地服务：Web Bluetooth 要求安全上下文，`http://127.0.0.1` 算，
`file://` 不算（且 Android Chrome 屏蔽 `file://` 导航）。

> 一次性设置：打开后点「添加到主屏幕」。Service Worker 会把界面缓存下来，
> **之后即使本地服务没开也能启动**（缓存优先策略）。

---

目录：[pwa/](../pwa/)

## 1. 架构

纯静态 PWA，通过 **Web Bluetooth** 连接设备。无需 APK/签名，本地起 HTTP 服务即可（`http://127.0.0.1` 被浏览器视为安全上下文）。

```
index.html  →  三标签页：应用 / 编辑器 / 日志
app.js      →  BLE 连接、命令收发、推送分片、模板、localStorage 持久化
style.css   →  深色主题样式
sw.js       →  Service Worker：缓存优先 + 后台更新（断网可用）
manifest.webmanifest → PWA 清单（standalone 模式）
```

## 2. `app.js` 关键逻辑

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

## 3. `sw.js` Service Worker

- 缓存名 `passport-pwa-v6`（**改动 pwa/ 下任何资源后必须递增这个版本号**，否则浏览器会一直用旧缓存）
- 缓存资源：index.html / style.css / app.js / manifest / icons
- 策略：缓存优先，后台更新；断网时返回缓存

---
