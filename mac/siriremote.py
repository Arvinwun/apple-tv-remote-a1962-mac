#!/usr/bin/env python3
"""Siri Remote -> macOS 输入设备。

复用 open-appleremote 的协议解码（decode.py）和手势引擎（gestures.py），
只把两头换掉：
    读  /sys/class/hidraw + ioctl(F0 AF)   ->  hidapi / IOHIDManager + send_feature_report
    写  /dev/uinput 虚拟键盘               ->  CGEvent 合成按键

用法:
    python siriremote.py scan                 # 看 macOS 有没有认出遥控器
    python siriremote.py watch                # 只打印解码事件，不按键
    python siriremote.py keys                 # 手势/按钮 -> 方向键等（需要辅助功能授权）
    python siriremote.py keys --dry-run       # 不真的发键，只打印
    python siriremote.py keys --map my.json   # 自定义键位
    ... 以上都支持 --replay tests/sample.jsonl
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(os.path.dirname(HERE), "open-appleremote")
sys.path.insert(0, REPO)

from appleremote import device as _device              # noqa: E402  (只为了 replay())
from appleremote.decode import Decoder, Touch, Buttons, VoiceChunk  # noqa: E402
from appleremote.gestures import Gestures              # noqa: E402

APPLE = 0x004C
MODELS = {
    0x026D: "gen-1 Siri Remote (A1513 / A1962)  <- 本项目支持",
    0x0221: "2/3 代 Siri Remote (A2540 / A2854)",
}
UNLOCK = bytes([0xF0, 0xAF])   # feature report: 让初代遥控器真正开始发触摸/按键报文


# ---------------------------------------------------------------- 读：hidapi

def enumerate_hid():
    import hid
    return hid.enumerate()


def find_remote():
    """返回 (path, pid, name)；没找到返回 None。只看 Apple 厂商号的 HID 设备。"""
    cands = []
    for d in enumerate_hid():
        if d.get("vendor_id") != APPLE:
            continue
        pid = d.get("product_id")
        name = d.get("product_string") or ""
        cands.append((d["path"], pid, name, d.get("usage_page"), d.get("usage")))
    # 优先已知的遥控器 PID，其次任何 Apple 的非键盘 HID
    known = [c for c in cands if c[1] in MODELS]
    return (known or cands or [None])[0]


class Live:
    """从 hidapi 读报文，产出 (t, report) / (t, None) 空闲 / (t, b"") 断线，与 Linux 版 reports() 同构。"""

    def __init__(self, path=None, idle=0.01, info=print):
        self.path, self.idle, self.say = path, idle, info
        self.last_note = None

    def note(self, msg):
        if msg != self.last_note:
            self.last_note = msg
            self.say(msg)

    def reports(self):
        import hid
        while True:
            path = self.path
            if not path:
                found = find_remote()
                path = found[0] if found else None
            if not path:
                self.note("等待遥控器：先配对，或按任意键唤醒")
                time.sleep(1)
                yield time.monotonic(), None
                continue
            dev = hid.device()
            try:
                dev.open_path(path)
            except Exception as e:
                self.note("打不开 %s: %s" % (path, e))
                time.sleep(2)
                yield time.monotonic(), None
                continue
            try:
                self.note("已连接: %s" % (path.decode(errors="replace") if isinstance(path, bytes) else path))
                try:
                    n = dev.send_feature_report(UNLOCK)
                    self.note("解锁完成（feature report F0 AF，写入 %s 字节）" % n)
                except Exception as e:
                    self.note("解锁失败: %s" % e)
                while True:
                    data = dev.read(64, 10)      # 10 ms 超时 = 空闲节拍，手势引擎靠它滑行
                    if data:
                        yield time.monotonic(), bytes(data)
                    else:
                        yield time.monotonic(), None
            except Exception as e:
                self.note("断开 (%s)：按一下遥控器唤醒" % e)
                yield time.monotonic(), b""
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
            time.sleep(0.5)


# ---------------------------------------------------------------- 写：CGEvent

# 键位与 Linux 版 keys.DEFAULT 一一对应：滑动/快滑 -> 方向键，click -> 回车，menu -> Esc ...
# 值可以是：
#   CODES 里的键名（"return" / "escape"）、CGKeyCode 数字、
#   "sys:xxx"（走系统定义键通道，只有音量/静音/播放这类需要）、None（关闭）
# 播放键默认给回车（用户指定）；想换回媒体键：--map '{"playpause": "sys:playpause"}'
DEFAULT = {"up": "up", "down": "down", "left": "left", "right": "right", "click": "return",
           "menu": "escape", "tv": "apps:ChatGPT,WorkBuddy,Claude", "playpause": "return", "volup": "pageup",
           "voldown": "pagedown", "siri": None}

# CGKeyCode（US 布局）
CODES = {
    "up": 126, "down": 125, "left": 123, "right": 124,
    "return": 36, "enter": 76, "escape": 53, "space": 49, "tab": 48, "delete": 51, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121, "playpause": 100,          # F8，Mac 上就是播放/暂停
    "volup": 72, "voldown": 73, "mute": 74,                    # kVK_VolumeUp/Down/Mute
}
CODES.update({c: code for c, code in zip("abcdefghijklmnopqrstuvwxyz", (0, 11, 8, 2, 14, 3, 5, 4, 34, 38, 40, 37, 46,
                                                                      45, 31, 35, 12, 15, 1, 17, 32, 9, 13, 7, 16, 6))})


def keymap(overrides=None):
    m = dict(DEFAULT)
    m.update(overrides or {})
    out = {}
    for name, key in m.items():
        if name not in DEFAULT:
            raise ValueError("未知的遥控器输入 %r，可用：%s" % (name, ", ".join(DEFAULT)))
        if key is None:
            continue
        if isinstance(key, int):
            out[name] = key
        elif isinstance(key, str) and key.startswith("apps:"):
            out[name] = key
        elif isinstance(key, str) and key.startswith("sys:"):
            if key[4:] not in SYSTEM_KEYS:
                raise ValueError("%r 不是系统定义键（可用：%s）" % (key, ", ".join(SYSTEM_KEYS)))
            out[name] = key
        elif key in CODES:
            out[name] = CODES[key]
        else:
            raise ValueError("未知键名 %r（用 up/return/escape/F1 这种，或直接写 CGKeyCode 数字）" % key)
    return out


# NX_SYSDEFINED 的键值（IOKit/hidsystem/ev_keymap.h）：这类媒体键不走 CGKeyCode，
# 必须用 NSEvent.otherEvent 造一个 subtype=8 的系统定义事件再 post。实测 CGEvent 的 72/73 对音量无效。
NX_SOUND_UP, NX_SOUND_DOWN, NX_MUTE, NX_PLAY = 0, 1, 7, 16
SYSTEM_KEYS = {"volup": NX_SOUND_UP, "voldown": NX_SOUND_DOWN, "mute": NX_MUTE, "playpause": NX_PLAY}


# 修饰键的 CGKeyCode 与对应 flag：模拟「双击修饰键」触发系统听写
MODIFIERS = {
    "option": (61, 0x80000),      # kVK_RightOption, kCGEventFlagMaskAlternate
    "control": (59, 0x40000),     # kVK_Control,     kCGEventFlagMaskControl
    "fn": (63, 0x800000),         # kVK_Function,    kCGEventFlagMaskSecondaryFn
    "command": (54, 0x100000),    # kVK_Command,     kCGEventFlagMaskCommand
}


MOD_FLAGS = {"cmd": 0x100000, "command": 0x100000, "opt": 0x80000, "option": 0x80000,
             "ctrl": 0x40000, "control": 0x40000, "shift": 0x20000}


def post_modifier(name, down):
    """按下/松开一个修饰键（长按语音输入用）。"""
    from Quartz import CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags, kCGHIDEventTap
    code, flag = MODIFIERS[name]
    ev = CGEventCreateKeyboardEvent(None, code, down)
    CGEventSetFlags(ev, flag if down else 0)
    CGEventPost(kCGHIDEventTap, ev)


def post_combo(spec):
    """发一个组合键，如 "cmd-d"、"cmd-shift-v"。"""
    from Quartz import CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags, kCGHIDEventTap
    parts = spec.split("-")
    key = parts[-1]
    code = CODES.get(key)
    if code is None:
        raise ValueError("未知键名 %r（组合键写法如 cmd-d）" % key)
    flags = 0
    for m in parts[:-1]:
        if m not in MOD_FLAGS:
            raise ValueError("未知修饰键 %r" % m)
        flags |= MOD_FLAGS[m]
    for down in (True, False):
        ev = CGEventCreateKeyboardEvent(None, code, down)
        CGEventSetFlags(ev, flags if down else 0)
        CGEventPost(kCGHIDEventTap, ev)
        if down:
            time.sleep(0.01)


def press_modifier_twice(name, gap=0.09):
    """连按两次修饰键。macOS 的听写快捷键（默认「按 Control 两次」）认的是这个动作。"""
    from Quartz import (CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
                        kCGHIDEventTap)
    code, flag = MODIFIERS[name]
    for _ in range(2):
        for down in (True, False):
            ev = CGEventCreateKeyboardEvent(None, code, down)
            CGEventSetFlags(ev, flag if down else 0)
            CGEventPost(kCGHIDEventTap, ev)
        time.sleep(gap)


def press_system(nx_key, hold=0.01):
    """发一个系统定义键（音量/播放暂停）。需要辅助功能授权，否则被系统静默丢弃。"""
    from AppKit import NSEvent
    from Quartz import CGEventPost, kCGHIDEventTap
    flags = 0xA00
    data1 = (nx_key << 16) | (0xA << 8)
    for data2 in (0xA, 0xB):                     # 0xA 按下，0xB 抬起
        ev = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
            14, (0.0, 0.0), flags, 0.0, 0, None, 8, data1, data2)
        if ev is None:
            raise RuntimeError("NSEvent.otherEvent 返回 None")
        cg = ev.CGEvent() if hasattr(ev, "CGEvent") else ev.cgEvent()   # pyobjc 里属性名是 CGEvent
        CGEventPost(kCGHIDEventTap, cg)
        if data2 == 0xA:
            time.sleep(hold)


def volume_now():
    import subprocess
    out = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"],
                         capture_output=True, text=True).stdout.strip()
    return int(out) if out.isdigit() else None


def volume_set(v):
    import subprocess
    subprocess.run(["osascript", "-e", "set volume output volume %d" % max(0, min(100, int(v)))],
                   capture_output=True)


class Keys:
    def __init__(self, mapping, dry_run=False, quiet=False, siri_action="hold-option",
                 voice_model="base", voice_lang="zh"):
        self.map, self.dry, self.quiet = mapping, dry_run, quiet
        self.siri_action = siri_action or "off"
        self.voice_model, self.voice_lang = voice_model, voice_lang
        self._siri_held = None
        self._voice = None
        self._voice_on = False
        self.vol_mode = "dry"
        self._last_vol = 0.0
        if not dry_run:
            from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
            self._make, self._post, self._tap = CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
            self.vol_mode = self._detect_volume_mode()

    def _detect_volume_mode(self):
        """量一下系统定义键能不能真的改音量：试三次，都不动就退到 osascript。

        环境变量 SIRI_VOL=system|osascript 可以跳过检测（怀疑检测误判时用）。
        """
        forced = os.environ.get("SIRI_VOL", "").strip()
        if forced in ("system", "osascript"):
            return forced
        before = volume_now()
        if before is None:
            return "osascript"
        for _ in range(3):
            try:
                press_system(NX_SOUND_UP)
            except Exception:
                return "osascript"
            time.sleep(0.3)
            if volume_now() != before:
                volume_set(before)          # 还原，别让自检把音量顶上去
                return "system"
        return "osascript"

    def _vol_step(self, delta):
        """osascript 兜底：读-改-写，带 120 ms 节流，避免按键重复时刷出一堆 osascript 进程。"""
        now = time.monotonic()
        if now - self._last_vol < 0.12:
            return
        self._last_vol = now
        v = volume_now()
        if v is not None:
            volume_set(v + delta)

    @staticmethod
    def _playpause_script():
        """播放/暂停的兜底：只对正在运行的媒体 App 发，绝不把没开的 App 拉起来。"""
        import subprocess
        for app in ("Music", "Spotify", "VLC", "IINA"):
            script = ('if application "%s" is running then\n'
                      '  tell application "%s" to playpause\n'
                      'end if' % (app, app))
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if r.returncode == 0 and r.stdout.strip():
                return app
        return None

    def _voice_input(self):
        """按住说话用的录音器。延迟到第一次用时才 import，避免没装依赖就起不来。"""
        if self._voice is None:
            from voice import VoiceInput
            self._voice = VoiceInput(
                model_size=self.voice_model, lang=self.voice_lang,
                info=lambda m: print("[voice] " + m, file=sys.stderr, flush=True),
            )
            self._voice.preload()
        return self._voice

    def siri_down(self):
        act = self.siri_action
        if act == "off":
            return
        if act == "voice":
            ok = True
            if not self.dry:
                ok = self._voice_input().start()
            if ok:
                self._voice_on = True
                via = "voice"
            else:
                # 麦克风打不开就退回「按住 Option」，别让这个键直接失效
                print("麦克风不可用，本次退回按住 Option", file=sys.stderr)
                if not self.dry:
                    post_modifier("option", True)
                self._siri_held = "option"
                via = "voice->hold-option"
        elif act.startswith("hold-"):
            mod = act[5:]
            if mod not in MODIFIERS:
                print("未知修饰键 %r（hold-option / hold-control / hold-fn）" % mod, file=sys.stderr)
                return
            if not self.dry:
                post_modifier(mod, True)
            self._siri_held = mod
            via = act
        elif act.startswith("double-"):
            if not self.dry:
                press_modifier_twice(act[7:])
            via = act
        elif act.startswith("key:"):
            code = CODES.get(act[4:])
            if code is None:
                print("未知键名 %r（--siri-action key:xxx）" % act[4:], file=sys.stderr)
                return
            if not self.dry:
                self.press(code)
            via = act
        else:                                        # 组合键，如 cmd-d
            if not self.dry:
                try:
                    post_combo(act)
                except ValueError as e:
                    print(str(e), file=sys.stderr)
                    return
            via = act
        if not self.quiet:
            print(json.dumps({"key": "siri", "via": via, "sent": not self.dry}, ensure_ascii=False), flush=True)

    def siri_up(self):
        if self._voice_on:
            self._voice_on = False
            if self._voice is not None:
                self._voice.stop()
        if self._siri_held:
            if not self.dry:
                post_modifier(self._siri_held, False)
            self._siri_held = None

    def release(self, name):
        """按键抬起：只有长按类动作需要处理。"""
        if name == "siri":
            self.siri_up()

    def press(self, code, hold=0.008):
        if self.dry:
            return
        for down in (True, False):
            ev = self._make(None, code, down)
            self._post(self._tap, ev)
            if down:
                time.sleep(hold)

    def emit(self, name):
        if name == "siri":
            self.siri_down()
            return
        code = self.map.get(name)
        if code is None:
            return
        if isinstance(code, str) and code.startswith("apps:"):
            names = [x.strip() for x in code[5:].split(",") if x.strip()]
            got = None if self.dry else cycle_apps(names)
            if not self.quiet:
                print(json.dumps({"key": name, "apps": names, "activated": got,
                                  "sent": not self.dry}, ensure_ascii=False), flush=True)
            return
        if isinstance(code, str) and code.startswith("sys:"):
            self._system_key(code[4:], name)
            return
        self.press(code)
        if not self.quiet:
            print(json.dumps({"key": name, "code": code, "sent": not self.dry}, ensure_ascii=False), flush=True)

    def _system_key(self, sysname, name):
        """走 NX_SYSDEFINED 通道的键：音量、静音、播放暂停。"""
        via = "sys"
        if self.dry:
            via = "sys(dry)"
        elif self.vol_mode == "system":
            press_system(SYSTEM_KEYS[sysname])
        elif sysname in ("volup", "voldown"):
            self._vol_step(6 if sysname == "volup" else -6)
            via = "osascript"
        elif sysname == "mute":
            v = volume_now()
            if v is not None:
                volume_set(0 if v > 0 else 50)
            via = "osascript"
        else:                                   # playpause
            app = self._playpause_script()
            via = "app:" + app if app else "none"
        if not self.quiet:
            print(json.dumps({"key": name, "via": via, "sent": not self.dry}, ensure_ascii=False), flush=True)


def _front_app_name():
    """当前前台 App 的名字。用 lsappinfo，别用 NSWorkspace.frontmostApplication()——
    后者在本进程里拿到的可能是过期值（实测激活成功了它还说没变）。"""
    import re
    import subprocess
    try:
        asn = subprocess.run(["lsappinfo", "front"], capture_output=True, text=True).stdout.strip().rstrip(":")
        if not asn:
            return ""
        out = subprocess.run(["lsappinfo", "info", "-only", "name", asn],
                             capture_output=True, text=True).stdout
        m = re.search(r'"LSDisplayName"="([^"]+)"', out)
        return m.group(1) if m else ""
    except Exception:
        return ""


def activate_app(name):
    """激活一个已在运行的 App。

    用 `open -a`（走 LaunchServices）而不是 NSRunningApplication.activateWithOptions_：
    后者从 macOS 14 起被系统忽略（实测前台不变），也不需要「自动化」授权。
    """
    import subprocess
    r = subprocess.run(["open", "-a", name], capture_output=True, text=True)
    return r.returncode == 0


def cycle_apps(names):
    """在几个 App 之间轮换：当前前台是其中一个就切到下一个，否则从第一个开始。"""
    front = _front_app_name().lower()
    idx = -1
    for i, n in enumerate(names):
        if n.lower() == front:
            idx = i
            break
    target = names[(idx + 1) % len(names)]
    return target if activate_app(target) else None


def accessibility_ok():
    """辅助功能授权状态。直接用 ctypes 问 ApplicationServices（pyobjc 的 Quartz 没导出这个符号）。"""
    try:
        import ctypes
        lib = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        lib.AXIsProcessTrusted.restype = ctypes.c_bool
        return bool(lib.AXIsProcessTrusted())
    except Exception:
        return None


def events_really_work():
    """真正判据：把光标推 1px 再读回，看系统是否接受了事件。

    AXIsProcessTrusted 在某些时机（刚重启 App、TCC 还在刷新）会短暂返回 false，
    但过几秒就正常了。只看它会让程序误判成「没权限」，白白丢掉前几秒的输入。
    这里直接试一次真实事件，比查标志可靠。
    """
    try:
        from Quartz import (CGEventCreate, CGEventCreateMouseEvent, CGEventGetLocation,
                            CGEventPost, kCGHIDEventTap, kCGEventMouseMoved)
        loc = CGEventGetLocation(CGEventCreate(None))
        x0, y0 = float(loc.x), float(loc.y)
        CGEventPost(kCGHIDEventTap,
                    CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x0 + 1, y0), 0))
        time.sleep(0.05)
        loc2 = CGEventGetLocation(CGEventCreate(None))
        moved = abs(float(loc2.x) - x0) >= 0.5
        CGEventPost(kCGHIDEventTap,          # 还原，别让用户察觉
                    CGEventCreateMouseEvent(None, kCGEventMouseMoved, (x0, y0), 0))
        return moved
    except Exception:
        return None


def wait_for_access(timeout=10.0):
    """等权限真正生效（App 刚重启时 TCC 要几秒才刷新）。

    返回 (ok, waited_seconds)。
    """
    started = time.monotonic()
    while True:
        if accessibility_ok() and events_really_work():
            return True, time.monotonic() - started
        if time.monotonic() - started >= timeout:
            return False, time.monotonic() - started
        time.sleep(0.5)


# ---------------------------------------------------------------- 主流程

def printer(kinds, keys=None):
    def out(kind, **f):
        if keys is not None and kind == "move":
            keys.emit(f["dir"])
            return
        if keys is not None and kind == "buttons":
            for name in f["pressed"]:
                keys.emit(name)
            for name in f["released"]:
                keys.release(name)          # 长按类动作需要知道什么时候松手
            return
        if kind not in kinds:
            return
        print(json.dumps(dict(kind=kind, **f), ensure_ascii=False), flush=True)
    return out


CONFIG_PATH = os.path.join(HERE, "config.json")
CONFIG = {}          # 由 apply_config 填充；面板写的就是这个文件


def apply_config(args):
    """把 mac/config.json 里的设置套到命令行参数上。命令行显式传的值优先。"""
    global CONFIG
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            CONFIG = json.load(f) or {}
    except Exception:
        CONFIG = {}
        return CONFIG
    if not args.mouse and CONFIG.get("mouse"):
        args.mouse = True
    if args.speed == 0.85 and isinstance(CONFIG.get("speed"), (int, float)):
        args.speed = float(CONFIG["speed"])
    if not args.accel and CONFIG.get("accel"):
        args.accel = True
    if not args.no_snap and CONFIG.get("snap") is False:
        args.no_snap = True
    if args.siri_action == "hold-option" and CONFIG.get("siri_action"):
        args.siri_action = CONFIG["siri_action"]
    if args.tv_apps is None and CONFIG.get("tv_apps"):
        args.tv_apps = ",".join(CONFIG["tv_apps"])
    try:                                    # 触摸方向是模块级常量，面板也能改
        import live_mac
        if "flip_x" in CONFIG:
            live_mac.FLIP_X = bool(CONFIG["flip_x"])
        if "flip_y" in CONFIG:
            live_mac.FLIP_Y = bool(CONFIG["flip_y"])
    except Exception:
        pass
    return CONFIG


PALM_SIZE = 2.8       # 手掌阈值。实测这块板的 size 能到 3.5（不是 0~1），见 is_palm。
PALM_ENTER = 4        # 连续几帧超阈值才认定手掌（约 60ms）
PALM_EXIT = 3         # 连续几帧低于阈值才解除

# 去抖状态。逐帧判断会把拇指滑动时的个别大帧误判成手掌，
# 而每一帧误判都会丢掉一段位移——表现就是「某个方向滑不动」。
_PALM = {"over": 0, "under": 0, "active": False}


def is_palm(size):
    """手掌误触判定（带迟滞）。size 是 MultitouchSupport 给的接触面积（5 档量化值）。

    注意值域：早期用指尖轻触测到的是 0.0038~0.16，但那只是最小几档。
    实际用拇指滑动时实测 p50=0.61、p90=2.04、最大 3.50（384 帧样本）。
    所以阈值必须在 0~4 量级上选，早期按 0~0.2 设的滑块范围是错的。

    所以这里不能逐帧判断：单帧偶尔超阈值不算，必须连续 PALM_ENTER 帧都超才认定；
    恢复也要连续 PALM_EXIT 帧低于阈值。这样拇指滑动时的一两个尖峰不会中断手势。

    默认关闭。想启用就把 config 的 palm_guard 设 true，并把 palm_size 调到
    比你自己滑动时的最大 size 再高一点（用 slide_probe.py 或面板的触摸页看）。
    """
    if not CONFIG.get("palm_guard", False):
        _PALM["over"] = _PALM["under"] = 0
        _PALM["active"] = False
        return False
    thresh = CONFIG.get("palm_size", PALM_SIZE)
    try:
        big = float(size) > float(thresh)
    except (TypeError, ValueError):
        return False
    if big:
        _PALM["over"] += 1
        _PALM["under"] = 0
        if _PALM["over"] >= PALM_ENTER:
            _PALM["active"] = True
    else:
        _PALM["under"] += 1
        _PALM["over"] = 0
        if _PALM["under"] >= PALM_EXIT:
            _PALM["active"] = False
    return _PALM["active"]


def adapt(report):
    """macOS 的按键报文是 fa <mask>，和 Linux 的 fc 00 <mask> 只差一个头，转一下就能复用现成解码器。"""
    if report and report[:1] == b"\xfa" and len(report) >= 2:
        return bytes([0xFC, 0x00, report[1]])
    return report


def run_mac(args, keys):
    """macOS 实机：按键走 HID 0xFA，触摸走私有 MultitouchSupport。"""
    from live_mac import LiveMac

    out = printer({"touch", "buttons", "voicechunk"} if args.cmd == "watch" else {"buttons"}, keys=keys)
    engine = Gestures(out)
    decoder = Decoder()
    live = LiveMac(enable_touch=not args.no_touch, debug=args.debug)

    for ev in live.events():
        if ev is None:
            engine.tick()
            continue
        kind = ev[0]
        if kind == "buttons":
            for e in decoder(bytes([0xFC, 0x00, ev[1]])):
                if args.cmd == "watch":
                    print(json.dumps({k: v for k, v in e._asdict().items() if k != "raw"},
                                     ensure_ascii=False), flush=True)
                else:
                    out("buttons", **{k: v for k, v in e._asdict().items() if k != "raw"})
        elif kind == "touch":
            _, t, x, y, down, size = ev
            if is_palm(size):
                engine.frame(t, x, y, False)      # 当成抬手：别让手掌继续累积手势
            else:
                engine.frame(t, x, y, down)
        engine.tick()


class _DryPointer:
    """--dry-run 时的替身：只打印，不动真实光标。"""
    def move_by(self, dx, dy):
        print(json.dumps({"mouse": "move", "dx": round(dx, 1), "dy": round(dy, 1)}, ensure_ascii=False), flush=True)

    def click(self, down, right=False):
        print(json.dumps({"mouse": "click", "down": down, "button": "right" if right else "left"},
                         ensure_ascii=False), flush=True)


def run_mac_mouse(args, keys):
    """鼠标模式：滑动移动指针，click 左键，Menu 右键，其余按钮照旧走键位表。"""
    from live_mac import LiveMac
    from pointer import Pointer

    live = LiveMac(enable_touch=not args.no_touch, debug=args.debug)
    ptr = (Pointer(speed=args.speed, accel=args.accel, verbose=args.debug, snap=not args.no_snap)
           if not args.dry_run else _DryPointer())
    decoder = Decoder()
    last = None
    print("鼠标模式：滑动=移动指针  click=左键  Menu=右键", file=sys.stderr, flush=True)

    for ev in live.events():
        if ev is None:
            continue
        if ev[0] == "touch":
            _, t, x, y, down, size = ev
            if is_palm(size):
                # 手掌：这一帧不移动指针，但**要留住坐标基准**。
                # 原来这里 last = None，等于每帧重新起算，误判一次就丢一段位移——
                # 叠加起来就是「某个方向怎么滑都不动」。
                last = (x, y)
                continue
            if not down:
                last = None                      # 抬手后重新起算，避免下次落下时跳一大段
                continue
            if last is None:
                last = (x, y)                     # 第一帧只定位，不移动
                continue
            dx, dy = x - last[0], y - last[1]
            last = (x, y)
            if dx or dy:
                ptr.move_by(dx, dy)
        elif ev[0] == "buttons":
            for e in decoder(bytes([0xFC, 0x00, ev[1]])):
                for name in e.pressed:
                    if name == "click":
                        ptr.click(True)
                    elif name == "menu":
                        ptr.click(True, right=True)
                    else:
                        keys.emit(name)
                for name in e.released:
                    if name in ("click", "menu"):
                        ptr.click(False)
                    else:
                        keys.release(name)
                if args.debug and (e.pressed or e.released):
                    print(json.dumps({"buttons": list(e.pressed), "released": list(e.released)},
                                     ensure_ascii=False), flush=True)


def run(args):
    source = (lambda: _device.replay(args.replay, realtime=not args.fast)) if args.replay else (
        lambda: Live(args.dev, info=lambda s: print(s, file=sys.stderr, flush=True)).reports())

    if args.cmd == "scan":
        rows = enumerate_hid()
        apple = [d for d in rows if d.get("vendor_id") == APPLE]
        print("共 %d 个 HID 设备，其中 Apple 厂商号(0x%04X) %d 个：" % (len(rows), APPLE, len(apple)))
        for d in apple:
            pid = d.get("product_id")
            print("  pid=0x%04X  usage=%s/%s  %-40s %s" % (
                pid, d.get("usage_page"), d.get("usage"), (d.get("product_string") or "")[:40],
                MODELS.get(pid, "未识别的 Apple HID")))
        if not any(d.get("product_id") in MODELS for d in apple):
            print("\n没看到遥控器。先配对：设置 → 蓝牙 → 遥控器按住 Menu + 音量+ 约 5 秒")
            return 1
        return 0

    keys = None
    if args.cmd == "keys":
        overrides = json.load(open(args.map)) if args.map else CONFIG.get("keymap")
        if args.tv_apps:
            overrides = dict(overrides or {}, tv="apps:" + args.tv_apps)
        keys = Keys(keymap(overrides), dry_run=args.dry_run, quiet=args.quiet,
                    siri_action=args.siri_action,
                    voice_model=args.voice_model, voice_lang=args.voice_lang)
        if not args.dry_run:
            print("音量键走了：%s" % ("系统定义键" if keys.vol_mode == "system" else "osascript 兜底"),
                  file=sys.stderr)
            ok, waited = wait_for_access()
            try:                              # 面板要显示的应该是**主程序**的权限，不是采集进程的
                with open("/tmp/siriremote-main.perm", "w", encoding="utf-8") as f:
                    f.write("1" if ok else "0")
            except Exception:
                pass
            if waited > 0.6:
                print("等待辅助功能权限生效：%.1f 秒" % waited, file=sys.stderr, flush=True)
            if ok is False:
                print("警告：没有辅助功能权限，合成的按键会被系统丢掉。", file=sys.stderr)
                print("      打开设置面板：open \"x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility\"",
                      file=sys.stderr)
                print("      把运行它的终端（或 .venv/bin/python）加进去，然后重开这个命令。", file=sys.stderr)
                import subprocess
                subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"])

    if not args.replay and sys.platform == "darwin":
        return run_mac_mouse(args, keys) if args.mouse else run_mac(args, keys)

    decode, engine = Decoder(), None
    out = printer({"touch", "buttons", "voicechunk"} if args.cmd == "watch" else {"buttons"},
                  keys=keys)
    engine = Gestures(out)

    for t, report in source():
        for ev in decode(adapt(report)):
            if isinstance(ev, Touch):
                engine.frame(ev.t, ev.x, ev.y, ev.down)
            else:
                if args.cmd == "watch":
                    print(json.dumps({k: v for k, v in ev._asdict().items() if k != "raw"},
                                     ensure_ascii=False), flush=True)
                elif isinstance(ev, Buttons):
                    out("buttons", **{k: v for k, v in ev._asdict().items() if k != "raw"})
        engine.tick()


def main():
    ap = argparse.ArgumentParser(description="把初代 Siri Remote 当 macOS 输入设备（open-appleremote 的 mac 后端）")
    ap.add_argument("cmd", choices=["scan", "watch", "keys"])
    ap.add_argument("--dev", metavar="PATH", help="指定 HID 设备路径（默认自动找遥控器）")
    ap.add_argument("--replay", metavar="FILE", help="回放录制文件，不需要硬件")
    ap.add_argument("--map", metavar="JSON", help='键位覆盖，如 {"click": "space", "tv": null}')
    ap.add_argument("--dry-run", action="store_true", help="只打印将要发送的按键，不真的发")
    ap.add_argument("--fast", action="store_true", help="回放时不等真实时间间隔（默认按录制节奏）")
    ap.add_argument("--no-touch", action="store_true", help="只用按键，不挂触控回调")
    ap.add_argument("--debug", action="store_true", help="打印原始触摸坐标，用于校准")
    ap.add_argument("--quiet", action="store_true", help="不打印每次按键")
    ap.add_argument("--mouse", action="store_true", help="滑动改为移动鼠标指针（click 左键 / Menu 右键）")
    ap.add_argument("--speed", type=float, default=0.85, help="指针速度，默认 0.85（越大越快）")
    ap.add_argument("--accel", action="store_true", help="开启指针加速度（按速度算，默认关）")
    ap.add_argument("--no-snap", action="store_true", help="关闭按下时的落点回正（如果你觉得它在跟你抢）")
    ap.add_argument("--tv-apps", metavar="A,B,C",
                    help="TV 键在哪些 App 之间轮换（默认 ChatGPT,WorkBuddy,Claude）")
    ap.add_argument("--voice-model", default="base",
                    help="语音识别模型：tiny/base/small/medium（默认 base，实测 3.3x 实时且中文准确）")
    ap.add_argument("--voice-lang", default="zh", help="识别语言，默认 zh")
    ap.add_argument("--siri-action", default="hold-option",
                    help="麦克风键触发什么：hold-option（默认，按住=按下 Option，松开=抬起，适配长按语音输入）、"
                         "cmd-d（发组合键，适配 ⌘D 式语音输入）、double-option（双击）、"
                         "key:xxx（单键）、off（关闭）")
    args = ap.parse_args()
    apply_config(args)
    try:
        sys.exit(run(args) or 0)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
