#!/usr/bin/env python3
"""termdash — tiny self-hosted dashboard server. stdlib only, made for Termux.

  python server.py            -> serves on 0.0.0.0:8080
  python server.py 9000       -> custom port
  TERMDASH_TOKEN=xyz python server.py   -> other devices can run commands with ?t=xyz

Apps (bots, sites, scripts) are started/supervised by this server and stop when it stops.
Commands/kill/app control always work from the tablet itself (localhost).
From other devices they need the token. No token set = view only for them.
"""
import json, os, re, shutil, subprocess, sys, time, threading, collections, signal
import urllib.request, ssl
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "data.json")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8080))
TOKEN = os.environ.get("TERMDASH_TOKEN", "")
HOME = os.path.expanduser("~")
START = time.time()
lock = threading.Lock()


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


# ---------------- persistence ----------------
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


def cfg():
    return load_data().get("cfg") or {}


# ---------------- console ----------------
CON = collections.deque(maxlen=1500)
con_id = 0
con_lock = threading.Lock()
CWD = HOME
RUNNING = {}   # pid -> Popen


def log(src, text):
    global con_id
    with con_lock:
        for line in str(text).rstrip("\n").split("\n"):
            con_id += 1
            CON.append({"id": con_id, "ts": time.time(), "src": src, "text": line[:2000]})
    if src in ("sys", "err"):
        print(f"[{src}] {text}", flush=True)


def tailer():
    """follow every file in cfg.logs like tail -f"""
    pos = {}
    while True:
        try:
            for f in cfg().get("logs") or []:
                path = os.path.expanduser(f)
                try:
                    size = os.path.getsize(path)
                except OSError:
                    if pos.get(path) != -1:
                        log("err", f"can't read log {f}")
                        pos[path] = -1
                    continue
                if pos.get(path, -1) < 0:
                    pos[path] = max(0, size - 3000)
                    log("sys", f"tailing {f}")
                if size < pos[path]:
                    pos[path] = 0
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
    global CWD
    log("$", cmd)
    c = cmd.strip()
    if c == "cd" or c.startswith("cd "):
        target = os.path.expanduser(c[2:].strip() or "~")
        target = os.path.normpath(os.path.join(CWD, target))
        if os.path.isdir(target):
            CWD = target
            log("sys", f"cwd {CWD.replace(HOME, '~', 1)}")
        else:
            log("err", f"cd: no such dir: {target}")
        return
    if c in ("clear", "cls"):
        return
    p = None
    try:
        p = subprocess.Popen(cmd, shell=True, cwd=CWD, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, bufsize=1, start_new_session=True,
                             env={**os.environ, "TERM": "dumb", "COLUMNS": "120"})
        RUNNING[p.pid] = p
        for line in p.stdout:
            log(">", line)
        code = p.wait()
        log("sys" if code == 0 else "err", f"exit {code}")
    except Exception as e:
        log("err", str(e))
    finally:
        if p:
            RUNNING.pop(p.pid, None)


def stop_all():
    n = 0
    for pid, p in list(RUNNING.items()):
        try:
            os.killpg(pid, signal.SIGTERM); n += 1
        except Exception:
            pass
    log("sys", f"^C sent to {n} command(s)" if n else "nothing running")


# ---------------- processes (own user, read from /proc) ----------------
CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
MYPID = os.getpid()
_scan = {"t": 0, "ticks": {}, "list": []}
_scan_lock = threading.Lock()


