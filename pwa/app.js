/* ============================================================================
 * Passport 助手 —— 通过 Web Bluetooth 给 FoloToy AI Passport 推送小程序
 *
 * 协议见 ../docs/PROTOCOL.md。要点：
 *   CMD 特征（写）：无上传任务时是 JSON 命令；上传中就是 app.py 的原始字节
 *   RSP 特征（通知）：设备 → 手机的 JSON，超过单包 MTU 时用 '~'/'!' 分片
 *   流控：每发一片数据等设备回 ack 再发下一片
 * ==========================================================================*/

'use strict';

const UUID_SERVICE = '7a5c0001-0000-4000-8000-70617373706f';
const UUID_CMD     = '7a5c0002-0000-4000-8000-70617373706f';
const UUID_RSP     = '7a5c0003-0000-4000-8000-70617373706f';

const MAX_APP_BYTES = 32 * 1024;
const NAME_RE = /^[a-z0-9_-]{1,16}$/;

let device = null, server = null, cmdChar = null, rspChar = null;
let connected = false;
let waiter = null;             // 当前等待中的响应
const inbox = [];              // 早到的消息先存这里
let frag = '';                 // 通知分片累积
let waking = false;            // 正在重连广播
let heartbeat = null;          // 心跳定时器
let pushing = false;           // 上传中：锁住其它命令按钮、停心跳

// 设备端有"连接空闲 25 秒就自动断开恢复广播"的看门狗。
// 我们每 10 秒 ping 一次续命：正常会话不会被误杀，
// 而页面被关掉/崩溃时设备能在 25 秒内自己恢复广播。
const HEARTBEAT_MS = 10000;

function startHeartbeat() {
  stopHeartbeat();
  heartbeat = setInterval(async () => {
    if (!connected) return;
    try { await sendCmd({ t: 'ping' }); } catch (_) { /* 断了就算了 */ }
  }, HEARTBEAT_MS);
}

function stopHeartbeat() {
  if (heartbeat) { clearInterval(heartbeat); heartbeat = null; }
}

const $ = id => document.getElementById(id);

/* ------------------------------------------------------------------ 日志 */
function log(msg, kind = 'sys') {
  const el = $('logs');
  const t = new Date().toLocaleTimeString('zh-CN', { hour12: false });
  const line = document.createElement('div');
  line.className = 't-' + kind;
  line.textContent = `[${t}] ${msg}`;
  el.appendChild(line);
  while (el.childElementCount > 800) el.removeChild(el.firstChild);
  if ($('autoScroll').checked) el.scrollTop = el.scrollHeight;
}

let toastTimer = null;
function toast(msg, ms = 2200) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), ms);
}

/* -------------------------------------------------------------- 消息收发 */
function onMsg(m) {
  if (m.t === 'log') { log('设备: ' + m.m, 'in'); return; }
  if (m.t === 'key') { log('按键: ' + m.k, 'in'); return; }
  if (m.t === 'pong') return;            // 心跳回包，丢掉就行
  if (m.t === 'state') {
    $('statRun').textContent = m.app || '无';
    log('运行状态: ' + (m.app || '主菜单'), 'in');
    return;
  }
  if (m.t === 'err') log('设备报错: ' + m.m, 'err');

  if (waiter && waiter.types.includes(m.t)) {
    const w = waiter; waiter = null; clearTimeout(w.timer); w.resolve(m);
  } else {
    inbox.push(m);
    if (inbox.length > 50) inbox.shift();
  }
}

function awaitMsg(types, timeout = 10000) {
  const i = inbox.findIndex(m => types.includes(m.t));
  if (i >= 0) return Promise.resolve(inbox.splice(i, 1)[0]);
  return new Promise((resolve, reject) => {
    if (waiter) { clearTimeout(waiter.timer); waiter.reject(new Error('请求被新命令覆盖')); }
    const timer = setTimeout(() => {
      if (waiter && waiter.timer === timer) waiter = null;
      reject(new Error('等待设备响应超时'));
    }, timeout);
    waiter = { types, resolve, reject, timer };
  });
}

function onNotify(event) {
  const text = new TextDecoder().decode(event.target.value);
  if (!text) return;
  const head = text[0];
  if (head === '~') { frag += text.slice(1); return; }
  let payload = text;
  if (head === '!') { frag += text.slice(1); payload = frag; frag = ''; }
  else frag = '';
  try {
    onMsg(JSON.parse(payload));
  } catch (e) {
    log('收到无法解析的数据: ' + text.slice(0, 80), 'err');
  }
}

async function sendCmd(obj) {
  const data = new TextEncoder().encode(JSON.stringify(obj));
  if (!cmdChar) throw new Error('未连接');
  log('→ ' + JSON.stringify(obj), 'out');
  await cmdChar.writeValue(data);
}

/* ------------------------------------------------------------------ 连接 */
/* 连接失败回滚。
 * GATT 可能其实已经连上了，只是握手/拉列表失败 —— 必须主动断开并清干净，
 * 否则界面卡在"已连接"、按钮可用、心跳还在打，点「断开」也无效。 */
