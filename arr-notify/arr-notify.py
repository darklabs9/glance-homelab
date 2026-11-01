#!/usr/bin/env python3
"""Groups Sonarr/Radarr notifications before they reach the phone, and attaches the poster to import notifications.

Sonarr and Radarr publish to a "raw" ntfy topic nobody subscribes to; this script, run every minute, reads that topic
and re-publishes to the real one:
  - Health Check Failure: held back HOLD_FAILURE_MIN minutes. If the matching "Restored" arrives first (an update, a
    restart, a VPN container recreated), nothing is sent. Otherwise the alert goes out (Radarr and Sonarr together if
    it is the same problem), then its "Restored" when it arrives. While UPDATE_LOCK_FILE is held by an updater, the
    countdown only starts once the update is over;
  - imports (Sonarr "Import Complete" / "Episode Downloaded", Radarr "Movie Downloaded"): one burst = ONE message,
    sent when nothing new arrived for a while (Sonarr 20 min, at most 1 h; Radarr 3 min, at most 15 min). A single
    import goes out as is. The poster of the (first) movie/series is attached, as a public TMDB/TheTVDB URL taken
    from Radarr/Sonarr, so the phone downloads it itself;
  - everything else (Manual Interaction Required, test...): as is, right away.
Notifications stay queued while sending fails.
Usage: arr-notify.py [--config FILE] [--print]   (--print: show what would be sent, send nothing, save nothing)
"""
import datetime, fcntl, json, os, re, sys, time, unicodedata, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
    """KEY=VALUE lines; real environment variables win over the file."""
    try:
        for line in open(path):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            os.environ.setdefault(k.strip(), v)
    except FileNotFoundError:
        pass


load_env_file(sys.argv[sys.argv.index("--config") + 1] if "--config" in sys.argv
              else os.environ.get("ARR_NOTIFY_CONFIG") or os.path.join(HERE, "config.env"))
E = os.environ.get

NTFY = E("NTFY_URL", "http://ntfy.example.lan").rstrip("/")
NTFY_TOKEN = E("NTFY_TOKEN", "")                 # optional access token (ntfy with auth)
TOPIC = E("NTFY_TOPIC", "homelab")               # the topic your phone subscribes to
SOURCE = E("NTFY_SOURCE_TOPIC", TOPIC + "-arr")  # the topic Sonarr/Radarr publish to
STATE = E("STATE_FILE", os.path.join(HERE, "arr-notify.state.json"))
UPDATE_LOCK = E("UPDATE_LOCK_FILE", "")          # optional: flock held by your updater while it runs
HEARTBEAT = E("HEARTBEAT_FILE", "")              # optional: small JSON written each run (for a staleness check)
HOLD_FAILURE = int(E("HOLD_FAILURE_MIN", "15")) * 60   # a failure shorter than this = no notification
QUIET = {"Sonarr": int(E("SONARR_QUIET_MIN", "20")) * 60, "Radarr": int(E("RADARR_QUIET_MIN", "3")) * 60}
MAX_WAIT = {"Sonarr": int(E("SONARR_MAX_WAIT_MIN", "60")) * 60, "Radarr": int(E("RADARR_MAX_WAIT_MIN", "15")) * 60}
QUIET_RESTORED = 120
POSTERS = E("POSTERS", "1") not in ("0", "false", "no")
API = {"Radarr": (E("RADARR_URL", "").rstrip("/") + "/api/v3/movie", E("RADARR_API_KEY", "")),
       "Sonarr": (E("SONARR_URL", "").rstrip("/") + "/api/v3/series", E("SONARR_API_KEY", ""))}
PRINT = "--print" in sys.argv
RE_ARR = re.compile(r"^(Sonarr|Radarr) - (.+?)\s*$")
RESOLVED = "The following issue is now resolved:"


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def key(msg):
    """First sentence: Sonarr says "... qBittorrent. Connection refused", Radarr "... qBittorrent. Failed to connect"."""
    return norm(msg.replace(RESOLVED, "").strip().split(". ")[0])[:60]


def same(a, b):
    n = min(len(a), len(b))
    return n >= 10 and a[:n] == b[:n]


def duration(s):
    m = max(1, round(s / 60))
    return "%d min" % m if m < 60 else "%d h %02d" % divmod(m, 60)


def no_link(msg):
    return re.sub(r"\s*https?://\S+", "", msg or "").strip()


def import_name(app, msg):
    """(group, detail): Sonarr "Series - 1x02 - Title [Q]" -> (Series, 1x02) ; Radarr -> (Movie (year), '')."""
    m = no_link(msg)
    if app == "Radarr":
        return re.sub(r"\s*\[[^\]]*\]$", "", m), ""
    parts = [x.strip() for x in re.sub(r"\s*\[[^\]]*\]$", "", m).split(" - ")]
    return parts[0], (parts[1] if len(parts) > 1 else "")


