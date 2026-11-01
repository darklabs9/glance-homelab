#!/usr/bin/env python3
"""Hourly forecast for the animated sky header (assets/sky.js).

Sources:
  - Home Assistant weather entity (action weather.get_forecasts, type hourly), 48 h
  - optional rain radar (Buienradar, Benelux only), which wins over the hourly forecast for the next 2 h
  - optional official warnings (MeteoAlarm Atom feed), shown as a coloured pill

Writes:
  <DATA_DIR>/weather.json      read by the Glance custom-api widget (alert text, readable without JS,
                               plus "brut" = the full payload copied into data-brut so the sky draws at once)
  <DATA_DIR>/weather-sky.json  re-read every 10 min by sky.js (same origin as Glance)

The rain alert text ("Light rain in 1 h 30 (20:00)", "No rain in the next 48 h"...) is computed here AND
recomputed every minute by sky.js with the same rule: keep both in sync.
--print: compute and print, write nothing.
"""
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import common as C

WEATHER_ENTITY = C.env("HA_WEATHER_ENTITY", "weather.forecast_home")
RAIN_RADAR = C.env("RAIN_RADAR", "none").lower()          # "buienradar" or "none"
ALARM_FEED = C.env("METEOALARM_FEED")                      # e.g. https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-belgium
ALARM_AREAS = [a.lower() for a in C.env_list("METEOALARM_AREAS")]   # area names as written in the feed
ALARM_CODES = C.env_list("METEOALARM_GEOCODES")            # optional EMMA/NUTS codes, e.g. BE32
OUT_WIDGET = C.data_path("weather.json")
OUT_SKY = C.data_path("weather-sky.json")
ALARM_CACHE = C.data_path("meteoalarm.json")
TZ = C.TZ
UA = {"User-Agent": "glance-homelab-dashboard"}


# short states read by sky.js: s clear, p partly cloudy, c overcast, f fog, r rain, n snow, o thunderstorm
def state_of(cond, clouds):
    if cond in ("lightning", "lightning-rainy"):
        return "o"
    if cond in ("snowy", "snowy-rainy"):
        return "n"
    if cond in ("rainy", "pouring", "hail"):
        return "r"
    if cond == "fog":
        return "f"
    if cond in ("sunny", "clear-night"):
        return "s"
    if cond == "partlycloudy":
        return "p"
    if cond == "cloudy":
        return "c"
    return "s" if clouds < 25 else "p" if clouds < 75 else "c"


DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def precip_type(r):
    """An hour counts as rain / snow / thunderstorm (same rule as sky.js)."""
    if r[2] == "o":
        return "o"
    if r[3] > 0:
        return "n" if r[2] == "n" else "r"
    return None


def episodes(rows, now):
    """Consecutive precipitation episodes to come (whole hours), plus strong wind periods (>= 45 km/h)."""
    E, cur, V = [], None, None
    for r in rows:
        if r[0] + 3600 <= now:          # hour already over
            continue
        ty = precip_type(r)
        if ty and cur and cur["ty"] == ty and abs(r[0] - cur["fin"]) < 60:
            cur["fin"] = r[0] + 3600
            cur["max"] = max(cur["max"], r[3])
        elif ty:
            cur = {"ty": ty, "deb": r[0], "fin": r[0] + 3600, "max": r[3]}
            E.append(cur)
        else:
            cur = None
        if r[5] >= 45:
            if V and abs(r[0] - V["fin"]) < 60:
                V["fin"] = r[0] + 3600
                V["max"] = max(V["max"], r[5])
            else:
                V = {"ty": "v", "deb": r[0], "fin": r[0] + 3600, "max": r[5]}
                E.append(V)
        else:
            V = None
    return E


