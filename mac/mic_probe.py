#!/usr/bin/env python3
"""麦克风通道探针：按住遥控器麦克风键，看 HID 层到底给多少音频字节。

    .venv/bin/python mac/mic_probe.py

结论怎么读：
  · 报文长度 <= 21、且第 3 字节之后只有 13 个音频字节  -> 只有 Opus 帧的前半截，无法解码（做不成麦克风）
  · 报文长度明显更长（几十~几百字节），或能拼出完整帧 -> 有戏，可以接 Opus 解码 + 虚拟声卡

只监听麦克风相关的集合（12/4、12/265），不碰 12/1（那会被正在跑的实例占着）。
"""
import argparse
import sys
import threading
import time

import hid

APPLE, GEN1 = 0x4C, 0x26D
# 全部集合都听：语音可能塞在 12/4（Consumer/Microphone），也可能混在 12/1 的 0xFC 报文里。
# 12/1 会被正在跑的 siriremote 实例独占，打不开时会明确提示。
WATCH = None


def label(d):
    return "%s/%s" % (d.get("usage_page"), d.get("usage"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=20)
    args = ap.parse_args()

    cands = [d for d in hid.enumerate()
             if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
    if not cands:
        print("没找到遥控器（按一下唤醒）")
        return 1
    targets = [d for d in cands if WATCH is None or (d.get("usage_page"), d.get("usage")) in WATCH]
    others = [d for d in cands if d not in targets]
    print("遥控器集合 %d 个，监听其中 %d 个：%s" % (
        len(cands), len(targets), ", ".join(label(d) for d in targets)))
    if others:
        print("（另外 %d 个跳过：%s）" % (len(others), ", ".join(label(d) for d in others)))

    results, lock, stop = {}, threading.Lock(), threading.Event()

    def worker(d):
        name = label(d)
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            hint = "（有 siriremote 在跑？它会独占 12/1，先 Ctrl-C 停掉）" if (d.get("usage"), d.get("usage_page")) == (1, 12) else ""
            with lock:
                results[name] = ("打不开", str(e) + hint, [])
            return
        packets = []
        try:
            while not stop.is_set():
                data = dev.read(128, 100)
                if data:
                    packets.append(bytes(data))
        finally:
            try:
                dev.close()
            except Exception:
                pass
            with lock:
                results[name] = ("ok", "", packets)

    threads = [threading.Thread(target=worker, args=(d,), daemon=True) for d in targets]
    for t in threads:
        t.start()

    print("\n监听 %.0f 秒 —— 现在请**按住遥控器上的麦克风键**（Siri 键）说几句话，然后松开。\n"
          % args.seconds, flush=True)
    for left in range(int(args.seconds), 0, -1):
        if left % 5 == 0:
            print("  还剩 %d 秒" % left, file=sys.stderr, flush=True)
        time.sleep(1)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    print("\n%-12s %6s  %-6s %s" % ("集合", "包数", "长度", "样例"))
    print("-" * 80)
    total = 0
    for name, (state, err, packets) in sorted(results.items()):
        if state != "ok":
            print("%-12s %6s  %-6s %s" % (name, "-", "-", err))
            continue
        total += len(packets)
        lengths = sorted({len(p) for p in packets})
        sample = " ".join(p.hex() for p in packets[:2]) if packets else "（静默）"
        print("%-12s %6d  %-6s %s" % (name, len(packets), ",".join(map(str, lengths)) or "-", sample))

    print()
    if total == 0:
        print("没收到包。可能：① 没按住麦克风键；② 12/1 被别的进程占着（看上面「打不开」那几行）")
        return 1

    sizes = [len(p) for _, (s, _, ps) in results.items() if s == "ok" for p in ps]
    print("最大报文长度：%d 字节" % max(sizes))
    if max(sizes) <= 21:
        print("=> 只有 21 字节：扣掉 8 字节头，每个报文只带 13 字节 Opus 片段。")
        print("   一个 20ms 的 Opus 帧通常 40~80 字节，靠这些片段拼不出可解码的流。")
        print("   macOS 也没有 HCI 抓包通道，所以「遥控器当麦克风」这条路走不通。")
    else:
        print("=> 报文比 21 字节长，值得继续挖：可能是完整帧，或者是另一种封装。")
        print("   把上面几行样例发出来，我按格式写解码。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