def poster(app, name):
    """https URL of the poster of `name` (Radarr "Movie (year)", Sonarr "Series"), None if not found."""
    url, api_key = API.get(app, ("", ""))
    if not POSTERS or not api_key or not url.startswith("http"):
        return None
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"X-Api-Key": api_key}), timeout=20) as r:
            items = json.load(r)
        n = norm(name)
        for x in items:
            if n in (norm(x.get("title")), norm("%s %s" % (x.get("title"), x.get("year")))):
                for im in x.get("images") or []:
                    u = im.get("remoteUrl") or ""
                    if im.get("coverType") == "poster" and u.startswith("https://"):
                        return u.replace("/t/p/original/", "/t/p/w500/")       # TMDB: 500 px is plenty for a phone
    except Exception as err:
        print("poster not found (%s %s): %s" % (app, name[:40], str(err)[:80]))
    return None


def burst_message(app, items):
    """Several imports from one app -> (title, message)."""
    groups = {}
    for it in items:
        g, d = import_name(app, it["msg"])
        groups.setdefault(g, [])
        if d and d not in groups[g]:
            groups[g].append(d)
    if app == "Radarr":
        title = "Radarr - %d movies downloaded" % len(items)
        lines = list(groups)
    else:
        title = "Sonarr - %d imports" % len(items) + (" · " + next(iter(groups)) if len(groups) == 1 else "")
        lines = ["%s: %s" % (g, ", ".join(d)) if d else g for g, d in groups.items()]
    return title, "\n".join(lines[:12]) + ("\nand %d more" % (len(lines) - 12) if len(lines) > 12 else "")


def failures_message(failures, now):
    apps = sorted({p["app"] for p in failures})
    if len({p["key"] for p in failures}) == 1:
        p = failures[0]
        return ("%s - Health Check Failure" % " and ".join(apps), "%s\nFor %s." % (p["msg"], duration(now - min(x["t"] for x in failures))),
                max(p["prio"] for p in failures))
    return ("%s - %d problems" % (" and ".join(apps), len(failures)),
            "\n".join("%s: %s (for %s)" % (p["app"], p["msg"], duration(now - p["t"])) for p in failures), max(p["prio"] for p in failures))


def restored_message(restored):
    apps = sorted({r["app"] for r in restored})
    if len({r["key"] for r in restored}) == 1:
        r = restored[0]
        txt = r["msg"] + ("\nDown for %s." % duration(r["duration"]) if r.get("duration") else "")
        return "%s - Health Check Restored" % " and ".join(apps), txt
    return ("%s - Health Check Restored" % " and ".join(apps),
            "\n".join("%s: %s%s" % (r["app"], r["msg"].replace(RESOLVED, "").strip(), " (down for %s)" % duration(r["duration"]) if r.get("duration") else "")
                      for r in restored))


