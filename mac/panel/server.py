#!/usr/bin/env python3
"""遥控器控制面板：本地 HTTP 服务，小米风格界面 + 实时触摸预览 + 键位编辑。

    .venv/bin/python mac/panel/server.py            # 然后浏览器打开 http://127.0.0.1:8787

只监听 127.0.0.1，不对外暴露。
面板本身不打开按键集合（12/1 会被主程序独占），所以可以和 `siriremote.py keys` 同时跑。
"""
import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
MAC = os.path.dirname(HERE)
sys.path.insert(0, MAC)
sys.path.insert(0, os.path.join(os.path.dirname(MAC), "open-appleremote"))

CONFIG_PATH = os.path.join(MAC, "config.json")
PORT = int(os.environ.get("PANEL_PORT", "8787"))

FEED = deque(maxlen=400)      # 实时事件环形缓冲，SSE 客户端按游标取
FEED_LOCK = threading.Lock()
FEED_SEQ = [0]

ROOT = os.path.dirname(MAC)
VENV_PY = os.path.join(ROOT, ".venv", "bin", "python")
MAIN = os.path.join(MAC, "siriremote.py")
MAIN_LOG = "/tmp/siriremote-main.log"
CHILD = {"proc": None}


PS_WORKS = [None]      # None 未知 / True 可用 / False 被环境限制


def _reap():
    """回收自己拉起的子进程，否则它退出后会变成僵尸，pgrep 仍能匹配到 —— 
    后果是「已在运行」永远为真、按钮再也点不动。"""
    proc = CHILD.get("proc")
    if proc is not None:
        try:
            proc.poll()
        except Exception:
            pass


