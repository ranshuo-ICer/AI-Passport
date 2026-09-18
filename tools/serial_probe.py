#!/usr/bin/env python3
"""串口探针：抓日志 / 发 AT 指令 / 跑产线自检。

用法：
    python tools/serial_probe.py                       # 自动找端口，抓 8 秒日志
    python tools/serial_probe.py -t 20                 # 抓 20 秒
    python tools/serial_probe.py -c "AT+TEST?"         # 发一条命令
    python tools/serial_probe.py -c "at+config=?" -c "AT+TEST?"
    python tools/serial_probe.py -p COM3 -b 115200 -r  # 先复位再抓
    python tools/serial_probe.py --selftest            # 直接跑 AT+TEST?

注意：带 '?' 或 '=' 的参数在部分 shell 里会被吃掉，请用引号包住。
"""

import argparse
import sys
import time

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("缺少 pyserial。请先运行： python -m pip install pyserial")
    sys.exit(1)


def find_port():
    cands = []
    for p in list_ports.comports():
        text = " ".join(str(x) for x in (p.description, p.manufacturer, p.hwid))
        if (p.vid or 0) == 0x303A or "jtag" in text.lower() or "espressif" in text.lower():
            cands.append(p.device)
    if cands:
        return cands[0]
    ports = [p.device for p in list_ports.comports()]
    return ports[0] if len(ports) == 1 else None


def drain(ser, seconds):
    out = b""
    end = time.time() + seconds
    while time.time() < end:
        n = ser.in_waiting
        if n:
            out += ser.read(n)
        else:
            time.sleep(0.03)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-p", "--port")
    ap.add_argument("-b", "--baud", type=int, default=115200)
    ap.add_argument("-t", "--time", type=float, default=8.0, help="抓取秒数")
    ap.add_argument("-c", "--cmd", action="append", default=[], help="要发送的命令")
    ap.add_argument("-r", "--reset", action="store_true", help="先硬件复位")
    ap.add_argument("--selftest", action="store_true", help="跑 AT+TEST?")
    ap.add_argument("--wait", type=float, default=1.5, help="每条命令等待秒数")
    args = ap.parse_args()

    port = args.port or find_port()
    if not port:
        print("找不到串口。用 -p COMx 指定。当前可见：")
        for p in list_ports.comports():
            print("   %-12s %s" % (p.device, p.description))
        return 1

    cmds = list(args.cmd)
    if args.selftest:
        cmds.insert(0, "AT+TEST?")

    print("端口 %s @ %d" % (port, args.baud))
    with serial.Serial(port, args.baud, timeout=0.3) as ser:
        time.sleep(0.3)

        if args.reset:
            ser.setDTR(False)
            ser.setRTS(True)
            time.sleep(0.15)
            ser.setRTS(False)
            time.sleep(0.05)
            print("已触发复位")
        ser.reset_input_buffer()

        if cmds:
            for c in cmds:
                ser.reset_input_buffer()
                ser.write((c + "\r\n").encode())
                text = drain(ser, args.wait).decode("utf-8", "replace")
                print("\n>>> %s" % c)
                print(text.rstrip() if text.strip() else "   (无响应)")
        else:
            print("--- 抓取 %.0f 秒 ---" % args.time)
            text = drain(ser, args.time).decode("utf-8", "replace")
            print(text if text.strip() else "(没有输出：波特率不对，或设备已关机)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
