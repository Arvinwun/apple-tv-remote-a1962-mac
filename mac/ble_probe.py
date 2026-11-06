#!/usr/bin/env python3
"""用 CoreBluetooth 探测 Siri Remote 的 GATT 特征。

为什么试这个：协议文档说遥控器把音频作为 **ATT handle 0x0023 的 notification、
每帧 101 字节**发出来；Linux 的 BlueZ 用 23 字节 MTU 缓冲读，所以 hidraw 只拿到
前 20 字节（13 字节 Opus）。macOS 的 HID 层更干脆，只给 2 字节状态位。

换句话说：音频在 GATT 层是完整的，只是被系统 HID 驱动挡在外面。
如果 CoreBluetooth 能直接订阅那个特征，就有希望拿到完整帧。

注意：连接会抢占系统 HOGP 的连接，按键盘/触摸会短暂中断，退出后自动恢复。
"""
import os
import sys
import time

import objc
from Foundation import NSObject, NSRunLoop, NSDate
from CoreBluetooth import CBCentralManager

# 遥控器的蓝牙广播名就是它的序列号（11 位大写字母数字）。
# 想指定具体设备就设环境变量 SIRI_REMOTE_NAME，否则按上面的格式自动识别。
TARGET_NAME = os.environ.get("SIRI_REMOTE_NAME", "")


def is_target(name):
    if TARGET_NAME:
        return TARGET_NAME in name
    return len(name) >= 10 and all(c.isupper() or c.isdigit() for c in name)
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0

STATE_NAMES = {0: "unknown", 1: "resetting", 2: "unsupported", 3: "unauthorized",
               4: "poweredOff", 5: "poweredOn"}


def flags_of(props):
    out = []
    for bit, name in ((0x01, "broadcast"), (0x02, "read"), (0x04, "writeNoResp"),
                      (0x08, "write"), (0x10, "notify"), (0x20, "indicate"),
                      (0x40, "authSignedWrite")):
        if props & bit:
            out.append(name)
    return ",".join(out) or "-"


class Probe(NSObject):
    def init(self):
        self = objc.super(Probe, self).init()
        self.central = None
        self.target = None
        self.done = False
        self.seen = set()
        self.idx = 0
        return self

    # ---- central
    def centralManagerDidUpdateState_(self, cm):
        st = cm.state()
        print("蓝牙状态: %d (%s)" % (st, STATE_NAMES.get(st, "?")), flush=True)
        if st == 5:
            print("扫描中 —— 请按一下遥控器任意键唤醒它（它睡了就不广播）", flush=True)
            cm.scanForPeripheralsWithServices_options_(None, None)
        elif st in (3, 4):
            print("扫不了：未授权或蓝牙没开", flush=True)
            self.done = True

    def centralManager_didDiscoverPeripheral_advertisementData_RSSI_(self, cm, p, adv, rssi):
        name = p.name() or "?"
        if name in self.seen:
            return
        self.seen.add(name)
        print("  发现 %-32s RSSI %s" % (name[:32], rssi), flush=True)
        if is_target(name):
            self.target = p
            cm.stopScan()
            print("→ 是遥控器，尝试连接（会短暂抢占系统 HID 连接）…", flush=True)
            cm.connectPeripheral_options_(p, None)

    def centralManager_didConnectPeripheral_(self, cm, p):
        print("→ 已连接，开始枚举服务", flush=True)
        p.setDelegate_(self)
        p.discoverServices_(None)

    def centralManager_didFailToConnectPeripheral_error_(self, cm, p, err):
        print("→ 连接失败: %s" % err, flush=True)
        self.done = True

    def centralManager_didDisconnectPeripheral_error_(self, cm, p, err):
        print("→ 断开: %s" % err, flush=True)
        self.done = True

    # ---- peripheral
    def peripheral_didDiscoverServices_(self, p, err):
        if err:
            print("→ 发现服务失败: %s" % err, flush=True)
            self.done = True
            return
        svcs = list(p.services() or [])
        print("→ 服务 %d 个:" % len(svcs), flush=True)
        for s in svcs:
            print("     %s%s" % (s.UUID().UUIDString(), "  (primary)" if s.isPrimary() else ""), flush=True)
        self.index = 0
        if not svcs:
            self.done = True
            return
        p.discoverCharacteristics_forService_(None, svcs[0])

    def peripheral_didDiscoverCharacteristicsForService_error_(self, p, svc, err):
        chs = list(svc.characteristics() or [])
        print("   [%s] 特征 %d 个:" % (svc.UUID().UUIDString(), len(chs)), flush=True)
        for c in chs:
            print("      %-40s %s" % (c.UUID().UUIDString(), flags_of(c.properties())), flush=True)
        self.index += 1
        svcs = list(p.services() or [])
        if self.index < len(svcs):
            p.discoverCharacteristics_forService_(None, svcs[self.index])
        else:
            print("→ 枚举完成", flush=True)
            self.done = True


def main():
    probe = Probe.alloc().init()
    cm = CBCentralManager.alloc().initWithDelegate_queue_(probe, None)
    probe.central = cm
    deadline = time.time() + SECONDS
    while not probe.done and time.time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
    try:
        cm.stopScan()
    except Exception:
        pass
    if not probe.done:
        print("（超时退出：没扫到遥控器，或者你还没按键唤醒）", flush=True)


if __name__ == "__main__":
    main()
