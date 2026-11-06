#!/usr/bin/env python3
"""同时监听遥控器的所有 HID 集合，看谁真的会说话。

    .venv/bin/python mac/listen.py --seconds 20
    # 跑起来后：反复滑动触摸板、按 click、按 Menu、按音量

输出：每个集合收到多少包、里面是什么。有包的那个集合就是数据源。
"""
import argparse
import sys
import threading
import time

import hid

APPLE, GEN1 = 0x004C, 0x026D
UNLOCK = bytes([0xF0, 0xAF])


def label(d):
    return "usage=%s/%s" % (d.get("usage_page"), d.get("usage"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--unlock", action="store_true", help="先发解锁 feature report F0 AF")
    args = ap.parse_args()

    cands = [d for d in hid.enumerate() if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
    if not cands:
        print("没找到 pid=0x026D（遥控器睡了？按一下）")
        return 1

    results = {}
    lock = threading.Lock()
    stop = threading.Event()

    def worker(d):
        name = label(d)
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            with lock:
                results[name] = ("打不开", str(e), [])
            return
        note = ""
        try:
            if args.unlock:
                try:
                    dev.send_feature_report(UNLOCK)
                except Exception as e:
                    note = "解锁失败: %s" % e
            packets = []
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
                results[name] = ("ok", note, packets)

    threads = [threading.Thread(target=worker, args=(d,), daemon=True) for d in cands]
    for t in threads:
        t.start()

    print("监听 %.0f 秒 —— 现在请：滑动触摸板几次、按一下 click、按 Menu、按音量+\n" % args.seconds, flush=True)
    for left in range(int(args.seconds), 0, -1):
        if left % 5 == 0:
            print("  还剩 %d 秒" % left, file=sys.stderr, flush=True)
        time.sleep(1)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    print("\n%-16s %6s  %s" % ("集合", "包数", "样例（最多 3 包）"))
    print("-" * 78)
    for name, (state, note, packets) in results.items():
        if state != "ok":
            print("%-16s %6s  %s" % (name, "-", note))
            continue
        sample = " ".join(p.hex() for p in packets[:3]) if packets else (note or "（静默）")
        print("%-16s %6d  %s" % (name, len(packets), sample))

    hit = [(n, p) for n, (s, _, p) in results.items() if p]
    print()
    if hit:
        print("有数据的集合：%s" % ", ".join(n for n, _ in hit))
        print("样例报文头：%s" % hit[0][1][0][:6].hex())
        print("=> 如果首字节是 fc，说明还是原版 0xFC 协议；否则是标准 HID 报文，需要另写解析")
    else:
        print("全静默。若刚才确实摸过遥控器，那就是这层拿不到输入：")
        print("  1) 遥控器插着 USB 时只走诊断通道 -> 拔线，用蓝牙重试")
        print("  2) macOS 26 没给初代建 HID ACL 链路 -> 系统层面不支持，改用 Linux 桥接")
    return 0


if __name__ == "__main__":
    sys.exit(main())