function rollbackConnect() {
  try {
    if (device && device.gatt && device.gatt.connected) device.gatt.disconnect();
  } catch (_) { /* 忽略 */ }
  teardownConnection('未连接', true);
  device = null;
}

/* 找回【之前已经授权过】的设备。
 * 走这条路完全不需要扫描选择器，也就不受"选择器里看不到设备"的影响。
 * navigator.bluetooth.getDevices() 在 Chrome/Edge 85+ 可用。 */
async function pickRemembered() {
  if (!navigator.bluetooth || !navigator.bluetooth.getDevices) return null;
  try {
    const list = await navigator.bluetooth.getDevices();
    log('已授权的设备数: ' + list.length);
    const hit = list.find(d => (d.name || '').indexOf('Passport') === 0)
             || list.find(d => d.id);
    return hit || null;
  } catch (e) {
    log('getDevices() 不可用: ' + e.message);
    return null;
  }
}

async function connect(forceChooser) {
  if (!navigator.bluetooth) {
    toast('这个浏览器不支持 Web Bluetooth');
    log('navigator.bluetooth 不存在：必须用 http://127.0.0.1 或 https 打开，'
        + '不能直接双击 index.html', 'err');
    return;
  }

  // 1) 先试已授权的设备（不用扫描）
  if (!forceChooser) {
    const known = await pickRemembered();
    if (known) {
      try {
        log('尝试直连已授权设备: ' + (known.name || known.id));
        device = known;
        attachDisconnectListener(device);
        await openGatt();
        waking = true;
        return;
      } catch (e) {
        log('直连失败（设备可能不在广播）: ' + e.message, 'err');
        rollbackConnect();
      }
    }
  }

  // 2) 打开设备选择器
  try {
    log('打开设备选择器（需要设备正在广播）…');
    device = await navigator.bluetooth.requestDevice({
      // 两个 filter 是"或"关系：靠服务 UUID 或名字前缀都能匹配上，
      // 万一某个广播字段没被浏览器解析出来还有退路。
      filters: [
        { services: [UUID_SERVICE] },
        { namePrefix: 'Passport' },
      ],
      optionalServices: [UUID_SERVICE],
    });
    attachDisconnectListener(device);
    await openGatt();
    waking = true;
  } catch (e) {
    rollbackConnect();
    log('连接失败: ' + (e.name || '') + ' ' + e.message, 'err');
    if (e.name === 'NotFoundError') {
      toast('没找到设备。确认设备开机、屏幕显示 Passport 菜单，且没被别人连着', 5000);
      log('排查建议：', 'err');
      log('  1) 设备屏幕上应显示 Passport 主菜单（说明系统在跑、在广播）', 'err');
      log('  2) 关掉其它占用蓝牙的程序 / 之前开着的本页面标签页', 'err');
      log('  3) 设置 → 蓝牙和其他设备：若列表里有 PassportOS，先删掉', 'err');
      log('  4) 把系统蓝牙开关关掉再打开，然后刷新本页重试', 'err');
      log('  5) 还不行就点「换设备」强制重开选择器', 'err');
    } else {
      toast('连接失败: ' + e.message);
    }
  }
}

async function openGatt() {
  server = await device.gatt.connect();
  const service = await server.getPrimaryService(UUID_SERVICE);
  cmdChar = await service.getCharacteristic(UUID_CMD);
  rspChar = await service.getCharacteristic(UUID_RSP);
  await rspChar.startNotifications();
  rspChar.addEventListener('characteristicvaluechanged', onNotify);

  // ★ 先握手，成功了才认为"已连接"。
  //   之前 connected=true / startHeartbeat() 放在握手之前，握手一抛错就留下
  //   "界面显示已连接、按钮可用、心跳还在打、但 device 已被置空"的僵死状态，
  //   点「断开」也没用（device.gatt 抛 TypeError 被吞），只能刷新页面。
  const p = awaitMsg(['hi', 'err'], 6000);
  await sendCmd({ t: 'hello' });
  const hi = await p;

  connected = true;
  setUi(true);
  startHeartbeat();
  const name = device.name || 'PassportOS';
  $('devName').textContent = name + ' 已连接';
  $('devName').classList.add('on');
  log('已连接: ' + name, 'sys');

  if (hi.t === 'hi') {
    log(`设备: ${hi.os}  已装 ${hi.apps} 个  可用 ${fmtBytes(hi.free)}  MTU=${hi.mtu}`, 'sys');
    $('statApps').textContent = hi.apps;
    $('statFree').textContent = fmtBytes(hi.free);
  }
  await refreshApps();
}

async function tryReconnect() {
  if (connected || !device || !device.gatt || !waking) return;
  try {
    await openGatt();
  } catch (e) {
    log('自动重连失败: ' + e.message, 'err');
  }
}

/* 统一的拆连接出口。
 * 正常断开、握手失败、用户主动断开都走这里，保证 UI / 定时器 / 会话缓冲
 * 不会留下半截状态。
 *
 * 必须清 inbox/frag/waiter：否则上一会话残留的 ack/done 会被下一轮推送
 * 当作第一个回应（流控静默错位），残留的 ls/err 会让新请求拿到旧结果，
 * 残留的 '~' 分片会被拼到新会话第一帧前面导致 JSON 解析失败。 */
