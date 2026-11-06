#!/bin/bash
# 把面板打包成菜单栏程序：SiriRemote.app（放在项目根目录）
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1          # 项目根
ROOT="$(pwd)"
APP="$ROOT/SiriRemote.app"
SRC="$ROOT/mac/panel/App.swift"
BIN="$ROOT/mac/panel/.build/SiriRemote"

echo "==> 编译"
mkdir -p "$ROOT/mac/panel/.build"
swiftc -O "$SRC" -o "$BIN" -framework Cocoa -framework WebKit -framework CoreBluetooth

echo "==> 组装 app bundle"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/SiriRemote"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>SiriRemote</string>
  <key>CFBundleDisplayName</key><string>Siri Remote</string>
  <key>CFBundleIdentifier</key><string>app.arvin.siriremote.panel</string>
  <key>CFBundleExecutable</key><string>SiriRemote</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSBluetoothAlwaysUsageDescription</key><string>读取 Siri Remote 的触摸与语音数据（绕过系统 HID 驱动）</string>
  <key>NSBluetoothPeripheralUsageDescription</key><string>读取 Siri Remote 的触摸与语音数据</string>
</dict>
</plist>
PLIST

echo "==> 临时签名"
codesign --force --sign - "$APP" >/dev/null 2>&1 || echo "（跳过签名）"

echo "完成：$APP"
echo "双击即可启动，图标会出现在菜单栏右上角。"
