#!/usr/bin/env python3
"""termdash — tiny self-hosted dashboard server. stdlib only, made for Termux.

  python server.py            -> serves on 0.0.0.0:8080
  python server.py 9000       -> custom port

Endpoints:
  GET  /             dashboard
  GET  /api/stats    tablet stats (battery via termux-api if installed)
  GET  /api/data     saved todos/notes/config
  POST /api/data     save todos/notes/config (JSON body)
  GET  /api/console  console lines (?since=<id>)
  POST /api/exec     run a shell command (only if TERMDASH_TOKEN is set)
"""
import json, os, shutil, subprocess, sys, time, threading, collections
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8080))
TOKEN = os.environ.get("TERMDASH_TOKEN", "")  # optional: require ?t=TOKEN for writes
lock = threading.Lock()
START = time.time()

# ---------- console ring buffer ----------
CON = collections.deque(maxlen=1500)
con_id = 0
con_lock = threading.Lock()


def log(src, text):
    global con_id
    with con_lock:
        for line in str(text).rstrip("\n").split("\n"):
            con_id += 1
            CON.append({"id": con_id, "ts": time.time(), "src": src, "text": line[:2000]})
    if src in ("sys", "err"):
        print(f"[{src}] {text}", flush=True)


def tailer():
    """follow every file listed in cfg.logs, like tail -f"""
    pos = {}
    while True:
        try:
            files = (load_data().get("cfg") or {}).get("logs") or []
            for f in files:
                path = os.path.expanduser(f)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    if pos.get(path) != -1:
                        log("err", f"can't read log {f}")
                        pos[path] = -1
                    continue
                if pos.get(path, -1) < 0:
                    pos[path] = max(0, size - 3000)   # show last bit on first sight
                    log("sys", f"tailing {f}")
                if size < pos[path]:
                    pos[path] = 0                      # file truncated/rotated
                if size > pos[path]:
                    with open(path, "rb") as fh:
                        fh.seek(pos[path])
                        chunk = fh.read(min(size - pos[path], 200_000))
                    pos[path] += len(chunk)
                    txt = chunk.decode("utf-8", "replace")
                    if txt.strip():
                        log(os.path.basename(path), txt)
        except Exception as e:
            log("err", f"tailer: {e}")
        time.sleep(1)


def run_cmd(cmd):
    log("$", cmd)
    try:
        p = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, cwd=os.path.expanduser("~"))
        t0 = time.time()
        for line in p.stdout:
            log(">", line)
            if time.time() - t0 > 300:
                p.kill(); log("err", "killed after 5 min"); break
        log("sys", f"exit {p.wait()}")
    except Exception as e:
        log("err", str(e))


def sh(cmd, timeout=4):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:
        return None


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return None


_batt_cache = {"t": 0, "v": None}


def battery():
    # termux-battery-status can be slow, cache 20s
    if time.time() - _batt_cache["t"] < 20:
        return _batt_cache["v"]
    raw = sh(["termux-battery-status"], timeout=6) if shutil.which("termux-battery-status") else None
    v = None
    if raw:
        try:
            j = json.loads(raw)
            v = {"pct": j.get("percentage"), "status": j.get("status"),
                 "plugged": j.get("plugged"), "temp": j.get("temperature")}
        except Exception:
            pass
    _batt_cache.update(t=time.time(), v=v)
    return v


def stats():
    s = {"time": time.time(), "server_uptime": time.time() - START}
    up = read("/proc/uptime")
    if up:
        s["uptime"] = float(up.split()[0])
    la = read("/proc/loadavg")
    if la:
        s["load"] = [float(x) for x in la.split()[:3]]
    s["cpus"] = os.cpu_count()
    mi = read("/proc/meminfo")
    if mi:
        m = {}
        for line in mi.splitlines():
            k, _, v = line.partition(":")
            m[k] = int(v.split()[0]) * 1024 if v.split() else 0
        total = m.get("MemTotal", 0)
        avail = m.get("MemAvailable", m.get("MemFree", 0))
        s["mem"] = {"total": total, "used": total - avail}
    disks = []
    for label, p in (("termux", os.path.expanduser("~")), ("sdcard", "/sdcard")):
        try:
            d = shutil.disk_usage(p)
            disks.append({"label": label, "total": d.total, "used": d.used})
        except Exception:
            pass
    s["disks"] = disks
    s["battery"] = battery()
    s["exec"] = bool(TOKEN)
    return s


def load_data():
    with lock:
        try:
            with open(DATA_FILE) as f:
                return json.load(f)
        except Exception:
            return {}


def save_data(d):
    with lock:
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, DATA_FILE)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/stats":
            return self.send(200, json.dumps(stats()))
        if path == "/api/data":
            return self.send(200, json.dumps(load_data()))
        if path == "/api/console":
            q = dict(x.split("=", 1) for x in self.path.partition("?")[2].split("&") if "=" in x)
            since = int(q.get("since", 0) or 0)
            with con_lock:
                lines = [l for l in CON if l["id"] > since]
            return self.send(200, json.dumps({"lines": lines[-500:], "last": con_id}))
        self.send(404, '{"error":"nope"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/api/data", "/api/exec"):
            return self.send(404, '{"error":"nope"}')
        if TOKEN and f"t={TOKEN}" not in self.path:
            return self.send(403, '{"error":"bad token"}')
        n = int(self.headers.get("Content-Length", 0))
        if path == "/api/exec":
            if not TOKEN:
                return self.send(403, '{"error":"exec disabled, set TERMDASH_TOKEN"}')
            cmd = (json.loads(self.rfile.read(n) or b"{}").get("cmd") or "").strip()
            if cmd:
                threading.Thread(target=run_cmd, args=(cmd,), daemon=True).start()
            return self.send(200, '{"ok":true}')
        if n > 2_000_000:
            return self.send(413, '{"error":"too big"}')
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
            d = load_data()
            d.update(body)
            save_data(d)
            if "cfg" in body:
                log("sys", "config saved")
            self.send(200, '{"ok":true}')
        except Exception as e:
            self.send(400, json.dumps({"error": str(e)}))


if __name__ == "__main__":
    ip = sh(["sh", "-c", "ifconfig 2>/dev/null | grep -oE 'inet (addr:)?192\\.168\\.[0-9.]+' | head -1"]) or ""
    log("sys", f"termdash up -> http://localhost:{PORT}")
    if ip:
        log("sys", f"on your wifi -> http://{ip.split()[-1].replace('addr:', '')}:{PORT}")
    log("sys", "exec " + ("ENABLED (token set)" if TOKEN else "disabled (set TERMDASH_TOKEN to enable)"))
    threading.Thread(target=tailer, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