def scan():
    """all our processes with cpu% / rss / pgrp, cached 1.5s"""
    with _scan_lock:
        now = time.time()
        if now - _scan["t"] < 1.5:
            return _scan["list"]
        dt = (now - _scan["t"]) if _scan["t"] else None
        out, ticks = [], {}
        boot_up = float((read("/proc/uptime") or "0").split()[0] or 0)
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            st = read(f"/proc/{d}/stat")
            cl = read(f"/proc/{d}/cmdline")
            if not st or not cl:
                continue
            try:
                rest = st[st.rindex(")") + 2:].split()
                t = int(rest[11]) + int(rest[12])
                pgrp = int(rest[2])
                started = int(rest[19]) / CLK
                rss = int((read(f"/proc/{d}/statm") or "0 0").split()[1]) * PAGE
            except Exception:
                continue
            pid = int(d)
            ticks[pid] = t
            prev = _scan["ticks"].get(pid)
            cpu = ((t - prev) / CLK / dt * 100) if (dt and prev is not None) else 0
            out.append({"pid": pid, "pgrp": pgrp, "cpu": round(cpu, 1), "rss": rss,
                        "age": max(0, boot_up - started) if boot_up else None,
                        "cmd": cl.replace("\0", " ").strip()[:160], "self": pid == MYPID})
        _scan.update(t=now, ticks=ticks, list=out)
        return out


def procs():
    groups = {st["pid"]: n for n, st in APPS.items() if st.get("proc") and st["proc"].poll() is None}
    out = []
    for p in scan():
        q = dict(p)
        q["app"] = groups.get(p["pgrp"])
        out.append(q)
    out.sort(key=lambda p: (p["cpu"], p["rss"]), reverse=True)
    return out[:15]


# ---------------- apps (built-in process manager) ----------------
APPS = {}   # name -> {proc, pid, status, started, restarts, backoff, want, last_exit}
LOGDIR = os.path.join(HERE, "logs")
RUNDIR = os.path.join(HERE, "run")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
safe = lambda n: re.sub(r"[^\w.-]", "_", n)


def app_cfg(name):
    for a in cfg().get("apps") or []:
        if a.get("name") == name:
            return a


def app_log(name, text):
    text = ANSI.sub("", str(text)).replace("\r", "")
    log(name, text)
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        path = os.path.join(LOGDIR, safe(name) + ".log")
        if os.path.exists(path) and os.path.getsize(path) > 2_000_000:
            os.replace(path, path + ".1")
        with open(path, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip("\n") + "\n")
    except Exception:
        pass


def _starttime(pid):
    st = read(f"/proc/{pid}/stat")
    try:
        return st[st.rindex(")") + 2:].split()[19]
    except Exception:
        return None


def kill_stale(name):
    """kill a leftover instance from a previous dash run (so the bot never runs twice)"""
    pf = os.path.join(RUNDIR, safe(name) + ".pid")
    try:
        pid, stt = open(pf).read().split()
        pid = int(pid)
        if _starttime(pid) == stt:
            os.killpg(pid, signal.SIGTERM)
            log("sys", f"killed leftover {name} (pid {pid})")
            time.sleep(1)
    except Exception:
        pass
    try:
        os.remove(pf)
    except Exception:
        pass


def start_app(name, manual=True):
    a = app_cfg(name)
    if not a:
        return log("err", f"no app called {name}")
    st = APPS.setdefault(name, {"status": "stopped", "restarts": 0, "proc": None})
    if st.get("proc") and st["proc"].poll() is None:
        return app_log(name, "[termdash] already running")
    if manual:
        st["restarts"], st["backoff"] = 0, 2
    st["want"] = True
    d = os.path.expanduser(a.get("dir") or "~")
    if not os.path.isdir(d):
        st["status"] = "crashed"
        return app_log(name, f"[termdash] error: folder not found: {d}")
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "FORCE_COLOR": "0", "NO_COLOR": "1"}
    try:
        p = subprocess.Popen(a["cmd"], shell=True, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, bufsize=1, errors="replace",
                             start_new_session=True, env=env)
    except Exception as e:
        st["status"] = "crashed"
        return app_log(name, f"[termdash] error: failed to start: {e}")
    st.update(proc=p, pid=p.pid, status="running", started=time.time())
    try:
        os.makedirs(RUNDIR, exist_ok=True)
        with open(os.path.join(RUNDIR, safe(name) + ".pid"), "w") as f:
            f.write(f"{p.pid} {_starttime(p.pid)}")
    except Exception:
        pass
    app_log(name, f"[termdash] started (pid {p.pid}): {a['cmd']}")
    threading.Thread(target=watch_app, args=(name, p), daemon=True).start()


