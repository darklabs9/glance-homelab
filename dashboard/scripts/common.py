"""Shared helpers for the dashboard collectors.

Configuration comes from environment variables, optionally loaded from a .env file
(DASHBOARD_ENV, default: config.env next to this file). Real environment variables win.
"""
import json
import os
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_env_file():
    path = os.environ.get("DASHBOARD_ENV") or os.path.join(HERE, "config.env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.split(" #", 1)[0].strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


_load_env_file()


def env(name, default=""):
    return os.environ.get(name, default)


def env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


def env_list(name, default="", sep=","):
    return [x.strip() for x in env(name, default).split(sep) if x.strip()]


TZ = ZoneInfo(env("TZ", "Europe/Brussels") or "UTC")
LAT = env_float("LAT", 50.85)
LON = env_float("LON", 4.35)

# Glance serves <ASSETS_DIR> at /assets. JSON goes to <ASSETS_DIR>/data, images to <ASSETS_DIR>/posters.
ASSETS_DIR = env("ASSETS_DIR", "/srv/glance/assets")
DATA_DIR = env("DATA_DIR", os.path.join(ASSETS_DIR, "data"))
POSTERS_DIR = os.path.join(ASSETS_DIR, "posters")

HA_URL = env("HA_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN = env("HA_TOKEN")

PRINT = "--print" in sys.argv


def log(*a):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def http_json(url, headers=None, data=None, method=None, timeout=30, tries=3, raw=False):
    """GET/POST with a few retries. data (dict) is sent as JSON."""
    h = dict(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method=method or ("POST" if body else "GET"))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read() if raw else json.load(r)
        except Exception as err:   # noqa: BLE001 - network errors of all kinds
            last = err
            if attempt < tries - 1:
                time.sleep(2)
    raise last


def ha(path, data=None, timeout=30):
    """Home Assistant REST API (GET, or POST when data is given)."""
    return http_json(HA_URL + path, {"Authorization": "Bearer " + HA_TOKEN}, data=data, timeout=timeout, tries=2)


def ha_states():
    return {s["entity_id"]: s for s in ha("/api/states")}


def write_json(path, obj, compact=True):
    """Atomic write that never follows a symlink; file is world-readable (Glance runs as another user)."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix="." + os.path.basename(path) + ".")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if isinstance(obj, str):
                fh.write(obj)
            else:
                json.dump(obj, fh, ensure_ascii=False, separators=(",", ":") if compact else None)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:   # noqa: BLE001
        return {} if default is None else default


def data_path(name):
    return os.path.join(DATA_DIR, name)


def num(x, default=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default