def label(e):
    """"Light rain", "Moderate snow", "Thunderstorm"... (usual thresholds in mm/h)."""
    m = e["max"]
    if e["ty"] == "o":
        return "Violent thunderstorm" if m >= 6 else "Thunderstorm"
    if e["ty"] == "n":
        return ("Light" if m < 0.5 else "Moderate" if m < 1.5 else "Heavy") + " snow"
    return ("Light" if m < 2.5 else "Moderate" if m < 7.5 else "Heavy") + " rain"


def delay(h):
    """Readable delay: "45 min", "1 h 30", then to the hour beyond 3 h."""
    if h < 1:
        m = max(5, round(h * 60 / 5) * 5)
        return "1 h" if m >= 60 else f"{m} min"
    if h < 3:
        H = int(h)
        M = round((h - H) * 60 / 5) * 5
        if M == 60:
            H, M = H + 1, 0
        return f"{H} h" + (f" {M:02d}" if M else "")
    return f"{round(h)} h"


def when(ts, now):
    """The time in brackets: "20:00", "midnight", "tonight 3:00", "tomorrow 7:00", "Tuesday 14:00"."""
    d = datetime.fromtimestamp(ts + 1800, TZ).replace(minute=0)   # rounded to the hour
    n = datetime.fromtimestamp(now, TZ)
    j = (d.date() - n.date()).days
    if j == 0:
        return f"{d.hour}:00"
    if j == 1 and d.hour == 0:
        return "midnight"
    if j == 1 and d.hour < 6 and n.hour >= 12:
        return f"tonight {d.hour}:00"
    if j == 1:
        return f"tomorrow {d.hour}:00"
    return f"{DAYS[d.weekday()]} {d.hour}:00"


def hh(ts):
    return f"{datetime.fromtimestamp(ts + 1800, TZ).hour}:00"


def hm(ts):
    d = datetime.fromtimestamp(ts, TZ)
    return f"{d.hour}:{d.minute:02d}"


# ---------- rain radar (Buienradar: 5-minute steps over 2 h, Benelux only) ----------
BENELUX = (49.3, 53.8, 2.3, 7.4)
RAIN_MIN = 0.2   # mm/h: below that, a few drops that do not matter


def radar(now):
    """{genere, pluie, deb, fin, jusqua, max} in Unix seconds, or None."""
    if RAIN_RADAR != "buienradar":
        return None
    if not (BENELUX[0] <= C.LAT <= BENELUX[1] and BENELUX[2] <= C.LON <= BENELUX[3]):
        return None
    url = "https://gpsgadget.buienradar.nl/data/raintext?lat=%.2f&lon=%.2f" % (C.LAT, C.LON)
    try:
        txt = C.http_json(url, UA, raw=True, timeout=20).decode()
    except Exception as err:   # noqa: BLE001
        C.log("radar unavailable:", err)
        return None
    n = datetime.fromtimestamp(now, TZ)
    pts = []
    for line in txt.split():
        v, t = line.split("|")
        h, m = map(int, t.split(":"))
        d = n.replace(hour=h, minute=m, second=0, microsecond=0)
        if d < n - timedelta(hours=12):      # past midnight
            d += timedelta(days=1)
        v = int(v)
        pts.append((int(d.timestamp()), 0.0 if v <= 0 else round(10 ** ((v - 109) / 32.0), 2)))   # documented formula
    if not pts:
        return None
    rain = [(t, x) for t, x in pts if x >= RAIN_MIN and t >= now - 300]
    out = {"genere": int(now), "pluie": bool(rain), "deb": None, "fin": None, "jusqua": pts[-1][0], "max": 0.0}
    if rain:
        start = rain[0][0]
        out["deb"] = start
        out["fin"] = next((t for t, x in pts if t > start and x < RAIN_MIN), None)
        out["max"] = max(x for _, x in rain)
    return out