function teardownConnection(label, quiet) {
  connected = false;
  stopHeartbeat();
  cmdChar = rspChar = server = null;
  inbox.length = 0;
  frag = '';
  if (waiter) {
    clearTimeout(waiter.timer);
    const w = waiter;
    waiter = null;
    try { w.reject(new Error(label || '连接已结束')); } catch (_) {}
  }
  setUi(false);
  $('devName').textContent = label || '已断开';
  $('devName').classList.remove('on');
  $('statRun').textContent = '–';
  if (!quiet) log('连接已结束: ' + (label || ''), 'sys');
}

function onDisconnected() {
  const was = connected;
  teardownConnection('已断开', true);
  log('设备已断开', 'err');
  if (was) toast('设备已断开');
}

/* gattserverdisconnected 每次重连都注册会累积（getDevices() 返回的是同一个
 * BluetoothDevice 对象），断开时会触发 N+1 次。所以按设备打标记只挂一次。 */
function attachDisconnectListener(dev) {
  if (dev.__ppHooked) return;
  dev.__ppHooked = true;
  dev.addEventListener('gattserverdisconnected', onDisconnected);
}

function setUi(on) {
  // 上传期间只留「断开」可用，其余命令一律锁死：
  // awaitMsg 只有一个等待槽，中途插入别的命令会把推送的 ack 等待顶掉。
  const lock = on && !pushing;
  for (const id of ['btnRefresh', 'btnSyncTime', 'btnStop', 'btnPush', 'btnPushRun'])
    $(id).disabled = !lock;
  $('btnConnect').textContent = on ? '断开' : '连接';
  $('btnPick').disabled = pushing;
}

/* ------------------------------------------------------------------ 操作 */
function fmtBytes(n) {
  if (n == null || n < 0) return '–';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' K';
  return (n / 1048576).toFixed(2) + ' M';
}

async function refreshApps() {
  if (!connected) return;
  const p = awaitMsg(['ls', 'err']);
  await sendCmd({ t: 'ls' });
  const r = await p;
  if (r.t !== 'ls') return;
  const list = $('appList');
  list.innerHTML = '';
  $('statApps').textContent = r.apps.length;
  if (!r.apps.length) {
    list.innerHTML = '<div class="empty">设备上还没有小程序，推送一个试试</div>';
    return;
  }
  for (const app of r.apps) {
    const row = document.createElement('div');
    row.className = 'app-row';
    row.innerHTML = `<div class="meta">
        <div class="nm"></div><div class="sz"></div></div>`;
    row.querySelector('.nm').textContent = app.title || app.n;
    row.querySelector('.sz').textContent = `${app.n} · ${fmtBytes(app.s)}`;
    const run = document.createElement('button');
    run.className = 'run'; run.textContent = '运行';
    run.onclick = () => runApp(app.n);
    const del = document.createElement('button');
    del.className = 'del'; del.textContent = '删除';
    del.onclick = () => removeApp(app.n);
    row.appendChild(run); row.appendChild(del);
    list.appendChild(row);
  }
}

async function runApp(name) {
  try {
    const p = awaitMsg(['run', 'err']);
    await sendCmd({ t: 'run', n: name });
    const r = await p;
    if (r.t === 'err') throw new Error(r.m);
    toast(r.ok ? `已在设备上启动 ${name}` : `${name} 启动失败，看设备屏幕`);
    $('statRun').textContent = r.ok ? name : '无';
  } catch (e) { log('运行失败: ' + e.message, 'err'); toast(e.message); }
}

async function removeApp(name) {
  if (!confirm(`确定从设备删除 ${name} ？`)) return;
  try {
    const p = awaitMsg(['rm', 'err']);
    await sendCmd({ t: 'rm', n: name });
    const r = await p;
    log(r.ok ? `已删除 ${name}` : `删除 ${name} 失败`, r.ok ? 'sys' : 'err');
    if (!r.ok) toast(`删除 ${name} 失败`);
    await refreshApps();
  } catch (e) {
    // 以前这里没有 catch：失败只会在控制台留一条 unhandled rejection，
    // 界面上什么都看不到。
    log('删除失败: ' + e.message, 'err');
    toast('删除失败: ' + e.message);
  }
}

async function syncTime() {
  const now = new Date();
  const p = awaitMsg(['time', 'err']);
  await sendCmd({
    t: 'time',
    epoch: Math.floor(now.getTime() / 1000),
    tz: -now.getTimezoneOffset() * 60,
  });
  const r = await p;
  if (r.t === 'time' && r.ok) { toast('设备时间已校准'); log('已对时 → ' + now.toLocaleString('zh-CN'), 'sys'); }
  else { toast('对时失败'); log('对时失败: ' + (r.m || ''), 'err'); }
}

async function stopApp() {
  const p = awaitMsg(['stop', 'err']);
  await sendCmd({ t: 'stop' });
  await p;
  $('statRun').textContent = '无';
  toast('已停止');
}

