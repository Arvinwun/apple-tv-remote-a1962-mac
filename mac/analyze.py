#!/usr/bin/env python3
"""分析 capture.py 录下来的文件：每个阶段各集合吐了什么，单字节 payload 的每一位对应哪个动作。

    .venv/bin/python mac/analyze.py [mac/captures/cap1.jsonl]
"""
import json
import sys
from collections import defaultdict


def load(path):
    marks, events = [], []
    for line in open(path):
        e = json.loads(line)
        (marks if e["kind"] == "mark" else events).append(e)
    return marks, events


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "mac/captures/cap1.jsonl"
    marks, events = load(path)
    print("录制 %s：%d 个包，%d 个阶段\n" % (path, len(events), len(marks)))

    spans = []
    for i, m in enumerate(marks):
        end = marks[i + 1]["t"] if i + 1 < len(marks) else float("inf")
        spans.append((m["phase"], m["t"], end))

    per_phase = {}
    for phase, a, b in spans:
        bucket = defaultdict(lambda: defaultdict(int))
        for e in events:
            if a <= e["t"] < b:
                bucket[(e["u"], e["hex"][:2])][e["hex"][2:]] += 1
        per_phase[phase] = bucket

    print("=" * 96)
    print("%-9s %-10s %-5s %-5s %s" % ("阶段", "集合", "ID", "包数", "payload × 次数"))
    print("=" * 96)
    for phase in per_phase:
        b = per_phase[phase]
        if not b:
            print("%-9s %-10s %-5s %-5d %s" % (phase, "-", "-", 0, "（静默）"))
            continue
        first = True
        for (usage, rid), payloads in sorted(b.items(), key=lambda kv: -sum(kv[1].values())):
            top = sorted(payloads.items(), key=lambda kv: -kv[1])[:5]
            print("%-9s %-10s 0x%-3s %-5d %s" % (
                phase if first else "", usage, rid, sum(payloads.values()),
                "  ".join("%s×%d" % (p or "(空)", n) for p, n in top)))
            first = False
    print()

    # 单字节 payload：列出每位在哪些阶段为 1
    single = defaultdict(lambda: defaultdict(set))     # (usage,rid) -> bit -> {phases}
    for phase, b in per_phase.items():
        for (usage, rid), payloads in b.items():
            for p in payloads:
                if len(p) == 2:
                    v = int(p, 16)
                    for bit in range(8):
                        if v >> bit & 1:
                            single[(usage, rid)][bit].add(phase)
    if single:
        print("单字节 payload 的位 -> 出现的动作：")
        for (usage, rid), bits in single.items():
            print("  %s report 0x%s" % (usage, rid))
            for bit in range(8):
                phases = bits.get(bit, set())
                print("    bit%d (0x%02X)  %s" % (bit, 1 << bit,
                                                  ", ".join(sorted(phases)) if phases else "-"))
        print()

    # 多字节 payload 的逐字节变化：找出哪些字节在动
    print("多字节 payload 里在各阶段变化的字节位置：")
    for (usage, rid) in sorted({(e["u"], e["hex"][:2]) for e in events}):
        byphase = {}
        for phase, b in per_phase.items():
            if (usage, rid) in b:
                byphase[phase] = sorted(b[(usage, rid)])
        if not byphase:
            continue
        lens = {len(p) // 2 for p in sum(byphase.values(), [])}
        if not lens or max(lens) <= 1:
            continue
        print("  %s report 0x%s（长度 %s 字节）" % (usage, rid, sorted(lens)))
        for phase, ps in byphase.items():
            print("    %-9s %s" % (phase, "  ".join(ps[:3])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
