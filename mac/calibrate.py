#!/usr/bin/env python3
"""触摸标定：四个方向各滑一次，看手势引擎输出的方向和步数对不对，并给出参数建议。

    .venv/bin/python mac/calibrate.py

不需要辅助功能权限（不发送任何按键）。会写一份 mac/captures/touch.jsonl，
里面是原始归一化坐标，便于事后分析。

判断标准：
  · dx 的符号：右滑应该 dx > 0（遥控器坐标系 +X 向右）
  · dy 的符号：上滑应该 dy > 0（+Y 向上）
  · 步数：一次滑满触摸板，希望能出 1~3 步
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "open-appleremote"))

from live_mac import LiveMac                              # noqa: E402
from appleremote.gestures import Gestures, STEP           # noqa: E402

WAIT = float(os.environ.get("CALIB_WAIT", "12"))   # 每个阶段最多等多久等到触摸

PHASES = [
    ("right", "从触摸板左边缘滑到右边缘（慢一点，约 1 秒）"),
    ("left", "从右边缘滑到左边缘"),
    ("up", "从下边缘滑到上边缘"),
    ("down", "从上边缘滑到下边缘"),
]


def main():
    live = LiveMac(enable_touch=True)
    events = live.events()          # 只建一次：每次调用都会新起线程
    moves = []
    raw = []
    frames = []          # 当前这一笔的 (t, x, y)
    engine = Gestures(lambda kind, **f: moves.append(f["dir"]) if kind == "move" else None)

    # 预热：私有框架认设备、挂回调都会往终端打几行字，混在提示里容易让人错过第一次滑动
    print("初始化触控框架…", flush=True)
    warm = time.monotonic()
    while time.monotonic() - warm < 2.0:
        next(events)

    print("\n跟着提示滑，每次滑完手离开触摸板。全程约 20 秒。\n")
    results = []

    for name, hint in PHASES:
        moves.clear()
        frames.clear()
        print(">>> %-5s %s" % (name, hint), flush=True)
        started = time.monotonic()
        seen_down = False
        while time.monotonic() - started < WAIT:
            ev = next(events)
            if not ev:
                engine.tick()
                continue
            if ev[0] != "touch":
                continue
            _, t, x, y, down, _size = ev
            if down and not seen_down:
                seen_down = True
                print("    触摸中…", flush=True)
            if seen_down:
                raw.append({"phase": name, "t": round(t, 4), "x": round(x, 1), "y": round(y, 1),
                            "down": down})
                if down:
                    frames.append((x, y))
                engine.frame(t, x, y, down)
                if not down and len(frames) > 2:
                    break
            engine.tick()
        engine.tick()
        time.sleep(0.3)

        if len(frames) < 3:
            print("    ！没抓到有效触摸（%.0f 秒内没摸到？）\n" % WAIT)
            results.append((name, None, None, 0))
            continue
        xs = [p[0] for p in frames]
        ys = [p[1] for p in frames]
        dx, dy = xs[-1] - xs[0], ys[-1] - ys[0]
        steps = len(moves)
        print("    dx=%+.0f  dy=%+.0f  行程=%.0f  步数=%d  方向=%s\n" % (
            dx, dy, max(abs(dx), abs(dy)), steps, ",".join(moves) or "无"))
        results.append((name, dx, dy, steps))

    # ---- 写原始数据
    out = os.path.join(HERE, "captures", "touch.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        for r in raw:
            f.write(json.dumps(r) + "\n")

    # ---- 结论
    print("=" * 66)
    print("阶段   期望方向  实际       行程      步数   判定")
    print("-" * 66)
    want = {"right": (1, 0), "left": (-1, 0), "up": (0, 1), "down": (0, -1)}
    bad_sign, no_steps, ok = [], [], 0
    for name, dx, dy, steps in results:
        if dx is None:
            print("%-6s %-9s %-9s %-9s %-5s %s" % (name, "?", "-", "-", 0, "没抓到"))
            continue
        wx, wy = want[name]
        got = "dx%+.0f" % dx if wx else "dy%+.0f" % dy
        sign_ok = (dx > 0) == (wx > 0) if wx else (dy > 0) == (wy > 0)
        verdict = "OK" if sign_ok and steps > 0 else ("方向反了" if not sign_ok else "没有步进")
        if not sign_ok:
            bad_sign.append(name)
        elif steps == 0:
            no_steps.append(name)
        else:
            ok += 1
        print("%-6s %-9s %-9s %-9s %-5d %s" % (name, name, got, "%.0f" % max(abs(dx), abs(dy)), steps, verdict))

    print()
    if bad_sign:
        only_y = all(n in ("up", "down") for n in bad_sign)
        only_x = all(n in ("left", "right") for n in bad_sign)
        if only_y:
            print("=> 上下反了：把 live_mac.py 顶部的 FLIP_Y 改成 False")
        elif only_x:
            print("=> 左右反了：把 SPAN 改成负数（或告诉我，我加个 FLIP_X）")
        else:
            print("=> 两个轴都反：FLIP_Y 改 False 且 SPAN 取负")
    if no_steps:
        print("=> 有触摸但没步进：行程不够 %.0f 或太快。" % STEP)
        print("   行程只有 %.0f 就先减 STEP，比如 STEP = %d" % (
            max(max(abs(r[1]), abs(r[2])) for r in results if r[1] is not None),
            int(max(max(abs(r[1]), abs(r[2])) for r in results if r[1] is not None) / 1.5) or 100))
    if ok == len(PHASES):
        print("=> 四个方向全对，可以直接开始用：.venv/bin/python mac/siriremote.py keys")
    print("\n原始数据：%s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
