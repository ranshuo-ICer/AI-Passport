#!/usr/bin/env python3
"""把 PassportOS 部署到设备（通过 USB 串口）。

底层用官方的 **mpremote** —— 不要自己拿 pyserial 手搓 raw REPL：
那个方案每传一个分片都要一次往返，慢且容易在超时上卡死
（本项目第一版就是这么写的，实测会卡住并留下截断的文件）。

本脚本做的事：
    1. 探测设备（确认对面是 MicroPython）
    2. 可选：清空设备上的 /passport 和 /apps
    3. 逐文件传输（**排除 __pycache__ 和 .pyc**）
    4. 回读设备上的文件大小，与本地逐一比对

用法：
    python tools/deploy.py                  # 自动找端口
    python tools/deploy.py COM3             # 指定端口
    python tools/deploy.py COM3 --clean     # 先清空再传（推荐：避免残留旧文件）
    python tools/deploy.py COM3 --apps-only # 只更新示例小程序
    python tools/deploy.py COM3 --no-builtin
    python tools/deploy.py COM3 --dry-run   # 只打印计划

依赖：pip install mpremote pyserial
"""

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OS_DIR = os.path.join(ROOT, "os")
BUILTIN_DIR = os.path.join(OS_DIR, "builtin")

SYSTEM_FILES = ["boot.py", "main.py"]
SYSTEM_DIRS = ["passport"]

IGNORE_DIRS = {"__pycache__", ".git"}
IGNORE_EXT = {".pyc", ".pyo", ".pyi"}

ESP_VID = 0x303A

# 清空设备目录：MicroPython 没有 shutil，自己递归删
CLEAN_CODE = (
    "import os\n"
    "def _rm(p):\n"
    "    try: names=os.listdir(p)\n"
    "    except OSError: return\n"
    "    for n in names:\n"
    "        f=p+'/'+n\n"
    "        try:\n"
    "            if os.stat(f)[0] & 0x4000: _rm(f)\n"
    "            else: os.remove(f)\n"
    "        except OSError: pass\n"
    "    try: os.rmdir(p)\n"
    "    except OSError: pass\n"
    "_rm('/passport')\n"
    "_rm('/apps')\n"
    "print('cleaned')\n"
)

VERIFY_CODE = (
    "import os\n"
    "def _w(d):\n"
    "    try: names=sorted(os.listdir(d))\n"
    "    except OSError: return\n"
    "    for n in names:\n"
    "        p=(d.rstrip('/')+'/'+n) if d!='/' else '/'+n\n"
    "        try: st=os.stat(p)\n"
    "        except OSError: continue\n"
    "        if st[0] & 0x4000: _w(p)\n"
    "        else: print(p, st[6])\n"
    "_w('/')\n"
)


def list_ports():
    try:
        from serial.tools import list_ports as lp
        return [(p.device, p.description) for p in lp.comports()]
    except ImportError:
        return []


def find_port():
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    cands = []
    for p in list_ports.comports():
        text = " ".join(str(x) for x in (p.description, p.manufacturer, p.hwid))
        if (p.vid or 0) == ESP_VID or "jtag" in text.lower() \
                or "espressif" in text.lower():
            cands.append(p.device)
    if cands:
        return cands[0]
    ports = [p.device for p in list_ports.comports()]
    return ports[0] if len(ports) == 1 else None


def mpremote_argv(port, *commands):
    argv = [sys.executable, "-m", "mpremote", "connect", port]
    for i, c in enumerate(commands):
        if i:
            argv.append("+")
        argv.extend(c)
    return argv


def call_mpremote(port, *commands, timeout=300):
    proc = subprocess.run(mpremote_argv(port, *commands),
                          capture_output=True, text=True, timeout=timeout)
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