def watch_app(name, p):
    for line in p.stdout:
        app_log(name, line)
    code = p.wait()
    st = APPS.get(name)
    if not st or st.get("proc") is not p:
        return
    try:
        os.remove(os.path.join(RUNDIR, safe(name) + ".pid"))
    except Exception:
        pass
    st["last_exit"] = code
    if not st.get("want"):
        st["status"] = "stopped"
        return app_log(name, f"[termdash] stopped (exit {code})")
    a = app_cfg(name)
    if a and a.get("restart", True):
        if time.time() - st["started"] > 60:
            st["backoff"] = 2
        delay = st.get("backoff", 2)
        st["backoff"] = min(60, delay * 2)
        st["status"] = "restarting"
        st["restarts"] += 1
        app_log(name, f"[termdash] crashed (exit {code}), restarting in {delay}s")
        log("err", f"{name} crashed (exit {code})")
        time.sleep(delay)
        if st.get("want") and st.get("proc") is p:
            start_app(name, manual=False)
    else:
        st["status"] = "crashed"
        app_log(name, f"[termdash] crashed (exit {code}), auto-restart is off")
        log("err", f"{name} crashed (exit {code})")


def stop_app(name):
    st = APPS.get(name)
    if not st:
        return
    st["want"] = False
    p = st.get("proc")
    if not p or p.poll() is not None:
        st["status"] = "stopped"
        return
    st["status"] = "stopping"
    try:
        os.killpg(p.pid, signal.SIGTERM)
    except Exception:
        pass
    for _ in range(60):
        if p.poll() is not None:
            break
        time.sleep(0.1)
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGKILL)
            app_log(name, "[termdash] didn't stop in 6s, force killed")
        except Exception:
            pass


def restart_app(name):
    stop_app(name)
    time.sleep(0.3)
    start_app(name)


def deploy_app(name):
    """git pull + install deps + restart"""
    a = app_cfg(name)
    if not a:
        return
    d = os.path.expanduser(a.get("dir") or "~")
    steps = []
    if os.path.isdir(os.path.join(d, ".git")):
        steps.append("git pull")
    if os.path.exists(os.path.join(d, "package.json")):
        steps.append("npm install --no-audit --no-fund")
    if os.path.exists(os.path.join(d, "requirements.txt")):
        steps.append("pip install -r requirements.txt")
    if a.get("build"):
        steps.append(a["build"])
    if not steps:
        return app_log(name, "[termdash] nothing to deploy (no .git / package.json / requirements.txt)")
    st = APPS.setdefault(name, {"status": "stopped", "restarts": 0, "proc": None})
    was_running = bool(st.get("proc")) and st["proc"].poll() is None
    if not was_running:
        st["status"] = "deploying"
    for cmd in steps:
        app_log(name, f"[deploy] $ {cmd}")
        p = subprocess.Popen(cmd, shell=True, cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, text=True, errors="replace")
        for line in p.stdout:
            app_log(name, "[deploy] " + line)
        if p.wait() != 0:
            if st["status"] == "deploying":
                st["status"] = "stopped"
            return app_log(name, f"[deploy] error: '{cmd}' failed, not restarting")
    app_log(name, "[deploy] done, restarting")
    restart_app(name)


