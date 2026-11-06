#!/usr/bin/env python3
"""按键自检：辅助功能授权、音量两条路哪条真的有效、播放/暂停有没有可接的 App。

    .venv/bin/python mac/test_keys.py

背景：音量/播放这类是 NX_SYSDEFINED 系统定义键，CGEvent 的 keycode（72/73/100）对它们无效
（实测音量 18→18）。可用的只有两条路：NSEvent 造系统定义事件，或者 osascript 读改写。
这个脚本分别实测，并用「系统音量读数」这种可观测量判断，不靠猜。
"""
import ctypes
import subprocess
import sys
import time

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "open-appleremote"))

import siriremote as sr

AS = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
AS.AXIsProcessTrusted.restype = ctypes.c_bool


def running_media_apps():
    out = subprocess.run(["osascript", "-e",
                          'tell application "System Events" to get name of every process'],
                         capture_output=True, text=True).stdout
    names = {n.strip() for n in out.replace("\n", ",").split(",")}
    return [a for a in ("Music", "Spotify", "VLC", "IINA") if a in names]


def main():
    force = "--force" in sys.argv
    actual = bool(AS.AXIsProcessTrusted())
    trusted = actual or force
    label = "已授权" if actual else "未授权" + ("（--force 强行继续，结果不可信）" if force else "")
    print("辅助功能授权：%s\n" % label)

    if not trusted:
        print("未授权时 CGEvent / 系统定义键都会被静默丢弃。先授权：")
        print('  open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"')
        print("把 Terminal 加进去，然后**重开终端**再跑本脚本。")
        return 2

    results = []
    start = sr.volume_now()
    print("当前音量：%s\n" % start)

    # 1. NSEvent 系统定义键
    before = sr.volume_now()
    err = ""
    try:
        sr.press_system(sr.NX_SOUND_UP)
    except Exception as e:
        err = "%s: %s" % (type(e).__name__, e)
    time.sleep(0.4)
    after = sr.volume_now()
    results.append(("系统定义键 NX_SOUND_UP", "%s -> %s" % (before, after),
                    (after != before) if not err else err))
    if after != before:
        sr.volume_set(before)

    # 2. osascript 兜底
    before = sr.volume_now()
    sr.volume_set(before + 6)
    time.sleep(0.25)
    after = sr.volume_now()
    results.append(("osascript 读改写", "%s -> %s" % (before, after), after != before))
    sr.volume_set(before)

    # 3. 播放/暂停能接到谁
    apps = running_media_apps()
    results.append(("播放/暂停 可接的 App", ", ".join(apps) if apps else "没有媒体 App 在跑", None))

    print("%-24s %-24s %s" % ("项目", "结果", "判定"))
    print("-" * 66)
    for name, detail, ok in results:
        mark = "有效" if ok is True else ("无效" if ok is False else ("-" if ok is None else ok))
        print("%-24s %-24s %s" % (name, detail, mark))

    # 4. 程序实际会选哪条
    k = sr.Keys(sr.keymap(), quiet=True)
    print("\n程序启动时会选：%s" % ("系统定义键" if k.vol_mode == "system" else "osascript 兜底"))
    if k.vol_mode == "osascript":
        print("  想强制用系统定义键：SIRI_VOL=system .venv/bin/python mac/siriremote.py keys")
        print("  强制用 osascript：  SIRI_VOL=osascript .venv/bin/python mac/siriremote.py keys")

    sr.volume_set(start)
    print("\n音量已还原为 %s" % start)
    return 0


if __name__ == "__main__":
    sys.exit(main())
