#!/usr/bin/env python3
"""分阶段录制：每个动作单独一段时间，录完立刻给出各阶段的报文指纹。

    .venv/bin/python mac/capture.py

按屏幕提示做动作（每步 2 秒左右），全程约 40 秒。结束后会打印：
每个阶段收到几个包、什么 report ID、payload 有哪些不同值。
把这些输出贴出来，就能反推按键位和触摸格式。
"""
import argparse
import json
import os
import threading
import time

import hid

APPLE, GEN1 = 0x004C, 0x026D

# (阶段名, 屏幕提示, 秒数)
PHASES = [
    ("idle1", "手离开遥控器，什么都别碰", 2.0),
    ("click", "按住触摸板（click）不要松", 1.5),
    ("release", "松开", 1.0),
    ("slide-r", "手指放触摸板上，向右滑（慢一点，滑满 2 秒）", 2.0),
    ("slide-l", "向左滑", 2.0),
    ("slide-u", "向上滑", 2.0),
    ("slide-d", "向下滑", 2.0),
    ("menu", "按一下 Menu", 1.2),
    ("tv", "按一下 TV", 1.2),
    ("playpause", "按一下播放/暂停", 1.2),
    ("volup", "按一下音量+", 1.2),
    ("voldown", "按一下音量-", 1.2),
    ("idle2", "手离开，结束", 2.0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  "captures", "cap1.jsonl"))
    ap.add_argument("--scale", type=float, default=1.0, help="所有阶段时长乘以这个系数")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)

    cands = [d for d in hid.enumerate() if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
    if not cands:
        print("没找到 pid=0x026D（遥控器睡了？按一下）")
        return 1

    events = []          # 内存里也留一份，方便最后汇总
    lock = threading.Lock()
    stop = threading.Event()
    t0 = time.monotonic()

    def worker(d):
        usage = "%s/%s" % (d.get("usage_page"), d.get("usage"))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception:
            return
        try:
            while not stop.is_set():
                data = dev.read(64, 100)
                if data:
                    with lock:
                        events.append({"kind": "report", "u": usage, "t": round(time.monotonic() - t0, 3),
                                       "hex": bytes(data).hex()})
        finally:
            try:
                dev.close()
            except Exception:
                pass

    threads = [threading.Thread(target=worker, args=(d,), daemon=True) for d in cands]
    for t in threads:
        t.start()

    marks = []
    print("开始录制，共 %d 步，约 %.0f 秒。跟着提示做就行。\n" % (
        len(PHASES), sum(p[2] for p in PHASES) * args.scale))
    for name, hint, secs in PHASES:
        secs *= args.scale
        marks.append({"kind": "mark", "phase": name, "t": round(time.monotonic() - t0, 3)})
        print(">>> %-10s %s  [%.1fs]" % (name, hint, secs), flush=True)
        time.sleep(secs)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    with open(args.out, "w") as f:
        for m in marks:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
        for e in events:
            f.write(json.dumps(e) + "\n")
    print("\n已写入 %s（%d 包）\n" % (args.out, len(events)))

    # ---- 汇总：每个阶段每个 (集合, reportID) 出现了哪些 payload
    phase_of = []
    for i, m in enumerate(marks):
        end = marks[i + 1]["t"] if i + 1 < len(marks) else 1e9
        phase_of.append((m["phase"], m["t"], end))

    print("%-10s %-10s %-6s %-5s %s" % ("阶段", "集合", "报ID", "包数", "payload 去重（最多 6 个）"))
    print("-" * 100)
    for phase, a, b in phase_of:
        buckets = {}
        for e in events:
            if not (a <= e["t"] < b):
                continue
            h = e["hex"]
            rid = h[:2]
            key = (e["u"], rid)
            buckets.setdefault(key, {})
            payload = h[2:]
            buckets[key][payload] = buckets[key].get(payload, 0) + 1
        if not buckets:
            print("%-10s %-10s %-6s %-5s %s" % (phase, "-", "-", 0, "（无包）"))
            continue
        for (usage, rid), payloads in sorted(buckets.items(), key=lambda kv: -sum(kv[1].values())):
            total = sum(payloads.values())
            top = sorted(payloads.items(), key=lambda kv: -kv[1])[:6]
            print("%-10s %-10s 0x%-4s %-5d %s" % (
                phase, usage, rid, total,
                "  ".join("%s×%d" % (p or "(空)", n) for p, n in top)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
