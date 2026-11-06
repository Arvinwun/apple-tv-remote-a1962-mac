#!/usr/bin/env python3
"""触摸面积探针：看遥控器触摸板到底给不给接触面积。

    .venv/bin/python mac/area_probe.py

分两个阶段，各 4 秒：
  ① 只用指尖轻轻点住（接触面越小越好）
  ② 用整个拇指肚按上去（尽量盖满）

如果两个阶段的 size / major_axis 明显不同，说明系统在算面积，可以用来做
「按压力度」「手掌误触过滤」这类功能；如果都是 0 或完全一样，就是没这个数据。
"""
import ctypes
import statistics
import sys
import time

MT_PATH = "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"


class Finger(ctypes.Structure):
    _fields_ = [
        ("frame", ctypes.c_int), ("timestamp", ctypes.c_double), ("identifier", ctypes.c_int),
        ("state", ctypes.c_int), ("foo3", ctypes.c_int), ("foo4", ctypes.c_int),
        ("normalized_x", ctypes.c_float), ("normalized_y", ctypes.c_float), ("size", ctypes.c_float),
        ("zero1", ctypes.c_int), ("angle", ctypes.c_float), ("major_axis", ctypes.c_float),
        ("minor_axis", ctypes.c_float), ("zero2", ctypes.c_int * 2), ("density", ctypes.c_float),
    ]


CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(Finger), ctypes.c_int,
                            ctypes.c_double, ctypes.c_int)

SAMPLES = []          # (phase, size, major, minor, density)
PHASE = ["warmup"]


def main():
    mt = ctypes.CDLL(MT_PATH)
    CF = ctypes.CDLL(CF_PATH)
    mt.MTDeviceCreateList.restype = ctypes.c_void_p
    mt.MTDeviceStart.argtypes = [ctypes.c_void_p, ctypes.c_int]
    mt.MTDeviceIsBuiltIn.argtypes = [ctypes.c_void_p]
    mt.MTDeviceIsBuiltIn.restype = ctypes.c_bool
    mt.MTRegisterContactFrameCallback.argtypes = [ctypes.c_void_p, CALLBACK]
    CF.CFArrayGetCount.restype = ctypes.c_long
    CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]

    def on_frame(dev, fingers, count, ts, frame_no):
        if count <= 0:
            return 0
        f = fingers[0]
        # 只留「真的摸在表面上」的帧：坐标必须在 0..1，且不是抬手态。
        # 不这样的话会混进大量悬停噪声，读出来的 size 是负的（实测见过 -2.03）。
        if not (0.0 <= f.normalized_x <= 1.0 and 0.0 <= f.normalized_y <= 1.0):
            return 0
        if f.state == 7:
            return 0
        SAMPLES.append((PHASE[0], f.size, f.major_axis, f.minor_axis, f.density))
        if len(SAMPLES) % 12 == 0:
            print("  [%s] size=%.4f  major=%.4f  minor=%.4f  density=%.4f" % (
                PHASE[0], f.size, f.major_axis, f.minor_axis, f.density), flush=True)
        return 0

    cb = CALLBACK(on_frame)
    arr = mt.MTDeviceCreateList()
    n = CF.CFArrayGetCount(arr)
    kept = 0
    kept_refs = []
    for i in range(n):
        devp = CF.CFArrayGetValueAtIndex(arr, i)
        if mt.MTDeviceIsBuiltIn(devp):
            continue
        kept_refs.append(devp)
        mt.MTRegisterContactFrameCallback(devp, cb)
        mt.MTDeviceStart(devp, 0)
        kept += 1
    if not kept:
        print("没找到遥控器的触摸设备（按一下遥控器唤醒？）")
        return 1

    CF.CFRunLoopGetCurrent.restype = ctypes.c_void_p
    CF.CFStringCreateWithCString.restype = ctypes.c_void_p
    CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    mode = CF.CFStringCreateWithCString(None, b"kCFRunLoopDefaultMode", 0x08000100)
    CF.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]

    print("设备已挂好，先空跑 1 秒…\n")
    PHASE[0] = "warmup"
    end = time.monotonic() + 1
    while time.monotonic() < end:
        CF.CFRunLoopRunInMode(mode, 0.1, False)

    for tag, hint in (("tip", "只用指尖，轻轻点住触摸板中心"),
                      ("pad", "整个拇指肚按上去，尽量盖满触摸板")):
        PHASE[0] = tag
        SAMPLES.clear() if tag == "tip" else None
        print(">>> %s：%s（4 秒）" % (tag, hint), flush=True)
        end = time.monotonic() + 4
        while time.monotonic() < end:
            CF.CFRunLoopRunInMode(mode, 0.2, False)
        vals = [s for s in SAMPLES if s[0] == tag]
        if vals:
            print("    收到 %d 帧\n" % len(vals), flush=True)
        else:
            print("    没收到任何帧（这 4 秒没摸到？）\n", flush=True)

    groups = {}
    for ph, size, major, minor, dens in SAMPLES:
        groups.setdefault(ph, []).append((size, major, minor, dens))

    print("=" * 64)
    if not groups:
        print("没采到数据。")
        return 1
    print("%-6s %8s %8s %8s %8s %6s" % ("阶段", "size均", "major均", "minor均", "density", "帧数"))
    means = {}
    for ph, rows in groups.items():
        size = statistics.mean(r[0] for r in rows)
        major = statistics.mean(r[1] for r in rows)
        minor = statistics.mean(r[2] for r in rows)
        dens = statistics.mean(r[3] for r in rows)
        means[ph] = size
        print("%-6s %8.4f %8.4f %8.4f %8.4f %6d" % (ph, size, major, minor, dens, len(rows)))

    print()
    valid = [r for rows in groups.values() for r in rows if 0.0 <= r[0] <= 1.0]
    total = sum(len(rows) for rows in groups.values())
    print("size 落在 0~1 的帧：%d / %d" % (len(valid), total))
    if len(means) >= 2 and all(v > 0 for v in means.values()):
        a, b = means.get("tip", 0), means.get("pad", 0)
        ratio = (b / a) if a else 0
        print("指尖 / 拇指肚 的 size 比值：%.2f" % ratio)
        if ratio >= 1.25:
            print("=> 能区分接触面积，可以用它做「按压力度」或误触过滤。")
        else:
            print("=> 有数值但两者差别不大，多半是驱动给的固定估值，实用性有限。")
    else:
        print("=> size 全为 0（或缺失）：这块触摸板不提供接触面积，只能拿到坐标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
