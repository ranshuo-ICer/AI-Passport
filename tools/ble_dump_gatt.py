#!/usr/bin/env python3
"""枚举 PassportOS 的 GATT 服务/特征值，用于排查"特征值找不到"。

    python tools/ble_dump_gatt.py
"""

import asyncio
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("缺少 bleak：  python pip install bleak")
    sys.exit(1)

SERVICE = "7a5c0001-0000-4000-8000-70617373706f"
EXPECT = {
    "7a5c0002-0000-4000-8000-70617373706f": "CMD (手机→设备, 写)",
    "7a5c0003-0000-4000-8000-70617373706f": "RSP (设备→手机, 通知)",
}


async def main():
    print("扫描 PassportOS …")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, ad: (d.name or "") == "PassportOS", timeout=15)
    if dev is None:
        print("没找到设备。确认设备在广播（python tools/ble_scan_detail.py）")
        return 1
    print("找到: %s (%s)" % (dev.name, dev.address))

    kwargs = {}
    try:
        from bleak.args.winrt import WinRTClientArgs
        kwargs["winrt"] = WinRTClientArgs(use_cached_services=False)
        print("使用 use_cached_services=False")
    except Exception as exc:                                  # noqa: BLE001
        print("(缓存开关不可用: %s)" % exc)

    c = BleakClient(dev, timeout=25.0, **kwargs)
    try:
        await c.connect()
    except Exception as exc:                                  # noqa: BLE001
        print("[X] 连接失败: %s: %s" % (type(exc).__name__, exc))
        return 1

    print("\n=== Windows 枚举到的 GATT 表 ===")
    seen = set()
    for s in c.services:
        print("SERVICE %s" % s.uuid)
        for ch in s.characteristics:
            seen.add(str(ch.uuid).lower())
            print("   CHAR  %s" % ch.uuid)
            print("         props = %s" % ", ".join(ch.properties))
            print("         handle= %s" % ch.handle)
            for d in ch.descriptors:
                print("         DESC  %s" % d.uuid)

    print("\n=== 期望的特征值是否都在 ===")
    ok = True
    for uuid, desc in EXPECT.items():
        found = uuid in seen
        ok = ok and found
        print("  %s  %s  %s" % ("✓" if found else "✗", uuid, desc))

    if not ok:
        print("\n缺少特征值。若设备侧日志显示两个句柄都注册了，")
        print("而这里只看到一个，说明是【客户端/协议栈】的发现过程有问题，")
        print("不是设备的问题 —— 重点查 Windows 蓝牙驱动或换台机器试试。")

    try:
        await c.disconnect()
    except Exception:                                         # noqa: BLE001
        pass
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
