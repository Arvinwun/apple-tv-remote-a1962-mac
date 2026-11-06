#!/bin/bash
# 双击运行：遥控器控制面板（浏览器打开 http://127.0.0.1:8787）
cd "$(dirname "$0")/.." || exit 1
echo "控制面板启动中…"
echo "浏览器打开 http://127.0.0.1:8787 ，按 Ctrl-C 停止"
echo
exec .venv/bin/python mac/panel/server.py
