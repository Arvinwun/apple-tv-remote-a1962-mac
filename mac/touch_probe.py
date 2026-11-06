#!/usr/bin/env python3
"""触摸探针：用私有 MultitouchSupport 框架读遥控器的触摸表面（macOS 把它当成一个多点触控设备接管了）。

    .venv/bin/python mac/touch_probe.py            # 列设备 + 听 15 秒
    .venv/bin/python mac/touch_probe.py --seconds 25

跑起来后请反复滑动触摸板：上、下、左、右各滑几次，中间停一下，再按几下 click。
输出里能看到 normalized_x/y 变化就说明拿到了原始触摸数据。
"""
import argparse
import ctypes
import sys
import time

MT_PATH = "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
CF = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


class Finger(ctypes.Structure):
    _fields_ = [
        ("frame", ctypes.c_int),
        ("timestamp", ctypes.c_double),
        ("identifier", ctypes.c_int),
        ("state", ctypes.c_int),
        ("foo3", ctypes.c_int),
        ("foo4", ctypes.c_int),
        ("normalized_x", ctypes.c_float),
        ("normalized_y", ctypes.c_float),
        ("size", ctypes.c_float),
        ("zero1", ctypes.c_int),
        ("angle", ctypes.c_float),
        ("major_axis", ctypes.c_float),
        ("minor_axis", ctypes.c_float),
        ("zero2", ctypes.c_int * 2),
        ("density", ctypes.c_float),
    ]


CALLBACK = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(Finger), ctypes.c_int,
                            ctypes.c_double, ctypes.c_int)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15)
    args = ap.parse_args()

    mt = ctypes.CDLL(MT_PATH)
    mt.MTDeviceCreateList.restype = ctypes.c_void_p
    mt.MTDeviceStart.argtypes = [ctypes.c_void_p, ctypes.c_int]
    mt.MTDeviceIsBuiltIn.argtypes = [ctypes.c_void_p]
    mt.MTDeviceIsBuiltIn.restype = ctypes.c_bool
    mt.MTDeviceGetDeviceID.argtypes = [ctypes.c_void_p]
    mt.MTDeviceGetDeviceID.restype = ctypes.c_uint64
    mt.MTRegisterContactFrameCallback.argtypes = [ctypes.c_void_p, CALLBACK]
    for name in ("MTDeviceCopyName", "MTDeviceGetFamilyID", "MTDeviceGetTransport"):
        if hasattr(mt, name):
            getattr(mt, name).argtypes = [ctypes.c_void_p]

    # CFArray 里的设备
    cfarray = mt.MTDeviceCreateList()
    CF.CFArrayGetCount.restype = ctypes.c_long
    CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
    CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
    CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
    n = CF.CFArrayGetCount(cfarray)
    devices = [CF.CFArrayGetValueAtIndex(cfarray, i) for i in range(n)]
    print("MultitouchSupport 找到 %d 个设备：" % n)
    for i, d in enumerate(devices):
        builtin = mt.MTDeviceIsBuiltIn(d)
        print("  #%d  deviceID=0x%X  内置=%s" % (i, mt.MTDeviceGetDeviceID(d), builtin))

    frames = []

    def on_frame(dev, fingers, count, timestamp, frame_no):
        items = []
        for i in range(count):
            f = fingers[i]
            items.append((round(f.normalized_x, 3), round(f.normalized_y, 3), f.state, round(f.size, 2)))
        frames.append((frame_no, count, items))
        if len(frames) <= 60:                       # 只打印前 60 帧，后面靠汇总
            print("frame %5d  %d 指  %s" % (frame_no, count,
                                            "  ".join("(%.3f,%.3f s=%d size=%.2f)" % it for it in items)),
                  flush=True)
        return 0

    cb = CALLBACK(on_frame)
    for d in devices:
        mt.MTRegisterContactFrameCallback(d, cb)
        mt.MTDeviceStart(d, 0)

    print("\n监听 %.0f 秒 —— 现在请滑动触摸板（四个方向各两次），然后按两下 click\n" % args.seconds, flush=True)

    # 需要跑 CFRunLoop 才能收到回调
    CF.CFRunLoopGetCurrent.restype = ctypes.c_void_p
    CF.CFStringCreateWithCString.restype = ctypes.c_void_p
    CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    mode = CF.CFStringCreateWithCString(None, b"kCFRunLoopDefaultMode", 0x08000100)
    CF.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        CF.CFRunLoopRunInMode(mode, min(0.5, end - time.monotonic()), False)

    print("\n共收到 %d 帧" % len(frames))
    if not frames:
        print("没收到任何触摸帧。可能原因：")
        print("  · 这个设备不给第三方回调（需要输入监控权限，或系统只给 AppleMultitouchDeviceUserClient）")
        print("  · 遥控器的触摸表面此刻没在跑（插着 USB 充电时 BLE 链路可能被暂停）")
        return 1
    xs = [it[0] for _, _, items in frames for it in items]
    ys = [it[1] for _, _, items in frames for it in items]
    print("x 范围 %.3f ~ %.3f   y 范围 %.3f ~ %.3f" % (min(xs), max(xs), min(ys), max(ys)))
    print("=> 拿到了归一化坐标，可以直接喂给 gestures.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
