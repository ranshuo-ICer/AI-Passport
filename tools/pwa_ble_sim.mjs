#!/usr/bin/env node
/* 在 Node 里真跑一遍 pwa/app.js 的连接逻辑（假 Bluetooth 栈）。
 *
 * 为什么需要它：手机上的 Web Bluetooth 没法自动化，而"自动重连"这段逻辑上一轮
 * 改完**一次都没执行过** —— 只做了语法检查。这个脚本用 vm 把 app.js 加载起来，
 * 喂一个假的 BluetoothDevice/GATT，然后驱动几个关键场景：
 *
 *   A. 正常连接 + 刷新列表        —— 命令收发与列表渲染
 *   B. 刷新时链路已悄悄断掉       —— 应当自动重连（而不是弹"请重新连接"）
 *   C. 上传过程中链路断掉         —— **绝对不许**重连（会把 hello 写进 app.py）
 *
 *     node tools/pwa_ble_sim.mjs
 */

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = join(HERE, '..', 'pwa', 'app.js');

const UUID_SERVICE = '7a5c0001-0000-4000-8000-70617373706f';
const UUID_CMD = '7a5c0002-0000-4000-8000-70617373706f';
const UUID_RSP = '7a5c0003-0000-4000-8000-70617373706f';

let PASS = 0, FAIL = 0;
function check(name, cond, extra = '') {
  if (cond) { PASS++; console.log('  ok   ' + name); }
  else { FAIL++; console.log('  FAIL ' + name + (extra ? '   ' + extra : '')); }
}

/* ----------------------------------------------------------- 假 DOM */
function mockEl() {
  const el = {
    value: '', textContent: '', disabled: false, checked: true,
    scrollTop: 0, scrollHeight: 0, childElementCount: 0, dataset: {}, style: {},
    files: [], href: '', download: '',
    children: [],
    classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
    appendChild(c) { this.children.push(c); },
    removeChild() {}, addEventListener() {},
    removeEventListener() {}, click() {}, focus() {},
    querySelector: () => mockEl(), querySelectorAll: () => [],
  };
  // innerHTML = '' 要真的清掉子节点，否则终端"清屏"测不出来
  let html = '';
  Object.defineProperty(el, 'innerHTML', {
    get: () => html,
    set: (v) => { html = v; if (v === '') el.children.length = 0; },
  });
  return el;
}

/* --------------------------------------------------- 假 Bluetooth 设备 */
function makeWorld(opts = {}) {
  const world = {
    writes: [],           // 设备收到的命令文本
    lens: [],             // 每次写入的**字节数**（JSON 编码后）
    connects: 0,          // gatt.connect() 次数
    notifyHandler: null,
    linkUp: true,         // false 模拟"链路已悄悄断掉"
    failWrite: false,     // 写入时抛 NetworkError
    failConnect: false,
    autoRespond: true,
    pyBuf: '',            // 终端源码分片累积
    pySrc: null,          // 最终真正执行的源码
  };

  function deviceReply(text) {
    // 极简假设备：认几个命令就回
    let m;
    try { m = JSON.parse(text); } catch { return; }
    const send = (obj) => {
      const raw = JSON.stringify(obj);
      const h = world.notifyHandler;
      if (h) h({ target: { value: new TextEncoder().encode(raw) } });
    };
    if (m.t === 'hello') send({ t: 'hi', os: 'PassportOS', apps: 3, free: 123456, mtu: 247 });
    else if (m.t === 'ls') send({ t: 'ls', apps: [
      { n: 'clock', title: 'Clock', s: 1529 },
      { n: 'dice', title: 'Dice', s: 1567 },
      { n: 'beats', title: 'Beats', s: 15253 },
    ] });
    else if (m.t === 'ping') send({ t: 'pong' });
    else if (m.t === 'py') {
      if (m.more) {
        world.pyBuf += m.c;
        send({ t: 'py', ok: true, out: '', ms: 0, buffered: world.pyBuf.length });
      } else {
        const src = world.pyBuf + m.c;
        world.pyBuf = '';
        world.pySrc = src;
        send({ t: 'py', ok: true, out: 'device got ' + src.length + ' chars\n', ms: 3 });
      }
    }
    else if (m.t === 'pyreset') { world.pyBuf = ''; send({ t: 'pyreset', ok: true }); }
    else if (m.t === 'stop') send({ t: 'stop', ok: true });
  }

  const cmdChar = {
    uuid: UUID_CMD,
    async writeValue(buf) {
      const text = new TextDecoder().decode(
        buf instanceof Uint8Array ? buf : new Uint8Array(buf));
      if (world.failWrite || !world.linkUp) {
        const e = new Error('GATT Server is disconnected. (4)');
        e.name = 'NetworkError';
        throw e;
      }
      world.writes.push(text);
      world.lens.push(new TextEncoder().encode(text).length);
      if (world.autoRespond) queueMicrotask(() => deviceReply(text));
    },
  };
  const rspChar = {
    uuid: UUID_RSP,
    async startNotifications() {},
    addEventListener(_t, fn) { world.notifyHandler = fn; },
  };
  const service = {
    async getCharacteristic(u) { return u === UUID_CMD ? cmdChar : rspChar; },
  };
  const server = {
    async getPrimaryService() { return service; },
    get connected() { return world.linkUp; },
  };
  const device = {
    id: 'sim', name: 'PassportOS',
    __handlers: {},
    addEventListener(t, fn) { device.__handlers[t] = fn; },
    gatt: {
      get connected() { return world.linkUp; },
      async connect() {
        world.connects++;
        if (world.failConnect) throw new Error('connect failed');
        world.linkUp = true;
        return server;
      },
      disconnect() { world.linkUp = false; },
    },
    fireDisconnected() { const h = device.__handlers.gattserverdisconnected; if (h) h(); },
  };

  world.device = device;
  return world;
}

