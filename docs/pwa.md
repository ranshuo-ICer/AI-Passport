# 手机端 App（Passport 助手）

路线 B 的手机端是一个 **Web Bluetooth PWA**，纯静态、无需 APK、无需签名 ——
在本地起一个 HTTP 服务就能用，可以「添加到主屏幕」当独立应用。

为什么要本地服务：Web Bluetooth 要求安全上下文，`http://127.0.0.1` 算，
`file://` 不算（且 Android Chrome 屏蔽 `file://` 导航）。

> 一次性设置：打开后点「添加到主屏幕」。Service Worker 会把界面缓存下来，
> **之后即使本地服务没开也能启动**（缓存优先策略）。

## 0. 在线地址（手机上直接用这个）

**<https://ranshuo-ICer.github.io/AI-Passport/>**

本地 `http://127.0.0.1:8790` 只有那台电脑能开，**手机访问不到**；而 Web Bluetooth
又必须要安全上下文。GitHub Pages 是 HTTPS 且公网可达，所以手机上用它 ——
这是目前唯一能在手机上跑起来的方式。

部署与更新见文末「部署到 GitHub Pages」。

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

> ### 手机上"显示已连接，但一点就报 GATT Server is disconnected"
>
> 这是真机上踩到的，成因有两半，缺一不可：
>
> 1. **设备端有 25 秒空闲看门狗**（`BLE_IDLE_TIMEOUT_MS`）：只要 25 秒没有任何
>    GATT 写入就主动断开、恢复广播。而**手机浏览器在页面切后台 / 锁屏时会把
>    `setInterval` 限流（Android Chrome 后台标签最低约每分钟一次）甚至直接冻结** ——
>    心跳一停，设备就把链路踢了。心跳从 10 秒改成 4 秒就是为了给前台留足余量。
> 2. **Android 的 Web Bluetooth 不保证把 `gattserverdisconnected` 事件送到页面**。
>    所以不能只靠事件：界面还以为连着、按钮还能点，直到下一次 GATT 操作才抛
>    `GATT Server is disconnected`（这句就是 `cmdChar.writeValue()` 抛出来的）。
>
> 所以三件事一起做：**动手前先看 `device.gatt.connected`**、**任何 GATT 操作
> 报链路错就当场判定断开**、**自动重连**（已授权设备重连不需要再弹选择器）。
> 后台被冻结是躲不掉的，靠自动重连兜底。
>
> ⚠ 上传过程中**绝不重连**：设备在数据模式下把收到的每个字节都当 `app.py` 的内容，
> 重连要发的 `{"t":"hello"}` 会被原样写进源码（就是 KNOWN_ISSUES #1 那类损坏）。
> 所以 `sendCmd()` 里的重连判断都带 `&& !pushing`。

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
| `startHeartbeat()` / `stopHeartbeat()` | 每 **4 秒** ping 一次，给设备端 25 秒空闲看门狗续命（原来是 10 秒 —— 手机上不够，见下） |
| `wakeUp()` | 回到前台 / 获得焦点 / 从 bfcache 恢复：补一次 ping，链路没了就重连 |
| `autoReconnect()` | 自动重连已授权设备（`gatt.connect()` 不会再弹选择器），3 次退避重试 |
| `handleLinkLost()` | 判定链路已断：清会话状态 + 自动重连 |
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

- 缓存名 `passport-pwa-v9`（**改动 pwa/ 下任何资源后必须递增这个版本号**，否则浏览器会一直用旧缓存）
- 缓存资源：index.html / style.css / app.js / manifest / icons
- 策略（v9 起）：
  - **代码类**（HTML / JS / CSS / webmanifest，含导航请求）→ **网络优先**
  - **图片类**（png/jpg/svg/ico/webp）→ 缓存优先，后台静默刷新
  - 网络优先的请求带 `cache: 'no-cache'`，强制跟服务器校验 ETag（通常只回 304），
    3 秒拿不到就回退缓存 —— 断网时仍然能打开（蓝牙不需要网络）

### 为什么从"全站缓存优先"改成"代码网络优先"

