#!/usr/bin/env python3
"""受控实验：客户端断开后，设备多久才察觉自己已经断开？

同时监听串口和 BLE，把两个时间线对齐，用来判断"断开后设备不广播"
到底是设备的问题还是主机（Windows）抓着链路不放。

    python tools/ble_disconnect_test.py
"""

import asyncio
import sys
import threading
import time

try:
    import serial
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("需要 bleak 和 pyserial")
    sys.exit(1)

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM3"
RSP = "7a5c0003-0000-4000-8000-70617373706f"

lines = []
stop = threading.Event()


def reader():
    try:
        s = serial.Serial(PORT, 115200, timeout=0.2)
    except Exception as exc:                                  # noqa: BLE001
        print("[串口打不开: %s]" % exc)
        return
    while not stop.is_set():
        try:
            n = s.in_waiting
            if n:
                for ln in s.read(n).decode("utf-8", "replace").splitlines():
                    ln = ln.strip()
                    if ln:
                        lines.append((time.time(), ln))
                        print("  [%.1fs 设备] %s" % (time.time() - T0, ln))
            else:
                time.sleep(0.05)
        except Exception:                                     # noqa: BLE001
            break
    s.close()


T0 = time.time()
threading.Thread(target=reader, daemon=True).start()
time.sleep(0.5)


async def main():
    print("扫描 …")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "") == "PassportOS", timeout=15)
    if dev is None:
        print("没找到设备")
        return 1

    kwargs = {}
    try:
        from bleak.args.winrt import WinRTClientArgs
        kwargs["winrt"] = WinRTClientArgs(use_cached_services=False)
    except Exception:                                         # noqa: BLE001
        pass

    c = BleakClient(dev, timeout=25.0, **kwargs)
    await c.connect()
    await c.start_notify(RSP, lambda _s, _d: None)
    print(">>> [%.1fs] 已连接" % (time.time() - T0))
    await asyncio.sleep(3)

    print(">>> [%.1fs] 调用 disconnect()" % (time.time() - T0))
    await c.disconnect()
    print(">>> [%.1fs] disconnect() 返回" % (time.time() - T0))

    print("\n等待设备察觉（最多 100 秒）…\n")
    await asyncio.sleep(100)
    return 0


try:
    asyncio.run(main())
finally:
    stop.set()
    time.sleep(0.3)
    print("\n" + "=" * 60)
    disc = [t for t, ln in lines if "已断开" in ln]
    adv = [t for t, ln in lines if "广播已启动" in ln]
    if disc:
        print("设备察觉断开用了 %.1f 秒" % (disc[0] - T0))
    else:
        print("设备在 100 秒内【始终没有】察觉断开")
        print("=> 是主机（Windows）抓着 BLE 链路不放，不是设备的问题")
    if adv:
        print("恢复广播于 %.1f 秒" % (adv[-1] - T0))
    print("=" * 60)
