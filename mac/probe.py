#!/usr/bin/env python3
"""探针：遥控器有 9 个 HID 集合，找出哪个承载 0xFC 报文。

对每个 pid=0x026D 的集合：打开 -> 发解锁 feature report (F0 AF) -> 读若干秒 -> 打印收到的字节。
初代遥控器解锁后会回一个包（原版样例录制第一条就是这个 "unlock reply"），所以不用碰它也能判断。

    .venv/bin/python mac/probe.py                 # 每集合静听 1.5 s
    .venv/bin/python mac/probe.py --seconds 8     # 静听久一点
    .venv/bin/python mac/probe.py --touch         # 边听边请你戳触摸板/按按钮（最可靠的判定）
"""
import argparse
import sys
import time

import hid

APPLE, GEN1 = 0x004C, 0x026D
UNLOCK = bytes([0xF0, 0xAF])


def read_for(dev, seconds, echo=False):
    got, t0 = [], time.monotonic()
    while time.monotonic() - t0 < seconds:
        data = dev.read(64, 100)
        if data:
            got.append(bytes(data))
            if echo:
                print("    " + bytes(data).hex(), flush=True)
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=1.5, help="每个集合静听多久（默认 1.5 s）")
    ap.add_argument("--touch", action="store_true", help="边听边等你操作遥控器")
    args = ap.parse_args()

    cands = [d for d in hid.enumerate() if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
    if not cands:
        print("没找到 pid=0x026D 的设备（遥控器睡了？按一下唤醒）")
        return 1
    if args.touch:
        print("接下来每个集合会听 %.0f 秒 —— 请反复滑动触摸板、按两下 click。\n" % args.seconds)
    print("找到 %d 个集合：\n" % len(cands))

    hits = []
    for d in cands:
        label = "usage=%s/%s" % (d.get("usage_page"), d.get("usage"))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            print("%-16s 打不开: %s" % (label, e))
            continue
        try:
            try:
                sent = dev.send_feature_report(UNLOCK)
            except Exception as e:
                print("%-16s 打开成功，解锁失败: %s" % (label, e))
                continue
            got = read_for(dev, args.seconds, echo=args.touch)
            print("%-16s 解锁写入 %s 字节，收 %d 包%s" % (
                label, sent, len(got), "：" + " ".join(x.hex() for x in got[:3]) if got else "（静默）"))
            if got:
                hits.append((d, got))
        finally:
            try:
                dev.close()
            except Exception:
                pass

    print()
    if hits:
        hits.sort(key=lambda h: -len(h[1]))
        d, got = hits[0]
        print("报文最多的集合：usage=%s/%s，%d 包" % (d.get("usage_page"), d.get("usage"), len(got)))
        print("样例：", got[0].hex())
        print("=> 是它" if got[0][:1] == b"\xfc" else "=> 但报文头不是 0xFC，可能不是这一层")
    else:
        print("全部静默。两个可能：解锁报文在 macOS 上不适用，或这层被系统 HID 驱动吃掉了。")
        print("先用 --touch 复测一次，确认是真的没数据，而不是遥控器在发呆：")
        print("  .venv/bin/python mac/probe.py --touch --seconds 6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
