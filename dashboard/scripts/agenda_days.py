#!/usr/bin/env python3
"""Agenda grouped by day for the Home page -> <DATA_DIR>/agenda.json.

  jours: the next AGENDA_DAYS days that have something (today included), each with the day's weather and its events.
Sources: Home Assistant calendar entities (AGENDA_CALENDARS). Events from AGENDA_OTHER_CALENDARS (public holidays,
school holidays...) are shown in grey instead of gold. Daily weather: Open-Meteo at LAT/LON (no key needed),
cached 30 min.
--print: print the result, write nothing.
"""
import datetime
import time

import common as C

CALS = C.env_list("AGENDA_CALENDARS", "calendar.personal")
OTHER = set(C.env_list("AGENDA_OTHER_CALENDARS"))
NB_DAYS = int(C.env("AGENDA_DAYS", "5"))
HORIZON = int(C.env("AGENDA_HORIZON_DAYS", "35"))
OUT = C.data_path("agenda.json")
WEATHER_CACHE = C.data_path(".agenda-weather-cache.json")
ABBR = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TZ = C.TZ


def hm(m):
    return "%d:%02d" % (m // 60, m % 60)


def google_events(now):
    out, seen = [], set()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - datetime.timedelta(days=31)   # stays already started
    end = now + datetime.timedelta(days=HORIZON + 1)
    for cal in CALS:
        path = "/api/calendars/%s?start=%s&end=%s" % (cal, start.strftime("%Y-%m-%dT%H:%M:%S"), end.strftime("%Y-%m-%dT%H:%M:%S"))
        try:
            data = C.ha(path, timeout=15)
        except Exception as err:   # noqa: BLE001
            print("calendar %s unavailable: %s" % (cal, str(err)[:80]))
            continue
        for ev in data:
            s = ev.get("start") or {}
            e = ev.get("end") or {}
            k = ((ev.get("summary") or "").strip().lower(), s.get("dateTime") or s.get("date"))
            if k in seen:            # same event in two calendars
                continue
            seen.add(k)
            out.append({"summary": ev.get("summary") or "Appointment", "start": s, "end": e,
                        "location": ev.get("location") or "", "cal": cal})
    return out


def normalise(evs):
    """-> [{d0: date, d1: last day included, h: minutes|None, t, detail, debut_ts, fin_ts, perso}]"""
    r = []
    for ev in evs:
        s, e = ev["start"], ev["end"]
        try:
            if s.get("dateTime"):
                # local time zone of the event date (not today's offset: DST changes would shift later events)
                a = datetime.datetime.fromisoformat(s["dateTime"]).astimezone(TZ).replace(tzinfo=None)
                b = (datetime.datetime.fromisoformat(e["dateTime"]).astimezone(TZ).replace(tzinfo=None)
                     if e.get("dateTime") else a + datetime.timedelta(hours=1))
                d1 = (b - datetime.timedelta(seconds=1)).date() if b > a else a.date()
                x = {"d0": a.date(), "d1": max(d1, a.date()), "h": a.hour * 60 + a.minute,
                     "debut_ts": int(a.timestamp()), "fin_ts": int(b.timestamp())}
            else:
                a = datetime.date.fromisoformat(s["date"])
                b = datetime.date.fromisoformat(e["date"]) if e.get("date") else a + datetime.timedelta(days=1)
                x = {"d0": a, "d1": max(a, b - datetime.timedelta(days=1)), "h": None, "debut_ts": 0, "fin_ts": 0}
        except Exception:   # noqa: BLE001
            continue
        place = (ev.get("location") or "").split(",")[0].strip()
        x.update({"t": ev["summary"].strip(), "detail": place[:60], "perso": ev.get("cal") not in OTHER})
        r.append(x)
    return r


def daily_weather():
    """{iso date: {tmax, tmin, pp, code}} from Open-Meteo, cached 30 min."""
    cache = C.read_json(WEATHER_CACHE)
    if cache.get("ts", 0) > time.time() - 1800:
        return cache.get("days", {})
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%.3f&longitude=%.3f&daily=temperature_2m_max,"
           "temperature_2m_min,precipitation_probability_max,weather_code&timezone=auto&forecast_days=8" % (C.LAT, C.LON))
    try:
        d = C.http_json(url, timeout=20)["daily"]
    except Exception as err:   # noqa: BLE001
        print("weather unavailable:", str(err)[:80])
        return cache.get("days", {})
    days = {t: {"tmax": d["temperature_2m_max"][i], "tmin": d["temperature_2m_min"][i],
                "pp": d["precipitation_probability_max"][i], "code": d["weather_code"][i]} for i, t in enumerate(d["time"])}
    if not C.PRINT:
        C.write_json(WEATHER_CACHE, {"ts": int(time.time()), "days": days})
    return days


