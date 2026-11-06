"""把 ioreg 里所有 Apple TV Remote 的 HID 集合列出来：用途、最大报文、描述符解析。"""

import re
import subprocess

OUT = subprocess.run(["ioreg", "-l", "-w0"], capture_output=True).stdout.decode("utf-8", "replace")

# report descriptor 里的全局/局部条目，只解我们关心的
USAGE_PAGES = {0x01: "Generic Desktop", 0x0C: "Consumer", 0x0D: "Digitizer", 0x20: "Sensor",
               0xFF00: "Vendor 0xFF00"}
USAGES = {
    (0x0C, 0xCD): "Play", (0x0C, 0xE9): "Volume+", (0x0C, 0xEA): "Volume-", (0x0C, 0xE2): "Mute",
    (0x0C, 0x41): "Menu-ish", (0x0C, 0x04): "?0x04", (0x0C, 0x109): "?0x109",
    (0x0D, 0x01): "Digitizer", (0x0D, 0x22): "Finger", (0x0D, 0x42): "TipSwitch", (0x0D, 0x30): "X",
    (0x0D, 0x31): "Y", (0x0D, 0x32): "InRange", (0x0D, 0x47): "Confidence", (0x0D, 0x51): "ContactID",
}


def descriptor_summary(hexstr):
    b = bytes.fromhex(hexstr)
    parts, i, page, rid = [], 0, None, None
    while i < len(b):
        p = b[i]
        size = (0, 1, 2, 4)[p & 3]
        tag = p & 0xFC
        data = b[i + 1:i + 1 + size]
        val = int.from_bytes(data, "little") if data else 0
        if p == 0x05:
            page = USAGES.get(("x",), None)
            v = val
            parts.append("page=%s" % USAGE_PAGES.get(v, hex(v)))
        elif p == 0x09:
            parts.append("usage=%s" % USAGES.get((page, val), hex(val)))
        elif p == 0x85:
            rid = val
            parts.append("reportID=0x%02X" % val)
        elif p == 0x75:
            parts.append("bits=%d" % val)
        elif p == 0x95:
            parts.append("count=%d" % val)
        elif p == 0x81:
            parts.append("INPUT(0x%02X)" % val)
        elif p == 0xB1:
            parts.append("FEATURE(0x%02X)" % val)
        elif p == 0xB2:
            parts.append("FEATURE(buf,0x%02X)" % val)
        elif p == 0x15:
            parts.append("min=%d" % val)
        elif p == 0x25 or p == 0x26:
            parts.append("max=%d" % val)
        i += 1 + size
    return " ".join(parts)


# 一个 IOHIDDevice 节点 = 从 "Product" = "Apple TV Remote" 往下到下一个级别相同的 "+-o"
blocks = re.split(r"\n\s{0,4}\+-o ", OUT)
count = 0
for b in blocks:
    if '"Product" = "Apple TV Remote"' not in b and '"Product" = "Apple TV Remote"' not in b.split("\n")[0]:
        continue
    if '"ReportDescriptor"' not in b:
        continue
    kv = dict(re.findall(r'"([^"]+)" = (.*)', b))
    count += 1
    print("#%d  usage=%s/%s  MaxInputReportSize=%s  MaxFeatureReportSize=%s" % (
        count, kv.get("PrimaryUsagePage", "?").strip(), kv.get("PrimaryUsage", "?").strip(),
        kv.get("MaxInputReportSize", "?").strip(), kv.get("MaxFeatureReportSize", "?").strip()))
    print("   Transport=%s  ReportInterval=%s" % (kv.get("Transport", "?").strip(), kv.get("ReportInterval", "?").strip()))
    print("   描述符: %s" % descriptor_summary(kv["ReportDescriptor"].strip().strip("<>")))
    print()
print("共 %d 个带描述符的集合" % count)
