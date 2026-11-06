#!/bin/bash
# 双击运行：遥控器触摸板当鼠标用（滑动移指针，click 左键，Menu 右键）
cd "$(dirname "$0")/.." || exit 1
echo "Siri Remote -> Mac 鼠标模式"
echo "按 Ctrl-C 停止。想调速度：加 --speed 1.2（默认 0.85）"
echo
exec .venv/bin/python mac/siriremote.py keys --mouse