def weather_txt(m):
    if not m or m.get("tmax") is None:
        return None
    t = "%d° / %d°" % (round(m["tmax"]), round(m["tmin"])) if m.get("tmin") is not None else "%d°" % round(m["tmax"])
    code, pp = m.get("code") or 0, int(m.get("pp") or 0)
    if code in (95, 96, 99):
        return [t, "thunderstorm", "al"]
    if code in (71, 73, 75, 77, 85, 86):
        return [t, "snow", "al"]
    if pp >= 20:
        return [t, "rain %d %%" % pp, "pl"]
    return [t, "dry", ""]


def main():
    now = datetime.datetime.now(TZ).replace(tzinfo=None)
    today, nts = now.date(), time.time()
    allev = normalise(google_events(now))

    def over(x):   # today: drop what is over (or started more than 1 h ago with no known end)
        if x["d1"] < today:
            return True
        if x["h"] is None or x["d0"] != today:
            return False
        if x["fin_ts"] and x["fin_ts"] > x["debut_ts"]:
            return x["fin_ts"] < nts
        return x["debut_ts"] and nts - x["debut_ts"] > 3600
    allev = [x for x in allev if not over(x)]
    allev.sort(key=lambda x: (x["d0"], -1 if x["h"] is None else x["h"], x["t"].lower()))
    wx = daily_weather()
    days = []
    for n in range(HORIZON + 1):
        d = today + datetime.timedelta(days=n)
        evs = [x for x in allev if x["d0"] == d or (n == 0 and x["d0"] < d <= x["d1"])]
        if not evs:
            continue
        rows = []
        for x in evs:
            det = [x["detail"]] if x["detail"] else []
            if x["d1"] > x["d0"]:
                det.append(("until %s %d" % (SHORT[x["d1"].weekday()], x["d1"].day)) if x["d0"] == d
                           else "day %d of %d" % ((d - x["d0"]).days + 1, (x["d1"] - x["d0"]).days + 1))
            all_day = x["h"] is None or x["d0"] != d
            rows.append({"heure": "" if all_day else hm(x["h"]), "quoi": x["t"][:90], "detail": " · ".join(det),
                         "debut_ts": 0 if all_day else x["debut_ts"], "fin_ts": 0 if all_day else x["fin_ts"],
                         "perso": bool(x.get("perso")), "classe": ""})
        m = weather_txt(wx.get(d.isoformat()))
        days.append({"date": d.isoformat(), "num": d.day, "abbr": ABBR[d.weekday()], "ecart": n,
                     "quand": "today" if n == 0 else "tomorrow" if n == 1 else "in %d days" % n,
                     "meteo_t": m[0] if m else "", "meteo_txt": m[1] if m else "", "meteo_cls": m[2] if m else "", "evs": rows})
        if len(days) >= NB_DAYS:
            break
    out = {"maj": int(nts), "maj_txt": now.strftime("%H:%M"), "nb_jours": len(days), "horizon": HORIZON, "jours": days}
    if C.PRINT:
        for j in days:
            print("%s %s %-11s %s %s" % (j["abbr"], j["num"], j["quand"], j["meteo_t"], j["meteo_txt"]))
            for e in j["evs"]:
                print("     %-6s %s%s" % (e["heure"] or "day", e["quoi"], (" · " + e["detail"]) if e["detail"] else ""))
        return
    C.write_json(OUT, out)


if __name__ == "__main__":
    main()