def apps_status():
    groups = collections.defaultdict(lambda: [0.0, 0])
    for p in scan():
        g = groups[p["pgrp"]]
        g[0] += p["cpu"]; g[1] += p["rss"]
    out = []
    for a in cfg().get("apps") or []:
        st = APPS.get(a.get("name"), {})
        alive = bool(st.get("proc")) and st["proc"].poll() is None
        cpu, rss = groups[st["pid"]] if alive else (0, 0)
        out.append({**a, "status": st.get("status", "stopped"), "pid": st.get("pid") if alive else None,
                    "uptime": time.time() - st["started"] if alive else None,
                    "restarts": st.get("restarts", 0), "last_exit": st.get("last_exit"),
                    "cpu": round(cpu, 1), "rss": rss})
    return out


def stop_everything(*_):
    ts = [threading.Thread(target=stop_app, args=(n,)) for n in list(APPS)]
    [t.start() for t in ts]
    [t.join(8) for t in ts]
    os._exit(0)


# ---------------- uptime checks ----------------
CHECKS = {}   # name -> {url, hist:[ms|None], code, last}
_ctx = ssl.create_default_context()


def check_one(c):
    url = c.get("url", "")
    t0 = time.time()
    code, ms = None, None
    try:
        req = urllib.request.Request(url, method="GET", headers={"User-Agent": "termdash/1"})
        with urllib.request.urlopen(req, timeout=8, context=_ctx) as r:
            r.read(256)
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = None
    if code is not None:
        ms = round((time.time() - t0) * 1000)
    name = c.get("name") or url
    prev = CHECKS.get(name, {"hist": []})
    was_up = prev.get("up")
    up = code is not None and code < 500
    hist = (prev["hist"] + [ms if up else None])[-40:]
    CHECKS[name] = {"url": url, "hist": hist, "code": code, "ms": ms, "up": up, "last": time.time()}
    if was_up is not None and was_up != up:
        log("sys" if up else "err", f"check {name}: {'UP' if up else 'DOWN'} ({code})")


def checker():
    while True:
        lst = cfg().get("checks") or []
        names = {c.get("name") or c.get("url") for c in lst}
        for k in list(CHECKS):
            if k not in names:
                CHECKS.pop(k)
        threads = [threading.Thread(target=check_one, args=(c,), daemon=True) for c in lst]
        [t.start() for t in threads]
        [t.join(10) for t in threads]
        time.sleep(max(15, int(cfg().get("checkEverySec", 60))))


# ---------------- stats ----------------
_batt = {"t": 0, "v": None}


def battery():
    if time.time() - _batt["t"] < 20:
        return _batt["v"]
    raw = sh(["termux-battery-status"], timeout=6) if shutil.which("termux-battery-status") else None
    v = None
    if raw:
        try:
            j = json.loads(raw)
            v = {"pct": j.get("percentage"), "status": j.get("status"),
                 "plugged": j.get("plugged"), "temp": j.get("temperature")}
        except Exception:
            pass
    _batt.update(t=time.time(), v=v)
    return v


def stats():
    s = {"time": time.time(), "server_uptime": time.time() - START, "cpus": os.cpu_count()}
    up = read("/proc/uptime")
    if up:
        s["uptime"] = float(up.split()[0])
    la = read("/proc/loadavg")
    if la:
        s["load"] = [float(x) for x in la.split()[:3]]
    mi = read("/proc/meminfo")
    if mi:
        m = {}
        for line in mi.splitlines():
            k, _, v = line.partition(":")
            m[k] = int(v.split()[0]) * 1024 if v.split() else 0
        total = m.get("MemTotal", 0)
        s["mem"] = {"total": total, "used": total - m.get("MemAvailable", m.get("MemFree", 0))}
    disks = []
    for label, p in (("termux", HOME), ("sdcard", "/sdcard")):
        try:
            d = shutil.disk_usage(p)
            disks.append({"label": label, "total": d.total, "used": d.used})
        except Exception:
            pass
    s["disks"] = disks
    s["battery"] = battery()
    s["cwd"] = CWD.replace(HOME, "~", 1)
    s["running"] = len(RUNNING)
    s["apps_up"] = sum(1 for st in APPS.values() if st.get("proc") and st["proc"].poll() is None)
    return s


