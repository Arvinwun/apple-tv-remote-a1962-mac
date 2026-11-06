"""按缩进解析 ioreg，列出遥控器所有节点（类名 + 用途 + 描述符 + 子节点）。"""

import re
import subprocess

TXT = subprocess.run(["ioreg", "-l", "-w0"], capture_output=True).stdout.decode("utf-8", "replace")
NODE = re.compile(r"^(.*?)\+-o (\S+)  <class ([^,>]+)")
PROP = re.compile(r'^[\s|]*"([^"]+)" = (.*)$')

nodes, cur, order = {}, None, []
for line in TXT.splitlines():
    m = NODE.match(line)
    if m:
        cur = {"name": m.group(2), "cls": m.group(3), "props": {}, "indent": len(m.group(1))}
        nodes[id(cur)] = cur
        order.append(cur)
        continue
    if cur:
        p = PROP.match(line)
        if p:
            cur["props"][p.group(1)] = p.group(2).strip()

remote = [n for n in order if n["props"].get("Product") == '"Apple TV Remote"' or "Apple TV Remote" in n["name"]]
print("遥控器节点 %d 个：\n" % len(remote))
for n in remote:
    k = n["props"]
    print("%-32s usage=%s/%s  MaxIn=%s" % (n["cls"], k.get("PrimaryUsagePage", "-"), k.get("PrimaryUsage", "-"),
                                           k.get("MaxInputReportSize", "-")))
    d = k.get("ReportDescriptor", "")
    if d.startswith("<"):
        raw = d.strip("<>")
        print("    desc %d bytes %s" % (len(raw) // 2, raw[:100].upper()))
    for key in ("Transport", "DeviceUsagePairs", "HIDDefaultBehavior", "ReportInterval", "LocationID"):
        if key in k:
            print("    %-16s %s" % (key, k[key][:150]))

print("\n挂在遥控器下面的服务：")
for n in order:
    if n["cls"] in ("IOHIDEventDriver", "IOHIDEventService", "IOHIDInterface", "AppleUserHIDDevice",
                    "AppleUserUSBHostHIDDevice", "IOHIDDevice", "AppleMultitouchDevice"):
        print("  %-32s %s" % (n["cls"], n["props"].get("Product", "").strip()))