/* -------------------------------------------------------------- 推送小程序 */
async function pushApp(name, title, source, alsoRun) {
  if (!connected) { toast('先连接设备'); return; }
  if (!NAME_RE.test(name)) { toast('名称只能用小写字母/数字/-/_，且 ≤16 字符'); return; }
  const bytes = new TextEncoder().encode(source);
  if (!bytes.length) { toast('代码是空的'); return; }
  if (bytes.length > MAX_APP_BYTES) {
    toast(`代码 ${fmtBytes(bytes.length)} 超过上限 ${fmtBytes(MAX_APP_BYTES)}`);
    return;
  }

  const btns = [$('btnPush'), $('btnPushRun')];
  btns.forEach(b => b.disabled = true);

  // 上传期间必须停心跳：设备在数据模式下收到的每个字节都直接写进 app.py，
  // 而心跳 {"t":"ping"} 是 12 字节 —— 会被当成源码写进文件，并让设备
  // 提前 12 字节认为收满，结果是**静默损坏且照常报成功**。
  // 设备端也已把 ping 加进控制命令白名单（双层防御），但客户端不该依赖它。
  // 顺带把所有命令按钮锁死：awaitMsg 只有一个等待槽，中途点别的按钮会把
  // 推送的 ack 等待顶掉 → 走 catch → 发 abort → 设备删掉半截应用。
  pushing = true;
  stopHeartbeat();
  setUi(connected);

  try {
    log(`开始推送 ${name}（${bytes.length} 字节）…`, 'sys');

    let p = awaitMsg(['put', 'err'], 8000);
    await sendCmd({ t: 'put', n: name, s: bytes.length, title: title || name });
    let r = await p;
    if (r.t === 'err') throw new Error(r.m);

    let chunk = 160;                 // 先乐观一点，写失败再退让
    let off = 0;
    let finished = false;

    while (off < bytes.length) {
      let slice = bytes.slice(off, off + chunk);
      let wrote = false;
      for (let attempt = 0; attempt < 5 && !wrote; attempt++) {
        try {
          await cmdChar.writeValue(slice);
          wrote = true;
        } catch (e) {
          if (slice.length <= 20) throw new Error('BLE 写入失败：' + e.message);
          chunk = Math.max(20, Math.floor(slice.length / 2));
          log(`写入被拒，分片降到 ${chunk} 字节重试`, 'err');
          slice = bytes.slice(off, off + chunk);
        }
      }
      // 重试 5 次仍没写出去时，以前会继续 awaitMsg 白等 10 秒才超时
      if (!wrote) throw new Error('BLE 写入连续失败，已放弃');

      const a = await awaitMsg(['ack', 'done', 'err'], 10000);
      if (a.t === 'err') throw new Error(a.m);
      // 以设备回的权威计数为准，避免"少存了却算作成功"
      const got = (typeof a.g === 'number') ? a.g : off + slice.length;
      if (got <= off && a.t !== 'done') {
        log('设备计数没有前进，中止', 'err');
        throw new Error('设备计数停滞');
      }
      off = got;
      if (a.t === 'done') { finished = true; break; }
      if (off % 1024 < chunk) log(`  ${off}/${bytes.length} 字节`, 'sys');
    }

    if (!finished) {
      p = awaitMsg(['done', 'err'], 8000);
      await sendCmd({ t: 'end' });
      r = await p;
      if (r.t === 'err') {
        // 设备回"没有正在进行的上传"说明它其实早已收满（done 被别的
        // 等待槽吃掉了），这时报失败是错的。
        if (String(r.m || '').indexOf('没有正在进行') >= 0) {
          log('设备已收满（end 多余），按成功处理', 'sys');
        } else {
          throw new Error(r.m);
        }
      }
    }

    if (finished && off !== bytes.length) {
      log(`警告：设备计数 ${off} 与源码长度 ${bytes.length} 不一致`, 'err');
    }

    log(`推送完成: ${name}`, 'sys');
    toast(`已推送 ${name}`);
    await refreshApps();
    if (alsoRun) await runApp(name);
  } catch (e) {
    log('推送失败: ' + e.message, 'err');
    toast('推送失败: ' + e.message, 3500);
    try { await sendCmd({ t: 'abort' }); } catch (_) { /* 忽略 */ }
  } finally {
    pushing = false;
    setUi(connected);
    if (connected) startHeartbeat();
  }
}

