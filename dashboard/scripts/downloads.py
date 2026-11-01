#!/usr/bin/env python3
"""Downloads card of the Media page -> <DATA_DIR>/downloads.json (cron every 2 min).

Sonarr + Radarr queues (grouped per movie / per season, with a readable reason when an import is blocked) and what
arrived in the library during the last 2 hours. Each queued item gets a "Delete and search another version" button
(Home Assistant webhook -> Sonarr/Radarr queue/bulk with blocklist, see homeassistant/dashboard_package.yaml).
Posters are cached in <ASSETS_DIR>/posters (same naming as media.py; their date is refreshed on every use so the
cleanup of media.py keeps them).
"""
import datetime
import hashlib
import os
import re
import sys
import urllib.parse

import common as C

OUT = C.data_path("downloads.json")
TZ = C.TZ
SONARR = C.env("SONARR_URL", "http://192.168.1.10:8989").rstrip("/") + "/api/v3"
RADARR = C.env("RADARR_URL", "http://192.168.1.10:7878").rstrip("/") + "/api/v3"
HS, HR = {"X-Api-Key": C.env("SONARR_KEY")}, {"X-Api-Key": C.env("RADARR_KEY")}
WEBHOOK = C.env("MEDIA_WEBHOOK_ID")


def get(url, headers=None, raw=False):
    return C.http_json(url, headers or {}, raw=raw, timeout=30)


