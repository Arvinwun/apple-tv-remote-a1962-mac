#!/usr/bin/env python3
"""macOS 侧的数据源：按键走 USB HID 的 0xFA 位掩码，触摸走私有 MultitouchSupport。

为什么分成两路：
  · 按键：hidapi 读 usage 12/1（Consumer），report ID 0xFA，payload 一个字节 = 按键位掩码，
    与 Linux 版 0xFC 协议的 byte2 完全一致（click 0x80 / menu 0x20 / playpause 0x08 / 音+ 0x02 / 音- 0x04 / tv 0x01 / siri 0x10）。
  · 触摸：macOS 建了 AppleMultitouchDevice（Transport = BLE, 序列号就是遥控器），
    触摸表面被系统的多点触控驱动接管，hidraw 那套 0xFC 报文根本不到用户态。
    只能走私有 MultitouchSupport.framework 的 contact frame 回调。

产出统一的事件流：
    ("buttons", mask)                    按键位掩码变化
    ("touch", t, x, y, down)             触摸（x/y 已换算成遥控器单位，约 ±825）
"""
import ctypes
import queue
import sys
import threading
import time

APPLE, GEN1 = 0x004C, 0x026D
CONSUMER = (12, 1)          # 按键所在集合：usage page 12 / usage 1
MT_PATH = "/System/Library/PrivateFrameworks/MultitouchSupport.framework/MultitouchSupport"
CF_PATH = "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"

SPAN = 1650.0               # 遥控器触摸面的宽度（Linux 版实测值），用来把 0..1 归一化坐标换算过去
FLIP_Y = False              # 实测：MT 的 normalized_y 向上递增（上滑 dy>0），所以不翻转
FLIP_X = False              # 万一左右是反的，把这个改 True

MT_STATE_LIFTED = 7


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


