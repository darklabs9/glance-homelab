#!/usr/bin/env python3
"""Utility meters (water, gas, electricity...) typed in by hand once a month -> <DATA_DIR>/meters.json.

How the readings get in: the "Meters" card posts the form to a Home Assistant webhook, which stores each index in
input_number.meter_<key> (see homeassistant/dashboard_package.yaml). A template sensor sensor.meter_<key> mirrors it
with state_class total_increasing, so Home Assistant keeps long-term statistics of every reading.

This script reads those statistics straight from the Home Assistant SQLite database (read-only), plus an optional
CSV of older readings (date;<key1>;<key2>;... with the index at that date), and writes:
  meters   {key: {label, unit, index}}           current index (form placeholders)
  tiles    [{key, label, unit, valeur, spark, txt, tendance}]   consumption per month between the last readings,
           with a sparkline of the last 6 readings and a short comparison ("stable", "+3 vs before")
  reading  {date, jours, due, txt}               last reading and whether a new one is due (>= 28 days)
  readings table of readings, most recent first
--print: print, write nothing.
"""
import csv
import datetime
import json
import os
import sqlite3

import common as C

DB = C.env("HA_DB", "/srv/homeassistant/config/home-assistant_v2.db")
CSV = C.env("METERS_CSV")
OUT = C.data_path("meters.json")
# key|label|unit ; entities used: sensor.meter_<key> (statistics) and input_number.meter_<key> (current index)
METERS = [m.split("|") for m in C.env_list("METERS", "water|Water|m³;gas|Gas|m³;elec_day|Electricity day|kWh;elec_night|Electricity night|kWh", sep=";")]
LAST_READING = C.env("METERS_LAST_READING_ENTITY", "input_text.meter_last_reading")
TZ = C.TZ


def short_date(d):
    return "%d %s" % (d.day, d.strftime("%b"))


def fmt(v):
    return ("%.0f" if abs(v) >= 10 else "%.1f") % v


def main():
    try:
        states = C.ha_states()
    except Exception as err:   # noqa: BLE001
        print("Home Assistant unavailable:", str(err)[:80])
        states = {}
    conn = None
    if os.path.exists(DB):
        conn = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
    by_date = {}
    out = {"generated": datetime.datetime.now(TZ).isoformat(timespec="minutes"), "meters": {}}
    for key, label, unit in METERS:
        sid, inp = "sensor.meter_" + key, "input_number.meter_" + key
        rows = []
        if conn:
            rows = list(conn.execute(
                "select s.start_ts, s.state from statistics s join statistics_meta m on s.metadata_id=m.id "
                "where m.statistic_id=? and s.state is not null order by s.start_ts", (sid,)))
        last = None
        for ts, st in rows:                          # a reading = a moment where the index changes
            if last is not None and st > last + 1e-6:
                by_date.setdefault(datetime.datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d"), {})[key] = st
            last = st
        cur = C.num(states.get(inp, {}).get("state"), 0)
        if cur and (last is None or cur > last + 1e-6):
            # today's reading counts at once (HA computes statistics only once an hour)
            dr = str(states.get(LAST_READING, {}).get("state", ""))
            dr = dr if len(dr) == 10 and dr[:2] == "20" else datetime.date.today().isoformat()
            by_date.setdefault(dr, {})[key] = cur
        out["meters"][key] = {"label": label, "unit": unit, "index": cur}
    if CSV and os.path.exists(CSV):                  # older readings, only for dates not already known
        with open(CSV, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                d = (row.get("date") or "").strip()[:10]
                if len(d) != 10 or d in by_date:
                    continue
                for key, *_ in METERS:
                    v = (row.get(key) or "").replace(",", ".").strip()
                    if v:
                        by_date.setdefault(d, {})[key] = float(v)

    table, prev, prevd = [], {}, {}
    for d in sorted(by_date):
        row = {"date": d}
        for key, *_ in METERS:
            idx = by_date[d].get(key)
            row[key] = idx
            if idx is not None and key in prev:
                days = (datetime.date.fromisoformat(d) - datetime.date.fromisoformat(prevd[key])).days
                row[key + "_conso"] = round(idx - prev[key], 3)
                row[key + "_permonth"] = round((idx - prev[key]) / days * 30.4, 1) if days > 0 else None
            if idx is not None:
                prev[key], prevd[key] = idx, d
        table.append(row)
    out["readings"] = table[::-1]

    tiles = []
    recent = out["readings"][:7]
    for key, label, unit in METERS:
        vals = [r.get(key + "_permonth") for r in recent if r.get(key + "_permonth") is not None][:6][::-1]
        if not vals:
            continue
        v, p = vals[-1], (vals[-2] if len(vals) > 1 else None)
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1.0
        pts = " ".join("%d,%.1f" % (2 + i * (56 / max(1, len(vals) - 1)), 16 - (x - lo) / span * 13) for i, x in enumerate(vals))
        trend, txt = "egal", "first reading"
        if p is not None:
            dlt, thr = v - p, max(0.5, 0.08 * p)
            txt = "stable" if abs(dlt) < thr else ("+%s vs before" if dlt > 0 else "−%s vs before") % fmt(abs(dlt))
            trend = "haut" if dlt >= thr else "bas" if -dlt >= thr else "egal"
        tiles.append({"key": key, "label": label, "unit": unit + "/month", "valeur": fmt(v), "spark": pts, "txt": txt, "tendance": trend})
    out["tiles"] = tiles

    if out["readings"]:
        dd = datetime.date.fromisoformat(out["readings"][0]["date"])
        days = (datetime.date.today() - dd).days
        due = days >= 28
        out["reading"] = {"date": short_date(dd), "jours": days, "due": due,
                          "txt": ("due" + (" this weekend" if datetime.date.today().weekday() >= 3 else " this week")) if due
                          else "next reading around %s" % short_date(dd + datetime.timedelta(days=30))}
    else:
        out["reading"] = {"date": "", "jours": 0, "due": True, "txt": "no reading yet"}

    if C.PRINT:
        print(json.dumps({k: v for k, v in out.items() if k != "readings"}, ensure_ascii=False, indent=1))
        return
    C.write_json(OUT, out)
    C.log("ok", len(table), "readings")


if __name__ == "__main__":
    main()
