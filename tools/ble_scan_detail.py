#!/usr/bin/env python3
"""详细 BLE 扫描：把每个设备的服务 UUID 和 manufacturer data 都打出来。

用来排查"设备说自己在广播，但扫不到"这类问题。

    python tools/ble_scan_detail.py [秒数]
"""

import asyncio
import sys

try:
    from bleak import BleakScanner
except ImportError:
    print("缺少 bleak：  python -m pip install bleak")
    sys.exit(1)

TARGET_SERVICE = "7a5c0001-0000-4000-8000-70617373706f"


async def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    print("扫描 %.0f 秒（列出服务 UUID / 厂商数据）…\n" % seconds)
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)

    hit = False
    for addr, (dev, adv) in sorted(found.items()):
        uuids = [str(u).lower() for u in (adv.service_uuids or [])]
        name = dev.name or adv.local_name or "?"
        is_target = TARGET_SERVICE in uuids
        if is_target:
            hit = True
        mark = "  <<<<<< PassportOS!" if is_target else ""
        print("%-20s rssi=%-5s %s%s" % (addr, adv.rssi, name, mark))
        if uuids:
            print("      services: %s" % ", ".join(uuids))
        if adv.manufacturer_data:
            for k, v in adv.manufacturer_data.items():
                print("      mfg 0x%04X: %s" % (k, v.hex()))
        if is_target:
            print("      ^^^ 找到目标服务 UUID")

    print("\n共 %d 个设备" % len(found))
    if hit:
        print("结果：找到 PassportOS 的广播服务 ✓")
        return 0
    print("结果：**没有**任何设备广播了 PassportOS 的服务 UUID")
    print("可能原因：")
    print("  - 设备正被别的中心设备连着（连接期间不广播），比如浏览器标签页没关")
    print("  - 设备进深睡了")
    print("  - 蓝牙适配器缓存/驱动问题，重启蓝牙再试")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
