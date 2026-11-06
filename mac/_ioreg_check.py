import re
import subprocess
import sys

out = subprocess.run(["ioreg", "-r", "-c", "IOHIDDevice", "-w0"], capture_output=True, text=True).stdout
blocks = out.split("+-o ")
for b in blocks:
    if "0x26d" not in b.lower():
        continue
    head = b.split("\n")[0][:90]
    kv = dict(re.findall(r'"([^"]+)" = (.*)', b))
    print("###", head)
    for k in ("Product", "PrimaryUsagePage", "PrimaryUsage", "VendorID", "ProductID", "Transport",
              "MaxInputReportSize", "MaxFeatureReportSize", "MaxOutputReportSize"):
        if k in kv:
            print("   %-18s %s" % (k, kv[k].strip()))
    desc = re.search(r'"ReportDescriptor" = <([0-9a-f]+)>', b)
    if desc:
        h = desc.group(1)
        print("   descriptor        %d bytes: %s%s" % (len(h) // 2, h[:120].upper(), "..." if len(h) > 120 else ""))
        print("   含 0xFC 报文ID    %s" % ("是" if "85fc" in h.lower() or "06fc" in h.lower() else "否"))
    print()
