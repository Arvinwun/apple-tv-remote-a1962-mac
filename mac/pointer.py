#!/usr/bin/env python3
"""把遥控器触摸板当鼠标：逐帧位移 -> CGEvent 鼠标事件。

为什么不用「相对位移」事件：CGEvent 的鼠标事件是**绝对坐标**，所以自己维护一个浮点位置，
累加位移后再取整发送，避免每帧各自取整造成的漂移。

坐标系：MT 给的 y 向上为正，屏幕坐标 y 向下为正，所以 y 要取反。
"""
import time
from collections import deque

from Quartz import (CGEventCreate, CGEventCreateMouseEvent, CGEventGetLocation, CGEventPost,
                    kCGHIDEventTap, kCGEventLeftMouseDown, kCGEventLeftMouseUp, kCGEventMouseMoved,
                    kCGEventRightMouseDown, kCGEventRightMouseUp, kCGMouseButtonLeft,
                    kCGMouseButtonRight, kCGEventLeftMouseDragged, kCGEventRightMouseDragged,
                    CGGetActiveDisplayList, CGDisplayBounds, CGMainDisplayID)

SPEED = 0.85          # 遥控器单位 -> 屏幕像素。满行程 1650 单位 ≈ 1400 px，约等于一屏宽
DEAD_ZONE = 0.6       # 小于这个像素的位移丢掉，压抖动
ACCEL_MAX = 1.6       # 快速划动最多放大到 1.6 倍（默认不开）
V_REF = 3000.0        # 单位/秒：速度达到这个值时加速度吃满

# —— 按下时防漂移 ——
# 手指按下触摸板会给触点一个位移（皮肤滚动），指针就飘走，点击落点跟着偏。
# 真触摸板靠压力感应区分"按下"和"移动"，我们只能：按下瞬间把指针拉回按下前的落点，
# 再在短时间内冻结移动；想拖动就得多走一点距离，才判定为拖动。
SNAP_WINDOW = 0.16    # 秒：按下时回看这么久的历史位置
SNAP_MAX = 55.0       # 像素：这段时间里位移小于它才算"抖动"，才回正；不然视为有意的移动
CLICK_LOCK = 0.14     # 秒：按下后这段时间内完全不动指针
DRAG_START = 14.0     # 像素：按住后要移动超过它才开始拖动


