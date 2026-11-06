#!/bin/bash
# 双击运行：把 Siri Remote 当 Mac 输入设备（有线模式）
cd "$(dirname "$0")/.." || exit 1
echo "Siri Remote -> Mac（有线）"
echo "遥控器要插着 Lightning 线。按 Ctrl-C 停止。"
echo
exec .venv/bin/python mac/siriremote.py keys --debug