def poster(url):
    if not url:
        return ""
    url = url.replace("/original/", "/w342/")
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ".jpg"
    path = os.path.join(C.POSTERS_DIR, name)
    if not os.path.exists(path):
        try:
            data = get(url, raw=True)
            if len(data) < 500:
                return ""
            os.makedirs(C.POSTERS_DIR, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.chmod(path + ".tmp", 0o644)
            os.replace(path + ".tmp", path)
        except Exception as e:   # noqa: BLE001
            print("poster", url[:80], e, file=sys.stderr)
            return ""
    else:
        os.utime(path)
    return "/assets/posters/" + name


def poster_of(obj):
    return poster(next((i.get("remoteUrl") for i in (obj or {}).get("images", []) if i.get("coverType") == "poster"), ""))


def hm(dt):
    return dt.astimezone(TZ).strftime("%H:%M")


def reason(r):
    msgs = " ".join(" ".join(m.get("messages") or []) + " " + (m.get("title") or "") for m in r.get("statusMessages") or [])
    msgs += " " + (r.get("errorMessage") or "")
    m = msgs.lower()
    if re.search(r"executable|dangerous|\.exe|\.lnk|\.scr", m):
        return "booby-trapped file (.exe), refused"
    if "invalid video" in m or "sample" in m:
        return "invalid video file"
    if "no files found" in m:
        return "no video file in the download"
    if "not an upgrade" in m or "custom format upgrade" in m:
        return "not better than the version already there"
    if r.get("status") == "failed" or "failed" in m:
        return "download failed"
    return re.sub(r"\s+", " ", msgs).strip()[:80] or "import impossible"


def ep_txt(eps):
    eps = sorted(set(eps))
    if len(eps) == 1:
        return "episode %d" % eps[0]
    if eps == list(range(eps[0], eps[-1] + 1)):
        return "episodes %d to %d" % (eps[0], eps[-1])
    return "%d episodes" % len(eps)


now = datetime.datetime.now(datetime.timezone.utc)
items, ok = [], True

# ---------- queues ----------
groups = {}
for base, h, movie in ((SONARR, HS, False), (RADARR, HR, True)):
    try:
        q = get(base + ("/queue?pageSize=100&includeMovie=true" if movie else "/queue?pageSize=100&includeSeries=true&includeEpisode=true"), h)
    except Exception as e:   # noqa: BLE001
        print("queue", base, e, file=sys.stderr)
        ok = False
        continue
    for r in q.get("records", []):
        if movie:
            m = r.get("movie") or {}
            key, title, base_sub, obj = ("movie", m.get("id") or r.get("title")), m.get("title") or r.get("title", "?"), str(m.get("year") or ""), m
        else:
            s, e = r.get("series") or {}, r.get("episode") or {}
            sn = e.get("seasonNumber", r.get("seasonNumber"))
            key, title, base_sub, obj = ("series", s.get("id"), sn), s.get("title") or r.get("title", "?"), "season %s" % sn, s
        g = groups.setdefault(key, {"titre": title, "base": base_sub, "obj": obj, "film": movie, "eps": [], "size": 0, "left": 0,
                                    "fin": None, "etats": [], "raisons": [], "ids": [], "dls": []})
        if r.get("id") is not None:
            g["ids"].append(str(r["id"]))
        if not movie and (r.get("episode") or {}).get("episodeNumber") is not None:
            g["eps"].append(r["episode"]["episodeNumber"])
        # a full season is one download, but Sonarr lists every episode with the size of the whole download:
        # count each download only once
        dlid = r.get("downloadId") or "rec%s" % r.get("id")
        if dlid not in g["dls"]:
            g["dls"].append(dlid)
            g["size"] += r.get("size") or 0
            g["left"] += r.get("sizeleft") or 0
        st, tds, tdst = (r.get("status") or "").lower(), (r.get("trackedDownloadStatus") or "").lower(), (r.get("trackedDownloadState") or "").lower()
        if st == "failed" or tds in ("warning", "error") and tdst in ("importpending", "importblocked", "failedpending", "importfailed") or tdst in ("importblocked", "failedpending"):
            g["etats"].append("bloque")
            g["raisons"].append(reason(r))
        elif st == "downloading" and (r.get("sizeleft") or 0) > 0:
            g["etats"].append("cours")
            if r.get("estimatedCompletionTime"):
                end = datetime.datetime.fromisoformat(r["estimatedCompletionTime"].replace("Z", "+00:00"))
                g["fin"] = max(g["fin"], end) if g["fin"] else end
        elif tdst in ("importing", "importpending") or st == "completed":
            g["etats"].append("import")
        else:
            g["etats"].append("attente")
            if st:
                g.setdefault("st", st)

WAIT = {"queued": "in the queue, will start", "paused": "paused", "delay": "waiting for a better version",
        "stalled": "stalled (no sources), retrying", "warning": "slowed down, retrying", "downloadclientunavailable": "download client unreachable"}
for g in groups.values():
    if g["film"]:
        title, sub = g["titre"], g["base"]
    else:
        title = "%s · %s" % (g["titre"], g["base"])
        sub = ep_txt(g["eps"]) if g["eps"] else ""
        if len(set(g["eps"])) == 1:
            title, sub = "%s · %s, %s" % (g["titre"], g["base"], sub), ""
        elif g["eps"]:
            sub = "%d episodes" % len(set(g["eps"]))
    pct = round((g["size"] - g["left"]) * 100 / g["size"]) if g["size"] else 0
    e = g["etats"]
    if "bloque" in e:
        state, detail, pct_txt = "bloque", g["raisons"][0], "!"
    elif "cours" in e:
        state = "cours"
        parts = [sub] if sub else []
        parts.append("%.1f GB left" % (g["left"] / 1e9) if g["left"] >= 1e9 else "%d MB left" % round(g["left"] / 1e6))
        if g["fin"]:
            end = g["fin"]
            same_day = end.astimezone(TZ).date() == now.astimezone(TZ).date()
            parts.append(("done around " if same_day else "done on %s around " % end.astimezone(TZ).strftime("%d/%m")) + hm(end))
        detail, pct_txt = " · ".join(parts), "%d %%" % pct
    elif "import" in e:
        state, detail, pct_txt, pct = "cours", ((sub + " · ") if sub else "") + "downloaded, copying to the library", "100 %", 100
    else:
        state, pct_txt = "attente", "—"
        detail = ((sub + " · ") if sub else "") + WAIT.get(g.get("st", ""), "waiting")
    delete = ""
    if g["ids"] and WEBHOOK:
        delete = "%s/api/webhook/%s?%s" % (C.HA_URL, WEBHOOK, urllib.parse.urlencode(
            {"target": "dl_delete", "app": "radarr" if g["film"] else "sonarr", "ids": ",".join(g["ids"]), "name": title}))
    items.append({"titre": title, "detail": detail, "etat": state, "pct": pct, "pct_txt": pct_txt, "poster": poster_of(g["obj"]), "supprimer": delete})

# ---------- arrived in the library (last 2 hours) ----------
since = (now - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
arrived = {}
for base, h, movie in ((SONARR, HS, False), (RADARR, HR, True)):
    try:
        hist = get(base + ("/history/since?date=%s&eventType=downloadFolderImported" % since)
                   + ("&includeMovie=true" if movie else "&includeSeries=true&includeEpisode=true"), h)
    except Exception as e:   # noqa: BLE001
        print("history", base, e, file=sys.stderr)
        ok = False
        continue
    for x in hist:
        d = datetime.datetime.fromisoformat(x["date"].replace("Z", "+00:00"))
        if movie:
            m = x.get("movie") or {}
            key, title, obj, ep = ("m", m.get("id")), m.get("title", "?"), m, None
        else:
            s, e = x.get("series") or {}, x.get("episode") or {}
            key, title, obj, ep = ("s", s.get("id"), e.get("seasonNumber")), "%s · season %s" % (s.get("title", "?"), e.get("seasonNumber")), s, e.get("episodeNumber")
        a = arrived.setdefault(key, {"titre": title, "obj": obj, "eps": [], "d": d})
        if ep is not None:
            a["eps"].append(ep)
        a["d"] = max(a["d"], d)
for a in arrived.values():
    items.append({"titre": a["titre"] + (", " + ep_txt(a["eps"]) if a["eps"] else ""), "detail": "arrived in the library at " + hm(a["d"]),
                  "etat": "fini", "pct": 100, "pct_txt": "✓", "poster": poster_of(a["obj"])})

order = {"bloque": 0, "cours": 1, "attente": 2, "fini": 3}
items.sort(key=lambda x: order[x["etat"]])
n = len([i for i in items if i["etat"] in ("cours", "attente", "bloque")])
res = {"maj": datetime.datetime.now(TZ).strftime("%H:%M"), "items": items, "nb_actifs": n,
       "entete": ("%d in progress" % n) if n else ("nothing in progress" if not items else "done"), "ok": ok}
C.write_json(OUT, res)
C.log("ok", n, "active,", len(items), "lines")
