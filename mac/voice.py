#!/usr/bin/env python3
"""按住说话 → 本地转写 → 粘贴到光标处。

设计要点：

- **模型常驻**。whisper 加载要 1~3 秒，每次按都重新加载会明显卡顿。这里只加载一次，
  也可以在启动时用 `preload()` 后台预热（第一次按就能立即出字）。
- **录音用 sounddevice（PortAudio）**，16 kHz 单声道——正好是 whisper 的输入格式，省一次重采样。
- **转写在后台线程**做，绝不阻塞遥控器的事件循环（否则按住说话时按键会卡）。
- **粘贴走剪贴板 + Cmd+V**。`CGEventKeyboardSetUnicodeString` 在部分 Electron 输入框里不生效，
  剪贴板这条路最稳。
"""
import os
import re
import subprocess
import threading
import time

SAMPLE_RATE = 16000
MIN_SECONDS = 0.35          # 短于这个长度不当语音，避免误触时弹出一堆幻觉文字
TRAILING_SILENCE = 0.25     # 松开后多录一点，避免最后一个字被切掉


def _clean(text, lang):
    """whisper 的中文输出常在字之间插空格，去掉它们（只去中文之间，别动英文）。"""
    text = text.strip()
    if lang.startswith("zh"):
        text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
        text = re.sub(r"\s+([，。！？；：、])", r"\1", text)
    return text.strip()


class VoiceInput:
    def __init__(self, model_size="base", lang="zh", info=None, on_text=None):
        self.model_size = model_size
        self.lang = lang
        self.info = info or (lambda s: None)
        self.on_text = on_text or self.paste
        self._model = None
        self._model_lock = threading.Lock()
        self._buf = []
        self._stream = None
        self._recording = False
        self._last_text = ""
        self._silent = 0        # 连续「录到静音」的次数，用来避免反复弹通知

    # ---------- 模型 ----------
    def _ensure_model(self):
        with self._model_lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel
            t = time.monotonic()
            self.info("加载语音模型 %s …" % self.model_size)
            self._model = WhisperModel(self.model_size, device="cpu", compute_type="int8")
            self.info("语音模型就绪（%.1fs）" % (time.monotonic() - t))
            return self._model

    def preload(self):
        """后台预热，别让第一次按键等太久。"""
        threading.Thread(target=self._ensure_model, daemon=True).start()

    # ---------- 录音 ----------
    def start(self):
        """开始录音。返回 False 表示麦克风打不开（调用方可以降级）。"""
        if self._recording:
            return True
        import sounddevice as sd
        self._buf = []
        self._recording = True

        def cb(indata, frames, t, status):
            if self._recording:
                self._buf.append(indata.copy())

        try:
            self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                          blocksize=int(SAMPLE_RATE * 0.05), callback=cb)
            self._stream.start()
            self.info("● 录音中…")
            return True
        except Exception as e:
            self._recording = False
            self._stream = None
            self.info("打不开麦克风: %s" % e)
            self._beep()
            return False

    def stop(self):
        if not self._recording:
            return
        time.sleep(TRAILING_SILENCE)          # 先等：这期间回调还要继续收，别提前关掉
        self._recording = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:
            pass
        self._stream = None

        if not self._buf:
            self.info("没录到音频")
            return
        import numpy as np
        audio = np.concatenate(self._buf, axis=0).flatten()
        self._buf = []
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            self.info("录音太短（%.2fs），忽略" % (len(audio) / SAMPLE_RATE))
            return
        import numpy as np
        peak = float(np.abs(audio).max())
        if peak < 1e-5:
            self._silent += 1
            self.info("麦克风是静音的：录了 %.1fs，峰值 %.7f。常见原因："
                      "笔记本合盖时内置麦克风会停用；或系统没给麦克风权限。" % (len(audio) / SAMPLE_RATE, peak))
            if self._silent <= 2:      # 只说两次，避免每次按都弹、都响
                self._notify("麦克风没有声音。笔记本合盖时内置麦克风会停用，"
                             "接外接麦或开盖后再试。")
                self._beep()
            return
        self._silent = 0
        threading.Thread(target=self._transcribe, args=(audio,), daemon=True).start()

    # ---------- 转写 ----------
    def _transcribe(self, audio):
        try:
            model = self._ensure_model()
            t = time.monotonic()
            segments, _info = model.transcribe(
                audio, language=self.lang, beam_size=1,
                vad_filter=True, vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
                initial_prompt="以下是普通话口语，请加上标点符号。",
            )
            text = _clean("".join(s.text for s in segments), self.lang)
            cost = time.monotonic() - t
            if not text:
                self.info("（没有识别到内容）")
                self._beep()
                return
            self._last_text = text
            self.info("转写 %.1fs 音频用了 %.2fs：%s" % (len(audio) / SAMPLE_RATE, cost, text))
            self.on_text(text)
        except Exception as e:
            self.info("转写失败: %s" % e)
            self._beep()

    # ---------- 输出 ----------
    @staticmethod
    def paste(text):
        """写剪贴板再发 Cmd+V。比逐字符输入稳，Electron 输入框也认。"""
        from AppKit import NSPasteboard
        from Quartz import (CGEventCreateKeyboardEvent, CGEventPost, CGEventSetFlags,
                            kCGHIDEventTap)
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, "public.utf8-plain-text")
        time.sleep(0.03)                     # 给剪贴板一点时间
        for down in (True, False):
            ev = CGEventCreateKeyboardEvent(None, 9, down)   # kVK_ANSI_V
            CGEventSetFlags(ev, 0x100000 if down else 0)     # kCGEventFlagMaskCommand
            CGEventPost(kCGHIDEventTap, ev)
            time.sleep(0.012)

    @staticmethod
    def _notify(msg):
        try:
            subprocess.Popen(["osascript", "-e",
                              'display notification "%s" with title "语音输入"' % msg],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass

    @staticmethod
    def _beep():
        try:
            subprocess.Popen(["osascript", "-e", "beep"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass


if __name__ == "__main__":
    # 自测：录 3 秒 → 转写 → 打印（不粘贴）
    import sys
    v = VoiceInput(model_size=os.environ.get("VOICE_MODEL", "small"),
                   on_text=lambda t: print("\n识别结果:", t))
    print("预热模型…")
    v.preload()
    print("3 秒后开始录音，说点什么（3 秒）")
    time.sleep(3)
    v.start()
    time.sleep(3)
    v.stop()
    time.sleep(4)
    print("完成")
