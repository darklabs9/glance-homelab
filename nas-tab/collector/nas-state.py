#!/usr/bin/env python3
"""nas-state: technical state of a Docker host for the Glance "NAS" page. Read only: it looks at Docker, /proc, /sys,
cron and a few optional log files, and writes one JSON file that assets/nas.js renders.

Run it from cron every minute (as root, or as a user allowed to talk to the Docker socket and read the logs).
Settings come from environment variables, optionally loaded from an env file (see config.example.env):
  nas-state.py [--config /path/config.env] [--print] [--summary]
    --print    print the JSON instead of writing anything (no cache, no history update)
    --summary  print only the problem list

Costs: `docker inspect` on every run; `docker stats` every 5 min; backup check every 30 min; cron scan every 10 min
(cached in STATE_DIR/nas-state-cache.json).
"""
import datetime, glob, gzip, json, os, re, socket, subprocess, sys, tempfile, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
PRINT = "--print" in sys.argv or "--summary" in sys.argv
NOW = datetime.datetime.now()


# ------------------------------------------------------------------ configuration
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


if "--config" in sys.argv:
    load_env_file(sys.argv[sys.argv.index("--config") + 1])
else:
    load_env_file(os.environ.get("NAS_TAB_CONFIG") or os.path.join(HERE, "config.env"))


def cfg(key, default=""):
    v = os.environ.get(key)
    return default if v is None or v == "" else v


def cfg_list(key, default=""):
    return [x.strip() for x in cfg(key, default).split(",") if x.strip()]


def cfg_num(key, default):
    try:
        return float(cfg(key, str(default)))
    except ValueError:
        return float(default)


OUTPUTS = cfg_list("OUTPUT", "./nas-state.json")
STATE_DIR = cfg("STATE_DIR", "/var/lib/nas-tab")
CACHE = os.path.join(STATE_DIR, "nas-state-cache.json")
UPDATE_HISTORY = cfg("UPDATE_HISTORY", os.path.join(STATE_DIR, "update-history.jsonl"))
CATALOGUE = cfg("CATALOGUE", os.path.join(HERE, "catalogue.json"))
LAN_PREFIX = cfg("LAN_PREFIX", "192.168.")
DATA_PATH = cfg("DATA_PATH", "/")
CADDYFILE = cfg("CADDYFILE")
CADDY_HOST_SUFFIX = cfg("CADDY_HOST_SUFFIX")          # e.g. .example.lan ; empty = any host block
UPDATES_FILE = cfg("UPDATES_FILE")
SPEEDTEST_JSON = cfg("SPEEDTEST_JSON")
UPS_NAME = cfg("UPS_NAME")
UPS_COMMAND = cfg("UPS_COMMAND", "upsc {name}")
KOPIA_CONTAINER = cfg("KOPIA_CONTAINER")
KOPIA_SOURCE = cfg("KOPIA_SOURCE")
BACKUP_STAMP_FILE = cfg("BACKUP_STAMP_FILE")
BACKUP_LABEL = cfg("BACKUP_LABEL", "Backup")
BACKUP_MAX_AGE_H = cfg_num("BACKUP_MAX_AGE_H", 30)
DISK_WARN_PCT = cfg_num("DISK_WARN_PCT", 85)
CPU_HOT_C = cfg_num("CPU_HOT_C", 85)
CRON_DIRS = cfg_list("CRON_DIRS", "/etc/cron.d")
CRON_LOG = cfg("CRON_LOG", "/var/log/cron.log")
CRON_IGNORE = set(cfg_list("CRON_IGNORE", "e2scrub_all,php,sysstat"))
KEY_TASKS = set(cfg_list("KEY_TASKS"))
DB_RX = re.compile(cfg("DB_PATTERN", r"postgres|redis|valkey|mariadb|mysql|mongo|-db$"))
OUTAGE_DAYS = 30


# ------------------------------------------------------------------ small helpers
def sh(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".nas-state-")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def parse_dt(s):
    """ISO date/time, tolerant (no timezone, 'T' or space)."""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(str(s).replace("T", " ")[:19])
    except ValueError:
        return None


