#!/usr/bin/env python3
"""一键诊断：Mac 上这个初代 Siri Remote 到底能不能当输入设备。

    .venv/bin/python mac/diagnose.py

分四步：
  1. 遥控器现在挂在哪（USB / 蓝牙）
  2. 蓝牙链路是 HID ACL 还是只有 BLE
  3. 有几个 HID 集合
  4. 20 秒实听 —— 按提示滑动触摸板/按按钮，看哪个集合真的吐报文
"""
import re
import subprocess
import sys
import threading
import time

import hid

APPLE, GEN1 = 0x004C, 0x026D
UNLOCK = bytes([0xF0, 0xAF])


def sh(cmd):
    return subprocess.run(cmd, capture_output=True).stdout.decode("utf-8", "replace")


def step1_usb():
    txt = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"], capture_output=True).stdout.decode("utf-8", "replace")
    on_usb = "Apple TV Remote@" in txt
    print("1. USB：%s" % ("插着（充电线在）" if on_usb else "没插"))
    return on_usb


def step2_bt():
    txt = sh(["system_profiler", "SPBluetoothDataType"])
    # 遥控器的条目名就是它的序列号（11 位大写字母数字），不写死具体值
    m = re.search(r"\b([A-Z0-9]{10,12}):(.*?)(?=\n\s{10}\S|\Z)", txt, re.S)
    if not m:
        print("2. 蓝牙：没在列表里（没配对？）")
        return None
    body = m.group(1)
    svc = re.search(r"Services:\s*(.+)", body)
    svc = svc.group(1).strip() if svc else "?"
    print("2. 蓝牙：Services: %s" % svc)
    if "BLE" in svc and "HID" not in svc:
        print("   -> 只有 BLE，没有 HID ACL。macOS 没把它当输入设备。")
    return svc


def step3_collections():
    cands = [d for d in hid.enumerate() if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
    print("3. HID 集合：%d 个  %s" % (len(cands), ", ".join(
        "%s/%s" % (d.get("usage_page"), d.get("usage")) for d in cands)))
    return cands


def step4_listen(cands, seconds=20):
    print("\n4. 实听 %d 秒 —— 现在开始：" % seconds)
    print("   ① 手指在触摸板上向左滑、向右滑、向上滑、向下滑（各两秒）")
    print("   ② 按下触摸板（click）两次")
    print("   ③ 按 Menu、TV、播放/暂停、音量+、音量-")
    for n in (3, 2, 1):
        print("   %d..." % n, end="", flush=True)
        time.sleep(0.6)
    print(" 开始！\n", flush=True)

    results, lock, stop = {}, threading.Lock(), threading.Event()

    def worker(d):
        name = "usage=%s/%s" % (d.get("usage_page"), d.get("usage"))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            with lock:
                results[name] = (None, str(e))
            return
        packets = []
        try:
            try:
                dev.send_feature_report(UNLOCK)
            except Exception:
                pass
            while not stop.is_set():
                data = dev.read(64, 100)
                if data:
                    packets.append(bytes(data))
        finally:
            try:
                dev.close()
            except Exception:
                pass
            with lock:
                results[name] = (packets, "")

    threads = [threading.Thread(target=worker, args=(d,), daemon=True) for d in cands]
    for t in threads:
        t.start()
    time.sleep(seconds)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    print("%-18s %6s  %s" % ("集合", "包数", "样例"))
    print("-" * 74)
    for name, (packets, err) in sorted(results.items(), key=lambda kv: -(len(kv[1][0] or []))):
        if packets is None:
            print("%-18s %6s  %s" % (name, "-", err))
        else:
            print("%-18s %6d  %s" % (name, len(packets),
                                     " ".join(p.hex() for p in packets[:3]) if packets else "（静默）"))
    return {k: v[0] for k, v in results.items() if v[0]}


def verdict(on_usb, svc, streams):
    print("\n" + "=" * 60)
    if not streams:
        print("结论：拿不到任何输入报文。")
        if on_usb:
            print("· 线还插着 —— 先拔掉，再跑一次（插线时只剩诊断通道）")
        if svc and "HID" not in svc:
            print("· 蓝牙只有 BLE 链路，macOS 26 没给初代遥控器建 HID ACL。")
            print("  这就是 BetterTouchTool 作者说的「macOS 15 之后初代没救」。")
            print("  可行路线只剩：树莓派/Linux 小主机跑原版 open-appleremote，事件转发给 Mac。")
        return
    first = sorted(streams.items(), key=lambda kv: -len(kv[1]))[0]
    head = first[1][0]
    print("结论：%s 会说话，%d 包。" % (first[0], len(first[1])))
    if head[:1] == b"\xfc":
        print("报文头 0xFC —— 就是原版协议，现有 decoder 能直接吃。")
        print("下一步：.venv/bin/python mac/siriremote.py keys --dev <该集合的 path>")
        print("（我可以把 find_remote() 改成按这个 usage 定位）")
    else:
        print("报文头 %s —— 标准 HID 报文，不是 0xFC 那套，需要按描述符另写解析。" % head[:3].hex())
        print("把这行输出贴给我，我按描述符写映射。")


def main():
    print("=== Siri Remote 在 macOS 上的可用性诊断 ===\n")
    on_usb = step1_usb()
    svc = step2_bt()
    cands = step3_collections()
    if not cands:
        print("\n没有 HID 集合，后面不用测了。")
        verdict(on_usb, svc, {})
        return 1
    streams = step4_listen(cands)
    verdict(on_usb, svc, streams)
    return 0


if __name__ == "__main__":
    sys.exit(main())