class Pointer:
    def __init__(self, speed=SPEED, accel=False, verbose=False, snap=True):
        self.speed, self.accel, self.verbose, self.snap = speed, accel, verbose, snap
        self._bounds = self._screen_bounds()     # 所有活动屏幕的并集（含多显示器）
        self.x, self.y = self._cursor()
        x0, y0, x1, y1 = self._bounds
        if not (x0 <= self.x <= x1 and y0 <= self.y <= y1):
            # 启动时读数就在屏幕外（系统里出现过 -4222、-6302 这种），说明这个值已经不可信。
            # 居中比钉在角落好：用户一滑就能立刻看到指针动，而不是从边上开始。
            self.x, self.y = (x0 + x1) / 2, (y0 + y1) / 2
        self._held = None          # 当前按下的键：None / "left" / "right"
        self._last_t = None
        self._hist = deque(maxlen=240)   # (t, x, y) 最近的指针位置，用于按下时回正
        self._press_t = 0.0
        self._press_pos = (0.0, 0.0)
        self._dragging = False

    @staticmethod
    def _screen_bounds():
        """所有活动显示器的并集，返回 (x0, y0, x1, y1)，右下是闭区间。"""
        try:
            _, ids, count = CGGetActiveDisplayList(16, None, None)
            if count:
                x0 = y0 = 1e9
                x1 = y1 = -1e9
                for i in range(count):
                    b = CGDisplayBounds(ids[i])
                    x0 = min(x0, b.origin.x)
                    y0 = min(y0, b.origin.y)
                    x1 = max(x1, b.origin.x + b.size.width)
                    y1 = max(y1, b.origin.y + b.size.height)
                return x0, y0, x1 - 1, y1 - 1
        except Exception:
            pass
        b = CGDisplayBounds(CGMainDisplayID())
        return b.origin.x, b.origin.y, b.origin.x + b.size.width - 1, b.origin.y + b.size.height - 1

    def _clamp(self):
        """把内部坐标钳在屏幕范围内。

        为什么必须有：CGEvent 的鼠标事件是绝对坐标，往同一方向一直滑，内部坐标会一路累加，
        而系统会把真实光标钉在屏幕边缘。两者一旦分离，反方向滑动就得先「还债」——
        实测内部坐标跑到 x=-4222，往右滑要滑三四个满行程才动，表现为「往右失灵」。
        """
        x0, y0, x1, y1 = self._bounds
        self.x = min(max(self.x, x0), x1)
        self.y = min(max(self.y, y0), y1)

    @staticmethod
    def _cursor():
        loc = CGEventGetLocation(CGEventCreate(None))
        return float(loc.x), float(loc.y)

    def _post(self, kind, button=kCGMouseButtonLeft):
        ev = CGEventCreateMouseEvent(None, kind, (self.x, self.y), button)
        CGEventPost(kCGHIDEventTap, ev)

    def move_by(self, dx_units, dy_units):
        """dx/dy 是遥控器单位（+x 右，+y 上）。返回实际移动的像素 (dx, dy)。

        加速按**速度**算（单位/秒），不能用每帧位移——那个值随帧率变化，结果不可复现。
        """
        px = dx_units * self.speed
        py = -dy_units * self.speed                      # 屏幕 y 向下
        if self.accel:
            now = time.monotonic()
            dt = (now - self._last_t) if self._last_t else 1 / 60.0
            self._last_t = now
            v = ((dx_units ** 2 + dy_units ** 2) ** 0.5) / max(dt, 1 / 240.0)
            k = min(ACCEL_MAX, 1.0 + v / V_REF)
            px, py = px * k, py * k
        if abs(px) < DEAD_ZONE and abs(py) < DEAD_ZONE:
            return 0.0, 0.0
        now = time.monotonic()
        if self._held:
            # 刚按下的短时间内冻住，之后要走出 DRAG_START 才算拖动
            if now - self._press_t < CLICK_LOCK:
                return 0.0, 0.0
            if not self._dragging:
                ox, oy = self._press_pos
                if ((self.x + px - ox) ** 2 + (self.y + py - oy) ** 2) ** 0.5 < DRAG_START:
                    return 0.0, 0.0
                self._dragging = True
        self.x += px
        self.y += py
        self._clamp()                                    # 别让内部坐标跑出屏幕，否则回不来
        self._hist.append((now, self.x, self.y))
        if self._held == "left":                     # 按住时发 dragged，拖动才有效
            self._post(kCGEventLeftMouseDragged, kCGMouseButtonLeft)
        elif self._held == "right":
            self._post(kCGEventRightMouseDragged, kCGMouseButtonRight)
        else:
            self._post(kCGEventMouseMoved)
        if self.verbose:
            print("mouse %.1f,%.1f" % (self.x, self.y), flush=True)
        return px, py

    def move_to(self, x, y):
        """直接跳到某个绝对坐标（测试/还原用）。"""
        self.x, self.y = float(x), float(y)
        self._clamp()
        self._post(kCGEventMouseMoved)

    def _snap_back(self):
        """按下时把指针拉回 SNAP_WINDOW 之前的落点，抵消按压造成的位移。

        取「窗口起点之前的最后一个样本」而不是「窗口内最老的样本」——按压造成的漂移
        往往正好从窗口起点开始，用窗口内的样本会把漂移本身当成基准。
        """
        now = time.monotonic()
        older = [h for h in self._hist if h[0] <= now - SNAP_WINDOW]
        if older:
            tx, ty = older[-1][1], older[-1][2]
        elif self._hist:
            tx, ty = self._hist[0][1], self._hist[0][2]     # 历史还不够长，退而求其次
        else:
            return
        drift = ((self.x - tx) ** 2 + (self.y - ty) ** 2) ** 0.5
        if drift > SNAP_MAX:                     # 移动太多，是故意的，不回正
            return
        self.x, self.y = tx, ty
        self._post(kCGEventMouseMoved)
        if self.verbose:
            print("mouse snap back %.1f px" % drift, flush=True)

    def click(self, down, right=False):
        """按住/松开。重复调用同一个方向不会重复发事件。"""
        want = ("right" if right else "left") if down else None
        if self._held == want or (self._held is None and not down):
            return
        if down:
            if self.snap:
                self._snap_back()                # 先回正，再按下：点击落点才是手指按下的地方
            self._press_t = time.monotonic()
            self._press_pos = (self.x, self.y)
            self._dragging = False
        self.x, self.y = self._cursor()          # 同步真实位置，避免漂移累积
        if down:
            self.x, self.y = self._press_pos     # 回正后的位置为准
            self._post(kCGEventRightMouseDown if right else kCGEventLeftMouseDown,
                       kCGMouseButtonRight if right else kCGMouseButtonLeft)
            self._held = want
        else:
            self._post(kCGEventRightMouseUp if self._held == "right" else kCGEventLeftMouseUp,
                       kCGMouseButtonRight if self._held == "right" else kCGMouseButtonLeft)
            self._held = None
        if self.verbose:
            print("mouse click %s %s" % (self._held or "released", "right" if right else "left"), flush=True)


if __name__ == "__main__":
    # 自测：移动 120px 再读回真实光标位置，验证合成事件生效，然后还原
    p = Pointer(speed=1.0, accel=False)
    start = p._cursor()
    print("起点:", start)
    p.move_by(60, 0)          # 60 单位 * 1.0 = 60 px 向右
    time.sleep(0.15)
    mid = p._cursor()
    print("移动后:", mid, "实际位移 dx=%.0f dy=%.0f" % (mid[0] - start[0], mid[1] - start[1]))
    p.move_by(-60, 0)
    time.sleep(0.15)
    print("还原后:", p._cursor())
