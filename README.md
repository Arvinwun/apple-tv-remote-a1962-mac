# Siri Remote → Mac

**English** | [简体中文](README.zh-CN.md)

Turn a first-generation Siri Remote (A1513 / A1962) into a mouse, keyboard and voice input device for macOS.

Apple removed native HID support for the 1st-gen remote in macOS 15 and offers no replacement API. This project bypasses the system driver: it reads the remote's button reports and capacitive touch grid directly, then synthesizes standard mouse and keyboard events.

Protocol decoding and gesture recognition come from [open-appleremote](https://github.com/a-funk/open-appleremote); only the *read device* and *write events* layers are macOS-specific.

## What it does

| Action | Default behaviour |
|---|---|
| Slide on the touch surface | Move the pointer (with click-drift correction and screen-edge clamping) |
| Press the touch surface | Left click; hold and move to drag |
| Menu | Right click |
| Play/Pause | Return |
| TV | Cycle between ChatGPT → WorkBuddy → Claude |
| Volume + / − | PageUp / PageDown |
| Microphone button | Hold to talk → local transcription → paste at cursor |

Everything is configurable from the menu bar panel, and changes apply immediately.

## How it works

Nothing about this remote is documented on macOS. Every channel was reverse-engineered:

| Layer | What we found |
|---|---|
| Buttons | macOS remaps the vendor report `0xFC` to `0xFA`, leaving a 2-byte bitmask (`click=0x80`, `menu=0x20`, `playpause=0x08`, `vol+=0x02`, `vol-=0x04`, `TV=0x01`, `Siri=0x10`) |
| Touch | Exposed as an `AppleMultitouchDevice`; only readable through the private `MultitouchSupport` framework. Device objects are re-wrapped on every enumeration, and `MTDeviceGetDeviceID` triggers SIGBUS |
| Output | `CGEvent` for mouse and keyboard. On macOS 26 the volume keys only respond to `osascript` — `NX_SYSDEFINED` events are silently dropped |
| Wake | Buttons reconnect by themselves over BLE HID; the touch callback does **not**, and must be re-registered manually |

The full reverse-engineering log (in Chinese, ~700 lines) lives in [`mac/README.md`](mac/README.md).

## Getting started

```bash
git clone --recursive https://github.com/Arvinwun/apple-tv-remote-a1962-mac.git
cd apple-tv-remote-a1962-mac

python3 -m venv .venv
.venv/bin/python -m pip install hidapi pyobjc-framework-Quartz pyobjc-framework-AVFoundation \
    sounddevice numpy faster-whisper
```

Pairing (required for wireless use):

1. Power off the Apple TV, otherwise the remote reconnects to it
2. Hold **Menu + Volume Up** on the remote for ~5 seconds
3. On the Mac: System Settings → Bluetooth → pick the hex address at the bottom of the list

Build and launch the menu bar app:

```bash
./mac/panel/build-app.sh
open SiriRemote.app
```

Click the menu bar icon, then **启动** (Start). The first run requires granting
**System Settings → Privacy & Security → Accessibility** to `SiriRemote.app`;
without it macOS silently discards every synthesized event.

Command line, without the app:

```bash
.venv/bin/python mac/siriremote.py keys            # keyboard mode
.venv/bin/python mac/siriremote.py keys --mouse    # pointer mode
.venv/bin/python mac/siriremote.py scan            # check whether the remote is detected
```

## Known limitations

- **The remote's own microphone is unreachable.** Two independent tests (wireless and wired, exclusive HID access, 10 seconds of speech) returned only a 2-byte button state and zero audio bytes; connecting over GATT directly exposes only battery and device-information services. Voice input therefore uses the Mac's own microphone.
- **The touch layer powers down when the remote sleeps** and sliding will not wake it — press any button first.
- **The built-in microphone is muted while the lid is closed** (hardware disabled). Voice input needs the lid open, iPhone Continuity, or an external mic.
- Capacitive area readings are coarse; palm rejection has to be calibrated per user.

## Layout

```
mac/
  siriremote.py        main program (scan / watch / keys)
  live_mac.py          data source: HID buttons + MultitouchSupport frames
  pointer.py           pointer synthesis (absolute accumulation, edge clamp, drift correction)
  voice.py             record → faster-whisper → paste
  config.json          settings (written by the panel)
  panel/               menu bar app (Swift + WKWebView) and local HTTP service
  calibrate.py         direction calibration
  slide_probe.py       read-only sliding diagnostics (runs alongside the main program)
  mic_probe.py         microphone channel verification
win/
  probe_win.py         Windows-side channel probe
open-appleremote/      upstream project (submodule)
```

## License

MIT. The upstream [open-appleremote](https://github.com/a-funk/open-appleremote) is also MIT.
