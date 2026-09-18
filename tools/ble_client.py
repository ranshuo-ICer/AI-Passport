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


async def cmd_push(cli, path, name, title, run_after):
    with open(path, "rb") as f:
        data = f.read()
    print("推送 %s (%d 字节) 为 %r …" % (path, len(data), name))

    await cli.send({"t": "put", "n": name, "s": len(data), "title": title})
    m = await cli.wait(["put", "err"])
    if m.get("t") == "err":
        print("  [X] %s" % m.get("m"))
        return False

    chunk = 160
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
        off += len(piece)
        if m.get("t") == "done":
            print("  ✓ 完成 %d 字节" % m.get("s", 0))
            if run_after:
                await cli.send({"t": "run", "n": name})
                print("  %s" % await cli.wait(["run", "err"]))
            return True
    await cli.send({"t": "end"})
    m = await cli.wait(["done", "err"])
    print("  ✓ %s" % m)
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
            ok = await cmd_push(cli, args.path, args.name, args.title, args.run)
            return 0 if ok else 1
        elif args.action == "run":
            await cli.send({"t": "run", "n": args.name})
            print(await cli.wait(["run", "err"]))
        elif args.action == "rm":
            await cli.send({"t": "rm", "n": args.name})
            print(await cli.wait(["rm", "err"]))
        elif args.action == "stop":
            await cli.send({"t": "stop"})
            print(await cli.wait(["stop", "err"]))
        elif args.action == "time":
            now = time.time()
            await cli.send({"t": "time", "epoch": int(now),
                            "tz": -time.timezone})
            print(await cli.wait(["time", "err"]))
        elif args.action == "console":
            print("监听中（Ctrl+C 退出）…")
            n = 0
            while True:
                await asyncio.sleep(1)
                n += 1
                if n % 25 == 0:          # 设备端 90 秒空闲会踢人，定期续命
                    try:
                        await cli.send({"t": "ping"})
                    except Exception:                         # noqa: BLE001
                        pass
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
    args = ap.parse_args()

    if args.action == "push":
        if not args.path or not os.path.isfile(args.path):
            print("push 需要指定一个存在的文件")
            return 1
        if not args.name:
            args.name = os.path.splitext(os.path.basename(args.path))[0].lower()
            args.name = "".join(ch for ch in args.name
                                if ch.isalnum() or ch in "_-")[:16] or "app"
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