class LiveMac:
    def __init__(self, info=None, enable_touch=True, debug=False, enable_buttons=True):
        self.info = info or (lambda s: print(s, file=sys.stderr, flush=True))
        self.enable_touch = enable_touch
        self.enable_buttons = enable_buttons   # 面板只做预览时不碰 12/1，避免和主程序抢独占
        self.debug = debug
        self.q = queue.Queue()
        self._cb = None          # 必须留引用，否则回调被回收
        self._note = None
        self._had_device = False
        self._reregister = threading.Event()
        self._mt_found = 0       # 当前挂上回调的「遥控器触摸板」数量，0 表示还没找到
        self._last_touch_t = 0.0
        self._last_key_t = 0.0
        self._dup = 0            # 被去重丢掉的帧数（重挂导致同一设备挂了多次回调时 > 0）
        self._last_frame_key = None

    # ---------------- 按键：hidapi
    def _restart_self(self, why):
        """用 exec 重建整个进程（pid 不变）。触控回调挂在失效的设备对象上时，这是最干净的解法。

        加退避：环境变量记次数，超过 3 次就不再重启，免得反复横跳。
        """
        import os
        n = int(os.environ.get("SIRI_MT_RESTARTS", "0"))
        if n >= 3:
            self.info("%s；已重启 %d 次仍不行，不再重启（按一下遥控器或手动重启主程序）" % (why, n))
            return
        self.info("%s → 重启进程重建触控回调（第 %d 次）" % (why, n + 1))
        os.environ["SIRI_MT_RESTARTS"] = str(n + 1)
        for h in (sys.stdout, sys.stderr):
            try:
                h.flush()
            except Exception:
                pass
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _note_once(self, msg):
        if msg != self._note:
            self._note = msg
            self.info(msg)

    def _hid_loop(self):
        import hid
        while True:
            cands = [d for d in hid.enumerate() if d.get("vendor_id") == APPLE
                     and d.get("product_id") == GEN1
                     and (d.get("usage_page"), d.get("usage")) == CONSUMER]
            if not cands:
                self._note_once("等遥控器：按一下任意键唤醒（它连 BLE，不用插线）")
                time.sleep(1)
                continue
            dev = hid.device()
            try:
                dev.open_path(cands[0]["path"])
            except Exception as e:
                self._note_once("打不开按键集合（%s）：同一时间只能有一个实例占用它，"
                                "先关掉另一个 siriremote.py" % e)
                time.sleep(2)
                continue
            # 两种情况都要重挂触控回调：
            #   ① 掉线后又回来了（`_had_device`）
            #   ② 一直没找到遥控器的触摸板（启动时遥控器在睡，唤醒后才出现）
            # 只认 ① 会漏掉「程序先起、遥控器后醒」这个最常见的场景。
            if self._had_device or self._mt_found == 0:
                self._reregister.set()
            self._had_device = True
            self._note_once("按键通道已连接")
            try:
                while True:
                    data = dev.read(64, 100)
                    if data and bytes(data)[:1] == b"\xfa" and len(data) >= 2:
                        # 能收到按键 = 遥控器已经在线，触摸板这会儿也该在。
                        now = time.monotonic()
                        self._last_key_t = now
                        # 两种触发：① 还没挂上回调；② 挂上了但 8 秒没收到触摸帧
                        # （唤醒时 MT 设备对象会被系统重建，而 HID 不一定断开，
                        #   所以「掉线又回来」这个信号压根不会来——这是关键补丁）
                        if self._mt_found == 0 or now - self._last_touch_t > 8.0:
                            self._reregister.set()
                        self.q.put(("buttons", bytes(data[1:2])[0]))
            except Exception as e:
                self._note_once("按键通道断开: %s" % e)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
            time.sleep(0.5)

    # ---------------- 触摸：MultitouchSupport
    def _mt_loop(self):
        try:
            mt = ctypes.CDLL(MT_PATH)
            CF = ctypes.CDLL(CF_PATH)
        except OSError as e:
            self.info("私有触控框架不可用: %s" % e)
            return
        mt.MTDeviceCreateList.restype = ctypes.c_void_p
        mt.MTDeviceStart.argtypes = [ctypes.c_void_p, ctypes.c_int]
        mt.MTDeviceIsBuiltIn.argtypes = [ctypes.c_void_p]
        mt.MTDeviceIsBuiltIn.restype = ctypes.c_bool
        mt.MTRegisterContactFrameCallback.argtypes = [ctypes.c_void_p, CALLBACK]
        CF.CFArrayGetCount.restype = ctypes.c_long
        CF.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        CF.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        CF.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]

        # 只认遥控器那块触摸板：内置触控板也在 MTDeviceCreateList 里，
        # 不区分来源的话，用笔记本触摸板也会被当成遥控器手势发方向键。
        self._devices = []      # 留住 CFArray 里的指针，别让它们被回收

        ts_logged = [0]

        def on_frame(dev, fingers, count, ts, frame_no):
            # 这里**不能**按 dev 查 sources 过滤。
            # register_all 只注册非内置设备（内置触控板直接跳过），所以到达这里的帧必然来自遥控器。
            # 之前用 sources[dev] 判断，重挂后 MTDeviceCreateList 返回的是新的包装对象（地址变了），
            # 而回调参数 dev 仍是底层设备指针 → 查不到 → 默认 True → 整帧被丢，
            # 表现为「重挂一次，滑动永久失灵」（日志里 _dup=0 却收不到任何帧就是这个）。

            # 只要有帧到达就说明触摸活着，健康检查看的应该是这个，
            # 不能放在去重后面——否则帧被误判重复时健康检查会跟着误触发。
            self._last_touch_t = time.monotonic()

            # 去重：MTDeviceCreateList 每次返回新的包装对象，「按指针判重」永远失败，
            # 重挂一次同一帧就会被投递多次（鼠标速度翻倍）。
            # 用 frame_no 而不是 timestamp：帧号每帧必增，不会像 ts 那样可能恒定不变
            # （上一版用 int(ts*100000)，实测把正常帧全判成重复，滑动直接失灵）。
            if frame_no == self._last_frame_key:
                self._dup += 1
                return 0
            self._last_frame_key = frame_no

            if self.debug and ts_logged[0] < 6:
                ts_logged[0] += 1
                self.info("frame_no=%d ts=%.6f count=%d" % (frame_no, ts, count))
            if count <= 0:
                # 最后一帧走后 count=0，用上一状态判断抬手
                self.q.put(("touch", time.monotonic(), 0.0, 0.0, False, 0.0))
                return 0
            f = fingers[0]
            down = f.state != MT_STATE_LIFTED
            x = (f.normalized_x - 0.5) * SPAN
            if FLIP_X:
                x = -x
            y = (0.5 - f.normalized_y) * SPAN if FLIP_Y else (f.normalized_y - 0.5) * SPAN
            if self.debug:
                self.info("raw nx=%.3f ny=%.3f state=%d size=%.3f" % (
                    f.normalized_x, f.normalized_y, f.state, f.size))
            # size：接触面积，实测只有 5 档（0 / 0.0038 / 0.038 / 0.080 / 0.160），
            # 指尖约 0.0038，整个拇指盖上去会到 0.16，用来做手掌误触过滤够用
            self.q.put(("touch", time.monotonic(), x, y, down, float(f.size)))
            return 0

        self._cb = CALLBACK(on_frame)
        self._devices = []          # 留住 CFArray 里的指针，别让它们被回收
        registered = set()          # 按指针去重，只在同一轮里用（指针每轮枚举都会变）

        CF.CFRunLoopGetCurrent.restype = ctypes.c_void_p
        CF.CFStringCreateWithCString.restype = ctypes.c_void_p
        CF.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        mode = CF.CFStringCreateWithCString(None, b"kCFRunLoopDefaultMode", 0x08000100)
        CF.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]

        def register_all():
            """扫描并挂回调，返回「遥控器触摸板」的数量。

            MTDeviceCreateList 每次调用返回的都是新的包装对象（指针每轮都变），
            所以不能靠指针判断设备是否已经挂过——只能在同一轮里用 registered 去重。
            （MTDeviceGetDeviceID 在这套系统上调用会 SIGBUS，别碰。）

            内置触控板直接跳过：它只会往队列里灌没用的幽灵帧，注册纯属浪费，
            而且重挂时还会叠加回调。
            """
            # 注意：这里**不要** CFRelease / MTUnregisterContactFrameCallback。
            # 实测过：加上「注销旧回调 + 释放对象」之后，回调就再也不触发了
            #（面板的采集进程同一份代码、同一台机器，能收到 3000+ 帧，
            #  差别只是它没走那条注销路径）。CFRelease 会让 MT 内部的引用悬空。
            #
            # 现在的做法是最朴素、也被验证有效的那种：只追加、只 Start，从不注销。
            arr = mt.MTDeviceCreateList()
            n = CF.CFArrayGetCount(arr) if arr else 0
            found = 0
            for i in range(n):
                devp = CF.CFArrayGetValueAtIndex(arr, i)
                if mt.MTDeviceIsBuiltIn(devp):
                    continue
                found += 1
                if devp in registered:
                    mt.MTDeviceStart(devp, 0)     # 幂等，把被系统停掉的流拉起来
                    continue
                self._devices.append(devp)
                if len(self._devices) > 16:       # 只留最近的，别无限攒
                    self._devices = self._devices[-16:]
                mt.MTRegisterContactFrameCallback(devp, self._cb)
                mt.MTDeviceStart(devp, 0)
                registered.add(devp)
                self.info("遥控器触摸板已挂回调（设备 #%d）" % i)
            return found

        self._mt_found = register_all()
        if self._mt_found:
            self.info("触控就绪（%d 个遥控器触摸板）" % self._mt_found)
        else:
            self.info("还没看到遥控器的触摸板，等它唤醒")

        retry_until = 0.0        # 在这个时间点之前持续重试（唤醒后 MT 设备可能晚于 HID 出现）
        health_next = 0.0        # 下次允许健康检查重挂的时间（失败就退避，避免刷屏）
        health_fail = 0
        self._last_touch_t = time.monotonic()
        while True:
            CF.CFRunLoopRunInMode(mode, 0.3, False)
            now = time.monotonic()

            # 健康检查：人明明在按键（说明遥控器醒着、链路正常），却两分钟收不到一帧触摸，
            # 基本就是回调死了。重挂是幂等的（不会重复注册），误触发也没有副作用。
            if (self._mt_found and now - self._last_touch_t > 20
                    and self._last_key_t and now - self._last_key_t < 20
                    and now >= health_next):
                health_next = now + min(300.0, 20.0 * (2 ** min(health_fail, 4)))
                health_fail += 1
                self.info("%d 秒没有触摸帧但按键正常，重挂触控回调（第 %d 次）"
                          % (int(now - self._last_touch_t), health_fail))
                self._last_touch_t = now
                self._reregister.set()
            elif now - self._last_touch_t < 2:
                health_fail = 0          # 收到帧就清零，下次失灵重新计数

            if self._reregister.is_set():
                self._reregister.clear()
                # 注意：这里**不清空** registered。设备对象没变（指针相同）时不能重复注册——
                # 同一设备挂两次回调会让每一帧投递两次，鼠标速度直接翻倍。
                # 唤醒后系统通常会创建新对象（指针变），那时才会真的被注册上。
                self._last_frame_key = None      # 重挂后帧号会从头来，别把第一帧当重复
                self._mt_found = register_all()
                if self._mt_found:
                    retry_until = 0.0
                    self.info("触控回调已重挂（%d 个遥控器触摸板，累计丢弃重复帧 %d）"
                              % (self._mt_found, self._dup))
                else:
                    # HID 已经回来但触摸板还没枚举出来，12 秒内每 0.3 秒再试
                    retry_until = now + 12.0
                    self.info("触摸板还没出现，12 秒内持续重试")
            elif retry_until:
                if now >= retry_until:
                    retry_until = 0.0
                    self.info("重试结束：仍没找到遥控器的触摸板")
                else:
                    self._last_frame_key = None      # 重挂后帧号会从头来，别把第一帧当重复
                    self._mt_found = register_all()
                    if self._mt_found:
                        retry_until = 0.0
                        self.info("触摸板出现，已挂回调（%d 个）" % self._mt_found)

    # ---------------- 对外
    def events(self, idle=0.01):
        """产出 ("buttons", mask) / ("touch", t, x, y, down, size)，并给出手势引擎需要的空闲节拍。"""
        if self.enable_buttons:
            threading.Thread(target=self._hid_loop, daemon=True).start()
        if self.enable_touch:
            threading.Thread(target=self._mt_loop, daemon=True).start()
        while True:
            try:
                yield self.q.get(timeout=idle)
            except queue.Empty:
                yield None
