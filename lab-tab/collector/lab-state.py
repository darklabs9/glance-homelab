#!/usr/bin/env python3
"""Lab page data for Glance: the nas-tab JSON, for a whole homelab instead of one Docker host.

nas-state.py reads one Docker host. A lab is many guests on a hypervisor cluster, each running a few
services, so this collector reads what such a lab usually already has:

  catalogue  groups and services, each with the guest it runs on and a health URL   (required)
  probes     one GET per health URL, in parallel; no credentials are ever sent
  Pulse      a pulse-kiosk style /api/summary: nodes, guests, storage, alerts, Uptime Kuma (optional)
  NUT        UPS charge and status over the NUT protocol, LIST VAR only                 (optional)
  GitLab     active pipeline schedules, shown as "Scheduled tasks" (read_api token)     (optional)

It writes the same JSON as nas-state.py, so nas-tab/assets/nas.js draws it.
Standard library only. Read only: GET requests and NUT LIST commands.

  python3 lab-state.py --config config.env --summary   # print one line, write nothing
  python3 lab-state.py --config config.env             # write OUTPUT once (cron)
  python3 lab-state.py --config config.env --loop 60   # write every 60 s (container)
"""
import concurrent.futures, datetime, json, os, socket, ssl, sys, tempfile, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "glance-homelab-lab-tab/1"


# ------------------------------------------------------------------ configuration (same rules as nas-state.py)
def load_env_file(path):
    """KEY=VALUE lines; real environment variables win over the file."""
    try:
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            os.environ.setdefault(k, v)
    except FileNotFoundError:
        pass


def cfg(key, default=""):
    v = os.environ.get(key)
    return default if v is None or v == "" else v


def cfg_num(key, default):
    try:
        return float(cfg(key, str(default)))
    except ValueError:
        return float(default)


