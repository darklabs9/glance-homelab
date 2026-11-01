#!/usr/bin/env python3
"""Wi-Fi micro-outage logger: log only, no notification. Feeds the "Outages" block of the NAS page.

The host (wired to the router) pings a few always-on Wi-Fi devices every 10 s. An outage = at least THRESHOLD
witnesses silent at the same time. Run from cron every minute (6 passes of 10 s, under flock).
State: STATE_FILE (ongoing outage) ; log: LOG_FILE, one JSON line per outage
({"start", "end", "seconds", "gateway": did the router still answer}). Read by nas-state.py.

The same script can watch the internet line: point WITNESSES at a few public IPs (1.1.1.1, 9.9.9.9, 8.8.8.8),
use another STATE_FILE / LOG_FILE, and set NET_OUTAGE_LOG / NET_OUTAGE_STATE in the nas-state config.

Usage: wifi-ping.py [--config /path/wifi-ping.env] [--print]   (--print shows each pass)
"""
import datetime, json, os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env_file(path):
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
              else os.environ.get("WIFI_PING_CONFIG") or os.path.join(HERE, "wifi-ping.env"))


def witnesses(raw):
    """WITNESSES="192.168.1.20=Smart speaker,192.168.1.21=Smart plug" (the label is optional)."""
    out = {}
    for part in raw.split(","):
        part = part.strip()
        if part:
            ip, _, name = part.partition("=")
            out[ip.strip()] = name.strip() or ip.strip()
    return out


STATE = os.environ.get("STATE_FILE", "/var/lib/nas-tab/wifi-ping.json")
LOG = os.environ.get("LOG_FILE", "/var/lib/nas-tab/outages-wifi.jsonl")
GATEWAY = os.environ.get("GATEWAY", "")
WITNESSES = witnesses(os.environ.get("WITNESSES", ""))
THRESHOLD = int(os.environ.get("THRESHOLD") or max(1, (len(WITNESSES) + 1) // 2))   # silent witnesses at once
STEP = int(os.environ.get("INTERVAL", "10"))     # seconds between two passes
STALE = 180                                      # an "ongoing" outage with no pass for 3 min (host down, cron skipped) is dropped
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "120"))
PRINT = "--print" in sys.argv


def now():
    return datetime.datetime.now().replace(microsecond=0)


def ping_all(ips):
    """{ip: True if it answered}, in parallel, 1 s max per device."""
    p = {ip: subprocess.Popen(["ping", "-c1", "-W1", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for ip in ips}
    return {ip: pr.wait() == 0 for ip, pr in p.items()}


def read_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"start": None, "gateway_down": False, "seen": None}


def write_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, STATE)


def log_outage(start, end, gateway_down):
    s = int((end - start).total_seconds())
    with open(LOG, "a") as f:
        f.write(json.dumps({"start": start.isoformat(), "end": end.isoformat(), "seconds": max(s, STEP),
                            "gateway": not gateway_down}) + "\n")
    if PRINT:
        print("outage logged:", start, s, "s", "(router silent too)" if gateway_down else "")


def prune():
    """Keep KEEP_DAYS days in the log (once a day, first pass after midnight)."""
    try:
        lim = (now() - datetime.timedelta(days=KEEP_DAYS)).isoformat()
        lines = [l for l in open(LOG) if l.strip() and json.loads(l)["start"] >= lim]
        tmp = LOG + ".tmp"
        open(tmp, "w").writelines(lines)
        os.replace(tmp, LOG)
    except FileNotFoundError:
        pass


def one_pass(st, rep, t):
    """Update the state with the answers rep ({ip: bool}, gateway included) at time t."""
    silent = [ip for ip in WITNESSES if not rep.get(ip)]
    cut = len(silent) >= THRESHOLD
    gw_ok = rep.get(GATEWAY, True) if GATEWAY else True
    seen = datetime.datetime.fromisoformat(st["seen"]) if st.get("seen") else None
    if st.get("start") and seen and (t - seen).total_seconds() > STALE:
        if PRINT:
            print("ongoing outage dropped (no pass since", seen, ")")
        st["start"] = None
    if cut and not st.get("start"):
        st["start"], st["gateway_down"] = t.isoformat(), not gw_ok
    elif cut:
        st["gateway_down"] = st.get("gateway_down") or not gw_ok
    elif st.get("start"):
        log_outage(datetime.datetime.fromisoformat(st["start"]), t, st.get("gateway_down"))
        st["start"], st["gateway_down"] = None, False
    st["seen"] = t.isoformat()
    if PRINT:
        print(t.strftime("%H:%M:%S"), "silent:", ", ".join(WITNESSES[i] for i in silent) or "none",
              "| router", "ok" if gw_ok else "silent", "| OUTAGE" if cut else "")
    return st


def main():
    if not WITNESSES:
        sys.exit("wifi-ping: no WITNESSES configured (see wifi-ping.example.env)")
    for p in (STATE, LOG):
        os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    st = read_state()
    if st.get("day") != datetime.date.today().isoformat():
        prune()
        st["day"] = datetime.date.today().isoformat()
    t0 = time.time()
    targets = list(WITNESSES) + ([GATEWAY] if GATEWAY else [])
    while time.time() - t0 < 55:
        began = time.time()
        st = one_pass(st, ping_all(targets), now())
        write_state(st)
        left = STEP - (time.time() - began)
        if time.time() - t0 + STEP >= 55:
            break
        if left > 0:
            time.sleep(left)


if __name__ == "__main__":
    main()