def human_duration(sec):
    sec = max(0, int(sec))
    if sec < 3600:
        return "%d min" % max(1, sec // 60)
    if sec < 86400 * 2:
        return "%d h" % (sec // 3600)
    return "%d d" % (sec // 86400)


def hm(dt):
    return "%02d:%02d" % (dt.hour, dt.minute)


def when(dt):
    """'3 min ago', 'this morning 08:30', 'yesterday 22:03', 'Sun 22:03', '14 Oct'."""
    if not dt:
        return ""
    d = NOW - dt
    if d.total_seconds() < 3600:
        return "%d min ago" % max(1, int(d.total_seconds() // 60))
    if dt.date() == NOW.date():
        return ("this morning " if dt.hour < 12 else "today ") + hm(dt)
    days = (NOW.date() - dt.date()).days
    if days == 1:
        return ("last night " if dt.hour < 6 else "yesterday ") + hm(dt)
    if days < 7:
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()] + " " + hm(dt)
    return dt.strftime("%d %b")


# ------------------------------------------------------------------ containers
def inspect_all():
    ids = sh(["docker", "ps", "-aq"]).split()
    if not ids:
        return []
    try:
        return json.loads(sh(["docker", "inspect"] + ids, 60) or "[]")
    except Exception:
        return []


def docker_stats(cache):
    if cache.get("stats_t", 0) > time.time() - 300 and cache.get("stats"):
        return cache["stats"]
    out = {}
    for l in sh(["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"], 60).splitlines():
        p = l.split("|")
        if len(p) != 3:
            continue
        m = re.match(r"([\d.]+)\s*([KMG]i?B)", p[2].strip())
        ram = ""
        if m:
            v = float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)[0]]
            ram = ("%.1f GB" % (v / 1024)) if v >= 1024 else "%d MB" % round(v)
        out[p[0]] = {"cpu": p[1].strip().replace("%", " %"), "ram": ram}
    cache["stats"], cache["stats_t"] = out, time.time()
    return out


def caddy_routes():
    """IP -> https://host (first Caddy site that proxies to it). Optional: CADDYFILE."""
    r = {}
    if not CADDYFILE:
        return r
    suffix = re.escape(CADDY_HOST_SUFFIX) if CADDY_HOST_SUFFIX else r"\.[a-z0-9-]+"
    host_rx = re.compile(r"^\s*(?:https?://)?([a-z0-9.*-]+%s)(?::\d+)?\s*\{" % suffix, re.I)
    try:
        host = None
        for l in open(CADDYFILE):
            m = host_rx.match(l)
            if m:
                host = m.group(1)
            m2 = re.search(r"reverse_proxy\s+(?:@\w+\s+)?(?:https?://)?(\d+\.\d+\.\d+\.\d+):(\d+)", l)
            if m2 and host and "*" not in host:
                r.setdefault(m2.group(1), "https://" + host)
    except Exception:
        pass
    return r


def image_version(c):
    lab = (c.get("Config") or {}).get("Labels") or {}
    v = lab.get("org.opencontainers.image.version") or lab.get("build_version", "")
    m = re.search(r"version:-\s*(\S+)", v)          # linuxserver.io build_version label
    if m:
        v = m.group(1)
    img = (c.get("Config") or {}).get("Image", "")
    tag = img.rsplit(":", 1)[1] if ":" in img.split("/")[-1] else "latest"
    return (v or tag)[:40], img.split("@")[0]


def load_catalogue():
    """Optional catalogue.json: {"groups": [...], "containers": [...]}. Missing file = Docker labels / defaults."""
    cat = read_json(CATALOGUE, None) or {}
    if isinstance(cat, list):           # plain list of containers
        cat = {"containers": cat}
    return cat.get("groups") or [], cat.get("containers") or []


def services(cs, st, cache, history):
    groups, cat = load_catalogue()
    by_name = {c["Name"].lstrip("/"): c for c in cs}
    ips_of = {}
    for c in cs:
        for n, v in ((c.get("NetworkSettings") or {}).get("Networks") or {}).items():
            if v.get("IPAddress"):
                ips_of.setdefault(c["Name"].lstrip("/"), []).append((n, v["IPAddress"]))
    id_to_name = {c["Id"]: c["Name"].lstrip("/") for c in cs}
    routes = caddy_routes()
    last_update = {}
    for h in history:
        last_update[h["c"]] = h["when"]
    available = {u["c"]: u for u in read_updates()}

    def address(name):
        c = by_name.get(name)
        if not c:
            return "", ""
        mode = (c.get("HostConfig") or {}).get("NetworkMode", "")
        if mode.startswith("container:"):
            owner = id_to_name.get(mode.split(":", 1)[1], mode.split(":", 1)[1][:12])
            ips = [ip for _, ip in ips_of.get(owner, []) if ip.startswith(LAN_PREFIX)]
            return "via %s%s" % (owner, (" · " + ips[0]) if ips else ""), ips[0] if ips else ""
        if mode == "host":
            return "host network", ""
        lan = [ip for _, ip in ips_of.get(name, []) if ip.startswith(LAN_PREFIX)]
        if lan:
            return lan[0], lan[0]
        others = [ip for _, ip in ips_of.get(name, [])]
        return ("%s (internal)" % others[0]) if others else "internal", ""

    def card(name):
        c = by_name.get(name)
        if not c:
            return {"state": "absent", "health": "none"}
        s = c.get("State") or {}
        health = (s.get("Health") or {}).get("Status", "none")
        try:
            started = datetime.datetime.fromisoformat(s.get("StartedAt", "")[:19])
            started = started + (datetime.datetime.now() - datetime.datetime.utcnow())   # UTC -> local
            up = human_duration((NOW - started).total_seconds()) if s.get("Running") else "stopped"
        except Exception:
            up = "?"
        ver, img = image_version(c)
        x = st.get(name, {})
        ip_txt, ip = address(name)
        u = available.get(name)
        return {"state": s.get("Status", "?"), "health": health, "up": up, "image": img, "version": ver, "ip": ip_txt,
                "cpu": x.get("cpu", "—"), "ram": x.get("ram", "—"), "url": routes.get(ip, ""),
                "updated": last_update.get(name, ""), "update_available": bool(u),
                "update_to": (u or {}).get("latest", ""),
                "restart_policy": ((c.get("HostConfig") or {}).get("RestartPolicy") or {}).get("Name", "")}

    def labels(name):
        return ((by_name[name].get("Config") or {}).get("Labels") or {}) if name in by_name else {}

    out, seen = [], set()
    entries = [dict(e) for e in cat if e.get("container") in by_name]
    # containers not in the catalogue: Docker labels (nas-tab.*), databases attached to their compose project
    listed = {e["container"] for e in entries}
    rest = sorted(n for n in by_name if n not in listed)
    projects = {}
    for n in rest:
        p = labels(n).get("com.docker.compose.project")
        if p and not DB_RX.search(n):
            projects.setdefault(p, []).append(n)
    attached = set()
    for e in entries:
        attached.update(e.get("deps") or [])
    auto_deps = {}
    for n in rest:
        lab = labels(n)
        if lab.get("nas-tab.group") or n in attached or not DB_RX.search(n):
            continue
        owners = projects.get(lab.get("com.docker.compose.project"), [])
        if owners:
            auto_deps.setdefault(owners[0], []).append(n)
            attached.add(n)
    for n in rest:
        if n in attached:
            continue
        lab = labels(n)
        if lab.get("nas-tab.hide") in ("1", "true"):
            continue
        entries.append({"container": n, "name": lab.get("nas-tab.name") or n,
                        "group": lab.get("nas-tab.group") or "other", "desc": lab.get("nas-tab.desc", ""),
                        "url": lab.get("nas-tab.url", ""), "sensitive": lab.get("nas-tab.sensitive", ""),
                        "deps": auto_deps.get(n, [])})

    for e in entries:
        f = card(e["container"])
        f.update({"name": e.get("name") or e["container"], "c": e["container"], "group": e.get("group") or "other",
                  "desc": e.get("desc", ""), "sensitive": e.get("sensitive") or "", "hidden": bool(e.get("hidden")),
                  "deps": [d for d in e.get("deps") or [] if d in by_name]})
        if e.get("url"):
            f["url"] = e["url"]
        out.append(f)
        seen.add(e["container"])
        seen.update(f["deps"])
    deps = {}
    for s in out:
        for d in s["deps"]:
            deps[d] = card(d)

    # groups: catalogue order first, then any group only seen in labels, "other" last
    known = {g["id"] for g in groups}
    for s in out:
        if s["group"] not in known:
            groups.append({"id": s["group"], "title": "Other" if s["group"] == "other" else s["group"].replace("-", " ").title()})
            known.add(s["group"])
    groups.sort(key=lambda g: g["id"] == "other")
    return out, deps, groups


# ------------------------------------------------------------------ update history (detected from image changes)
def track_updates(cs, cache):
    """When a container's image ID changes between two runs, log it as an update. Works with Watchtower,
    `docker compose pull && up -d`, or any other updater. Returns the history (oldest first)."""
    prev = cache.get("images") or {}
    now_imgs = {}
    for c in cs:
        name = c["Name"].lstrip("/")
        ver, _ = image_version(c)
        now_imgs[name] = [c.get("Image", ""), ver]
    new = []
    for name, (img, ver) in now_imgs.items():
        old = prev.get(name)
        if old and old[0] and img and old[0] != img:
            new.append({"c": name, "from": old[1], "to": ver, "at": NOW.isoformat(timespec="seconds")})
    if not PRINT:
        cache["images"] = now_imgs
        if new:
            os.makedirs(os.path.dirname(os.path.abspath(UPDATE_HISTORY)), exist_ok=True)
            with open(UPDATE_HISTORY, "a") as f:
                for n in new:
                    f.write(json.dumps(n, ensure_ascii=False) + "\n")
    hist = []
    try:
        for l in open(UPDATE_HISTORY):
            try:
                j = json.loads(l)
                dt = parse_dt(j.get("at"))
                if dt and (NOW - dt).days < 90:
                    hist.append({"c": j["c"], "from": j.get("from", ""), "to": j.get("to", ""), "dt": dt})
            except Exception:
                continue
    except Exception:
        pass
    hist.sort(key=lambda h: h["dt"])
    for h in hist:
        h["when"] = h["dt"].strftime("%d %b")
    return hist


def read_updates():
    """Optional UPDATES_FILE: {"items": [{"container": "sonarr", "current": "4.0.1", "latest": "4.0.2"}]}
    ("releases": [{"tag": ...}] is accepted instead of "latest")."""
    if not UPDATES_FILE:
        return []
    out = []
    for i in (read_json(UPDATES_FILE, {}) or {}).get("items", []):
        rel = i.get("releases") or []
        out.append({"c": i.get("container", ""), "current": i.get("current", ""),
                    "latest": i.get("latest") or (rel[0].get("tag") if rel else "") or ""})
    return [x for x in out if x["c"]]


def updates(hist):
    recent = [{"c": h["c"], "from": h["from"], "to": h["to"], "when": when(h["dt"])}
              for h in reversed(hist) if (NOW - h["dt"]).days < 14]
    return {"available": read_updates(), "recent": recent[:12]}


# ------------------------------------------------------------------ system
def raid_state():
    """Software RAID from /proc/mdstat: [{"name": "md1", "level": "raid1", "status": "UU"}]."""
    out = []
    try:
        cur = None
        for l in open("/proc/mdstat"):
            m = re.match(r"^(md\w+)\s*:\s*\w+\s+(raid\d+)?", l)
            if m:
                cur = {"name": m.group(1), "level": m.group(2) or "", "status": ""}
                out.append(cur)
                continue
            s = re.search(r"\[([U_]+)\]\s*$", l.strip())
            if cur and s and not cur["status"]:
                cur["status"] = s.group(1)
    except Exception:
        pass
    return [r for r in out if r["status"]]


def disk_temps():
    """Drive temperatures from the kernel drivetemp hwmon driver (if loaded)."""
    out = []
    for h in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            if open(h + "/name").read().strip() == "drivetemp":
                out.append(int(open(h + "/temp1_input").read()) // 1000)
        except Exception:
            pass
    return out


def ups_state():
    if not UPS_NAME:
        return None
    raw = sh(UPS_COMMAND.format(name=UPS_NAME).split(), 15)
    vals = dict(l.split(": ", 1) for l in raw.splitlines() if ": " in l)
    status = vals.get("ups.status", "")
    if not status:
        return {"state": "unknown", "charge": None, "load": None}
    state = "on battery" if "OB" in status.split() else "mains"
    if "LB" in status.split():
        state = "low battery"
    num = lambda k: int(float(vals[k])) if k in vals else None
    return {"state": state, "charge": num("battery.charge"), "load": num("ups.load")}


def system(cs):
    s = {"ups": ups_state(), "raid": raid_state(), "disk_temps": disk_temps()}
    try:
        st = os.statvfs(DATA_PATH)
        tot, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        used = tot - st.f_bfree * st.f_frsize
        s["disk"] = {"used": round(used / 1e12, 2), "total": round(tot / 1e12, 2),
                     "pct": round(used * 100 / max(1, used + free))}
    except Exception:
        pass
    temps = {}
    for z in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            temps[open(z + "/type").read().strip()] = int(open(z + "/temp").read()) // 1000
        except Exception:
            pass
    s["cpu_temp"] = temps.get("x86_pkg_temp") or (max(temps.values()) if temps else None)
    try:
        mem = {}
        for l in open("/proc/meminfo"):
            k, v = l.split(":")
            mem[k] = int(v.split()[0])
        s["ram"] = {"used": round((mem["MemTotal"] - mem["MemAvailable"]) / 1048576, 1), "total": round(mem["MemTotal"] / 1048576)}
        s["uptime_days"] = int(float(open("/proc/uptime").read().split()[0]) // 86400)
    except Exception:
        pass
    try:
        s["load1"], _, s["load15"] = os.getloadavg()
    except Exception:
        pass
    s["containers"] = {"total": len(cs), "running": sum(1 for c in cs if (c.get("State") or {}).get("Running"))}
    return s


def speedtest():
    """Optional SPEEDTEST_JSON (file path or URL), e.g. a Speedtest Tracker export: download/upload/ping."""
    if not SPEEDTEST_JSON:
        return {}
    try:
        if SPEEDTEST_JSON.startswith("http"):
            j = json.loads(urllib.request.urlopen(SPEEDTEST_JSON, timeout=10).read())
        else:
            j = read_json(SPEEDTEST_JSON, {}) or {}
        d = j.get("data", j)
        if isinstance(d, list):
            d = d[0]
        down = d.get("download_bits") or (d.get("download") or 0) * 1e6
        up = d.get("upload_bits") or (d.get("upload") or 0) * 1e6
        return {"down": round(down / 1e6), "up": round(up / 1e6), "ping": round(float(d.get("ping") or 0))}
    except Exception:
        return {}


def internet(out):
    r = speedtest()
    week = NOW - datetime.timedelta(days=7)
    nets = [e for e in out["events"] if e["type"] == "net" and e["dt"] >= week]
    if out["sources"].get("net"):
        r["outages_7d"] = len(nets)
        r["last_outage"] = when(nets[0]["dt"]) if nets else ""
    for o in out["ongoing"]:
        if o["type"] == "net":
            r["down_since"] = hm(parse_dt(o["since"]))
    wifis = [e for e in out["events"] if e["type"] == "wifi"]
    if wifis:
        r["wifi_last"] = "%s (%s)" % (when(wifis[0]["dt"]), human_duration(wifis[0]["s"]) if wifis[0]["s"] >= 60 else "%d s" % wifis[0]["s"])
    return r


# ------------------------------------------------------------------ backups (optional)
def backup(cache):
    r = {}
    dt = None
    if KOPIA_CONTAINER:
        if cache.get("kopia_t", 0) < time.time() - 1800:
            cmd = ["docker", "exec", KOPIA_CONTAINER, "kopia", "snapshot", "list"] + ([KOPIA_SOURCE] if KOPIA_SOURCE else [])
            lines = [l for l in sh(cmd, 90).splitlines() if re.match(r"\s+20\d\d-", l)]
            cache["kopia"] = lines[-1].split()[:2] if lines else None
            cache["kopia_t"] = time.time()
        if cache.get("kopia"):
            dt = parse_dt(" ".join(cache["kopia"]))
    elif BACKUP_STAMP_FILE:
        try:
            dt = datetime.datetime.fromtimestamp(os.path.getmtime(BACKUP_STAMP_FILE))
        except OSError:
            dt = None
    else:
        return None
    r["label"] = BACKUP_LABEL
    if dt:
        r["last"] = when(dt)
        r["age_h"] = round((NOW - dt).total_seconds() / 3600, 1)
    r["max_age_h"] = BACKUP_MAX_AGE_H
    return r


# ------------------------------------------------------------------ scheduled tasks
def cron_human(c):
    mi, h, dom, mon, dow = c
    days = {"0": "Sunday", "7": "Sunday", "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday",
            "5": "Friday", "6": "Saturday", "1-5": "weekdays"}
    hh = lambda hr, m: "%02d:%02d" % (int(hr), int(m))
    if c == ["*"] * 5:
        return "every minute"
    if mi.startswith("*/") and h == "*" and dom == mon == dow == "*":
        return "every %s min" % mi[2:]
    if mi.isdigit() and h == "*" and dom == mon == dow == "*":
        return "hourly (:%02d)" % int(mi)
    if mi.isdigit() and h.startswith("*/") and dom == mon == dow == "*":
        return "every %s h" % h[2:]
    if mi.isdigit() and h.isdigit() and dom == mon == "*":
        return ("daily " + hh(h, mi)) if dow == "*" else ("%s %s" % (days.get(dow, "days " + dow), hh(h, mi)))
    if re.match(r"^\d+(,\d+)+$", mi) and h == dom == mon == dow == "*":
        v = sorted(int(x) for x in mi.split(","))
        gaps = {(b - a) for a, b in zip(v, v[1:] + [v[0] + 60])}
        return ("every %d min" % gaps.pop()) if len(gaps) == 1 else "hourly at " + ", ".join(":%02d" % x for x in v)
    if mi.isdigit() and re.match(r"^[\d,]+$", h) and dom == mon == "*" and dow == "*":
        return "daily at " + ", ".join(hh(x, mi) for x in h.split(","))
    if mi.isdigit() and h.isdigit() and re.match(r"^\d+-\d+$", dom) and mon == dow == "*":
        return "days %s to %s of the month at %s" % (dom.split("-")[0], dom.split("-")[1], hh(h, mi))
    if mi.isdigit() and h.isdigit() and dom.isdigit() and mon == dow == "*":
        return "day %s of the month at %s" % (dom, hh(h, mi))
    ms = re.match(r"^(?:\*|\d+-\d+)/(\d+)$", mi)
    if ms and dom == mon == dow == "*" and h == "*":
        return "every %s min" % ms.group(1)
    if ms and dom == mon == dow == "*" and re.match(r"^[\d,-]+$", h):
        return "every %s min, " % ms.group(1) + ", ".join(("%s:00 to %s:59" % tuple(x.split("-"))) if "-" in x else x + "h" for x in h.split(","))
    if mi.isdigit() and h.isdigit() and re.match(r"^\d+-\d+$", dom) and re.match(r"^[\d,]+$", mon) and dow == "*":
        names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        return "days %s to %s of %s at %s" % (dom.split("-")[0], dom.split("-")[1], ", ".join(names[int(x) - 1] for x in mon.split(",")), hh(h, mi))
    return " ".join(c)


def cron_runs():
    """Last start time of each cron command, read from the cron log ("CRON[pid]: (user) CMD (...)").
    Returns ({command[:200]: timestamp string}, reader) ; the reader is reused for older rotated logs."""
    rx = re.compile(r"^(\S+(?: +\d+ [\d:]+)?) \S+ CRON\[\d+\]: \(\S+\) CMD \((.*)\)$")

    def read(path):
        seen = {}
        try:
            f = gzip.open(path, "rt", errors="replace") if path.endswith(".gz") else open(path, errors="replace")
            with f:
                for l in f:
                    if "CMD (" not in l:
                        continue
                    m = rx.match(l.rstrip("\n"))
                    if m:
                        seen[m.group(2)[:200]] = m.group(1)
        except Exception:
            pass
        return seen
    out = {}
    for path in (CRON_LOG + ".1", CRON_LOG):          # oldest first
        out.update(read(path))
    return out, read


def log_time(s):
    """ISO timestamp (rsyslog high precision) or classic syslog 'Nov  1 21:05:01' (current year assumed)."""
    dt = parse_dt(s)
    if dt:
        return dt
    try:
        dt = datetime.datetime.strptime("%d %s" % (NOW.year, re.sub(r"\s+", " ", s)), "%Y %b %d %H:%M:%S")
        return dt.replace(year=NOW.year - 1) if dt > NOW + datetime.timedelta(days=1) else dt
    except ValueError:
        return None


def with_next_runs(lst):
    """Next run of each task, recomputed every run (the rest is cached 10 min). freq "continuous" = at least one line
    that runs hourly or more often; "scheduled" = the rest, sorted by next run on the page."""
    try:
        import cron_detail as CD
    except Exception:
        return lst
    for x in lst:
        cr = x.get("crons") or []
        p = [y for y in (CD.next_run(c, NOW) for c in cr) if y]
        x["next"] = min(p).isoformat(timespec="minutes") if p else None
        x["freq"] = "continuous" if any(c[1] == "*" or not c[0].isdigit() for c in cr) else "scheduled"
    return lst


def tasks(cache):
    if cache.get("tasks_t", 0) > time.time() - 600 and cache.get("tasks") is not None and cache.get("tasks_v") == 1:
        return with_next_runs(cache["tasks"])
    try:
        import cron_detail as CD
    except Exception:
        CD = None
    out = []
    runs, read_old = cron_runs()
    old = None
    files = []
    for d in CRON_DIRS:
        files += sorted(glob.glob(os.path.join(d, "*")))
    for f in files:
        name = os.path.basename(f)
        if "." in name or name in CRON_IGNORE:       # cron skips files with a dot in their name (backups...)
            continue
        try:
            raw = [l.strip() for l in open(f, errors="replace") if l.strip() and not l.strip().startswith("#")]
        except Exception:
            continue
        lines = []
        for l in raw:
            first = l.split()[0]
            if "=" in first or first == "@reboot":
                continue
            if first in CD_MACROS:
                l = CD_MACROS[first] + " " + l.split(None, 1)[1]
            if len(l.split(None, 6)) == 7:
                lines.append(l)
        if not lines:
            continue
        cmds = [l.split(None, 6)[6][:200] for l in lines]
        ts = [runs[c] for c in cmds if c in runs]
        if not ts:                                    # weekly tasks: look in older rotated logs
            if old is None:
                old = {}
                for g in (CRON_LOG + ".4.gz", CRON_LOG + ".3.gz", CRON_LOG + ".2.gz"):
                    old.update(read_old(g))
            ts = [old[c] for c in cmds if c in old]
        last = max((t for t in (log_time(x) for x in ts) if t), default=None)
        crons = [l.split(None, 6)[:5] for l in lines]
        scripts = []
        for l in lines:
            sc = CD.script_of(l.split(None, 6)[6]) if CD else None
            if sc and sc not in scripts:
                scripts.append(sc)
        desc = next((x for x in (CD.description(sc) for sc in scripts) if x), "") if CD else ""
        sched = []
        for c in crons:
            hc = cron_human(c)
            if hc not in sched:
                sched.append(hc)
        out.append({"name": name, "last": when(last) if last else "", "key": name in KEY_TASKS, "crons": crons,
                    "schedule": " · ".join(sched), "script": scripts[0] if scripts else "", "desc": desc})
    cache["tasks"], cache["tasks_t"], cache["tasks_v"] = out, time.time(), 1
    return with_next_runs(out)


try:
    from cron_detail import MACROS as CD_MACROS
except Exception:
    CD_MACROS = {}


# ------------------------------------------------------------------ outages (Wi-Fi / internet / power)
# Each type reads an optional log file (<TYPE>_OUTAGE_LOG) and an optional state file (<TYPE>_OUTAGE_STATE, for
# "ongoing"). Two line formats are accepted, even mixed in one file:
#   JSON lines: {"start": "2026-11-01T21:05:10", "seconds": 40, "note": "...", "gateway": true}
#               (this is what wifi-ping.py writes)
#   text lines: "2026-11-01 21:05:10 DOWN ..." / "... UP ..." / "... BOOT" (watcher restarted). The words are
#               regexes you can change: <TYPE>_DOWN_RE, <TYPE>_UP_RE, <TYPE>_BOOT_RE (or OUTAGE_*_RE for all types).
TYPES = ("net", "wifi", "power")
ENV_PREFIX = {"net": "NET", "wifi": "WIFI", "power": "POWER"}


def _rx(t, what, default):
    return re.compile(cfg("%s_%s_RE" % (ENV_PREFIX[t], what)) or cfg("OUTAGE_%s_RE" % what, default), re.I)


def read_outage_log(t, path):
    ev, ongoing = [], None
    down_rx, up_rx, boot_rx = _rx(t, "DOWN", r"^DOWN\b"), _rx(t, "UP", r"^UP\b"), _rx(t, "BOOT", r"^BOOT\b")
    start = None
    try:
        f = open(path, errors="replace")
    except OSError:
        return ev, None
    with f:
        for l in f:
            l = l.rstrip("\n")
            if l.startswith("{"):
                try:
                    j = json.loads(l)
                    dt = parse_dt(j.get("start") or j.get("debut"))
                    s = int(j.get("seconds", j.get("secondes", 0)) or 0)
                    note = j.get("note", "")
                    if not note and j.get("gateway", j.get("box", True)) is False:
                        note = "the router did not answer either"
                    if dt:
                        ev.append({"dt": dt, "type": t, "s": s, "note": note})
                except Exception:
                    pass
                continue
            m = re.match(r"^(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d)\S*\s+(.*)$", l)
            if not m:
                continue
            dt, msg = parse_dt(m.group(1)), m.group(2)
            if dt is None:
                continue
            if down_rx.search(msg):
                start = start or dt
            elif up_rx.search(msg) and start:
                ev.append({"dt": start, "type": t, "s": int((dt - start).total_seconds()), "note": ""})
                start = None
            elif boot_rx.search(msg) and start:
                note = "the host shut down during the outage" if t == "power" else "watcher restarted"
                ev.append({"dt": start, "type": t, "s": int((dt - start).total_seconds()), "note": note})
                start = None
    if start and NOW - start < datetime.timedelta(hours=12 if t != "power" else 48):
        ongoing = start
    return ev, ongoing


def outages():
    lim = NOW - datetime.timedelta(days=OUTAGE_DAYS)
    ev, ongoing, sources = [], [], {}
    for t in TYPES:
        log = cfg(ENV_PREFIX[t] + "_OUTAGE_LOG")
        state = cfg(ENV_PREFIX[t] + "_OUTAGE_STATE")
        sources[t] = bool(log or state)
        if log:
            e, start = read_outage_log(t, log)
            ev += e
            if start:
                ongoing.append({"type": t, "since": start.isoformat(timespec="minutes")})
        if state:      # wifi-ping.py style state: {"start": iso or null, "seen": iso}
            stj = read_json(state, {}) or {}
            st_start, seen = parse_dt(stj.get("start") or stj.get("debut")), parse_dt(stj.get("seen") or stj.get("vu"))
            if st_start and seen and (NOW - seen).total_seconds() < 180 and not any(o["type"] == t for o in ongoing):
                ongoing.append({"type": t, "since": st_start.isoformat(timespec="minutes")})
    ev = [x for x in ev if x["dt"] >= lim]
    # context that helps to read the list
    powers = [x for x in ev if x["type"] == "power"]
    nets = [x for x in ev if x["type"] == "net"]
    for x in ev:
        if x.get("note") or x["type"] == "power":
            continue
        if any(c["dt"] - datetime.timedelta(minutes=1) <= x["dt"] <= c["dt"] + datetime.timedelta(seconds=c["s"] + 300) for c in powers):
            x["note"] = "after the power cut"
        elif x["type"] == "wifi" and any(abs((n["dt"] - x["dt"]).total_seconds()) <= 120 for n in nets):
            x["note"] = "at the same time as internet"
    longs = [x for x in ev if x["s"] >= 900]
    if longs:
        m = max(longs, key=lambda x: x["s"])
        if not m.get("note"):
            m["note"] = "longest this month"
    for x in ev:
        if not x.get("note") and x["s"] < 60:
            x["note"] = "micro-outage"
    ev.sort(key=lambda x: x["dt"], reverse=True)
    return {"events": ev, "ongoing": ongoing, "sources": sources}


# ------------------------------------------------------------------ problems ("To do" list)
def problems(svc, deps, sysm, upd, bkp):
    pb = []
    for name, f in [(s["c"], s) for s in svc] + list(deps.items()):
        if f.get("state") in ("exited", "dead", "created") and f.get("restart_policy") in ("always", "unless-stopped", "on-failure"):
            pb.append({"id": "stopped:" + name, "level": "down", "title": "%s is stopped" % name, "detail": "it should be running"})
        elif f.get("health") == "unhealthy":
            pb.append({"id": "health:" + name, "level": "down", "title": "%s is unhealthy" % name, "detail": "its health check fails"})
    for d in upd["available"]:
        pb.append({"id": "update:" + d["c"], "level": "info", "title": "Update available for %s" % d["c"],
                   "detail": "%s → %s" % (d["current"] or "?", d["latest"] or "?")})
    if bkp and (bkp.get("age_h") is None or bkp["age_h"] > BACKUP_MAX_AGE_H):
        pb.append({"id": "backup", "level": "down",
                   "title": ("No backup for %d h" % bkp["age_h"]) if bkp.get("age_h") is not None else "No backup found",
                   "detail": bkp.get("label", "")})
    if (sysm.get("disk") or {}).get("pct", 0) >= DISK_WARN_PCT:
        pb.append({"id": "disk", "level": "warn", "title": "Disk %d %% full" % sysm["disk"]["pct"],
                   "detail": "e.g. docker image prune"})
    bad = [r for r in sysm.get("raid") or [] if "_" in r["status"]]
    if bad:
        pb.append({"id": "raid", "level": "down", "title": "RAID degraded: " + ", ".join("%s [%s]" % (r["name"], r["status"]) for r in bad),
                   "detail": "a disk is missing or failed"})
    if (sysm.get("ups") or {}).get("state") in ("on battery", "low battery"):
        pb.append({"id": "ups", "level": "down", "title": "Power cut: UPS %s" % sysm["ups"]["state"],
                   "detail": "battery %s %%" % sysm["ups"].get("charge")})
    if (sysm.get("cpu_temp") or 0) >= CPU_HOT_C:
        pb.append({"id": "temp", "level": "warn", "title": "CPU is hot: %d °C" % sysm["cpu_temp"], "detail": ""})
    order = {"down": 0, "warn": 1, "info": 2}
    return sorted(pb, key=lambda p: order.get(p["level"], 3))


def links():
    """LINKS="Uptime Kuma|https://uptime.example.lan;Dozzle|https://dozzle.example.lan"."""
    out = []
    for part in cfg("LINKS").split(";"):
        if "|" in part:
            t, u = part.split("|", 1)
            out.append({"t": t.strip(), "u": u.strip()})
    return out


def host_ip():
    ip = cfg("HOST_IP")
    if ip:
        return ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))           # TEST-NET address, nothing is sent
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = ""
    return ip


def main():
    cache = read_json(CACHE, {}) or {}
    cs = inspect_all()
    st = docker_stats(cache)
    hist = track_updates(cs, cache)
    svc, deps, groups = services(cs, st, cache, hist)
    sysm = system(cs)
    upd = updates(hist)
    bkp = backup(cache)
    out = outages()
    d = {
        "generated": NOW.isoformat(timespec="seconds"),
        "generated_ts": int(time.time()),        # the page compares it with the viewer's clock (stale data banner)
        "labels": {"host": cfg("HOST_LABEL", socket.gethostname()), "host_ip": host_ip(), "isp": cfg("ISP_LABEL", "Internet"),
                   "router": cfg("ROUTER_LABEL", "Router"), "router_ip": cfg("ROUTER_IP"), "short_prefix": cfg("SHORT_IP_PREFIX"),
                   "logs_url": cfg("LOGS_URL"), "ups_note": cfg("UPS_NOTE")},
        "groups": groups, "services": svc, "deps": deps,
        "system": sysm, "internet": internet(out), "backup": bkp, "updates": upd,
        "tasks": tasks(cache), "links": links(),
        "outages": {"events": [{"t": x["dt"].isoformat(timespec="minutes"), "type": x["type"], "s": x["s"], "note": x.get("note", "")}
                               for x in out["events"]],
                    "ongoing": out["ongoing"], "sources": out["sources"]},
    }
    d["problems"] = problems(svc, deps, sysm, upd, bkp)
    if "--summary" in sys.argv:
        for p in d["problems"]:
            print(p["level"], "|", p["title"])
        print(len(svc), "services,", len(deps), "attached,", sysm["containers"], "| tasks:", len(d["tasks"]),
              "| outages:", len(d["outages"]["events"]))
        return
    if PRINT:
        print(json.dumps(d, ensure_ascii=False, indent=1))
        return
    for path in OUTPUTS:
        try:
            write_json(path, d)
        except Exception as e:
            print("cannot write", path, ":", e, file=sys.stderr)
    try:
        write_json(CACHE, cache)
    except Exception as e:
        print("cannot write cache", CACHE, ":", e, file=sys.stderr)


if __name__ == "__main__":
    main()