# ------------------------------------------------------------------ helpers
def when(dt, now):
    """'3 min ago', 'today 08:30', 'yesterday 22:03', 'Sun 22:03', '14 Oct'."""
    if not dt:
        return ""
    dt = dt.astimezone(now.tzinfo)
    d = (now - dt).total_seconds()
    if d < 3600:
        return "%d min ago" % max(1, int(d // 60))
    if dt.date() == now.date():
        return "today " + dt.strftime("%H:%M")
    days = (now.date() - dt.date()).days
    if days == 1:
        return "yesterday " + dt.strftime("%H:%M")
    if days < 7:
        return dt.strftime("%a %H:%M")
    return dt.strftime("%d %b").lstrip("0")


def parse_iso(s):
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def get_json(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=dict({"User-Agent": UA, "Accept": "application/json"}, **(headers or {})))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# Health checks send no credentials and only read a status code, so self-signed LAN certificates are accepted.
_NOVERIFY = ssl.create_default_context()
_NOVERIFY.check_hostname = False
_NOVERIFY.verify_mode = ssl.CERT_NONE


def probe(url, timeout):
    """{'code': HTTP status or None, 'ms': latency, 'error': text}. Redirects are followed."""
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout, context=_NOVERIFY) as r:
            code = r.status
        err = ""
    except urllib.error.HTTPError as e:
        code, err = e.code, ""
    except Exception as e:  # refused, timeout, DNS, TLS: the service did not answer
        reason = getattr(e, "reason", e)
        code, err = None, str(reason)[:80] or type(e).__name__
    return {"code": code, "ms": int((time.monotonic() - t0) * 1000), "error": err}


def probe_all(urls, timeout, workers):
    urls = sorted(set(u for u in urls if u))
    if not urls:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(zip(urls, ex.map(lambda u: probe(u, timeout), urls)))


def nut_ups(host, name, port=3493, timeout=5):
    """{'state': 'mains'|'on battery'|raw, 'charge': int, 'load': int} from a NUT server, or None."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(("LIST VAR %s\n" % name).encode())
            buf = b""
            while b"END LIST VAR" not in buf and b"ERR" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
    except OSError:
        return None
    v = {}
    for line in buf.decode(errors="replace").splitlines():
        parts = line.split(" ", 3)   # VAR <ups> <key> "<value>"
        if len(parts) == 4 and parts[0] == "VAR":
            v[parts[2]] = parts[3].strip('"')
    if "ups.status" not in v:
        return None
    flags = v["ups.status"].split()
    state = "on battery" if "OB" in flags else "mains" if "OL" in flags else v["ups.status"]
    num = lambda k: int(float(v[k])) if v.get(k, "").replace(".", "", 1).isdigit() else None
    return {"state": state, "charge": num("battery.charge"), "load": num("ups.load")}


_GL_CACHE = {"t": 0, "v": None}


def gitlab_tasks(now):
    """Active pipeline schedules of one project, as nas-tab tasks. Cached 10 min in loop mode."""
    api, project, token = cfg("GITLAB_API").rstrip("/"), cfg("GITLAB_PROJECT"), cfg("GITLAB_TOKEN")
    if not (api and project and token):
        return None
    if time.time() - _GL_CACHE["t"] < 600 and _GL_CACHE["v"] is not None:
        return _GL_CACHE["v"]
    base = "%s/projects/%s/pipeline_schedules" % (api, urllib.parse.quote(project, safe=""))
    h = {"PRIVATE-TOKEN": token}
    out = []
    for s in get_json(base + "?per_page=100", h):
        if not s.get("active"):
            continue
        last = (get_json("%s/%d" % (base, s["id"]), h).get("last_pipeline") or {})
        lt = parse_iso(last.get("created_at") or last.get("updated_at"))
        out.append({
            "name": s.get("description") or "schedule %d" % s["id"],
            "key": False,
            "crons": [s.get("cron", "").split()],
            "schedule": "%s (%s)" % (s.get("cron", "?"), s.get("cron_timezone", "UTC")),
            "next": s.get("next_run_at"),
            "last": (last.get("status", "") + (" · " + when(lt, now) if lt else "")) if last else "",
            "script": "pipeline on " + s.get("ref", "?").replace("refs/heads/", ""),
            "desc": "GitLab pipeline schedule #%d in %s." % (s["id"], project),
            "freq": "scheduled",
        })
    _GL_CACHE.update(t=time.time(), v=out)
    return out


# ------------------------------------------------------------------ the JSON
LEVELS = {"critical": "down", "warning": "warn"}
LOUD_TIERS = {"critical", "important"}


def check_text(pr, km):
    if pr:
        return ("HTTP %d · %d ms" % (pr["code"], pr["ms"])) if pr["code"] else "no answer · " + pr["error"]
    if km:
        return "Uptime Kuma: " + km["status"]
    return "no health check"


def build(cat, probes, P=None, ups=None, tasks=None, now=None):
    """Pure: catalogue + probe results + Pulse summary (+ UPS, tasks) -> nas-state JSON."""
    now = now or datetime.datetime.now().astimezone()
    P = P or {}
    guests = {g.get("vmid"): g for g in (P.get("guests") or {}).get("list") or []}
    kuma = (P.get("kuma") or {}).get("monitors") or []
    kuma_by = {m["name"].lower(): m for m in kuma}
    used_kuma, services, problems = set(), [], []

    for s in cat.get("containers") or []:
        c, name, tier = s["container"], s.get("name") or s["container"], s.get("tier") or ""
        pr = probes.get(s.get("health") or "")
        g = guests.get(s.get("vmid"))
        km = kuma_by.get(name.lower()) or kuma_by.get(c.lower())
        if km:
            used_kuma.add(km["name"])
        if g and g.get("status") != "running":
            state, health = g.get("status") or "stopped", "none"
        elif pr:
            ok = pr["code"] is not None and (pr["code"] < 400 or pr["code"] in (401, 403))
            state = "running" if pr["code"] is not None else "unreachable"
            health = "healthy" if ok else "unhealthy"
        elif km:
            state = "running" if km["status"] in ("up", "maintenance") else km["status"]
            health = "healthy" if km["status"] == "up" else "none"
        elif g:
            state, health = "running", "none"
        else:
            state, health = s.get("assume", "unknown"), "none"

        facts = [["Runs on", s.get("where") or "—", True], ["Address", s.get("ip") or "—"], ["Tier", tier or "—"]]
        if g:
            facts += [["Guest CPU", "%.0f %%" % g.get("cpu", 0)], ["Guest memory", "%.0f %%" % g.get("mem", 0)]]
        facts += [["Check", check_text(pr, km), True]]
        facts += [list(f) for f in s.get("facts") or []]
        services.append({
            "c": c, "name": name, "group": s.get("group") or "other", "desc": s.get("desc", ""),
            "url": s.get("url", ""), "sensitive": s.get("sensitive", ""), "hidden": bool(s.get("hidden")),
            "deps": [], "state": state, "health": health, "ip": s.get("ip", ""), "up": "",
            "ram": ("%.0f %%" % g["mem"]) if g and g.get("mem") is not None else "", "cpu": "",
            "image": "", "version": "", "updated": "", "update_available": False, "update_to": "",
            "restart_policy": "", "facts": facts,
        })
        if state != "running" or health == "unhealthy":
            what = "is " + state if state != "running" else "fails its health check"
            problems.append({"id": "svc:" + c, "level": "down" if tier in LOUD_TIERS else "warn",
                             "title": "%s %s" % (name, what),
                             "detail": "%s · %s" % (s.get("where") or "?", check_text(pr, km))})

    for a in (P.get("alerts") or {}).get("groups") or []:
        if a.get("unacked"):
            problems.append({"id": "pulse:" + a.get("type", "?"), "level": LEVELS.get(a.get("level"), "info"),
                             "title": "%s · %d" % (a.get("label", "Alert"), a.get("count", 0)),
                             "detail": "Pulse: " + a.get("detail", "")})
    for m in kuma:
        if m["status"] in ("down", "pending") and m["name"] not in used_kuma:
            problems.append({"id": "kuma:" + m["name"], "level": "down" if m["status"] == "down" else "warn",
                             "title": "%s is %s" % (m["name"], m["status"]),
                             "detail": "Uptime Kuma · %s %% up recently" % m.get("uptime", "?")})
    if ups and ups["state"] != "mains":
        problems.append({"id": "ups", "level": "down", "title": "UPS " + ups["state"],
                         "detail": "battery %s %%" % ups.get("charge")})
    rank = {"down": 0, "warn": 1}
    problems.sort(key=lambda p: rank.get(p["level"], 2))

    nodes = P.get("nodes") or []
    G = P.get("guests") or {}
    system = {}
    if nodes:
        vm, lxc = G.get("vm") or {}, G.get("lxc") or {}
        temps = [n["temp"] for n in nodes if n.get("temp") is not None]
        system = {
            "containers": {"running": vm.get("running", 0) + lxc.get("running", 0),
                           "total": vm.get("total", 0) + lxc.get("total", 0), "label": "Guests"},
            "cpu_temp": int(max(temps)) if temps else None,
            "ram": {"used": round(sum(n.get("memUsed", 0) for n in nodes) / 1e9),
                    "total": round(sum(n.get("memTotal", 0) for n in nodes) / 1e9)},
            "uptime_days": int(min(n.get("uptime", 0) for n in nodes) // 86400),
            "load1": max(((n.get("load") or [0])[0] for n in nodes), default=0),
        }
        st = [x for x in P.get("storage") or [] if x.get("totalBytes")]
        if st:
            x = max(st, key=lambda x: x.get("used", 0))
            system["disk"] = {"used": round(x["usedBytes"] / 1e12, 1), "total": round(x["totalBytes"] / 1e12, 1),
                              "pct": round(x.get("used", 0)), "label": "TB · %s %s" % (x["name"], x.get("host") or "")}
    if ups:
        system["ups"] = ups

    links = []
    for part in cfg("LINKS").split(";"):
        if "|" in part:
            t, u = part.split("|", 1)
            links.append({"t": t.strip(), "u": u.strip()})

    out = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%S"), "generated_ts": int(now.timestamp()),
        "labels": {"host": cfg("HOST_LABEL", P.get("title") or "Lab"),
                   "host_ip": cfg("HOST_IP", " · ".join(n["name"] for n in nodes)),
                   "isp": cfg("ISP_LABEL", "Internet"), "router": cfg("ROUTER_LABEL", "Router"),
                   "router_ip": cfg("ROUTER_IP"), "short_prefix": cfg("SHORT_IP_PREFIX"),
                   "logs_url": cfg("LOGS_URL"), "ups_note": cfg("UPS_NOTE")},
        "groups": cat.get("groups") or [], "services": services, "deps": {}, "system": system,
        "internet": {}, "backup": None, "links": links,
        "outages": {"events": [], "ongoing": [], "sources": {}}, "problems": problems,
    }
    if tasks is not None:   # absent means "no source": nas.js then hides the block
        out["tasks"] = tasks
    return out


# ------------------------------------------------------------------ run
def collect():
    now = datetime.datetime.now().astimezone()
    cat = json.load(open(cfg("CATALOGUE", os.path.join(HERE, "catalogue.json"))))
    probes = probe_all([s.get("health") for s in cat.get("containers") or []],
                       cfg_num("PROBE_TIMEOUT", 5), int(cfg_num("PROBE_WORKERS", 16)))
    P = None
    if cfg("PULSE_SUMMARY_URL"):
        try:
            P = get_json(cfg("PULSE_SUMMARY_URL"))
        except Exception as e:
            print("pulse: %s" % e, file=sys.stderr)
    ups = nut_ups(cfg("NUT_HOST"), cfg("NUT_UPS", "ups"), int(cfg_num("NUT_PORT", 3493))) if cfg("NUT_HOST") else None
    try:
        tasks = gitlab_tasks(now)
    except Exception as e:
        print("gitlab: %s" % e, file=sys.stderr)
        tasks = _GL_CACHE["v"]
    return build(cat, probes, P, ups, tasks, now)


def write(data):
    for path in [p.strip() for p in cfg("OUTPUT", "./lab-state.json").split(",") if p.strip()]:
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)


def main(argv):
    if "--config" in argv:
        load_env_file(argv[argv.index("--config") + 1])
    else:
        load_env_file(os.environ.get("LAB_TAB_CONFIG") or os.path.join(HERE, "config.env"))
    loop = int(argv[argv.index("--loop") + 1]) if "--loop" in argv else 0
    while True:
        t0 = time.monotonic()
        try:
            d = collect()
            if "--summary" in argv:
                up = sum(1 for s in d["services"] if s["state"] == "running" and s["health"] != "unhealthy")
                print("%d / %d up · %d problems · %d tasks" % (up, len(d["services"]), len(d["problems"]),
                                                               len(d.get("tasks") or [])))
                return 0
            write(d)
        except Exception as e:  # keep looping: the page shows "data frozen" if this never recovers
            print("lab-state: %s" % e, file=sys.stderr)
            if not loop:
                return 1
        if not loop:
            return 0
        time.sleep(max(1, loop - (time.monotonic() - t0)))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