/* ------------------------------------------------------------------ 模板 */
const TEMPLATES = {
  '最小示例': `"""最小可运行的小程序。"""
TITLE = "Hello"

def setup(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text_center("HELLO", 140, 0x07FF, 0x0000, 2)

def on_key(ctx, key):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text_center(key.upper(), 140, 0xFFE0, 0x0000, 3)
`,
  '按键计数器': `"""演示按键交互。"""
TITLE = "Counter"

def setup(ctx):
    ctx.n = ctx.kv_get("n", 0)
    draw(ctx)

def draw(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text("COUNT", 8, 8, 0x07FF, 0x0000)
    ctx.lcd.text_center(str(ctx.n), 120, 0xFFFF, 0x0000, 4)

def on_key(ctx, key):
    if key == "up":
        ctx.n += 1
    elif key == "down":
        ctx.n -= 1
    else:
        ctx.n = 0
    ctx.kv_set("n", ctx.n)
    draw(ctx)
`,
  '滚动色带': `"""演示 loop() 动画，以及什么时候该退出。"""
import time

TITLE = "Rainbow"

COLORS = (0xF800, 0xFD20, 0xFFE0, 0x07E0, 0x07FF, 0x001F, 0xF81F)

def setup(ctx):
    ctx.i = 0

def loop(ctx):
    if ctx.frame % 3:
        return
    ctx.i = (ctx.i + 1) % len(COLORS)
    ctx.lcd.fill(0x0000)
    for k in range(7):
        ctx.lcd.fill_rect(0, 40 + k * 30, ctx.w, 26,
                          COLORS[(ctx.i + k) % len(COLORS)])
    ctx.lcd.text("up/down speed", 8, ctx.h - 18, 0x8410, 0x0000)
`,
  '时钟': `"""数字时钟，需要先在 App 里点「给设备对时」。"""
import time

TITLE = "Clock"

def setup(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text("TIME", 8, 8, 0x07FF, 0x0000)
    ctx.last = None

def loop(ctx):
    t = time.localtime()
    if t[0] < 2020:
        s = time.ticks_ms() // 1000
        txt = "%02d:%02d:%02d" % (s // 3600, (s // 60) % 60, s % 60)
    else:
        txt = "%02d:%02d:%02d" % (t[3], t[4], t[5])
    if txt != ctx.last:
        ctx.last = txt
        ctx.lcd.text_center(txt, 130, 0xFFFF, 0x0000, 3)
`,
  '骰子': `"""按键掷骰子。"""
import random
import time

TITLE = "Dice"

LAYOUT = {
  1: ((1,1),), 2: ((0,0),(2,2)), 3: ((0,0),(1,1),(2,2)),
  4: ((0,0),(2,0),(0,2),(2,2)), 5: ((0,0),(2,0),(1,1),(0,2),(2,2)),
  6: ((0,0),(2,0),(0,1),(2,1),(0,2),(2,2)),
}

def draw(ctx):
    l = ctx.lcd
    l.fill(0x0000)
    side, x0, y0 = 150, 45, 70
    l.fill_rect(x0, y0, side, side, 0xFFFF)
    step = side // 4
    r = step // 2 - 4
    for gx, gy in LAYOUT[ctx.v]:
        cx, cy = x0 + step * (gx + 1), y0 + step * (gy + 1)
        l.fill_rect(cx - r, cy - r, r * 2, r * 2, 0x0000)
    l.text_center(str(ctx.v), y0 + side + 14, 0xFFE0, 0x0000, 3)

def setup(ctx):
    random.seed(time.ticks_ms())
    ctx.v = random.randint(1, 6)
    draw(ctx)

def on_key(ctx, key):
    if key == "ok":
        ctx.v = random.randint(1, 6)
        draw(ctx)
`,
  '设备信息': `"""读内存 / 电量 / 运行时长。"""
import gc
import time

TITLE = "System"

def setup(ctx):
    ctx.lcd.fill(0x0000)
    ctx.lcd.text("PASSPORT OS", 8, 6, 0x07FF, 0x0000)

def loop(ctx):
    if ctx.frame % 20:
        return
    l = ctx.lcd
    rows = (
        ("uptime", "%ds" % (time.ticks_ms() // 1000)),
        ("heap", "%dK" % (gc.mem_free() // 1024)),
        ("battery", ctx.battery.label()),
        ("ble", "on" if ctx.shell.link.connected else "off"),
    )
    y = 40
    for k, v in rows:
        l.fill_rect(0, y, ctx.w, 22, 0x0000)
        l.text(k, 8, y + 6, 0x8410, 0x0000)
        l.text(v, ctx.w - 8 * len(v) - 8, y + 6, 0xFFFF, 0x0000)
        y += 24
`,
  '木鱼': `"""木鱼 —— 敲一下，功德 +1。

按键：
    OK    敲一下
    UP    切换 累计 / 今日
    DOWN  静音开关
    （长按 OK 退出回主菜单，这是系统行为）

数据用 ctx.kv_* 掉电保持：
    total  累计次数 —— 永不重置
    today  今日次数 —— 跨天自动归零（需要先在 App 里对过时）
    day    上次记账的日期
    view   上次看的是累计还是今日
    muted  静音开关

设计说明：
  * 屏幕只有 8x8 ASCII 点阵字体，**显示不了中文**，所以界面文案用英文。
  * 木鱼本体是一个"逐行填充 + 竖向渐变"的圆：一共 2r+1 次 fill_rect，
    一次性画好不再重画。敲击反馈只重画计数器那一小块和三条冲击线，
    保证按下去是"跟手"的（每次敲击只产生个位数次 SPI 事务）。
  * 音效只用 ctx.audio.tone()：两声短促的音，模拟"笃"的一下。
    真木鱼的木质瞬态还原不了，但节奏感在。
  * 每次敲击都写盘太费 Flash，所以每 FLUSH_EVERY 次落一次盘；
    退出时 teardown() 会强制写一次，断电最多丢 4 下。
"""

import math
import time

TITLE = "Mu Yu"

# ---------------------------------------------------------------- 配色 RGB565
BG = 0x0000
BAR = 0x18E3          # 顶栏 / 底栏
SEP = 0x39E7          # 分隔线
SLIT = 0x18C3         # 鱼口
GOLD = 0xFE60         # 计数（常态）
GOLD_HI = 0xFFF0      # 计数（敲击瞬间）
ACCENT = 0x07FF
DIM = 0x8410
WARN = 0xF800


def _rgb(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


# 木色：上方受光、下方背光，8 级渐变
_TOP = (208, 158, 104)
_BOT = (72, 46, 28)
WOOD = tuple(_rgb(_TOP[0] + (_BOT[0] - _TOP[0]) * k // 7,
                  _TOP[1] + (_BOT[1] - _TOP[1]) * k // 7,
                  _TOP[2] + (_BOT[2] - _TOP[2]) * k // 7) for k in range(8))
HILITE = _rgb(232, 198, 156)

# ---------------------------------------------------------------- 版面
TITLE_H = 21
VIEW_Y = 28
FISH_CX = 120
FISH_CY = 146
FISH_R = 54
COUNT_Y = 212
COUNT_H = 44
PLUS_X = FISH_CX + FISH_R - 6
PLUS_Y = FISH_CY - FISH_R + 6
FOOT_Y = 298

FLASH_MS = 90          # 敲击高亮持续
FLUSH_EVERY = 5        # 每敲几下写一次盘


# ================================================================ 绘图工具
def _isqrt(n):
    """MicroPython 的 math 不保证有 isqrt，这里手写一个稳妥的。

    用 math.sqrt 起步再微调，避免浮点边界把结果算少 1。
    """
    if n <= 0:
        return 0
    x = int(math.sqrt(n))
    while x > 0 and x * x > n:
        x -= 1
    while (x + 1) * (x + 1) <= n:
        x += 1
    return x


def _fill_circle(lcd, cx, cy, r, shades=None, color=None):
    """逐行填充圆。shades 非空时按 y 做竖向渐变（看着像球）。"""
    n = len(shades) if shades else 1
    for dy in range(-r, r + 1):
        dx = _isqrt(r * r - dy * dy)
        c = shades[(dy + r) * n // (2 * r + 1)] if shades else color
        lcd.fill_rect(cx - dx, cy + dy, 2 * dx + 1, 1, c)


# ================================================================ 界面
def _draw_frame(ctx):
    lcd = ctx.lcd
    lcd.fill(BG)
    lcd.fill_rect(0, 0, ctx.w, TITLE_H, BAR)
    lcd.text2x("MU YU", 8, 3, ACCENT, BAR)
    lcd.fill_rect(0, TITLE_H, ctx.w, 1, SEP)

    lcd.fill_rect(0, ctx.h - 22, ctx.w, 22, BAR)
    lcd.text("OK hit  UP view", 6, ctx.h - 16, DIM, BAR)
    lcd.text("DN mute", ctx.w - 7 * 8 - 6, ctx.h - 16, DIM, BAR)


def _draw_view(ctx):
    """左上角那一行：当前看的是累计还是今日 + 静音状态 + 电量。"""
    lcd = ctx.lcd
    lcd.fill_rect(0, VIEW_Y - 2, ctx.w, 14, BG)

    label = "TOTAL" if ctx.view == "total" else "TODAY"
    lcd.text(label, 8, VIEW_Y, ACCENT if ctx.view != "total" else GOLD, BG)

    if ctx.muted:
        lcd.text("MUTED", 88, VIEW_Y, WARN, BG)

    bat = ctx.battery.label()
    lcd.text(bat, ctx.w - 8 * len(bat) - 8, VIEW_Y, DIM, BG)


def _draw_fish(ctx):
    """一次性画好木鱼本体，之后不再重画。"""
    lcd = ctx.lcd
    _fill_circle(lcd, FISH_CX, FISH_CY, FISH_R, WOOD)
    # 鱼口：一条横向的暗缝
    sw = FISH_R * 3 // 2
    sh = max(5, FISH_R // 7)
    lcd.fill_rect(FISH_CX - sw // 2, FISH_CY + FISH_R // 5, sw, sh, SLIT)
    # 左上高光
    _fill_circle(lcd, FISH_CX - FISH_R // 3, FISH_CY - FISH_R // 3,
                 FISH_R // 5, None, HILITE)


def _draw_counter(ctx, hot):
    lcd = ctx.lcd
    n = ctx.today if ctx.view == "today" else ctx.total
    s = "%d" % n
    # 位数多了自动缩字号，免得超出 240 宽
    scale = 5 if len(s) <= 4 else (4 if len(s) <= 6 else 3)
    h = 8 * scale
    y = COUNT_Y + (COUNT_H - h) // 2
    lcd.fill_rect(0, COUNT_Y, ctx.w, COUNT_H, BG)
    lcd.text_center(s, y, GOLD_HI if hot else GOLD, BG, scale)


def _marks(ctx, color):
    """敲击的三条冲击线（在木鱼左侧）。3 次 fill_rect，几乎不耗时。"""
    lcd = ctx.lcd
    x = FISH_CX - FISH_R - 14
    lcd.fill_rect(x, FISH_CY - 26, 10, 3, color)
    lcd.fill_rect(x + 4, FISH_CY - 10, 8, 3, color)
    lcd.fill_rect(x + 8, FISH_CY + 6, 6, 3, color)


# ================================================================ 数据
def _today_str():
    """RTC 没对过时返回 None（那时"今日"退化为本次开机累计）。"""
    t = time.localtime()
    if t[0] < 2020:
        return None
    return "%04d-%02d-%02d" % (t[0], t[1], t[2])


def _roll_day(ctx):
    """跨天就把"今日"清零。返回 True 表示确实翻了篇。"""
    d = _today_str()
    if d is None or d == ctx.day:
        return False
    ctx.day = d
    ctx.today = 0
    ctx.kv_set("day", d)
    ctx.kv_set("today", 0)
    return True


def _knock(ctx):
    """敲击音：两声短音，音高随次数轻微浮动，听起来不那么机械。"""
    a = ctx.audio
    if ctx.muted or a is None or not a.ok:
        return
    try:
        base = 1150 + (ctx.total % 5) * 18
        a.tone(base, 34)
        a.tone(base * 2 // 3, 26)
    except Exception:
        pass


def _save(ctx):
    ctx.kv_set("total", ctx.total)
    ctx.kv_set("today", ctx.today)
    ctx.kv_flush()


# ================================================================ 生命周期
def setup(ctx):
    ctx.muted = bool(ctx.kv_get("muted", 0))
    ctx.view = ctx.kv_get("view", "total")
    if ctx.view not in ("total", "today"):
        ctx.view = "total"
    ctx.total = int(ctx.kv_get("total", 0))
    ctx.today = int(ctx.kv_get("today", 0))
    ctx.day = ctx.kv_get("day", "")
    _roll_day(ctx)

    ctx.hot = False
    ctx.hot_until = 0
    ctx.unsaved = 0

    if ctx.audio and ctx.audio.ok:
        try:
            ctx.audio.set_volume(70)
        except Exception:
            pass

    _draw_frame(ctx)
    _draw_fish(ctx)
    _draw_view(ctx)
    _draw_counter(ctx, False)


def on_key(ctx, key):
    if key == "ok":
        _roll_day(ctx)
        ctx.total += 1
        ctx.today += 1
        ctx.hot_until = time.ticks_add(time.ticks_ms(), FLASH_MS)
        ctx.unsaved += 1

        # 反馈：数字变亮 + "+1" + 三条冲击线（都是小面积重画）
        _draw_counter(ctx, True)
        if not ctx.hot:
            ctx.hot = True
            ctx.lcd.text("+1", PLUS_X, PLUS_Y, GOLD_HI, BG)
            _marks(ctx, GOLD_HI)

        _knock(ctx)

        if ctx.unsaved >= FLUSH_EVERY:
            _save(ctx)
            ctx.unsaved = 0

    elif key == "up":
        ctx.view = "today" if ctx.view == "total" else "total"
        ctx.kv_set("view", ctx.view)
        _draw_view(ctx)
        _draw_counter(ctx, False)

    elif key == "down":
        ctx.muted = not ctx.muted
        ctx.kv_set("muted", 1 if ctx.muted else 0)
        _draw_view(ctx)
        if not ctx.muted:
            _knock(ctx)          # 取消静音时立刻给个听觉确认


def loop(ctx):
    # 高亮到期就收回
    if ctx.hot and time.ticks_diff(time.ticks_ms(), ctx.hot_until) >= 0:
        ctx.hot = False
        ctx.lcd.fill_rect(PLUS_X, PLUS_Y, 16, 8, BG)
        _marks(ctx, BG)
        _draw_counter(ctx, False)

    # 每 ~0.6 秒查一次是否跨天
    if ctx.frame % 30 == 0:
        if _roll_day(ctx):
            _draw_view(ctx)
            _draw_counter(ctx, False)


def teardown(ctx):
    _save(ctx)
`,
};

