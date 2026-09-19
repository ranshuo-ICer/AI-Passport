#!/usr/bin/env python3
"""PassportOS 的命令行 BLE 客户端（不依赖浏览器）。

既是调试工具，也是 Web App 之外的备用推送方式。

用法：
    python tools/ble_client.py scan
    python tools/ble_client.py hello
    python tools/ble_client.py ls
    python tools/ble_client.py repro              # 复现"命令不是合法json"
    python tools/ble_client.py push os/builtin/dice/app.py --name dice --title Dice
    python tools/ble_client.py push x.py --name t --title T --run
    python tools/ble_client.py run clock
    python tools/ble_client.py rm clock
    python tools/ble_client.py stop
    python tools/ble_client.py time
    python tools/ble_client.py console            # 一直挂着看设备日志

依赖：pip install bleak
"""

import argparse
import asyncio
import os
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("缺少 bleak。请先运行：  python -m pip install bleak")
    sys.exit(1)

SERVICE = "7a5c0001-0000-4000-8000-70617373706f"
CMD = "7a5c0002-0000-4000-8000-70617373706f"
RSP = "7a5c0003-0000-4000-8000-70617373706f"

DEVICE_NAME = "PassportOS"

# 与设备端 apps.valid_name() 保持一致：a-z0-9_- 且 ≤16 字符
_NAME_OK = set("abcdefghijklmnopqrstuvwxyz0123456789_-")
_NAME_RE = __import__("re").compile(r"^[a-z0-9_-]{1,16}$")


def _safe_name(s):
    """把用户输入归一化成合法应用名。

    注意不能用 str.isalnum()：它对中文也为真，`push 时钟.py` 会得到非法名。
    push / run / rm 统一走这里，避免三个子命令各写一份规则。
    """
    s = "".join(ch for ch in (s or "").lower() if ch in _NAME_OK)
    return s[:16] or "app"


# --------------------------------------------------------------------- 连接
async def find_device(timeout=12.0):
    print("扫描 %s …" % DEVICE_NAME)
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "") == DEVICE_NAME
        or SERVICE.lower() in [str(u).lower() for u in (ad.service_uuids or [])],
        timeout=timeout,
    )
    if dev is None:
        print("没找到设备。确认：设备已开机、屏幕显示 Passport 菜单、电脑蓝牙已开。")
    return dev


class Client:
    def __init__(self, client):
        self.c = client
        self.frag = ""
        self.inbox = []
        self.logs = []

    def handle(self, _sender, data: bytearray):
        text = data.decode("utf-8", "replace")
        if not text:
            return
        head = text[0]
        if head == "~":
            self.frag += text[1:]
            return
        if head == "!":
            self.frag += text[1:]
            payload, self.frag = self.frag, ""
        else:
            payload, self.frag = text, ""
        import json
        try:
            msg = json.loads(payload)
        except ValueError:
            print("  [!] 收到无法解析的通知: %r" % payload[:80])
            return
        t = msg.get("t")
        if t == "log":
            print("  [设备日志] %s" % msg.get("m"))
            self.logs.append(msg.get("m"))
            return
        if t == "key":
            print("  [按键] %s" % msg.get("k"))
            return
        if t == "state":
            print("  [状态] 运行中: %s" % (msg.get("app") or "无"))
            return
        if t == "err":
            print("  [设备报错] %s" % msg.get("m"))
        self.inbox.append(msg)

    async def wait(self, types, timeout=8.0):
        end = time.time() + timeout
        while time.time() < end:
            for i, m in enumerate(self.inbox):
                if m.get("t") in types:
                    return self.inbox.pop(i)
            await asyncio.sleep(0.03)
        raise TimeoutError("等 %s 超时" % types)

    async def send(self, obj):
        import json
        payload = json.dumps(obj).encode()
        await self.c.write_gatt_char(CMD, payload, response=True)
        return len(payload)


