# Siri Remote → Mac

把初代 Siri Remote（A1513 / A1962）当成 Mac 的鼠标、键盘和语音输入设备。

macOS 从 15 起移除了对初代遥控器的原生 HID 支持，官方没有任何可用入口。这个项目绕开系统驱动，
直接读遥控器的按键报文和触摸栅格，再合成成标准的鼠标/键盘事件。

基于 [open-appleremote](https://github.com/a-funk/open-appleremote) 的协议解码与手势引擎，
只把「读设备」和「写事件」两层换成了 macOS 实现。

## 能做什么

| 操作 | 默认行为 |
|---|---|
| 手指在触摸板上滑动 | 移动鼠标指针（带按压防飘、屏幕边界钳制） |
| 按下触摸板 | 左键单击，按住可拖拽 |
| Menu | 右键 |
| 播放/暂停 | Return |
| TV | 在 ChatGPT → WorkBuddy → Claude 之间轮换 |
| 音量 + / − | PageUp / PageDown |
| 麦克风键 | 按住说话 → 本地转写 → 粘到光标处 |

全部可在菜单栏面板里改，改动即时生效。

## 技术要点

这台遥控器在 macOS 上没有任何公开接口，每条通道都是逆向出来的：

| 层 | 情况 |
|---|---|
| 按键 | macOS 把厂商报文 `0xFC` 重映射成 `0xFA`，只剩 2 字节位掩码（`click=0x80`、`menu=0x20`、`playpause=0x08`、`音量+=0x02`、`音量-=0x04`、`TV=0x01`、`Siri=0x10`） |
| 触摸 | 被系统注册成 `AppleMultitouchDevice`，只能通过私有框架 `MultitouchSupport` 读；设备对象每次枚举都是新包装，且 `MTDeviceGetDeviceID` 会 SIGBUS |
| 输出 | `CGEvent` 合成鼠标/键盘；音量在 macOS 26 上只认 `osascript`，系统定义键（NX_SYSDEFINED）已被封 |
| 唤醒 | 按键走 BLE HID 能自己重连，触摸走私有回调**不会**，必须自己重挂 |

细节和踩坑记录在 [`mac/README.md`](mac/README.md)，那份文档比这份长得多。

## 快速开始

```bash
git clone --recursive https://github.com/Arvinwun/apple-tv-remote-a1962-mac.git
cd apple-tv-remote-a1962-mac

python3 -m venv .venv
.venv/bin/python -m pip install hidapi pyobjc-framework-Quartz pyobjc-framework-AVFoundation \
    sounddevice numpy faster-whisper
```

配对（无线必需）：

1. Apple TV 断电，否则遥控器会连回电视
2. 遥控器按住 **Menu + 音量+** 约 5 秒
3. Mac：系统设置 → 蓝牙 → 选列表最下面那串十六进制地址

构建并启动菜单栏程序：

```bash
./mac/panel/build-app.sh
open SiriRemote.app
```

点菜单栏图标 → 「启动」。首次需要在 **系统设置 → 隐私与安全性 → 辅助功能** 里勾选 `SiriRemote.app`，
否则合成的事件会被系统静默丢弃。

命令行方式（不经 App）：

```bash
.venv/bin/python mac/siriremote.py keys            # 键盘模式
.venv/bin/python mac/siriremote.py keys --mouse    # 鼠标模式
.venv/bin/python mac/siriremote.py scan            # 只检查是否识别到遥控器
```

## 已知限制

- **遥控器自带麦克风拿不到。** 两次实测（无线/有线、独占通道、按住说话 10 秒），HID 层只给 2 字节状态位，零音频字节；GATT 直连也只暴露电池和设备信息服务。语音功能用的是 Mac 自己的麦克风。
- **遥控器休眠后触摸层断电**，滑动唤不醒它，必须先按一下按钮。
- **合盖使用内置麦克风是静音的**（硬件停用），语音功能需要开盖、iPhone 连续互通或外接麦。
- 面积类传感器只有粗略分级，手掌误触过滤需要按个人手感标定。

## 目录

```
mac/
  siriremote.py        主程序（scan / watch / keys）
  live_mac.py          数据源：HID 按键 + MultitouchSupport 触摸
  pointer.py           指针合成（绝对坐标累加、边界钳制、按压防飘）
  voice.py             录音 → faster-whisper 转写 → 粘贴
  config.json          配置（面板写入）
  panel/               菜单栏 App（Swift + WKWebView）与本地 HTTP 服务
  calibrate.py         方向标定
  slide_probe.py       滑动诊断（只读，可与主程序同时运行）
  mic_probe.py         麦克风通道验证
win/
  probe_win.py         Windows 侧通道探测
open-appleremote/      上游项目（submodule）
```

## 许可

MIT。上游 [open-appleremote](https://github.com/a-funk/open-appleremote) 同为 MIT。
