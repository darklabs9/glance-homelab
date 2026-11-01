#!/usr/bin/env python3
"""Home page data -> <DATA_DIR>/home.json (cron every 5 min, or every minute if you like).

  todo     "To do" card: only actions, most urgent first. Each item:
           {quoi, detail, icone, type, urgence 0-2, lien?, bouton?, bouton_txt?}
           Sources (each optional): items of a Home Assistant to-do list (with a "Done" button), robot vacuum not
           run for N days (with a "Start" button), low batteries, pending Home Assistant updates, Paperless inbox,
           monthly meter reading (from meters.json).
  pieces   room tiles at the top of the Home card: temperature (colour from cool to warm), humidity, 1 h trend
  courses  number of items left on the Mealie shopping list (badge on the Groceries button)
--print: print, write nothing.
"""
import datetime
import json
import re
import urllib.parse

import common as C

OUT = C.data_path("home.json")
WEBHOOK = C.env("HOME_WEBHOOK_ID")                       # HA webhook for the Home card buttons (see homeassistant/)
HA_PUBLIC = C.env("HA_PUBLIC_URL", C.HA_URL).rstrip("/")
TODO_ENTITY = C.env("TODO_ENTITY")                       # e.g. todo.chores
VACUUM = C.env("VACUUM_ENTITY")                          # e.g. vacuum.robot
VACUUM_LAST = C.env("VACUUM_LAST_CLEAN_ENTITY")          # timestamp sensor of the last cleaning
VACUUM_DAYS = int(C.env("VACUUM_REMIND_DAYS", "3"))
BATTERY_EXCLUDE = C.env("BATTERY_EXCLUDE", "phone|watch|vacuum|robot")   # regex on entity_id (rechargeable devices)
PAPERLESS_URL = C.env("PAPERLESS_URL").rstrip("/")
PAPERLESS_TOKEN = C.env("PAPERLESS_TOKEN")
PAPERLESS_INBOX_LINK = C.env("PAPERLESS_INBOX_LINK")
MEALIE_URL = C.env("MEALIE_URL").rstrip("/")
MEALIE_TOKEN = C.env("MEALIE_TOKEN")
MEALIE_LIST_ID = C.env("MEALIE_LIST_ID")
# "Living room|sensor.living_room_temperature|sensor.living_room_humidity|🛋;Bedroom|sensor.bedroom_temperature||🛏"
ROOMS = [r.split("|") for r in C.env_list("ROOMS", sep=";")]


def webhook(target, **extra):
    q = urllib.parse.urlencode(dict(target=target, **extra))
    return "%s/api/webhook/%s?%s" % (C.HA_URL, WEBHOOK, q) if WEBHOOK else ""


def hue(t):
    """Hue of a room temperature: cool (< 19.5) = blue 210->180, fine = green 160->120, warm (> 23.5) = orange 45->15."""
    if t < 19.5:
        return int(max(180, min(210, 210 - (t - 16) * 8.6)))
    if t <= 23.5:
        return int(160 - (t - 19.5) * 10)
    return int(max(15, 45 - (t - 23.5) * 8))


def trend_1h(entity):
    """Temperature change over the last hour (None if unknown)."""
    try:
        start = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        h = C.ha("/api/history/period/%s?filter_entity_id=%s&minimal_response&no_attributes" % (urllib.parse.quote(start), entity))
        pts = [float(x["state"]) for x in (h[0] if h else []) if x.get("state") not in (None, "", "unknown", "unavailable")]
        return round(pts[-1] - pts[0], 1) if pts else None
    except Exception:   # noqa: BLE001
        return None


def rooms(states):
    out = []
    for r in ROOMS:
        name, temp_e = r[0], r[1] if len(r) > 1 else ""
        hum_e, icon = (r[2] if len(r) > 2 else ""), (r[3] if len(r) > 3 else "")
        st = states.get(temp_e, {}).get("state")
        try:
            t = float(st)
        except (TypeError, ValueError):
            continue
        hum = C.num(states.get(hum_e, {}).get("state"), 0) if hum_e else 0
        d = trend_1h(temp_e)
        out.append({"nom": name, "ic": icon, "t": t, "t_txt": "%.1f" % t, "hum": int(hum), "teinte": hue(t),
                    "etat": "cool" if t < 19 else "fine" if t <= 23.5 else "warm",
                    "tend": "" if d is None or abs(d) < 0.3 else ("↑ " if d > 0 else "↓ ") + "%.1f° in 1 h" % abs(d)})
    return out