def _pids_via_ps():
    out = subprocess.run(["/bin/ps", "-axo", "pid=,stat=,command="],
                         capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, stat, cmd = parts
        # 必须是我们自己的解释器起的：否则命令行里恰好带这个路径的 shell / grep 也会被误杀
        if not cmd.startswith(VENV_PY) or "mac/siriremote.py" not in cmd or stat.startswith("Z"):
            continue
        if pid_s.isdigit() and int(pid_s) != os.getpid():
            pids.append(int(pid_s))
    return pids


def _pids_via_pgrep():
    """退回方案：ps 被沙箱拦时用。pgrep 会匹配到「命令行里带这个字符串」的无关进程，
    所以不直接信它——拿到 pid 后再用 ps 校验命令行前缀，校验不了就只认自己的子进程。"""
    out = subprocess.run(["pgrep", "-fl", "mac/siriremote.py"],
                         capture_output=True, text=True).stdout
    pids = []
    for line in out.splitlines():
        pid_s, _, cmd = line.partition(" ")
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        if pid == os.getpid():
            continue
        if not cmd.startswith(VENV_PY):
            continue
        pids.append(pid)
    return pids


def main_pids():
    """真正在跑的主程序。优先用 ps 排除僵尸；ps 被限制时退回 pgrep（僵尸已由 _reap 清掉）。"""
    _reap()
    if PS_WORKS[0] is not False:
        try:
            pids = _pids_via_ps()
            PS_WORKS[0] = True
            return pids
        except Exception:
            PS_WORKS[0] = False
    try:
        return _pids_via_pgrep()
    except Exception:
        return []


def start_main():
    pids = main_pids()
    if pids:
        return {"ok": True, "pid": pids[0], "already": True}
    if not os.path.exists(VENV_PY):
        return {"ok": False, "error": "找不到 .venv/bin/python"}
    log = open(MAIN_LOG, "ab", buffering=0)
    try:
        # 不能加 start_new_session=True。setsid 会让子进程脱离会话，
        # TCC 的「负责进程」链因此断掉，系统改按 .venv/bin/python 自身判定权限
        # → 合成的事件全被静默丢弃（表现为「按键部分正常、滑动全死」）。
        args = [VENV_PY, MAIN, "keys"]
        try:                                   # 面板里勾了「调试日志」就把每帧触摸都记下来
            if (load_config() or {}).get("debug"):
                args.append("--debug")
        except Exception:
            pass
        proc = subprocess.Popen(args, cwd=ROOT,
                                stdout=log, stderr=log)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    CHILD["proc"] = proc
    # 等它把设备打开（按键集合要独占，稍等再回状态）
    for _ in range(12):
        time.sleep(0.1)
        if proc.poll() is not None:
            with open(MAIN_LOG, "a", encoding="utf-8") as f:
                f.write("\n[panel] 主程序退出，返回码 %s\n" % proc.returncode)
            return {"ok": False, "error": "主程序退出（%s），见 %s" % (proc.returncode, MAIN_LOG)}
    return {"ok": True, "pid": proc.pid}


def touch_stats():
    """分析最近一段连续触摸：nx/ny 范围、dx/dy 累计、帧数。

    用来分清「滑不动」到底是手没滑够（物理行程小），还是代码把帧丢了。
    """
    with FEED_LOCK:
        rows = list(FEED)
    if not rows:
        return {"ok": False, "error": "还没有触摸数据"}

    # 从末尾往前找第一段连续的 down（抬手后 last=None，中间会有 down=false）
    seg = []
    for e in reversed(rows):
        if not e.get("down"):
            if seg:
                break
            continue
        seg.append(e)
    seg.reverse()
    if len(seg) < 5:
        return {"ok": False, "error": "最近一段只有 %d 帧，先滑一下再试" % len(seg)}

    xs = [float(e.get("x") or 0) for e in seg]
    ys = [float(e.get("y") or 0) for e in seg]
    sizes = sorted(float(e.get("size") or 0) for e in seg)
    dur = (seg[-1].get("t", 0) - seg[0].get("t", 0)) or 0
    return {
        "ok": True,
        "frames": len(seg),
        "seconds": round(dur, 2),
        "x_first": round(xs[0], 1), "x_last": round(xs[-1], 1),
        "y_first": round(ys[0], 1), "y_last": round(ys[-1], 1),
        "x_span": round(max(xs) - min(xs), 1), "y_span": round(max(ys) - min(ys), 1),
        "dx_sum": round(xs[-1] - xs[0], 1), "dy_sum": round(ys[-1] - ys[0], 1),
        "size_p50": round(sizes[len(sizes) // 2], 4), "size_max": round(sizes[-1], 4),
    }


def palm_calibrate(seconds=6.0):
    """根据最近的触摸数据算出用户实际滑动时的接触面积，给出建议阈值。

    背景：这块触摸板只有 5 档面积，拇指滑动时经常就是 0.038~0.080，
    和手掌轻搭区分不开。固定阈值必然误伤，只能按每个人自己的手感来定。
    """
    since = time.time() - seconds
    with FEED_LOCK:
        rows = [e for e in FEED if e.get("t", 0) >= since and e.get("down")]
    sizes = sorted(float(e.get("size") or 0) for e in rows)
    if len(sizes) < 20:
        return {"ok": False, "error": "数据太少（%d 帧），请按住触摸板正常滑动几秒再试" % len(sizes)}

    def pct(q):
        return sizes[min(len(sizes) - 1, int(len(sizes) * q))]

    p50, p90, p95, mx = pct(0.50), pct(0.90), pct(0.95), sizes[-1]
    suggest = min(4.0, max(0.05, round(p95 * 1.2, 2)))
    return {"ok": True, "frames": len(sizes), "p50": round(p50, 4),
            "p90": round(p90, 4), "p95": round(p95, 4), "max": round(mx, 4),
            "suggest": suggest}


def run_diag():
    """用**和 start_main 完全相同的参数**跑一次权限/鼠标自检。

    这样结论才和主程序可比：同样的父进程、同样的解释器、同样的 cwd。
    输出一行 JSON，直接透传给前端/curl。
    """
    path = os.path.join(HERE, "diag.py")
    try:
        proc = subprocess.Popen([VENV_PY, path], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=10)
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    line = (out or "").strip().splitlines()
    if not line:
        return {"ok": False, "error": (err or "无输出").strip()[:400]}
    try:
        data = json.loads(line[-1])
    except Exception:
        return {"ok": False, "error": "输出无法解析: " + line[-1][:300]}
    data["ok"] = True
    data["child_of"] = os.getpid()
    return data


def stop_main():
    pids = main_pids()
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    for _ in range(20):
        if not main_pids():
            break
        time.sleep(0.1)
    left = main_pids()
    for pid in left:                       # 还不走就硬杀
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    CHILD["proc"] = None
    return {"ok": True, "stopped": pids}


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


STATE = {"present": False, "collections": 0, "trusted": None}
PROBE = {"proc": None, "tries": 0, "no_hid": False}


def device_state():
    """设备状态来自采集子进程的缓存，绝不在这里调 hid.enumerate()。

    原因见 probe.py：enumerate 会和触摸那边的 CFRunLoop 打架，直接把进程打崩。
    """
    state = dict(STATE)
    pids = main_pids()
    state["running"] = bool(pids)
    state["pid"] = pids[0] if pids else None
    state["probe"] = PROBE["proc"] is not None and PROBE["proc"].poll() is None
    # 主程序自己的权限（采集进程的 trusted 不能代表它，两者可能不一致）
    state["main_trusted"] = None
    try:
        with open("/tmp/siriremote-main.perm", encoding="utf-8") as f:
            state["main_trusted"] = f.read().strip() == "1"
    except Exception:
        pass
    return state


def _probe_reader(proc):
    """读子进程的 JSON 行，更新 STATE / FEED；子进程退出就重拉。"""
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except Exception:
            continue
        if ev.get("kind") == "state":
            STATE["present"] = bool(ev.get("present"))
            STATE["collections"] = int(ev.get("collections") or 0)
            if "trusted" in ev:
                STATE["trusted"] = ev["trusted"]
        elif ev.get("kind") == "touch":
            with FEED_LOCK:
                FEED_SEQ[0] += 1
                ev["seq"] = FEED_SEQ[0]
                ev["t"] = time.time()          # 服务器收到的时间，标定时要按时间窗口取
                ev.pop("kind", None)
                FEED.append(ev)
    try:
        proc.wait(timeout=0.2)
    except Exception:
        pass


def _kill_stale_probes():
    """上次服务被杀时留下的小采集进程，先清掉，避免同时跑两个 MT/HID 客户端。"""
    try:
        out = subprocess.run(["pgrep", "-fl", "panel/probe.py"], capture_output=True, text=True).stdout
    except Exception:
        return
    for line in out.splitlines():
        pid_s, _, cmd = line.partition(" ")
        if not pid_s.isdigit():
            continue
        pid = int(pid_s)
        # 必须是我们的解释器起的进程：光靠 pgrep -f 会误伤命令行里提到 probe.py 的 shell
        if not cmd.startswith(VENV_PY):
            continue
        if pid in (os.getpid(), os.getppid()):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass


def _spawn_probe():
    args = [VENV_PY, os.path.join(HERE, "probe.py")]
    if PROBE["no_hid"]:
        args.append("--no-hid")
    try:
        proc = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, bufsize=1)
    except Exception:
        return None
    PROBE["proc"] = proc
    threading.Thread(target=_probe_reader, args=(proc,), daemon=True).start()
    return proc


def start_touch_feed():
    """起采集子进程，并在它崩溃时自动重启（连续崩 3 次就只保留触摸、放弃 HID 轮询）。"""
    _kill_stale_probes()
    _spawn_probe()

    def watchdog():
        while True:
            proc = PROBE["proc"]
            if proc is not None and proc.poll() is not None:
                PROBE["tries"] += 1
                if PROBE["tries"] >= 3:
                    PROBE["no_hid"] = True          # HID 轮询是主要崩溃源，崩多了就先关掉
                time.sleep(min(5.0, 0.5 * PROBE["tries"]))
                _spawn_probe()
            time.sleep(1.0)

    threading.Thread(target=watchdog, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass

    def handle_error(self, request, client_address):
        """浏览器/curl 断开连接是常态，别把 traceback 刷满终端。"""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return
        super().handle_error(request, client_address)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # 根路径就给紧凑版：菜单栏弹窗和浏览器看到的是同一套界面。
        # 完整网页版留在 /full（不再从界面链接过去，只作调试后备）。
        if self.path in ("/", "/index.html", "/compact.html", "/full"):
            name = "index.html" if self.path == "/full" else "compact.html"
            with open(os.path.join(HERE, name), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            self._send(200, json.dumps(load_config(), ensure_ascii=False))
        elif self.path == "/api/touch_stats":
            self._send(200, json.dumps(touch_stats(), ensure_ascii=False))
        elif self.path.startswith("/api/palm_calibrate"):
            secs = 6.0
            if "?" in self.path:
                q = self.path.split("?", 1)[1]
                for part in q.split("&"):
                    if part.startswith("s="):
                        try:
                            secs = float(part[2:])
                        except ValueError:
                            pass
            self._send(200, json.dumps(palm_calibrate(secs), ensure_ascii=False))
        elif self.path == "/api/diag":
            self._send(200, json.dumps(run_diag(), ensure_ascii=False))
        elif self.path == "/api/status":
            self._send(200, json.dumps(device_state(), ensure_ascii=False))
        elif self.path.startswith("/events"):
            self.stream_events()
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path in ("/api/run", "/api/stop"):
            res = start_main() if self.path == "/api/run" else stop_main()
            self._send(200, json.dumps(res, ensure_ascii=False))
            return
        if self.path != "/api/config":
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            n = int(self.headers.get("Content-Length", "0"))
            cfg = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(cfg, dict):
                raise ValueError("配置必须是对象")
            save_config(cfg)
            self._send(200, json.dumps({"ok": True}, ensure_ascii=False))
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))

    def stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        cursor = 0
        try:
            while True:
                with FEED_LOCK:
                    items = [e for e in FEED if e["seq"] > cursor]
                for e in items:
                    cursor = e["seq"]
                    payload = "data: %s\n\n" % json.dumps(e)
                    self.wfile.write(payload.encode("utf-8"))
                if not items:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                threading.Event().wait(0.06)
        except (BrokenPipeError, ConnectionResetError):
            return


def _cleanup():
    proc = PROBE.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    if CHILD["proc"] is not None or main_pids():
        stop_main()


atexit.register(_cleanup)
signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), os._exit(0)))


def main():
    start_touch_feed()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    print("控制面板： http://127.0.0.1:%d" % PORT, flush=True)
    print("配置写到 %s" % CONFIG_PATH, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