# ---------- official warnings (MeteoAlarm Atom feed) ----------
NS = {"a": "http://www.w3.org/2005/Atom", "cap": "urn:oasis:names:tc:emergency:cap:1.2"}
TYPES = {"wind": "wind", "snow": "snow / ice", "ice": "snow / ice", "thunder": "thunderstorm", "fog": "fog",
         "high temp": "heat", "heat": "heat", "low temp": "cold", "cold": "cold", "coastal": "coastal",
         "forest": "forest fire", "avalanche": "avalanche", "rain flood": "rain / flood", "flood": "flood", "rain": "rain"}
LEVELS = {"yellow": 1, "orange": 2, "red": 3}


def _t(e, tag):
    x = e.find(tag, NS)
    return (x.text or "").strip() if x is not None else ""


def warnings_feed(now):
    """Alerts for the configured area, cached 15 min: [{level, type, rank}]."""
    if not ALARM_FEED or not (ALARM_AREAS or ALARM_CODES):
        return []
    cache = C.read_json(ALARM_CACHE)
    if cache.get("ts", 0) > now - 900:
        return cache.get("alerts", [])
    try:
        root = ET.fromstring(C.http_json(ALARM_FEED, UA, raw=True, timeout=20))
    except Exception as err:   # noqa: BLE001
        C.log("warnings feed unavailable:", err)
        return cache.get("alerts", [])
    alerts = {}
    for e in root.findall("a:entry", NS):
        area = _t(e, "cap:areaDesc").lower()
        codes = [_t(g, "value") or _t(g, "cap:value") for g in e.findall("cap:geocode", NS)]
        if not (any(a in area for a in ALARM_AREAS) or any(c in codes for c in ALARM_CODES)):
            continue
        if _t(e, "cap:message_type").lower() == "cancel":
            continue
        m = re.match(r"\s*(green|yellow|orange|red)\b", _t(e, "a:title"), re.I)   # the level is in the title
        color = m.group(1).lower() if m else ""
        if color not in LEVELS:
            continue
        exp = _t(e, "cap:expires")
        try:
            if exp and datetime.fromisoformat(exp.replace("Z", "+00:00")).timestamp() < now:
                continue
        except ValueError:
            pass
        raw = ""
        for p in e.findall("cap:parameter", NS):
            if (_t(p, "cap:valueName") or _t(p, "valueName")) == "awareness_type":
                raw = _t(p, "cap:value") or _t(p, "value")
        raw = (raw or _t(e, "cap:event")).lower().replace("-", " ")
        typ = next((v for k, v in TYPES.items() if k in raw), "warning")
        key = typ + "|" + color
        alerts[key] = {"level": color, "type": typ, "rank": LEVELS[color]}
    out = sorted(alerts.values(), key=lambda a: -a["rank"])
    if not C.PRINT:
        C.write_json(ALARM_CACHE, {"ts": int(now), "alerts": out})
    return out


def warning(now):
    a = warnings_feed(now)
    if not a:
        return None
    x = a[0]
    return {"niveau": x["level"], "texte": f"{x['level'].capitalize()} warning · {x['type']}"}