def todo_items(states):
    todo = []
    now = datetime.datetime.now(C.TZ)

    # 1) Home Assistant to-do list
    if TODO_ENTITY:
        try:
            rep = C.ha("/api/services/todo/get_items?return_response", {"entity_id": TODO_ENTITY, "status": "needs_action"})
            for it in rep["service_response"][TODO_ENTITY]["items"]:
                due, urg = it.get("due") or "", 0
                if due:
                    try:
                        dd = datetime.date.fromisoformat(due[:10])
                        urg = 2 if dd < now.date() else 1 if dd == now.date() else 0
                    except ValueError:
                        pass
                todo.append({"quoi": it.get("summary", "?"), "detail": it.get("description") or "", "icone": "📅", "type": "autre",
                             "urgence": urg, "bouton": webhook("todo_done", item=it.get("uid") or it.get("summary", "")),
                             "bouton_txt": "Done"})
        except Exception as err:   # noqa: BLE001
            print("to-do list unavailable:", str(err)[:80])

    # 2) robot vacuum not run for N days (no line while it is cleaning or returning)
    last = states.get(VACUUM_LAST, {}).get("state", "") if VACUUM_LAST else ""
    if last not in ("", "unknown", "unavailable"):
        try:
            days = (now.date() - datetime.datetime.fromisoformat(last).astimezone(C.TZ).date()).days
            if days >= VACUUM_DAYS and states.get(VACUUM, {}).get("state") not in ("cleaning", "returning"):
                todo.append({"quoi": "Start the robot vacuum", "detail": "%d days since the last cleaning" % days, "icone": "🤖",
                             "type": "robot", "urgence": 2 if days > VACUUM_DAYS else 1,
                             "bouton": webhook("robot"), "bouton_txt": "Start"})
        except ValueError:
            pass

    # 3) low batteries (rechargeable devices excluded)
    low = [s for s in states.values()
           if s.get("attributes", {}).get("device_class") == "battery"
           and s["state"].replace(".", "").isdigit() and float(s["state"]) < 20
           and not re.search(BATTERY_EXCLUDE, s["entity_id"])]
    if low:
        names = ", ".join(re.sub(r"\s*[Bb]attery( level)?$", "", s["attributes"].get("friendly_name", "?")) for s in low[:2])
        todo.append({"quoi": "Replace a battery: " + names, "detail": "below 20 %", "icone": "🔋", "type": "pile", "urgence": 1})

    # 4) Home Assistant updates
    ups = [s for s in states.values() if s["entity_id"].startswith("update.") and s["state"] == "on"]
    if ups:
        n = len(ups)
        todo.append({"quoi": "%d update%s available" % (n, "s" if n > 1 else ""),
                     "detail": ", ".join(s["attributes"].get("title") or s["attributes"].get("friendly_name", "?") for s in ups[:3]),
                     "icone": "🔄", "type": "maj", "urgence": 0, "lien": HA_PUBLIC + "/config/updates"})

    # 5) Paperless inbox
    if PAPERLESS_URL and PAPERLESS_TOKEN:
        try:
            r = C.http_json(PAPERLESS_URL + "/api/documents/?is_in_inbox=true&page_size=1",
                            {"Authorization": "Token " + PAPERLESS_TOKEN, "Accept": "application/json"}, timeout=20)
            n = int(r.get("count") or 0)
            if n:
                todo.append({"quoi": "File %d document%s" % (n, "s" if n > 1 else ""), "detail": "in Paperless", "icone": "📄",
                             "type": "classer", "urgence": 0, "lien": PAPERLESS_INBOX_LINK or C.env("PAPERLESS_PUBLIC_URL")})
        except Exception as err:   # noqa: BLE001
            print("paperless unavailable:", str(err)[:80])

    # 6) monthly meter reading (meters.py decides when it is due)
    rd = (C.read_json(C.data_path("meters.json")).get("reading") or {})
    if rd.get("due"):
        todo.append({"quoi": "Read the meters", "detail": "then type the readings in the Meters card", "icone": "📟",
                     "type": "autre", "urgence": 2 if now.day > 10 else 1 if now.day > 3 else 0})

    todo.sort(key=lambda x: -x["urgence"])
    return todo


def groceries():
    if not (MEALIE_URL and MEALIE_TOKEN):
        return {}
    h = {"Authorization": "Bearer " + MEALIE_TOKEN}
    try:
        lid = MEALIE_LIST_ID
        if not lid:
            lists = (C.http_json(MEALIE_URL + "/api/households/shopping/lists?perPage=20", h) or {}).get("items") or []
            lid = lists[0]["id"] if lists else ""
        if not lid:
            return {}
        items = (C.http_json(MEALIE_URL + "/api/households/shopping/items?queryFilter=shoppingListId%%3D%s&perPage=500" % lid, h)
                 or {}).get("items") or []
        return {"restants": sum(1 for x in items if not x.get("checked")), "total": len(items)}
    except Exception as err:   # noqa: BLE001
        print("mealie unavailable:", str(err)[:80])
        return C.read_json(OUT).get("courses", {})


def main():
    states = C.ha_states()
    out = {"genere": datetime.datetime.now(C.TZ).isoformat(timespec="minutes"),
           "todo": todo_items(states), "pieces": rooms(states), "courses": groceries()}
    out["nb_todo"] = len(out["todo"])
    if C.PRINT:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    C.write_json(OUT, out)
    C.log("ok", len(out["todo"]), "to do,", len(out["pieces"]), "rooms")


if __name__ == "__main__":
    main()
