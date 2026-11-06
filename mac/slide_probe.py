#!/usr/bin/env python3
"""只读诊断：往四个方向滑，看触摸层到底给出了什么。**不会移动鼠标。**

它只挂 MultitouchSupport 的回调，不碰 HID 的按键集合（12/1），
所以可以和主程序同时跑，互不干扰。

用法：
    .venv/bin/python mac/slide_probe.py

输出每段：收到多少帧、有多少被手掌误触过滤掉、有多少被当成抬手、
坐标累计变化了多少，以及判定。
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "open-appleremote"))

from live_mac import LiveMac          # noqa: E402

# 和主程序用同一份配置：palm_guard 默认已经是 false
CFG = {}
try:
    with open(os.path.join(HERE, "config.json"), encoding="utf-8") as f:
        CFG = json.load(f) or {}
except Exception:
    pass
GUARD = bool(CFG.get("palm_guard", False))
PALM = float(CFG.get("palm_size", 0.02))

SEG = float(os.environ.get("SLIDE_SEG", "5"))   # 每段秒数
PHASES = [
    ("right", "往右滑", "dx 应为正"),
    ("left",  "往左滑", "dx 应为负"),
    ("up",    "往上滑", "dy 应为正"),
    ("down",  "往下滑", "dy 应为负"),
]


def main():
    live = LiveMac(enable_buttons=False, enable_touch=True)
    events = live.events()
    print("只读诊断：不会移动鼠标。可以和主程序同时跑。")
    print("手掌过滤：%s（阈值 %.3f）" % ("已启用" if GUARD else "已关闭", PALM))
    print("每段 %.0f 秒，跟着提示滑。全程 %.0f 秒。\n" % (SEG, SEG * len(PHASES)))
    time.sleep(0.8)

    rows = []
    for name, hint, expect in PHASES:
        print(">>> %-5s %s（%.0f 秒）" % (name, hint, SEG), flush=True)
        last = None
        n = palm = lifted = 0
        would = 0                 # 如果开着过滤，这一帧会不会被丢
        dxa = dya = 0.0
        moves = 0
        sizes = []
        t0 = time.monotonic()
        while time.monotonic() - t0 < SEG:
            try:
                e = next(events)
            except StopIteration:
                break
            if not e or e[0] != "touch":
                continue
            _, _t, x, y, down, size = e
            n += 1
            sizes.append(float(size))
            over = float(size) > PALM
            if over:
                would += 1
            if GUARD and over:
                palm += 1
                last = None
                continue
            if not down:
                lifted += 1
                last = None
                continue
            if last is None:
                last = (x, y)
                continue
            dx, dy = x - last[0], y - last[1]
            last = (x, y)
            dxa += dx
            dya += dy
            if dx or dy:
                moves += 1
        med = statistics.median(sizes) if sizes else 0.0
        mx = max(sizes) if sizes else 0.0
        rows.append((name, expect, n, palm, lifted, moves, dxa, dya, med, mx, would))
        print("    帧 %d   size 中位 %.4f 最大 %.4f   超阈值 %d   抬手 %d   有效 %d   dx %+.0f  dy %+.0f"
              % (n, med, mx, would, lifted, moves, dxa, dya), flush=True)

    print("\n" + "=" * 80)
    print("%-6s %-10s %5s %8s %6s %8s %9s %9s   判定"
          % ("阶段", "期望", "帧数", "size中位", "有效帧", "dx累计", "dy累计", "超阈值"))
    print("-" * 80)
    for name, expect, n, palm, lifted, moves, dxa, dya, med, mx, would in rows:
        if n == 0:
            verdict = "没收到任何触摸帧"
        elif GUARD and palm >= max(1, n // 2):
            verdict = "**被手掌过滤丢掉大部分**"
        elif lifted >= max(1, n // 2):
            verdict = "多数帧被当成抬手"
        elif moves == 0:
            verdict = "坐标完全没变化"
        elif (name == "right" and dxa <= 0) or (name == "left" and dxa >= 0) \
                or (name == "up" and dya <= 0) or (name == "down" and dya >= 0):
            verdict = "**方向相反**"
        else:
            verdict = "正常"
        print("%-6s %-10s %5d %8.4f %6d %9.0f %9.0f %8d   %s"
              % (name, expect, n, med, moves, dxa, dya, would, verdict))
    print("=" * 80)
    print("把这张表贴给我即可。")


if __name__ == "__main__":
    main()
