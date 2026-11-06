"""诊断：权限到底有没有生效。

由面板用**和启动主程序完全相同的参数**拉起（同一父进程、同一解释器），
这样结论才有可比性 —— 直接区分「没权限」和「触摸没数据」两类故障。

输出一行 JSON 到 stdout。
"""
import ctypes
import json
import time

AS = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
AS.AXIsProcessTrusted.restype = ctypes.c_bool


def main():
    out = {"trusted": None, "mouse_moved": None, "moved_px": None, "error": None}
    try:
        out["trusted"] = bool(AS.AXIsProcessTrusted())

        from Quartz import (CGEventCreate, CGEventCreateMouseEvent, CGEventGetLocation,
                            CGEventPost, kCGHIDEventTap, kCGEventMouseMoved)

        def cursor():
            loc = CGEventGetLocation(CGEventCreate(None))
            return float(loc.x), float(loc.y)

        x0, y0 = cursor()
        out["from"] = [round(x0), round(y0)]

        # 往右推 40px，等系统处理，再读回
        CGEventPost(kCGHIDEventTap,
                    CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x0 + 40, y0), 0))
        time.sleep(0.3)
        x1, _ = cursor()
        out["moved_px"] = round(x1 - x0, 1)
        out["mouse_moved"] = abs(x1 - x0) > 5

        # 还原（不管有没有权限都试一次，失败也无副作用）
        CGEventPost(kCGHIDEventTap,
                    CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x0, y0), 0))
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, e)
    print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
