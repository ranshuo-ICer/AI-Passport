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
    idle = [t for t, ln in lines if "空闲超过" in ln]

    # ⚠ 设备端现在有 25 秒空闲看门狗（BLE_IDLE_TIMEOUT_MS）。所以"设备多久察觉
    #   断开"这个数字只有在看门狗触发【之前】出现才有意义；否则测到的是看门狗
    #   的兜底时间，不是链路本身的断开检测。以前这个脚本会把两种结论混着打印，
    #   自相矛盾，这里显式区分。
    if idle and disc and disc[0] > idle[0]:
        print("结论：客户端 disconnect() 之后设备【没有】收到断开事件。")
        print("      设备是在 %.1f 秒被自己的空闲看门狗救回来的（看门狗 %.1f 秒触发）。"
              % (disc[0] - T0, idle[0] - T0))
        print("      => 印证：Windows 会抓着 BLE 链路，必须由设备端主动断开")
        print("         （客户端收尾时发 {\"t\":\"bye\"}）。")
    elif disc:
        print("结论：设备在 %.1f 秒察觉断开（早于看门狗），链路断开事件正常。"
              % (disc[0] - T0))
    else:
        print("结论：设备在 100 秒内【始终没有】察觉断开，看门狗也没在窗口内触发")
        print("      （看门狗默认 25 秒，出现这种情况请检查设备固件是否是最新的）。")
    if adv:
        print("恢复广播于 %.1f 秒" % (adv[-1] - T0))
    print("=" * 60)