# --------------------------------------------------------------------- 计划
def collect():
    """返回 (需要的远程目录集合, [(本地文件, 远程目录), ...])"""
    dirs, copies = set(), []

    def add(local, remote_dir):
        if not os.path.isfile(local):
            return
        dirs.add(remote_dir)
        copies.append((local.replace("\\", "/"), remote_dir))

    for f in SYSTEM_FILES:
        add(os.path.join(OS_DIR, f), ":")

    for d in SYSTEM_DIRS:
        base = os.path.join(OS_DIR, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames if x not in IGNORE_DIRS]
            rel = os.path.relpath(dirpath, OS_DIR).replace("\\", "/")
            for fn in sorted(filenames):
                if os.path.splitext(fn)[1].lower() in IGNORE_EXT:
                    continue
                add(os.path.join(dirpath, fn), ":" + rel)

    if os.path.isdir(BUILTIN_DIR):
        for n in sorted(x for x in os.listdir(BUILTIN_DIR)
                        if os.path.isdir(os.path.join(BUILTIN_DIR, x))):
            base = os.path.join(BUILTIN_DIR, n)
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [x for x in dirnames if x not in IGNORE_DIRS]
                rel = os.path.relpath(dirpath, base).replace("\\", "/")
                remote = ":apps/%s" % n if rel == "." else ":apps/%s/%s" % (n, rel)
                for fn in sorted(filenames):
                    if os.path.splitext(fn)[1].lower() in IGNORE_EXT:
                        continue
                    add(os.path.join(dirpath, fn), remote)
    return dirs, copies


def _with_parents(path):
    """':apps/clock' → {':apps', ':apps/clock'}；根 ':' 要排除（它总是存在）。"""
    parts = path.split("/")
    out = set()
    for i in range(1, len(parts) + 1):
        p = "/".join(parts[:i])
        if p != ":":
            out.add(p)
    return out


def plan_commands(dirs, copies, apps_only, no_builtin):
    """返回 (需要建的目录列表, [(本地文件, 远程目录), ...])。

    注意：建目录**不能**用 mpremote 的 fs mkdir 塞进 `+` 链里 ——
    链中任何一条失败（比如目录已存在）会让后面所有命令一起中止，
    于是父目录建不出来，cp 就报 "destination is not a directory"。
    所以改成一次性 exec 一段幂等的 mkdir 脚本。
    """
    kept = []
    for local, remote in copies:
        if apps_only and not remote.startswith(":apps"):
            continue
        if no_builtin and remote.startswith(":apps"):
            continue
        kept.append((local, remote))

    used_dirs = set()
    for _, remote in kept:
        used_dirs |= _with_parents(remote)
    ordered = sorted(used_dirs, key=lambda s: (s.count("/"), s))
    return ordered, kept


def mkdir_code(dirs):
    body = ",\n  ".join(repr(d.lstrip(":")) for d in dirs)
    return (
        "import os\n"
        "for _d in [\n  %s,\n]:\n"
        "    try: os.mkdir('/' + _d)\n"
        "    except OSError: pass\n"
        "print('mkdir ok')\n" % body
    )