# ---------------- http ----------------
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def authed(self):
        if self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            return True
        return bool(TOKEN) and f"t={TOKEN}" in self.path

    def send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def qs(self):
        return dict(x.split("=", 1) for x in self.path.partition("?")[2].split("&") if "=" in x)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/stats":
            s = stats(); s["exec"] = self.authed()
            return self.send(200, json.dumps(s))
        if path == "/api/data":
            return self.send(200, json.dumps(load_data()))
        if path == "/api/procs":
            return self.send(200, json.dumps(procs()))
        if path == "/api/apps":
            return self.send(200, json.dumps(apps_status()))
        if path == "/api/checks":
            return self.send(200, json.dumps(CHECKS))
        if path == "/api/console":
            since = int(self.qs().get("since", 0) or 0)
            with con_lock:
                lines = [l for l in CON if l["id"] > since]
            return self.send(200, json.dumps({"lines": lines[-500:], "last": con_id}))
        self.send(404, '{"error":"nope"}')

    def do_POST(self):
        path = self.path.split("?")[0]
        n = int(self.headers.get("Content-Length", 0))
        if n > 2_000_000:
            return self.send(413, '{"error":"too big"}')
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.send(400, '{"error":"bad json"}')

        if path == "/api/data":
            if TOKEN and not self.authed():
                return self.send(403, '{"error":"bad token"}')
            d = load_data(); d.update(body); save_data(d)
            if "cfg" in body:
                log("sys", "config saved")
                names = {a.get("name") for a in (body["cfg"] or {}).get("apps") or []}
                for n in list(APPS):
                    if n not in names:
                        threading.Thread(target=stop_app, args=(n,), daemon=True).start()
            return self.send(200, '{"ok":true}')

        if path == "/api/app":
            if not self.authed():
                return self.send(403, '{"error":"not allowed from this device, use ?t=TOKEN"}')
            name, action = body.get("name"), body.get("action")
            fn = {"start": start_app, "stop": stop_app, "restart": restart_app, "deploy": deploy_app}.get(action)
            if not fn or not app_cfg(name):
                return self.send(400, '{"error":"bad app/action"}')
            threading.Thread(target=fn, args=(name,), daemon=True).start()
            return self.send(200, '{"ok":true}')

        if path in ("/api/exec", "/api/stop", "/api/kill"):
            if not self.authed():
                return self.send(403, '{"error":"not allowed from this device, use ?t=TOKEN"}')
            if path == "/api/exec":
                cmd = (body.get("cmd") or "").strip()
                if cmd:
                    threading.Thread(target=run_cmd, args=(cmd,), daemon=True).start()
            elif path == "/api/stop":
                stop_all()
            else:
                pid = int(body.get("pid", 0))
                if pid and pid != MYPID:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        log("sys", f"killed {pid}")
                    except Exception as e:
                        log("err", f"kill {pid}: {e}")
            return self.send(200, '{"ok":true}')
        self.send(404, '{"error":"nope"}')


if __name__ == "__main__":
    ip = sh(["sh", "-c", "ifconfig 2>/dev/null | grep -oE 'inet (addr:)?192\\.168\\.[0-9.]+' | head -1"]) or ""
    log("sys", f"termdash up -> http://localhost:{PORT}")
    if ip:
        log("sys", f"on your wifi -> http://{ip.split()[-1].replace('addr:', '')}:{PORT}")
    log("sys", "remote commands " + ("need ?t=TOKEN" if TOKEN else "off (tablet-only). set TERMDASH_TOKEN to allow"))
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop_everything)
    for a in cfg().get("apps") or []:
        kill_stale(a.get("name", ""))
        if a.get("autostart"):
            start_app(a["name"])
    for fn in (tailer, checker):
        threading.Thread(target=fn, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
