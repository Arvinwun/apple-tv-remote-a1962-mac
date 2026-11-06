#!/usr/bin/env python3
"""采集子进程：HID 状态轮询 + 触摸帧，用 JSON 行往标准输出吐。

为什么要单独一个进程：
  hid.enumerate() 会创建 IOHIDManager 并把它挂到当前线程的 run loop 上，而触摸回调那边
  开着另一个 CFRunLoop。两者并发时会在 __CFCheckCFInfoPACSignature 里 SIGTRAP 崩溃
  （实测：面板每次 /api/status 都调 enumerate，点几次就整个服务挂掉）。
  把它隔离到子进程，崩了父进程重拉即可，HTTP 服务不受影响。

输出：
  {"kind":"state","present":true,"collections":8}
  {"kind":"touch","x":123.4,"y":-56.7,"down":true,"size":0.0038}
"""
import ctypes
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.dirname(HERE)
sys.path.insert(0, MAC)
sys.path.insert(0, os.path.join(os.path.dirname(MAC), "open-appleremote"))

APPLE, GEN1 = 0x4C, 0x26D
NO_HID = "--no-hid" in sys.argv

_AS = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
_AS.AXIsProcessTrusted.restype = ctypes.c_bool


def trusted():
    """辅助功能授权。没有它，主程序合成的按键会被系统静默丢弃。"""
    try:
        return bool(_AS.AXIsProcessTrusted())
    except Exception:
        return None


def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def hid_loop():
    import hid
    last = None
    while True:
        try:
            cols = [d for d in hid.enumerate()
                    if d.get("vendor_id") == APPLE and d.get("product_id") == GEN1]
            st = {"kind": "state", "present": bool(cols), "collections": len(cols),
                  "trusted": trusted()}
        except Exception as e:
            st = {"kind": "state", "present": False, "collections": 0, "error": str(e),
                  "trusted": trusted()}
        if st != last:
            emit(st)
            last = st
        time.sleep(3.0)


def mt_loop():
    from live_mac import LiveMac
    live = LiveMac(enable_touch=True, enable_buttons=False, info=lambda s: None)
    for ev in live.events():
        if not ev or ev[0] != "touch":
            continue
        _, t, x, y, down, size = ev
        emit({"kind": "touch", "x": round(x, 1), "y": round(y, 1),
              "down": bool(down), "size": round(float(size), 4)})


def main():
    if not NO_HID:
        threading.Thread(target=hid_loop, daemon=True).start()
    threading.Thread(target=mt_loop, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