# --------------------------------------------------------------------- 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("port", nargs="?", help="串口，如 COM3")
    ap.add_argument("--no-builtin", action="store_true")
    ap.add_argument("--apps-only", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-reset", action="store_true")
    ap.add_argument("--clean", action="store_true",
                    help="先删除设备上的 /passport 和 /apps（破坏性，需确认）")
    ap.add_argument("--yes", action="store_true",
                    help="跳过 --clean 的二次确认（非交互环境必须显式加）")
    args = ap.parse_args()

    # --dry-run 要在【探测串口之前】就返回：它的承诺是"只打印计划"，
    # 没接设备也应该能跑（以前是在 find_port() 之后才判断，没设备直接 exit 1）。
    dirs, copies = collect()
    mkdir_dirs, kept = plan_commands(dirs, copies, args.apps_only, args.no_builtin)
    print("计划：%d 个文件 → 设备（%d 个目录）" % (len(kept), len(mkdir_dirs)))
    if args.dry_run:
        for local, remote in kept:
            print("   %-52s → %s/"
                  % (local.replace(ROOT.replace("\\", "/") + "/", ""), remote))
        print("\n目录: %s" % ", ".join(mkdir_dirs))
        if args.clean:
            print("[!] 已指定 --clean：真正执行时会先删除设备上的 /passport 和 /apps")
        print("（--dry-run，未连接设备、未执行）")
        return 0

    try:
        import mpremote  # noqa: F401
    except ImportError:
        print("缺少 mpremote。请先运行：  python -m pip install mpremote")
        return 1

    # 破坏性操作先确认，而且放在碰设备之前 —— 免得到最后一步才问，
    # 更免得删完 /passport 之后传输失败、设备直接起不来。
    if args.clean:
        print("[!] --clean 会【递归删除】设备上的 /passport 和 /apps")
        print("    删完到传输成功之间，设备是不完整的 —— 中途失败会起不来。")
        if not args.yes:
            if not sys.stdin.isatty():
                print("[X] 非交互环境未加 --yes，拒绝执行破坏性清理。")
                print("    确实要清理请显式加上:  --clean --yes")
                return 1
            try:
                ans = input("    确认删除请输入 yes: ").strip().lower()
            except EOFError:
                ans = ""
            if ans != "yes":
                print("已取消。")
                return 1
        print("    已确认。")

    port = args.port or find_port()
    if not port:
        print("找不到串口。请插好设备并用参数指定，例如：")
        print("    python tools/deploy.py COM3")
        print("\n当前可见端口：")
        for dev, desc in list_ports():
            print("   %-12s %s" % (dev, desc))
        return 1
    print("端口: %s" % port)

    # 1) 探测
    rc, out, err = call_mpremote(
        port, ["exec", "import sys;print(sys.implementation.name, sys.version.split()[0])"],
        timeout=60)
    out = out.strip()
    if rc != 0 or "micropython" not in out:
        print("[X] 设备上没有响应 MicroPython REPL。")
        print("    %s" % ((err or out).strip()[-300:]))
        print("    检查：设备开机了吗？已烧 MicroPython 了吗？串口被占用了吗？")
        return 1
    print("设备固件: MicroPython %s" % out.split()[-1])

    # 2) 可选清空（确认已在最前面做过）
    if args.clean:
        print("清空设备上的 /passport 和 /apps …")
        rc, out, err = call_mpremote(port, ["exec", CLEAN_CODE], timeout=90)
        if "cleaned" not in out:
            print("  [!] 清理未确认成功，继续（可能是本来就没有）: %s"
                  % ((err or out).strip()[-160:]))

    # 3) 建目录（一次 exec，幂等；失败不致命）
    if mkdir_dirs:
        rc, out, err = call_mpremote(port, ["exec", mkdir_code(mkdir_dirs)], timeout=90)
        if "mkdir ok" not in out:
            print("  [!] 建目录未确认: %s" % ((err or out).strip()[-160:]))

    # 4) 传输。mpremote 的 `+` 链中任何一条失败会中止整条链，
    #    所以批次失败时逐条重跑来定位到底哪个文件有问题。
    print("开始传输 …")
    cps = [["fs", "cp", local, remote + "/"] for local, remote in kept]
    failed = []

    def run_batch(batch):
        rc, out, err = call_mpremote(port, *batch)
        if rc == 0:
            return
        if len(batch) == 1:
            msg = (err or out).strip().splitlines()
            failed.append((" ".join(batch[0]), msg[-1] if msg else "未知错误"))
            return
        for one in batch:                    # 链被打断，逐条定位
            rc2, out2, err2 = call_mpremote(port, one)
            if rc2 != 0:
                msg = (err2 or out2).strip().splitlines()
                failed.append((" ".join(one), msg[-1] if msg else "未知错误"))

    batch = []
    for c in cps:
        batch.append(c)
        if len(batch) >= 8:
            run_batch(batch)
            batch = []
    if batch:
        run_batch(batch)

    if failed:
        print("\n[X] %d 个文件传输失败：" % len(failed))
        for c, e in failed[:8]:
            print("    %s\n      %s" % (c, e))
        return 1

    # 4) 回读校验
    print("回读校验 …")
    rc, out, err = call_mpremote(port, ["exec", VERIFY_CODE], timeout=120)
    remote = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.startswith("/"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) == 2 and parts[1].isdigit():
            remote[parts[0]] = int(parts[1])

    bad = []
    for local, remote_dir in kept:
        rpath = ("/" + os.path.basename(remote_dir) if remote_dir != ":" else "")
        if remote_dir == ":":
            rpath = "/" + os.path.basename(local)
        elif remote_dir.startswith(":apps"):
            rpath = "/apps" + remote_dir[len(":apps"):] + "/" + os.path.basename(local)
        else:
            rpath = "/" + remote_dir[1:] + "/" + os.path.basename(local)
        lsize = os.path.getsize(local)
        rsize = remote.get(rpath)
        if rsize != lsize:
            bad.append((rpath, lsize, rsize))

    if bad:
        print("\n[X] 有 %d 个文件在设备上大小不对：" % len(bad))
        for p, l, r in bad[:10]:
            print("    %-40s 本地 %6d  设备 %s" % (p, l, "缺失" if r is None else r))
        return 1
    print("全部 %d 个文件大小一致 ✓" % len(kept))

    if not args.no_reset:
        print("软复位，启动 PassportOS …")
        call_mpremote(port, ["reset"], timeout=60)

    print("\n完成！设备屏幕应该显示 Passport 主菜单。")
    print("接着打开「Passport 助手」App，点「连接」即可推送小程序。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