/* ------------------------------------------------------ 加载 app.js */
function loadApp(world) {
  const els = {};
  const sandbox = {
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    queueMicrotask, Promise, TextEncoder, TextDecoder, Date, JSON, Math,
    URL: { createObjectURL: () => 'blob:x', revokeObjectURL() {} },
    Blob: class {},
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    prompt: () => null, confirm: () => true,
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    location: { href: 'http://localhost/', reload() {} },
    __els: els,          // 元素按 id 缓存：真 DOM 也是同一个对象
    document: {
      hidden: false,
      getElementById: (id) => (els[id] || (els[id] = mockEl())),
      createElement: () => mockEl(),
      querySelectorAll: () => [],
      addEventListener() {},
    },
    navigator: {
      bluetooth: {
        async getDevices() { return [world.device]; },
        async requestDevice() { return world.device; },
      },
      serviceWorker: { register: async () => ({}) },
    },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;

  const src = readFileSync(SRC, 'utf8') + `
;globalThis.__t = {
  connect, refreshApps, pushApp, sendCmd, autoReconnect, isLinkError,
  adviseConnectFailure, splitConsoleSrc, termSend, termReset,
  termText: () => (__els['termOut'].children || []).map(c => c.textContent).join(''),
  termClear: () => { __els['termOut'].innerHTML = ''; },
  get connected(){ return connected; },
  get reconnecting(){ return reconnecting; },
  get pushing(){ return pushing; },
  get device(){ return device; },
  setDevice(d){ device = d; },
  logs: () => logText,
  resetLog(){ logText = ""; },
};`;
  const ctx = vm.createContext(sandbox);
  // 收集日志文本，便于断言
  vm.runInContext('var logText = "";', ctx);
  vm.runInContext(src.replace(
    'function log(msg, kind = \'sys\') {',
    'function log(msg, kind = \'sys\') { try{ logText += msg + "\\n"; }catch(_){}'
  ), ctx);
  return ctx.__t;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ------------------------------------------------------------- 场景 */
async function main() {
  console.log('='.repeat(66));
  console.log('在 Node 里真跑 pwa/app.js（假 Bluetooth 栈）');
  console.log('='.repeat(66));

  // ---------------------------------------------------- A 正常流程
  console.log('\n[A] 正常连接 + 刷新列表');
  {
    const w = makeWorld();
    const app = loadApp(w);
    await app.connect(true);
    check('连接成功', app.connected === true);
    check('握手 hello 发出去了', w.writes.includes('{"t":"hello"}'));
    await app.refreshApps();
    check('刷新发出了 ls', w.writes.includes('{"t":"ls"}'));
    check('刷新后仍然连接', app.connected === true);
  }

  // ------------------------------------------- B 刷新时链路已断
  console.log('\n[B] 刷新时链路已悄悄断掉（Android 不保证送达断开事件的那种）');
  {
    const w = makeWorld();
    const app = loadApp(w);
    await app.connect(true);
    check('先确认已连接', app.connected === true);

    const before = w.connects;
    w.linkUp = false;                 // 链路静默死掉：事件不会来
    app.setDevice(w.device);

    await app.refreshApps().catch(() => {});   // 这一步应当触发自动重连
    await sleep(400);
    check('确实尝试了重连', w.connects > before, `connects ${before} -> ${w.connects}`);
    check('重连之后又回到已连接', app.connected === true,
          'connected=' + app.connected);
    check('日志里说明了是链路断开而不是甩英文报错',
          /链路已断开|自动重连/.test(app.logs()), app.logs().split('\n').slice(-3).join(' | '));
  }

  // ---------------------------------------------------- C 上传中不许重连
  console.log('\n[C] 上传过程中链路断掉 —— 绝对不许重连');
  {
    const w = makeWorld();
    const app = loadApp(w);
    await app.connect(true);
    const before = w.connects;

    // 进上传态：put 之后设备开始收数据
    const p = app.pushApp('simapp', 'Sim', 'x = 1\n', false).catch(() => {});
    await sleep(50);
    check('已经进入 pushing 状态', app.pushing === true, 'pushing=' + app.pushing);

    w.linkUp = false;                 // 上传中断链
    await sleep(300);
    check('上传中断链**没有**触发重连',
          w.connects === before, 'connects %d -> %d' % (before, w.connects));
    await p;
  }

  // ------------------------------------------- D 心跳自愈（什么都不点）
  console.log('\n[D] 链路静默死掉，用户什么都不做 —— 心跳应当自己接回来');
  {
    const w = makeWorld();
    const app = loadApp(w);
    await app.connect(true);
    const before = w.connects;
    w.linkUp = false;                 // 锁屏期间设备看门狗把链路踢了
    // 心跳 4 秒一次：等一个周期多一点
    await sleep(4600);
    check('没有任何用户操作，心跳也把链路接回来了',
          app.connected === true && w.connects > before,
          `connected=${app.connected} connects ${before} -> ${w.connects}`);
    check('接回来时又发了一次 hello 完成握手',
          w.writes.filter(t => t === '{"t":"hello"}').length >= 2);
  }

  // ------------------------------------------- E 三种失败要给出不同建议
  console.log('\n[E] 连接失败的分类诊断（混成一句「连接失败」会让排查原地打转）');
  {
    const w = makeWorld();
    const app = loadApp(w);

    const caseOf = (err) => { app.resetLog(); app.adviseConnectFailure(err); return app.logs(); };

    const cancelled = Object.assign(new Error('User cancelled the requestDevice() chooser.'),
                                    { name: 'NotFoundError' });
    const none = Object.assign(new Error('No Bluetooth devices were found.'), { name: 'NotFoundError' });
    const stackBad = Object.assign(new Error('GATT Server is disconnected. Cannot retrieve services.'),
                                   { name: 'NetworkError', stage: 'getPrimaryService' });
    const noReply = Object.assign(new Error('等待设备响应超时'), { stage: 'hello-handshake' });

    const t1 = caseOf(cancelled), t2 = caseOf(none), t3 = caseOf(stackBad), t4 = caseOf(noReply);

    check('用户取消 → 明确说这不是故障，且不刷排查清单',
          /取消/.test(t1) && !/排查建议/.test(t1));
    check('真的没设备 → 给出排查清单', /排查建议/.test(t2));
    check('拿不到服务 → 指向【系统蓝牙栈】而不是设备',
          /系统蓝牙栈/.test(t3) && !/断电重开/.test(t3));
    check('连上了但没回话 → 说明链路是通的，并提示断电重开',
          /链路是通的/.test(t4) && /断电重开/.test(t4));
    check('四种情况的建议互不相同',
          new Set([t1, t2, t3, t4]).size === 4);
  }

  // ------------------------------------------- F 终端：分片发送长源码
  console.log('\n[F] 终端：一次 GATT 写硬上限 512 字节，长源码必须分片');
  {
    const w = makeWorld();
    const app = loadApp(w);
    await app.connect(true);

    // 1) 切片器本身：每片 JSON 都不能超过 512（实测硬上限），且可无损还原
    const src = 'x = 1\n' + 'print("中文也要能切", x)\n'.repeat(40) + 'y = 2\n';
    const parts = app.splitConsoleSrc(src);
    const enc = new TextEncoder();
    const maxJson = Math.max(...parts.map(p =>
      enc.encode(JSON.stringify({ t: 'py', c: p, more: true })).length));
    check('切片器：片数 > 1', parts.length > 1, `${parts.length} 片`);
    check('切片器：每片 JSON <= 512 字节（真机硬上限）',
          maxJson <= 512, `最大 ${maxJson} 字节`);
    check('切片器：拼接后与原文完全一致', parts.join('') === src);
    check('切片器：非 ASCII 也按字节量（不会超）', maxJson <= 400,
          `最大 ${maxJson} 字节`);

    // 2) 真的发一遍：设备必须收到完整源码
    w.lens.length = 0;
    w.pySrc = null;
    await app.termSend(src);
    check('★ 设备最终拿到的源码与原文一致',
          w.pySrc === src, `设备收到 ${w.pySrc ? w.pySrc.length : 0} 字 / 原文 ${src.length} 字`);
    check('★ 除最后一片外都带 more:true', (() => {
      const pys = w.writes.filter(t => t.startsWith('{"t":"py"'));
      if (pys.length < 2) return false;
      return pys.slice(0, -1).every(t => t.includes('"more":true')) &&
             !pys[pys.length - 1].includes('"more"');
    })());
    check('★ 每一次写入都在 512 字节以内',
          Math.max(...w.lens) <= 512, `最大 ${Math.max(...w.lens)} 字节`);
    check('终端里显示了设备的输出',
          w.pySrc && app.termText().includes('device got ' + src.length),
          app.termText().slice(-120));

    // 3) 清屏 / 清变量
    app.termClear();
    check('清屏后终端输出为空', app.termText() === '', app.termText().slice(0, 40));
    await app.termReset();
    check('清空变量会清掉没发完的源码缓冲', w.pyBuf === '');
  }

  console.log('\n' + '='.repeat(66));
  console.log('通过 %d 项，失败 %d 项', PASS, FAIL);
  console.log('='.repeat(66));
  process.exit(FAIL ? 1 : 0);
}

main();