def process(st, new, now, updating, send, log=print, poster=None):
    """Core logic (no network): updates st, calls send(title, msg, prio, tags, click[, image]) -> bool.
    Returns False if a send failed (st["since"] does not move past an unsent message)."""
    st.setdefault("imports", {}); st.setdefault("failures", []); st.setdefault("restored", [])
    if updating:
        st["update_seen"] = now
    for m in new:
        title, msg = m.get("title") or "", m.get("message") or ""
        k = RE_ARR.match(title)
        what = k.group(2) if k else ""
        app = k.group(1) if k else ""
        t = m.get("time", now)
        if what in ("Import Complete", "Movie Downloaded", "Episode Downloaded"):
            st["imports"].setdefault(app, []).append({"t": t, "title": title, "msg": msg, "prio": m.get("priority", 3),
                                                      "tags": m.get("tags"), "click": m.get("click")})
        elif what == "Health Check Failure":
            c = key(msg)
            if not any(p["app"] == app and same(p["key"], c) for p in st["failures"]):
                st["failures"].append({"app": app, "key": c, "t": t, "msg": msg, "prio": m.get("priority", 3),
                                       "tags": m.get("tags"), "sent": False})
        elif what == "Health Check Restored":
            c = key(msg)
            p = next((p for p in st["failures"] if p["app"] == app and same(p["key"], c)), None)
            if p:
                st["failures"].remove(p)
                if not p["sent"]:
                    log("short failure, nothing sent: %s %s (%s)" % (app, p["msg"][:60], duration(t - p["t"])))
                    st["since"] = m["id"]
                    continue
            st["restored"].append({"app": app, "key": c, "t": t, "msg": msg, "prio": m.get("priority", 3), "tags": m.get("tags"),
                                   "duration": (t - p["t"]) if p else None})
        else:                                     # Manual Interaction Required, test, anything else: as is, right away
            if not send(title or "Sonarr/Radarr", msg, m.get("priority", 3), m.get("tags"), m.get("click")):
                return False
            log("forwarded as is: %s" % title)
        st["since"] = m["id"]

    ok = True
    # failures that last (counted from the end of an update)
    start = lambda p: max(p["t"], st.get("update_seen") or 0)
    due = [p for p in st["failures"] if not p["sent"] and now - start(p) >= HOLD_FAILURE]
    if due:
        batch = due + [p for p in st["failures"] if not p["sent"] and p not in due and any(same(p["key"], d["key"]) for d in due)]
        title, msg, prio = failures_message(batch, now)
        if send(title, msg, prio, batch[0].get("tags"), None):
            for p in batch:
                p["sent"] = True
            log("sent: %d lasting failure(s)" % len(batch))
        else:
            ok = False
    if st["restored"] and now - st["restored"][-1]["t"] >= QUIET_RESTORED:
        r = st["restored"]
        title, msg = restored_message(r)
        if send(title, msg, max(x["prio"] for x in r), r[0].get("tags"), None):
            log("sent: %d restored" % len(r))
            st["restored"] = []
        else:
            ok = False
    for app, items in list(st["imports"].items()):
        if not items or not (now - items[-1]["t"] >= QUIET.get(app, 300) or now - items[0]["t"] >= MAX_WAIT.get(app, 1800)):
            continue
        img = poster(app, import_name(app, items[0]["msg"])[0]) if poster else None   # poster of the first item
        if len(items) == 1:
            i = items[0]
            sent = send(i["title"], i["msg"], i["prio"], i.get("tags"), i.get("click"), img)
        else:
            title, msg = burst_message(app, items)
            sent = send(title, msg, max(i["prio"] for i in items), items[0].get("tags"), None, img)
        if sent:
            log("sent: %d %s import(s)" % (len(items), app))
            st["imports"][app] = []
        else:
            ok = False
    return ok


def auth_headers():
    return {"Authorization": "Bearer " + NTFY_TOKEN} if NTFY_TOKEN else {}


def read_new(since):
    url = "%s/%s/json?poll=1&since=%s" % (NTFY, SOURCE, since)
    with urllib.request.urlopen(urllib.request.Request(url, headers=auth_headers()), timeout=20) as r:
        lines = r.read().decode().splitlines()
    return [m for m in (json.loads(l) for l in lines if l.strip()) if m.get("event") == "message"]


def send(title, msg, prio, tags=None, click=None, image=None):
    if PRINT:
        print("NOTIF> [p%s] %s | %s%s" % (prio, title, msg.replace("\n", " / "), " | image " + image if image else ""))
        return True
    d = {"topic": TOPIC, "title": title, "message": msg, "priority": prio}
    if tags:
        d["tags"] = tags
    if click:
        d["click"] = click
    if image:
        d["attach"], d["filename"] = image, "poster.jpg"
    try:
        h = {"Content-Type": "application/json"}
        h.update(auth_headers())
        r = urllib.request.urlopen(urllib.request.Request(NTFY, data=json.dumps(d).encode(), headers=h), timeout=15)
        return 200 <= r.status < 300
    except Exception as err:
        print("ntfy send failed:", err)
        return False


def update_running():
    """True while another process holds UPDATE_LOCK_FILE (flock)."""
    if not UPDATE_LOCK:
        return False
    try:
        with open(UPDATE_LOCK, "a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except Exception:
        return False


def write_json(path, data):
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def main():
    now = time.time()
    try:
        st = json.load(open(STATE))
    except Exception:
        st = {"since": "2h"}
    stamp = lambda *a: print(datetime.datetime.now().strftime("%F %H:%M"), *a)
    try:
        new = read_new(st.get("since") or "2h")
    except Exception as err:
        stamp("cannot read ntfy:", str(err)[:100])
        return 1
    process(st, new, now, update_running(), send, stamp, poster)
    if PRINT:
        print("waiting: %d import(s), %d failure(s) held back, %d restored" % (
            sum(len(v) for v in st["imports"].values()), sum(not p["sent"] for p in st["failures"]), len(st["restored"])))
        return 0
    write_json(STATE, st)
    if HEARTBEAT:
        write_json(HEARTBEAT, {"generated": datetime.datetime.now().isoformat(timespec="minutes"),
                               "waiting": sum(len(v) for v in st["imports"].values()) + len(st["restored"])})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