# ---------- alert text ----------
def alert(rows, now, rad=None):
    E = episodes(rows, now)
    P = [e for e in E if e["ty"] != "v"]
    V = [e for e in E if e["ty"] == "v"]
    # within 2 h, the radar wins
    if rad and rad["pluie"] and rad["deb"]:
        m = rad["max"]
        name = ("Light" if m < 2.5 else "Moderate" if m < 7.5 else "Heavy") + " rain"
        if rad["deb"] <= now + 180:
            html = f"{name} until {hm(rad['fin'])}" if rad["fin"] else f"{name} now"
        else:
            html = f"{name} in {delay((rad['deb'] - now) / 3600)} <em>({hm(rad['deb'])})</em>"
        return {"classe": "r", "html": html, "texte": html.replace("<em>", "").replace("</em>", "")}
    if rad and rad["jusqua"]:
        P = [e for e in P if e["fin"] > rad["jusqua"]]          # dry radar: forecast rain inside its window does not come
        if P and P[0]["deb"] < rad["jusqua"] and P[0]["ty"] != "o":
            t = f"No rain before {hm(rad['jusqua'])}"
            return {"classe": "sec", "html": t, "texte": t}
    if not P:
        return {"classe": "sec", "html": "No rain in the next 48 h", "texte": "No rain in the next 48 h"}
    e = P[0]
    name = label(e)
    d = (e["deb"] - now) / 3600
    cl = e["ty"]
    if d <= 0.05:
        html = f"{name} until {hh(e['fin'])}"
    elif d >= (24 if e["ty"] == "o" else 12):   # "Light rain in 24 h" reads oddly: say "No rain before ..." from 12 h
        word = "snow" if e["ty"] == "n" else "thunderstorm" if e["ty"] == "o" else "rain"
        html = f"No {word} before {when(e['deb'], now)}"
        cl = "sec"
    else:
        html = f"{name} in {delay(d)} <em>({when(e['deb'], now)})</em>"
    if e["ty"] == "o":
        vm = max([r[5] for r in rows if e["deb"] <= r[0] < e["fin"]] or [0])
        if vm >= 30:
            html += f" · wind {round(vm)} km/h"
    else:
        v = next((x for x in V if x["fin"] > e["deb"] - 3600 and x["deb"] < e["fin"] + 3600), None)
        if v:
            html += f" · wind {round(v['max'])} km/h"
    return {"classe": cl, "html": html, "texte": html.replace("<em>", "").replace("</em>", "")}


def hourly(now):
    rep = C.ha("/api/services/weather/get_forecasts?return_response",
               {"entity_id": WEATHER_ENTITY, "type": "hourly"})
    fc = rep["service_response"][WEATHER_ENTITY]["forecast"]
    rows = []
    for f in fc:
        ts = datetime.fromisoformat(f["datetime"]).timestamp()
        cl = float(f.get("cloud_coverage") or 0)
        rows.append([int(ts), round(float(f.get("temperature") or 0), 1), state_of(f.get("condition", ""), cl),
                     round(float(f.get("precipitation") or 0), 1), round(cl), round(float(f.get("wind_speed") or 0))])
    if len(rows) < 6:
        raise SystemExit("hourly forecast too short (%d h)" % len(rows))
    at = C.ha("/api/states/" + WEATHER_ENTITY).get("attributes", {})
    cur = {"ts": int(now), "temp": round(float(at.get("temperature", rows[0][1])), 1),
           "nuages": round(float(at.get("cloud_coverage", rows[0][4]) or 0))}
    return rows, cur


def main():
    now = datetime.now(timezone.utc).timestamp()
    rows, cur = hourly(now)
    sun = C.ha("/api/states/sun.sun").get("attributes", {})
    rad = radar(now)
    out = {
        "maj": datetime.now(TZ).strftime("%Y-%m-%dT%H:%M"),
        "lat": round(C.LAT, 3), "lon": round(C.LON, 3),
        "heures": rows,                 # [ts, temp °C, state, mm, clouds %, wind km/h]
        "actuel": cur,
        "soleil": {"lever": sun.get("next_rising"), "coucher": sun.get("next_setting")},
        "vigilance": warning(now),
        "radar": rad,
        "alerte": alert(rows, now, rad),
    }
    brut = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    if C.PRINT:
        print(json.dumps({k: v for k, v in out.items() if k != "heures"}, ensure_ascii=False, indent=1))
        for r in rows[:30]:
            print(datetime.fromtimestamp(r[0], TZ).strftime("%a %H:00"), r[1:])
        print(len(rows), "hours,", len(brut), "bytes")
        return
    C.write_json(OUT_WIDGET, dict(out, brut=brut))
    C.write_json(OUT_SKY, brut)
    C.log("ok", len(rows), "h,", out["alerte"]["texte"])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:   # noqa: BLE001
        C.log("ERROR", type(err).__name__, err)
        raise SystemExit(1)