以前整站都是"缓存优先 + 后台更新"，后果是：**每次部署后，用户第一次打开
拿到的必然是旧版**，要关掉再开一次才会更新。加上 GitHub Pages 给所有文件发
`Cache-Control: max-age=600`，这条路径能连续骗过两次加载。表现出来就是
"代码明明改了，手机上还是老行为" —— 很容易误判成逻辑有 bug，然后去改本来就
正确的代码。改完之后，部署后的第一次打开就是新版。

### 版本号必须三处一致

| 位置 | 内容 |
| --- | --- |
| `pwa/sw.js` | `const CACHE = 'passport-pwa-v9'` |
| `pwa/app.js` | `const APP_VERSION = 'v9'` |
| `pwa/index.html` | `<span id="appVer">`（由 app.js 的 `init()` 填入） |

界面上标题旁会显示这个版本号。**手机上报版本号**是判断"跑的是新版还是缓存旧版"
的唯一可靠依据 —— 这三处不一致时 `tools/check_pwa.py` 会失败，所以别手工改一处。

### 本地怎么验（不需要手机）

`tools/pwa_ble_sim.mjs` 用 Node 的 `vm` 把 `pwa/app.js` 真加载起来，配一套假的
`navigator.bluetooth` / GATT 设备，驱动三条关键路径：

```bash
node tools/pwa_ble_sim.mjs
```

| 场景 | 断言 |
| --- | --- |
| A 正常连接 + 刷新列表 | hello / ls 真的写出去了，刷新后仍连接 |
| B 刷新时链路**已悄悄断掉** | 触发自动重连，重连后恢复已连接（而不是弹"请重新连接"） |
| C 上传过程中链路断掉 | **绝不重连**（重连要发的 hello 会被设备当 app.py 源码写进去，就是 #1 那类损坏） |

这个测试是必要的：手机上的 Web Bluetooth 没法自动化，而这段重连逻辑如果只做
语法检查，等于没测过。它当初一跑就抓到一个真实缺陷 —— `refreshApps()` 先建等待器
再 `sendCmd`，写失败抛异常时等待器永远等不到 `await`，成了"未处理的 Promise 拒绝"
（浏览器控制台满屏 `Uncaught (in promise)`）。修法见 `awaitMsg()` 里那个空 `catch`。

---

## 4. 部署到 GitHub Pages

在线地址：**<https://ranshuo-ICer.github.io/AI-Passport/>**

```sh
python tools/pages_deploy.py            # 发布/更新
python tools/pages_deploy.py --dry-run  # 只看看会发布哪些文件
```

**改完 `pwa/` 之后必须重新跑一次**，站点不会自己更新。别忘了同时递增 `sw.js`
里的缓存版本号，否则手机上还是旧界面。

### 为什么是 `gh-pages` 分支，而不是 GitHub Actions

两条路都要在仓库设置里点一次（`actions/configure-pages` 的 `enablement` 参数虽能
自动启用 Pages，但它的 action.yml 写明**必须提供非 `GITHUB_TOKEN` 的 PAT**）。
而 `gh-pages` 分支有概率被 GitHub **自动启用** —— 本项目实测推上去就直接生效了，
一次都不用点。所以这条路的期望成本更低。

### 为什么不放进你的 GitHub 主页（Hexo 博客）

`ranshuo-ICer.github.io` 是 Hexo 博客，而 `hexo deploy` **通常强推覆盖整个仓库** ——
往里手工放一个 `passport/` 目录，下次部署博客就没了。放在项目自己的 Pages 下
（`/AI-Passport/`）与博客互不干扰。

### 实现要点（`tools/pages_deploy.py`）

用 `git hash-object` / `update-index` / `write-tree` / `commit-tree` 直接构造一个
**孤立提交**，全程不碰 `main` 的工作区、也不切分支 —— `git worktree` 和
`checkout --orphan` 都会动到当前检出状态，在这个仓库里那样做风险太大。

发布内容 = `pwa/` 下的文件（排除 `*.py` 这种开发脚本）+ 一个 `.nojekyll`
（关掉 Jekyll，它默认会忽略下划线开头的文件）。

> ⚠ 站点内容是 `pwa/` 的**副本**，`gh-pages` 分支不要手工编辑 —— 下次
> `pages_deploy.py` 会用 `main` 的内容整体覆盖它。

### 自检

```sh
python tools/verify_pages.py     # 逐个抓回线上文件，和本地 pwa/ 逐字节比对
```

---
