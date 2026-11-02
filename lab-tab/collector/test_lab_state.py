"""Offline self-check for lab-state.py build(): python3 test_lab_state.py"""
import datetime, importlib.util, os

spec = importlib.util.spec_from_file_location("lab_state", os.path.join(os.path.dirname(__file__), "lab-state.py"))
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

NOW = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=datetime.timezone.utc)
CAT = {"groups": [{"id": "entry", "title": "Front door", "entry": True}, {"id": "apps", "title": "Apps"}],
       "containers": [
           {"container": "proxy", "name": "Proxy", "group": "entry", "health": "https://proxy/", "vmid": 1, "tier": "critical"},
           {"container": "wiki", "name": "Wiki", "group": "apps", "health": "http://wiki/", "vmid": 2, "tier": "standard"},
           {"container": "vault", "name": "Vault", "group": "apps", "vmid": 3, "tier": "important"},
           {"container": "media", "name": "Jellyfin", "group": "apps", "tier": "standard"},
           {"container": "ghost", "name": "Ghost", "group": "apps", "health": "http://ghost/", "tier": "lab"},
       ]}
PROBES = {"https://proxy/": {"code": 200, "ms": 40, "error": ""},
          "http://wiki/": {"code": 502, "ms": 12, "error": ""},
          "http://ghost/": {"code": None, "ms": 5000, "error": "timed out"}}
P = {"title": "Lab", "nodes": [{"name": "n1", "temp": 61.0, "memUsed": 8e9, "memTotal": 16e9, "uptime": 3 * 86400, "load": [2, 1, 1]},
                               {"name": "n2", "temp": 70.4, "memUsed": 4e9, "memTotal": 16e9, "uptime": 9 * 86400, "load": [5, 1, 1]}],
     "guests": {"vm": {"running": 1, "total": 2}, "lxc": {"running": 2, "total": 2},
                "list": [{"vmid": 1, "status": "running", "node": "n1", "cpu": 3.0, "mem": 40.0},
                         {"vmid": 2, "status": "running", "node": "n2", "cpu": 1.0, "mem": 20.0},
                         {"vmid": 3, "status": "stopped", "node": "n2", "cpu": 0, "mem": 0}]},
     "storage": [{"name": "tank", "host": "n1", "used": 91.2, "usedBytes": 3.6e12, "totalBytes": 4e12},
                 {"name": "small", "host": "n2", "used": 10, "usedBytes": 1e11, "totalBytes": 1e12}],
     "alerts": {"groups": [{"type": "memory", "label": "Memory", "level": "warning", "count": 2, "unacked": 2, "detail": "x"},
                           {"type": "old", "label": "Acked", "level": "critical", "count": 1, "unacked": 0, "detail": "y"}]},
     "kuma": {"monitors": [{"name": "Jellyfin", "status": "down", "uptime": 90},
                           {"name": "DNS", "status": "pending", "uptime": 99}]}}

d = L.build(CAT, PROBES, P, {"state": "mains", "charge": 100, "load": 20}, None, NOW)
S = {s["c"]: s for s in d["services"]}
assert (S["proxy"]["state"], S["proxy"]["health"]) == ("running", "healthy")
assert (S["wiki"]["state"], S["wiki"]["health"]) == ("running", "unhealthy")
assert S["vault"]["state"] == "stopped"                 # guest status wins over everything
assert S["media"]["state"] == "down"                    # matched to the Kuma monitor by name
assert S["ghost"]["state"] == "unreachable"
ids = [p["id"] for p in d["problems"]]
assert "kuma:Jellyfin" not in ids and "kuma:DNS" in ids  # a matched monitor is not reported twice
assert "pulse:memory" in ids and "pulse:old" not in ids  # acknowledged alerts stay off the list
assert [p["level"] for p in d["problems"]] == sorted((p["level"] for p in d["problems"]), key={"down": 0, "warn": 1}.get)
assert {p["id"]: p["level"] for p in d["problems"]}["svc:vault"] == "down"   # important tier is loud
assert {p["id"]: p["level"] for p in d["problems"]}["svc:ghost"] == "warn"   # lab tier is quiet
sy = d["system"]
assert sy["containers"] == {"running": 3, "total": 4, "label": "Guests"}
assert sy["cpu_temp"] == 70 and sy["ram"] == {"used": 12, "total": 32} and sy["uptime_days"] == 3
assert sy["disk"]["pct"] == 91 and sy["disk"]["label"].startswith("TB · tank")
assert "tasks" not in d                                  # no GitLab source: nas.js hides the block
assert d["labels"]["host"] == "Lab" and d["labels"]["host_ip"] == "n1 · n2"
assert L.build(CAT, {}, None, None, [], NOW)["tasks"] == []  # works with no Pulse at all
print("lab-state self-check ok")
