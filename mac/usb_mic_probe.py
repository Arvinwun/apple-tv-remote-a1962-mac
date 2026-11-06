#!/usr/bin/env python3
"""有线模式下，12/1 到底给什么：只有按钮字节，还是带音频 payload。

之前那次探测是无线（BLE）状态，而且 12/1 被主程序占着没打开，等于没测。
这次先 stop 主程序，独占 12/1，插着 USB 读。

判据：
  - 长度恒为 2（fa 10）→ 只有按钮状态位，没有音频
  - 出现 21 或更长      → 带 Opus payload，有戏
"""
import collections
import sys
import time

import hid

SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 90
APPLE, GEN1 = 0x4C, 0x26D


def main():
    target = None
    for d in hid.enumerate():
        if (d.get("vendor_id"), d.get("product_id"),
                d.get("usage_page"), d.get("usage")) == (APPLE, GEN1, 12, 1):
            target = d
            break
    if not target:
        print("没找到 12/1 —— 遥控器可能睡着了，按一下键唤醒")
        return

    dev = hid.device()
    try:
        dev.open_path(target["path"])
    except Exception as e:
        print("打不开 12/1：%s（主程序还在跑？先停掉）" % e)
        return

    print("已独占 12/1，监听 %.0f 秒 —— 现在按住麦克风键说几句话。" % SECONDS, flush=True)
    stats = collections.Counter()
    samples = {}
    total = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < SECONDS:
        data = dev.read(64, 100)
        if not data:
            continue
        b = bytes(data)
        if not b:
            continue
        total += 1
        n = len(b)
        stats[n] += 1
        if n not in samples:
            samples[n] = b.hex()
    dev.close()

    print("\n总包数 %d，用时 %.1fs" % (total, time.monotonic() - t0))
    if not total:
        print("一个包都没有 —— 麦克风键按了吗？")
        return
    print("%-6s %6s  %s" % ("长度", "包数", "样例（完整 hex）"))
    print("-" * 70)
    for n, c in sorted(stats.items()):
        print("%-6d %6d  %s" % (n, c, samples[n]))
    print("-" * 70)
    longest = max(stats)
    if longest <= 2:
        print("结论：最长只有 %d 字节（按钮状态位），没有任何音频 payload。" % longest)
    else:
        print("结论：出现了 %d 字节的包，可能带 Opus 片段，需要进一步看结构。" % longest)


if __name__ == "__main__":
    main()
