#!/usr/bin/env python3
"""Useful numbers for the appliance details of the Home card -> <DATA_DIR>/appliances.json.

Reads the cycle log written by Home Assistant (see homeassistant/dashboard_package.yaml, shell_command
appliance_log): one CSV line per finished cycle, `date;device;minutes;kwh;cost;offpeak`
(device = washer | dryer | dishwasher, offpeak = 1 when the cycle started in off-peak hours).
Texts are ready to display: last cycle, this month, average of the last 10 cycles.
--print: print, write nothing.
"""
import datetime
import json

import common as C

LOGS = C.env_list("APPLIANCE_LOGS", "/srv/homeassistant/config/appliances.csv")
CUR = C.env("CURRENCY", "€")
OUT = C.data_path("appliances.json")
NAMES = {"washer": ("wash", "washes"), "dryer": ("drying cycle", "drying cycles"), "dishwasher": ("cycle", "cycles")}


def num(x, d=2):
    return "%.*f" % (d, x)


def duration(m):
    m = int(round(m))
    return "%d min" % m if m < 60 else "%d h %02d" % divmod(m, 60)


def read():
    cycles = []
    for f in LOGS:
        try:
            lines = open(f, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        for line in lines:
            p = line.strip().split(";")
            if len(p) < 6 or p[1] not in NAMES:
                continue
            try:
                when = datetime.datetime.strptime(p[0], "%Y-%m-%d %H:%M")
                val = [float(v) if v.strip() else None for v in p[2:5]]
            except ValueError:
                continue
            cycles.append({"quand": when, "app": p[1], "min": val[0], "kwh": val[1], "cout": val[2], "creuse": p[5].strip() == "1"})
    return sorted(cycles, key=lambda c: c["quand"])


def summary(cycles, app, now):
    one, many = NAMES[app]
    cs = [c for c in cycles if c["app"] == app]
    r = {"n_total": len(cs)}
    month = [c for c in cs if c["quand"].year == now.year and c["quand"].month == now.month]
    measured = [c for c in cs if c["min"] is not None and c["cout"] is not None]
    if measured:
        d = measured[-1]
        r["dernier"] = "%s · %s kWh · %s %s · %s" % (duration(d["min"]), num(d["kwh"] or 0), num(d["cout"]), CUR,
                                                      "off-peak" if d["creuse"] else "peak")
        recent = measured[-10:]
        r["moy_min"] = round(sum(c["min"] for c in recent) / len(recent))
        r["moy_cout"] = num(sum(c["cout"] for c in recent) / len(recent))
        r["moyenne"] = "%s per %s · ≈ %s %s" % (duration(r["moy_min"]), one, r["moy_cout"], CUR)
    n = len(month)
    txt = "%d %s" % (n, one if n == 1 else many) if n else "no %s yet" % one
    mc = [c for c in month if c["cout"] is not None]
    if mc:
        txt += " · %s %s" % (num(sum(c["cout"] for c in mc)), CUR)
        if app == "washer" and n:
            txt += " · %d off-peak" % sum(1 for c in mc if c["creuse"])
    r["mois"] = txt
    return r


def main():
    now = datetime.datetime.now()
    cycles = read()
    d = {"maj": now.strftime("%Y-%m-%dT%H:%M")}
    for app in NAMES:
        d[app] = summary(cycles, app, now)
    if C.PRINT:
        print(json.dumps(d, ensure_ascii=False, indent=1))
    else:
        C.write_json(OUT, d)


if __name__ == "__main__":
    main()