function buildTemplates() {
  const box = $('templates');
  box.innerHTML = '';
  for (const [name, code] of Object.entries(TEMPLATES)) {
    const b = document.createElement('button');
    b.textContent = name;
    b.onclick = () => { $('editor').value = code; saveLocal(); toast('已载入：' + name); };
    box.appendChild(b);
  }
}

/* ------------------------------------------------------------ 本机持久化 */
const LS_KEY = 'passport_editor_v1';
function saveLocal() { try { localStorage.setItem(LS_KEY, $('editor').value); } catch (_) {} }
function loadLocal() {
  try { return localStorage.getItem(LS_KEY) || ''; } catch (_) { return ''; }
}

/* ------------------------------------------------------------------ 绑定 */
function bind() {
  $('btnConnect').onclick = async () => {
    if (connected) {
      // 用户主动断开 → 关掉自动重连，否则切走再切回来会"悄悄又连上"。
      waking = false;
      // 先说再见，让【设备端】主动断开。Windows 在客户端 disconnect() 之后
      // 会抓着 BLE 链路，设备侧一分钟都察觉不到、期间不广播，下一次就连不上。
      try { await sendCmd({ t: 'bye' }); } catch (_) {}
      await new Promise(r => setTimeout(r, 350));
      try { device.gatt.disconnect(); } catch (_) {}
      teardownConnection('已断开', true);
    } else await connect(false);
  };
  $('btnPick').onclick = async () => {
    waking = false;
    if (connected) {
      try { await sendCmd({ t: 'bye' }); } catch (_) {}
      await new Promise(r => setTimeout(r, 350));
      try { device.gatt.disconnect(); } catch (_) {}
      teardownConnection('已断开', true);
    }
    log('强制重新打开设备选择器 …');
    await connect(true);
  };
  $('btnRefresh').onclick = () => refreshApps().catch(e => toast(e.message));
  $('btnSyncTime').onclick = () => syncTime().catch(e => toast(e.message));
  $('btnStop').onclick = () => stopApp().catch(e => toast(e.message));

  $('btnPush').onclick = () => pushApp(
    $('pushName').value.trim(), $('pushTitle').value.trim(), currentSource(), false);
  $('btnPushRun').onclick = () => pushApp(
    $('pushName').value.trim(), $('pushTitle').value.trim(), currentSource(), true);

  $('pushSource').onchange = () => {
    if ($('pushSource').value === 'file') $('pushFile').click();
    updateCodeLen();
  };
  $('pushFile').onchange = async () => {
    const f = $('pushFile').files[0];
    if (!f) return;
    const text = await f.text();
    $('editor').value = text;
    saveLocal();
    $('pushName').value = (f.name.replace(/\.py$/i, '') || 'app')
      .toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 16) || 'app';
    log(`已载入文件 ${f.name}（${text.length} 字符）`, 'sys');
  };

  $('editor').addEventListener('input', () => { saveLocal(); updateCodeLen(); });
  $('btnSaveLocal').onclick = () => { saveLocal(); toast('已存到本机'); };
  $('btnLoadLocal').onclick = () => {
    const v = loadLocal();
    if (!v) { toast('本机没有存过代码'); return; }
    $('editor').value = v; updateCodeLen(); toast('已读回');
  };
  $('btnFromTpl').onclick = () => {
    const names = Object.keys(TEMPLATES);
    const pick = prompt('输入要载入的模板名：\n' + names.join(' / '), names[0]);
    if (pick && TEMPLATES[pick]) { $('editor').value = TEMPLATES[pick]; saveLocal(); updateCodeLen(); toast('已载入'); }
  };
  $('btnDownload').onclick = () => {
    const blob = new Blob([$('editor').value], { type: 'text/x-python' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = ($('pushName').value.trim() || 'app') + '.py';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $('btnClearLog').onclick = () => { $('logs').innerHTML = ''; };

  document.querySelectorAll('.tab').forEach(tab => {
    tab.onclick = () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.add('hidden'));
      tab.classList.add('active');
      $('tab-' + tab.dataset.tab).classList.remove('hidden');
      if (tab.dataset.tab === 'editor') updateCodeLen();
    };
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden && waking) tryReconnect();
  });

  // 关标签页/刷新时尽量说声再见（尽力而为，发不出去还有设备端看门狗兜底）
  window.addEventListener('beforeunload', () => {
    if (connected && cmdChar) {
      try { cmdChar.writeValue(new TextEncoder().encode('{"t":"bye"}')); } catch (_) {}
    }
  });
}

/* 推送内容的唯一来源就是编辑器。
 * 选文件时 onchange 已经把内容灌进 editor 了，所以这里不需要分支
 * （以前那个 if 块里只有注释，等于死代码）。 */
function currentSource() {
  return $('editor').value;
}

function updateCodeLen() {
  const n = new TextEncoder().encode($('editor').value).length;
  $('codeLen').textContent = n;
  $('editorInfo').textContent =
    `${n} 字节 / 上限 ${MAX_APP_BYTES} 字节　` +
    (n > MAX_APP_BYTES ? '⚠ 超限' : '✓ 可推送');
}

/* ------------------------------------------------------------------ 起步 */
(function init() {
  bind();
  buildTemplates();
  const saved = loadLocal();
  $('editor').value = saved || TEMPLATES['最小示例'];
  updateCodeLen();
  setUi(false);

  if (!navigator.bluetooth) {
    log('⚠ 这个浏览器没有 Web Bluetooth。请用 Chrome / Edge（http://localhost 或 https）。', 'err');
    toast('浏览器不支持 Web Bluetooth', 5000);
  } else {
    log('就绪。点右上角「连接」选择 PassportOS 设备。');
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch(() => {});
  }
})();
