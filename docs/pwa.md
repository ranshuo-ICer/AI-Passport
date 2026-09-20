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

- 缓存名 `passport-pwa-v7`（**改动 pwa/ 下任何资源后必须递增这个版本号**，否则浏览器会一直用旧缓存）
- 缓存资源：index.html / style.css / app.js / manifest / icons
- 策略：缓存优先，后台更新；断网时返回缓存

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
