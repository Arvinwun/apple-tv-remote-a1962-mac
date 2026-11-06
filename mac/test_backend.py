#!/usr/bin/env python3
"""合成报文自测：不接遥控器，也不真的敲键盘，验证 解码 -> 手势 -> 键位 整条链路。

    .venv/bin/python mac/test_backend.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "open-appleremote"))

import siriremote as sr                                    # noqa: E402
from appleremote.decode import Decoder, Touch, Buttons     # noqa: E402
from appleremote.gestures import Gestures                  # noqa: E402


def touch_report(x, y, clock=0, state=5, buttons=0):
    """一帧触摸报文：fc 01 BB 32 CC CC SS + 一个 7 字节手指记录。x/y 是遥控器的坐标（约 ±1650）。"""
    fx, fy = x & 0xFF, y & 0xFF
    f1 = ((x >> 8) & 0x0F) | ((y & 0x0F) << 4)
    f2 = (y >> 4) & 0xFF
    finger = bytes([fx, f1, f2, 0x20, 0x0A, 0x17, state & 7])
    return bytes([0xFC, 0x01, buttons, 0x32, clock & 0xFF, (clock >> 8) & 0xFF, 0x00]) + finger


def button_report(mask):
    return bytes([0xFC, 0x00, mask])


class Harness:
    """把报告喂进和 siriremote.py 完全相同的 decoder + gestures + printer 管线，收下发出的按键。"""

    def __init__(self):
        self.sent, self.events = [], []
        self.now = [1000.0]
        self.keys = sr.Keys(sr.keymap(), dry_run=True)
        self.keys.press = lambda code, hold=0: self.sent.append(code)
        self.out = sr.printer({"touch", "buttons", "voicechunk"}, keys=self.keys)
        self.engine = Gestures(self.out, clock=lambda: self.now[0])
        self.decoder = Decoder()

    def feed(self, report, dt=0.01):
        for ev in self.decoder(report):
            if isinstance(ev, Touch):
                self.engine.frame(ev.t, ev.x, ev.y, ev.down)
            elif isinstance(ev, Buttons):
                self.out("buttons", **{k: v for k, v in ev._asdict().items() if k != "raw"})
        self.engine.tick()
        self.now[0] += dt


def swipe(h, start, step, n, dt=0.01, clock0=0):
    x0, y0 = start
    dx, dy = step
    for i in range(n):
        h.feed(touch_report(x0 + dx * i, y0 + dy * i, clock=clock0 + int(i * dt * 2000)), dt)
    h.feed(touch_report(x0 + dx * n, y0 + dy * n, clock=clock0 + int(n * dt * 2000), state=7), dt)


def main():
    C = sr.CODES
    failures = []

    def check(name, got, want):
        ok = got == want
        print(("  ok   " if ok else "  FAIL ") + name, "->", got, "" if ok else "(期望 %s)" % (want,))
        if not ok:
            failures.append(name)

    # 1. 静止手指：不该产生任何按键
    h = Harness()
    for i in range(60):
        h.feed(touch_report(20, 10, clock=int(i * 20)))
    check("静止手指无输出", h.sent, [])

    # 2. 向右滑：方向键 右 (124)
    h = Harness()
    swipe(h, (0, 0), (18, 0), 90)
    check("右滑 -> 右方向键", h.sent, [C["right"]] * 2)

    # 3. 向下滑：方向键 下 (125)
    h = Harness()
    swipe(h, (0, 600), (0, -18), 90)
    check("下滑 -> 下方向键", h.sent, [C["down"]] * 2)

    # 4. click 按下：回车 (36)，且按住时只发一次
    h = Harness()
    h.feed(button_report(0x80))
    h.feed(button_report(0x80))
    h.feed(button_report(0x00))
    check("click -> 回车（按住不连发）", h.sent, [C["return"]])

    # 5. 其余按钮
    # 播放键默认映射成回车；音量走系统定义键通道（dry-run 下不发，所以预期为空）
    for mask, key, label in ((0x20, "escape", "menu"), (0x01, None, "tv"),
                             (0x08, "return", "playpause"), (0x02, "pageup", "volup"),
                             (0x04, "pagedown", "voldown")):
        h = Harness()
        h.feed(button_report(mask))
        h.feed(button_report(0x00))
        check("%s -> %s" % (label, key or "系统通道"), h.sent, [C[key]] if key else [])

    # 6. siri 默认不映射
    h = Harness()
    h.feed(button_report(0x10))
    h.feed(button_report(0x00))
    check("siri 默认不发声", h.sent, [])

    # 7. 断线时释放按住的键（decoder 收到 b"" 会补一个 release 事件）
    h = Harness()
    h.feed(button_report(0x80))
    h.feed(b"")
    check("断线不重复发键", h.sent, [C["return"]])

    # 8. 自定义键位覆盖 + null 关闭
    m = sr.keymap({"click": "space", "tv": None})
    check("click 改空格", m["click"], sr.CODES["space"])
    check("tv 关闭", m.get("tv"), None)

    print("\n%s" % ("全部通过" if not failures else "%d 项失败: %s" % (len(failures), failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
