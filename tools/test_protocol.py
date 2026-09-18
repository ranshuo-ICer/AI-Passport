#!/usr/bin/env python3
"""在电脑上验证 PassportOS 的 BLE 协议状态机（不需要硬件）。

用桩模块顶替 `bluetooth` / `machine`，把 upload / 命令分发 / 分片组装
这几条最容易出错的路径跑一遍。

    python tools/test_protocol.py
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "os"))

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (("   " + extra) if extra and not cond else ""))


# --------------------------------------------------------------------- 桩
class FakeBLE:
    def __init__(self):
        self._irq = None
        self.sent = []
        self._last = b""
        self.adv_calls = 0

    def active(self, v=True):
        pass

    def config(self, **kw):
        pass

    def irq(self, fn):
        self._irq = fn

    def gatts_register_services(self, services):
        self.services = services
        return ((10, 11),)

    def gap_advertise(self, *a, **kw):
        self.adv_calls += 1

    def gatts_read(self, handle):
        return self._last

    def gatts_notify(self, conn, handle, data):
        self.sent.append(bytes(data))

    # 测试辅助：模拟手机写入 CMD 特征
    def phone_write(self, payload, applink, handle=10):
        self._last = payload
        self._irq(3, (1, handle))          # _IRQ_GATTS_WRITE


class FakeHost:
    def __init__(self):
        self.launched = None
        self.stopped = False
        self.running = None

    def launch(self, name):
        self.launched = name
        self.running = name
        return True

    def stop_app(self):
        self.stopped = True
        self.running = None

    def current_app(self):
        return self.running


def install_stubs():
    # MicroPython 的 time 模块有 ticks_ms / ticks_diff，CPython 没有。
    # 设备端代码用了它们（连接空闲看门狗），所以这里补上桩。
    import time as _time
    if not hasattr(_time, "ticks_ms"):
        _time.ticks_ms = lambda: int(_time.monotonic() * 1000) & 0x3FFFFFFF
        _time.ticks_diff = lambda a, b: a - b
        _time.ticks_add = lambda a, b: a + b
        _time.sleep_ms = lambda ms: _time.sleep(ms / 1000.0)

    bt = types.ModuleType("bluetooth")
    bt.BLE = FakeBLE
    bt.UUID = lambda s: s
    bt.FLAG_NOTIFY = 0x10
    bt.FLAG_WRITE = 0x08
    bt.FLAG_WRITE_NO_RESPONSE = 0x04
    sys.modules["bluetooth"] = bt

    mach = types.ModuleType("machine")

    class RTC:
        last = None

        def datetime(self, tup):
            RTC.last = tup

    mach.RTC = RTC
    sys.modules["machine"] = mach
    return bt, mach


def reassemble(frames):
    """把 '~'/'!' 分片还原成完整消息列表。"""
    out, buf = [], ""
    for fr in frames:
        text = fr.decode()
        head = text[0]
        if head == "~":
            buf += text[1:]
        elif head == "!":
            buf += text[1:]
            out.append(buf)
            buf = ""
        else:
            out.append(text)
            buf = ""
    assert buf == "", "分片没有正确结束: %r" % buf
    return out


def main():
    bt, mach = install_stubs()
    tmp = tempfile.mkdtemp(prefix="passport-test-")

    from passport import config as C
    from passport import apps as apps_mod
    C.APPS_DIR = os.path.join(tmp, "apps")
    apps_mod.C.APPS_DIR = C.APPS_DIR

    from passport.blepush import AppLink

    host = FakeHost()
    logs = []
    link = AppLink(host, log=logs.append)
    link.start()

    ble = link.ble
    ble._irq(1, (1, 0, b""))          # 模拟手机连上（否则设备不会回通知）
    say = ble.phone_write

    def drain():
        link.poll()

    def msgs():
        return reassemble(ble.sent)

    def last():
        m = msgs()
        return json.loads(m[-1]) if m else None

    print("\n[1] 握手")
    ble.sent.clear()
    say(json.dumps({"t": "hello"}).encode(), link)
    drain()
    r = last()
    check("hello → hi", r and r.get("t") == "hi", repr(r))
    check("hi 带 OS 版本", r and r.get("os") == "PassportOS", repr(r))

    print("\n[2] 上传小程序（分片 + 流控）")
    source = ("TITLE = \"T\"\n" * 40 +
              "def setup(ctx):\n    ctx.lcd.fill(0)\n").encode()
    name = "demo1"
    ble.sent.clear()
    say(json.dumps({"t": "put", "n": name, "s": len(source),
                    "title": "Demo One"}).encode(), link)
    drain()
    r = last()
    check("put 被接受", r and r.get("t") == "put", repr(r))

    # 分成 7 字节一片，故意制造很多小包
    off, acks, done = 0, 0, False
    while off < len(source):
        piece = source[off:off + 7]
        ble.sent.clear()
        say(piece, link)
        drain()
        m = last()
        if m and m.get("t") == "ack":
            acks += 1
            check_off = m.get("g")
            if check_off != off + len(piece):
                check("ack 进度正确", False, "期望 %d 得到 %r" % (off + len(piece), check_off))
                break
        elif m and m.get("t") == "done":
            done = True
        off += len(piece)
    check("每片都有 ack", acks == len(source) // 7, "acks=%d" % acks)
    check("最后一片触发 done", done)

    path = os.path.join(C.APPS_DIR, name, "app.py")
    written = open(path, "rb").read() if os.path.exists(path) else b""
    check("落盘内容与源码一致", written == source,
          "写入 %d 字节 / 期望 %d" % (len(written), len(source)))
    meta = os.path.join(C.APPS_DIR, name, "meta.json")
    check("meta.json 已生成", os.path.exists(meta))
    if os.path.exists(meta):
        check("meta 标题正确", json.load(open(meta)).get("title") == "Demo One")

    print("\n[3] 列表 / 运行 / 停止 / 删除")
    ble.sent.clear()
    say(json.dumps({"t": "ls"}).encode(), link)
    drain()
    r = last()
    check("ls 返回 app 列表", r and r.get("t") == "ls" and
          any(a["n"] == name for a in r.get("apps", [])), repr(r))

    ble.sent.clear()
    say(json.dumps({"t": "run", "n": name}).encode(), link)
    drain()
    r = last()
    check("run 调用 host.launch", host.launched == name and r.get("ok") is True, repr(r))

    ble.sent.clear()
    say(json.dumps({"t": "stop"}).encode(), link)
    drain()
    check("stop 调用 host.stop_app", host.stopped is True)

    ble.sent.clear()
    say(json.dumps({"t": "rm", "n": name}).encode(), link)
    drain()
    r = last()
    check("rm 删除成功", r and r.get("ok") is True, repr(r))
    check("目录已消失", not os.path.exists(os.path.join(C.APPS_DIR, name)))

    print("\n[4] 对时")
    ble.sent.clear()
    say(json.dumps({"t": "time", "epoch": 1800000000, "tz": 8 * 3600}).encode(), link)
    drain()
    r = last()
    check("time 设置 RTC", r and r.get("ok") is True, repr(r))
    check("RTC 收到 8 元组", isinstance(mach.RTC.last, tuple) and len(mach.RTC.last) == 8,
          repr(mach.RTC.last))

    ble.sent.clear()
    say(json.dumps({"t": "time", "epoch": 5}).encode(), link)
    drain()
    check("非法 epoch 被拒", last().get("ok") is False)

    print("\n[5] 错误处理")
    ble.sent.clear()
    say(b"{ this is not json", link)
    drain()
    check("坏 JSON 回错误", last().get("t") == "err")

    ble.sent.clear()
    say(json.dumps({"t": "put", "n": "Bad Name!", "s": 10}).encode(), link)
    drain()
    check("非法应用名被拒", last().get("t") == "err")

    ble.sent.clear()
    say(json.dumps({"t": "put", "n": "ok", "s": C.MAX_APP_SIZE + 1}).encode(), link)
    drain()
    check("超限体积被拒", last().get("t") == "err")

    ble.sent.clear()
    say(json.dumps({"t": "nonsense"}).encode(), link)
    drain()
    check("未知命令回错误", last().get("t") == "err")

    print("\n[6] 上传中断 + 大响应分片")
    ble.sent.clear()
    say(json.dumps({"t": "put", "n": "half", "s": 100}).encode(), link)
    drain()
    ble.sent.clear()
    say(b"0123456789", link)
    drain()
    check("中途收到 ack", last().get("t") == "ack")
    ble.sent.clear()
    say(json.dumps({"t": "abort"}).encode(), link)
    drain()
    check("abort 回执", last().get("t") == "abort")

    # 生成很多 app，逼 ls 响应超过单包
    for i in range(12):
        d = os.path.join(C.APPS_DIR, "app%02d" % i)
        os.makedirs(d, exist_ok=True)
        open(os.path.join(d, "app.py"), "w").write("x = 1\n")
        json.dump({"title": "Long Application Title %02d" % i},
                  open(os.path.join(d, "meta.json"), "w"))
    ble.sent.clear()
    say(json.dumps({"t": "ls"}).encode(), link)
    drain()
    frames = ble.sent
    check("大响应被切成多包", len(frames) > 1, "%d 帧" % len(frames))
    check("多包用 ~/! 分片", all(f[:1] in (b"~", b"!", b"{") for f in frames),
          repr([f[:1] for f in frames[:3]]))
    parsed = msgs()
    ok = False
    for m in parsed:
        try:
            o = json.loads(m)
            if o.get("t") == "ls" and len(o.get("apps", [])) == 12:
                ok = True
        except ValueError:
            pass
    check("分片能还原成完整 ls", ok)

    print("\n[7] 断开重连会重新广播")
    before = ble.adv_calls
    ble.sent.clear()
    ble._irq(1, (1, 0, b""))          # CONNECT
    check("连接后 connected=True", link.connected)
    ble._irq(2, (1, 0, b""))          # DISCONNECT
    check("断开后重新广播", ble.adv_calls > before)
    check("断开后 connected=False", not link.connected)

    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
