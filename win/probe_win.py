#!/usr/bin/env python3
"""在 Windows 上探测初代 Siri Remote（A1513/A1962）能不能当输入设备。

    pip install hidapi
    python probe_win.py

它做四件事：
  1. 列出所有 Apple(0x05AC) HID 设备，看遥控器有没有出现、以什么集合出现
  2. 逐个集合打开，读 feature report 0xBB（设备信息），确认通道活着
  3. 20 秒实听：请你滑动触摸板 + 按按键，看哪个集合吐报文
  4. 给结论：按键能不能拿、触摸能不能拿

注意：Windows 上不存在 macOS 那个 AppleMultitouchDevice，
所以 Digitizer 集合有可能反而读得到（也可能被 Windows 的触摸驱动吃掉）——只能实测。
"""
import sys
import threading
import time

try:
    import hid
except ImportError:
    sys.exit("缺依赖：pip install hidapi")

APPLE, GEN1 = 0x05AC, 0x026D
UNLOCK = bytes([0xF0, 0xAF])


def main():
    rows = hid.enumerate()
    apple = [d for d in rows if d.get("vendor_id") == APPLE]
    remote = [d for d in apple if d.get("product_id") == GEN1]

    print("HID 设备 %d 个，Apple 厂商号 %d 个，其中 pid=0x026D %d 个\n" % (len(rows), len(apple), len(remote)))
    for d in apple:
        print("  vid=0x%04X pid=0x%04X usage=%s/%s  %s" % (
            d.get("vendor_id"), d.get("product_id"), d.get("usage_page"), d.get("usage"),
            (d.get("product_string") or "")[:40]))

    if not remote:
        print("\n没看到遥控器。检查：")
        print("  · 蓝牙配对了吗（设置 → 蓝牙 → 按住 Menu + 音量+ 5 秒）")
        print("  · 或者用 Lightning 线插上，看是否作为 USB HID 出现")
        return 1

    print("\n遥控器的集合，逐个试 feature report 0xBB：")
    for d in remote:
        name = "usage=%s/%s" % (d.get("usage_page"), d.get("usage"))
        dev = hid.device()
        try:
            dev.open_path(d["path"])
        except Exception as e:
            print("  %-14s 打不开: %s" % (name, e))
            continue
        try:
            try:
                r = dev.get_feature_report(0xBB, 39)
                print("  %-14s feature 0xBB -> %s" % (name, bytes(r).hex()[:60] if r else "空"))
            except Exception as e:
                print("  %-14s feature 0xBB 失败 (%s)" % (name, e))
        finally:
            dev.close()

    print("\n实听 20 秒 —— 现在开始：滑动触摸板四个方向，按 click、Menu、TV、播放、音量±")
    for n in (3, 2, 1):
        print("  %d..." % n, end="", flush=True)
        time.sleep(0.7)
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

    threads = [threading.Thread(target=worker, args=(d,), daemon=True) for d in remote]
    for t in threads:
        t.start()
    time.sleep(20)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    print("%-16s %6s  %s" % ("集合", "包数", "样例（最多 3 包）"))
    print("-" * 74)
    for name, (packets, err) in sorted(results.items(), key=lambda kv: -(len(kv[1][0] or []))):
        if packets is None:
            print("%-16s %6s  %s" % (name, "-", err))
        else:
            print("%-16s %6d  %s" % (name, len(packets),
                                     " ".join(p.hex() for p in packets[:3]) if packets else "（静默）"))

    streams = {k: v[0] for k, v in results.items() if v[0]}
    print("\n" + "=" * 62)
    if not streams:
        print("结论：Windows 上同样拿不到输入报文。")
        print("     （要么设备只走诊断通道，要么这些集合都被系统驱动占住了）")
        return 1
    best = sorted(streams.items(), key=lambda kv: -len(kv[1]))[0]
    rid = best[1][0][:1].hex()
    print("结论：%s 有数据，report ID 0x%s" % (best[0], rid.upper()))
    if rid == "fa":
        print("  -> 和 Mac 上是同一套按键协议，按键可用，我可以直接给 Windows 版驱动")
    elif rid == "fc":
        print("  -> 原版 0xFC 协议！连触摸都在里面，比 macOS 还好办")
    else:
        print("  -> 新 report ID，把这行输出发我，我按它写解析")
    return 0


if __name__ == "__main__":
    sys.exit(main())