async def connect():
    dev = await find_device()
    if dev is None:
        return None

    # Windows 会缓存 GATT 服务表。设备换过固件（特征值增删）之后，
    # 缓存就过期了 —— 表现为 "Characteristic ... was not found"，
    # 但设备侧明明已经注册成功。use_cached_services=False 强制重新发现。
    kwargs = {}
    try:
        from bleak.args.winrt import WinRTClientArgs
        kwargs["winrt"] = WinRTClientArgs(use_cached_services=False)
        print("已启用 use_cached_services=False（绕开 Windows GATT 缓存）")
    except Exception as exc:                                  # noqa: BLE001
        print("(无法关闭 GATT 缓存: %s)" % exc)

    # 实测 Windows 的 GATT 发现【不稳定】：偶尔连上了却只拿到半个服务表，
    # 报 "Characteristic was not found"。重连一两次基本都能成功，
    # 所以这里做成带退避的重试，而不是一次失败就放弃。
    last = None
    for attempt in range(1, 4):
        c = BleakClient(dev, timeout=25.0, **kwargs)
        try:
            await c.connect()
            cli = Client(c)
            await c.start_notify(RSP, cli.handle)
            print("已连接: %s%s" % (dev.address,
                                    "" if attempt == 1 else "（第 %d 次尝试）" % attempt))
            return cli
        except Exception as exc:                              # noqa: BLE001
            last = exc
            try:
                await c.disconnect()
            except Exception:                                 # noqa: BLE001
                pass
            if attempt < 3:
                print("  [!] 第 %d 次连接失败（%s），1.5 秒后重试 …"
                      % (attempt, type(exc).__name__))
                await asyncio.sleep(1.5)

    print("[X] 连接/订阅失败: %s: %s" % (type(last).__name__, last))
    print("    若是 'Characteristic was not found'：")
    print("    -> 设备 GATT 表变过而 Windows 还在用旧缓存，或本次发现不完整。")
    print("       到「设置 -> 蓝牙和其他设备」删掉 PassportOS，")
    print("       或把系统蓝牙开关关掉再打开，然后重试。")
    return None


# --------------------------------------------------------------------- 命令
async def cmd_hello(cli):
    await cli.send({"t": "hello"})
    m = await cli.wait(["hi", "err"])
    print("  %s" % m)
    return m


async def cmd_ls(cli):
    await cli.send({"t": "ls"})
    m = await cli.wait(["ls", "err"])
    if m.get("t") == "ls":
        for a in m["apps"]:
            print("  %-12s %-16s %6d B" % (a["n"], a.get("title", ""), a["s"]))
        if not m["apps"]:
            print("  (设备上还没有小程序)")
    return m


async def cmd_push(cli, path, name, title, run_after, force_chunk=0):
    with open(path, "rb") as f:
        data = f.read()
    print("推送 %s (%d 字节) 为 %r …" % (path, len(data), name))

    await cli.send({"t": "put", "n": name, "s": len(data), "title": title})
    m = await cli.wait(["put", "err"])
    if m.get("t") == "err":
        print("  [X] %s" % m.get("m"))
        return False

    # 分片大小按**协商到的 MTU** 来，不要再硬编码。
    # 原来写死 160，而设备侧 BLE_MTU = 247、实测协商结果也是 247 ——
    # 每个分片白白浪费 84 字节；而**每个分片都是一次 ATT 往返 + 一次 ack 通知**，
    # 分片数就是吞吐瓶颈。
    # ⚠ 注意 BleakClient 在 cli.c 里，不是 cli 本身；取不到就**退回 160**
    #   （实测可用值），绝不能退回 23 算出的 20 —— 那会让推送慢 6 倍（实测过）。
    if force_chunk:
        chunk = force_chunk
        print("  指定分片 %d 字节" % chunk)
    else:
        mtu = getattr(getattr(cli, "c", None), "mtu_size", 0) or 0
        if mtu > 23:
            chunk = max(20, min(240, mtu - 3))
            print("  协商 MTU %d，分片 %d 字节" % (mtu, chunk))
        else:
            chunk = 160
            print("  未取到 MTU，沿用默认分片 %d 字节" % chunk)
    off = 0
    while off < len(data):
        piece = data[off:off + chunk]
        try:
            await cli.c.write_gatt_char(CMD, piece, response=True)
        except Exception as exc:                              # noqa: BLE001
            if len(piece) <= 20:
                print("  [X] 写入失败: %s" % exc)
                return False
            chunk = max(20, len(piece) // 2)
            print("  [!] 写入被拒，分片降到 %d" % chunk)
            continue
        m = await cli.wait(["ack", "done", "err"], timeout=10)
        if m.get("t") == "err":
            print("  [X] %s" % m.get("m"))
            return False
        # 以设备回的【权威计数】g 为准推进，而不是自己加 len(piece)。
        # 否则设备只存了一半、或者 ack 是上一轮残留的，客户端照样打印 ✓。
        got = m.get("g")
        if m.get("t") == "done":
            got = m.get("s", got)
        if not isinstance(got, int) or got <= off:
            print("  [X] 设备计数没有前进（off=%s, g=%r），中止" % (off, got))
            return False
        off = got
        if m.get("t") == "done":
            if off != len(data):
                print("  [X] 设备声称完成 %d 字节，但我们发了 %d 字节"
                      % (off, len(data)))
                return False
            print("  ✓ 完成 %d 字节（设备计数一致）" % off)
            if run_after:
                await cli.send({"t": "run", "n": name})
                print("  %s" % await cli.wait(["run", "err"]))
            return True
    await cli.send({"t": "end"})
    m = await cli.wait(["done", "err"])
    if m.get("t") == "err":
        print("  [X] %s" % m.get("m"))
        return False
    if m.get("s") != len(data):
        print("  [X] 完成计数 %r 与源码长度 %d 不符" % (m.get("s"), len(data)))
        return False
    print("  ✓ 完成 %d 字节" % len(data))
    if run_after:
        await cli.send({"t": "run", "n": name})
        print("  %s" % await cli.wait(["run", "err"]))
    return True


# --------------------------------------------------------------------- 复现
async def cmd_repro(cli):
    """复现 '命令不是合法json'。

    怀疑点：BLE 特征值的缓冲区是定长的，客户端写入【更短】的值后，
    IDF 可能仍保留上一次的尾巴。于是 gatts_read() 拿到的就不只是新数据。
    这里先写长的再写短的，看设备怎么报。
    """
    print("\n[1] 先写一条较长的命令")
    long_cmd = {"t": "hello", "padding": "X" * 40}
    await cli.send(long_cmd)
    print("    发出的长度: %d" % len(str(long_cmd)))
    m = await cli.wait(["hi", "err"], timeout=6)
    print("    设备回应: %s" % {k: v for k, v in m.items() if k != "name"})

    print("\n[2] 紧接着写一条【更短】的合法命令")
    short_cmd = {"t": "ls"}
    await cli.send(short_cmd)
    print("    发出的长度: %d  ->  %s" % (len(str(short_cmd)), short_cmd))
    try:
        m = await cli.wait(["ls", "err"], timeout=6)
        if m.get("t") == "err" and "json" in str(m.get("m", "")):
            print("    *** 复现成功：%s" % m.get("m"))
            print("    -> 确认是特征值缓冲区的残留尾巴导致的")
            return True
        print("    设备回应正常: t=%s" % m.get("t"))
    except TimeoutError:
        print("    超时（设备没回应）")

    print("\n[3] 反过来：先短后长")
    await cli.send({"t": "ping"})
    try:
        print("    %s" % await cli.wait(["pong", "err"], timeout=5))
    except TimeoutError:
        print("    超时")
    await cli.send({"t": "hello"})
    try:
        print("    %s" % await cli.wait(["hi", "err"], timeout=5))
    except TimeoutError:
        print("    超时")
    print("\n未复现。")
    return False


# --------------------------------------------------------------------- 主流程
async def run(args):
    cli = await connect()
    if cli is None:
        return 1

    import json
    try:
        if args.action == "hello":
            await cmd_hello(cli)
        elif args.action == "ls":
            await cmd_ls(cli)
        elif args.action == "repro":
            await cmd_repro(cli)
        elif args.action == "push":
            ok = await cmd_push(cli, args.path, args.name, args.title, args.run,
                                args.chunk)
            return 0 if ok else 1
        elif args.action == "run":
            # 位置参数和 --name 都接受：docstring 里写的是 `run clock`，
            # 但原来只读 args.name，于是 `run clock` 实际发出去的是空名字，
            # 设备端 launch("") 直接返回 False —— 静默的"假成功"，比报错难查。
            name = _safe_name(args.name or args.path)
            if not _NAME_RE.match(name):
                print("应用名 %r 不合法（只允许 a-z0-9_-，≤16 字符）" % name)
                return 1
            await cli.send({"t": "run", "n": name})
            reply = await cli.wait(["run", "err"])
            print(reply)
            # ok 是设备端 launch() 的权威返回值：名字不合法、载入失败都会是 False。
            if not isinstance(reply, dict) or not reply.get("ok"):
                return 1
        elif args.action == "rm":
            name = _safe_name(args.name or args.path)
            if not _NAME_RE.match(name):
                print("应用名 %r 不合法（只允许 a-z0-9_-，≤16 字符）" % name)
                return 1
            await cli.send({"t": "rm", "n": name})
            reply = await cli.wait(["rm", "err"])
            print(reply)
            if not isinstance(reply, dict) or not reply.get("ok"):
                return 1
        elif args.action == "stop":
            await cli.send({"t": "stop"})
            print(await cli.wait(["stop", "err"]))
        elif args.action == "time":
            now = time.time()
            await cli.send({"t": "time", "epoch": int(now),
                            "tz": -time.timezone})
            print(await cli.wait(["time", "err"]))
        elif args.action == "console":
            # console [名字] --seconds N
            # 带上名字就先运行它再监听 —— 这是唯一能证明"小程序在真机上跑起来
            # 不报错"的办法：OS 把 setup/loop/on_key 的异常打成日志通知发出来，
            # 而 push --run 的连接太短，跑起来之后的错误根本收不到。
            if args.path:
                name = _safe_name(args.name or args.path)
                await cli.send({"t": "run", "n": name})
                print("  运行 %s: %s" % (name, await cli.wait(["run", "err"])))
            if args.seconds:
                print("监听 %d 秒…" % args.seconds)
            else:
                print("监听中（Ctrl+C 退出）…")
            n = 0
            while not args.seconds or n < args.seconds:
                await asyncio.sleep(1)
                n += 1
                if n % 10 == 0:          # 看门狗 BLE_IDLE_TIMEOUT_MS=25s，10 秒续一次命
                    try:
                        await cli.send({"t": "ping"})
                    except Exception:                         # noqa: BLE001
                        pass
            if args.seconds:
                print("  %d 秒内没有「出错」日志即视为正常" % args.seconds)
    finally:
        # 必须由设备端断开：Windows 在客户端 disconnect() 之后会抓着 BLE 链路，
        # 设备 60 秒都察觉不到，期间不广播 —— 下一次调用就会"找不到设备"。
        try:
            await cli.send({"t": "bye"})
            await asyncio.sleep(0.4)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            await cli.c.disconnect()
            await asyncio.sleep(0.6)
        except Exception:                                     # noqa: BLE001
            pass
    return 0


async def scan():
    print("扫描 BLE 设备（8 秒）…")
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for addr, (dev, adv) in sorted(found.items()):
        mark = "  <== PassportOS" if (dev.name or "") == DEVICE_NAME else ""
        print("  %-20s %-22s rssi=%s%s"
              % (addr, (dev.name or "?")[:22], adv.rssi, mark))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["scan", "hello", "ls", "repro", "push",
                                       "run", "rm", "stop", "time", "console"])
    ap.add_argument("path", nargs="?", help="push 时的本地文件")
    ap.add_argument("--name", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--run", action="store_true", help="push 完立即运行")
    ap.add_argument("--chunk", type=int, default=0,
                    help="push 的分片字节数；0 = 按协商 MTU 自动（便于实测对比）")
    ap.add_argument("--seconds", type=int, default=0,
                    help="console 监听多少秒后退出；0 = 一直挂着。"
                         "配合位置参数可先运行某个小程序再监听")
    args = ap.parse_args()

    if args.action == "push":
        if not args.path or not os.path.isfile(args.path):
            print("push 需要指定一个存在的文件")
            return 1
        # 应用名规则和设备端 apps.valid_name() 一致：只允许 a-z0-9_-，≤16 字符。
        if not args.name:
            args.name = _safe_name(os.path.splitext(os.path.basename(args.path))[0])
        else:
            args.name = _safe_name(args.name)
        if not _NAME_RE.match(args.name):
            print("应用名 %r 不合法（只允许 a-z0-9_-，≤16 字符）" % args.name)
            return 1
        if not args.title:
            args.title = args.name

    try:
        if args.action == "scan":
            return asyncio.run(scan())
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\n已中断")
        return 130
    except Exception as exc:                                  # noqa: BLE001
        print("[X] %s: %s" % (type(exc).__name__, exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
