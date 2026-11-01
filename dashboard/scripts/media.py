#!/usr/bin/env python3
"""Media page data -> <DATA_DIR>/media.json (cron every 10 min).

Gathers Jellyfin (continue watching, new in the library), Sonarr/Radarr (this week, later, downloads) and
Jellyseerr/Overseerr (trending, upcoming movies and series, anime, your requests) into one JSON. Posters are saved
under <ASSETS_DIR>/posters so Glance serves them itself (no mixed-content images, no live calls from the page).
The slow parts (trending, upcoming) are only recomputed every 6 h (--all forces it).
IMDb ratings come from ratings.json (ratings.py) when available, TMDB votes otherwise.
"""
import datetime
import hashlib
import os
import re
import sys
import time

import common as C

OUT = C.data_path("media.json")
RATINGS = C.data_path("ratings.json")
TZ = C.TZ
JF = C.env("JELLYFIN_URL", "http://192.168.1.10:8096").rstrip("/")
USER = C.env("JELLYFIN_USER_ID")
SONARR = C.env("SONARR_URL", "http://192.168.1.10:8989").rstrip("/")
RADARR = C.env("RADARR_URL", "http://192.168.1.10:7878").rstrip("/")
SEERR = C.env("SEERR_URL", "http://192.168.1.10:5055").rstrip("/")
JF_WEB = C.env("JELLYFIN_PUBLIC_URL", JF).rstrip("/") + "/web/#/details?id="
SEERR_WEB = C.env("SEERR_PUBLIC_URL", SEERR).rstrip("/")
LANG = C.env("MEDIA_LANGUAGE", "en")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

HJF = {"Authorization": "MediaBrowser Token=" + C.env("JELLYFIN_KEY")}
HSO, HRA, HSE = {"X-Api-Key": C.env("SONARR_KEY")}, {"X-Api-Key": C.env("RADARR_KEY")}, {"X-Api-Key": C.env("SEERR_KEY")}


def get(url, headers=None, raw=False):
    return C.http_json(url, headers or {}, raw=raw, timeout=30)


os.makedirs(C.POSTERS_DIR, exist_ok=True)
used = set()


def poster(url, headers=None):
    """Download the image once and return the path Glance serves."""
    if not url:
        return ""
    name = hashlib.md5(url.encode()).hexdigest()[:16] + ".jpg"
    path = os.path.join(C.POSTERS_DIR, name)
    if not os.path.exists(path):
        try:
            data = get(url, headers, raw=True)
            if len(data) < 500:
                return ""
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.chmod(path + ".tmp", 0o644)
            os.replace(path + ".tmp", path)
        except Exception as e:   # noqa: BLE001
            print("poster", url[:80], e, file=sys.stderr)
            return ""
    used.add(name)
    return "/assets/posters/" + name


def jf_poster(item):
    # series poster for an episode, else the item's own
    if item.get("SeriesId") and item.get("SeriesPrimaryImageTag"):
        i, tag = item["SeriesId"], item["SeriesPrimaryImageTag"]
    else:
        i, tag = item["Id"], (item.get("ImageTags") or {}).get("Primary", "")
    if not tag:
        return ""
    return poster(f"{JF}/Items/{i}/Images/Primary?fillHeight=450&fillWidth=300&quality=85&tag={tag}", HJF)


def jf_backdrop(item):
    if item.get("BackdropImageTags"):
        i, tag = item["Id"], item["BackdropImageTags"][0]
    elif item.get("ParentBackdropImageTags"):
        i, tag = item["ParentBackdropItemId"], item["ParentBackdropImageTags"][0]
    else:
        return ""
    return poster(f"{JF}/Items/{i}/Images/Backdrop?maxWidth=1280&quality=80&tag={tag}", HJF)


def tmdb(path, size="w342"):
    return poster(f"https://image.tmdb.org/t/p/{size}{path}") if path else ""


def arr_poster(obj):
    img = next((i.get("remoteUrl") for i in (obj or {}).get("images", []) if i.get("coverType") == "poster"), "")
    return poster(img.replace("/original/", "/w342/")) if img else ""


def when(d):
    """'today', 'yesterday', 'Mon 29 Sep'"""
    j = (datetime.datetime.now(TZ).date() - d).days
    if j == 0:
        return "today"
    if j == 1:
        return "yesterday"
    if j == -1:
        return "tomorrow"
    return f"{DAYS[d.weekday()]} {d.day} {MONTHS[d.month - 1]}"


def ep_code(i):
    return "S%02dE%02d" % (i.get("ParentIndexNumber") or 0, i.get("IndexNumber") or 0)


old = C.read_json(OUT)
ratings = C.read_json(RATINGS).get("ratings", {})
res = {"maj": datetime.datetime.now(TZ).strftime("%H:%M")}
FIELDS = "Fields=ProviderIds,Overview,DateCreated,Genres"

# ---------- Continue watching: ONE title, the last one played ----------
try:
    resume = get(f"{JF}/Users/{USER}/Items/Resume?Limit=10&MediaTypes=Video&{FIELDS}", HJF)["Items"]
    nextup = get(f"{JF}/Shows/NextUp?UserId={USER}&Limit=10&{FIELDS}", HJF)["Items"]
    # sorted by the series' last play: a started episode must not hide a series watched later (only in NextUp)
    played = get(f"{JF}/Users/{USER}/Items?SortBy=DatePlayed&SortOrder=Descending&Filters=IsPlayed&Recursive=true"
                 f"&IncludeItemTypes=Episode&Limit=100&Fields=UserData", HJF)["Items"]
    last_series = {}
    for e in played:
        d = (e.get("UserData") or {}).get("LastPlayedDate") or ""
        if e.get("SeriesId") and d > last_series.get(e["SeriesId"], ""):
            last_series[e["SeriesId"]] = d

    def last_played(it):
        d = (it.get("UserData") or {}).get("LastPlayedDate") or ""
        return max(d, last_series.get(it.get("SeriesId"), ""))
    items = sorted(resume + nextup, key=last_played, reverse=True)
    if items:
        it = items[0]
        series = it.get("Type") == "Episode"
        rt, pos = it.get("RunTimeTicks") or 0, (it.get("UserData") or {}).get("PlaybackPositionTicks") or 0
        pct = round(pos * 100 / rt) if rt and pos else 0
        try:
            base = get(f"{JF}/Users/{USER}/Items/{it['SeriesId']}", HJF) if it.get("SeriesId") else it
        except Exception:   # noqa: BLE001
            base = it
        lp, seen = last_played(it), ""
        if lp:
            dl = datetime.datetime.fromisoformat(lp[:19] + "+00:00").astimezone(TZ)
            j = (datetime.datetime.now(TZ).date() - dl.date()).days
            t = dl.strftime("%H:%M")
            seen = (("tonight" if dl.hour >= 17 else "today") + " at " + t) if j == 0 else (f"yesterday at {t}" if j == 1 else f"on {dl.day} {MONTHS[dl.month - 1]}")
        note = base.get("CommunityRating")
        res["ce_soir"] = {
            "titre": it.get("SeriesName") if series else it["Name"],
            "pct": pct, "lien": JF_WEB + it["Id"],
            "etat": ("Next episode" if series else "To watch") if not pct else "",
            "fond": jf_backdrop(it), "poster": jf_poster(it), "joue": seen,
            "ep": f"S{it.get('ParentIndexNumber') or 0}.E{it.get('IndexNumber') or 0} · {it['Name']}" if series else "",
            "note": f"{note:.1f}" if note else "",
            "annee": str(base.get("ProductionYear") or ""),
            "genres": " · ".join((base.get("Genres") or [])[:2]),
            "reste": round((rt - pos) / 600000000) if rt else 0,
            "duree": round(rt / 600000000) if rt else 0}
    else:
        res["ce_soir"] = {}
except Exception as e:   # noqa: BLE001
    print("continue", e, file=sys.stderr)
    res["ce_soir"] = old.get("ce_soir", {})

# ---------- New in the library (last 10 days) ----------
try:
    limit = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=10)
    # Jellyfin 10.10+ only returns top-level series without ParentId: ask each movies/tv library separately
    new = []
    for view in get(f"{JF}/Users/{USER}/Views", HJF).get("Items", []):
        if view.get("CollectionType") in ("movies", "tvshows"):
            new += get(f"{JF}/Users/{USER}/Items/Latest?ParentId={view['Id']}&Limit=30&GroupItems=true&{FIELDS}", HJF)
    new.sort(key=lambda it: it.get("DateCreated", ""), reverse=True)
    lst = []
    for it in new:
        d = datetime.datetime.fromisoformat(it.get("DateCreated", "1970-01-01T00:00:00Z")[:19] + "+00:00")
        if d < limit:
            continue
        t = it.get("Type")
        if t == "Episode":
            badge, title, sub = "EPISODE", it.get("SeriesName"), ep_code(it)
        elif t == "Season":
            badge, title, sub = "SEASON", it.get("SeriesName"), it["Name"]
        elif t == "Series":
            badge, title, sub = "SERIES", it["Name"], (f"{it.get('ChildCount')} season(s)" if it.get("ChildCount") else "")
        elif t == "Movie":
            badge, title, sub = "MOVIE", it["Name"], str(it.get("ProductionYear") or "")
        else:
            continue
        lst.append({"titre": title, "sous": sub, "badge": badge, "quand": when(d.astimezone(TZ).date()),
                    "poster": jf_poster(it), "lien": JF_WEB + it["Id"], "vu": (it.get("UserData") or {}).get("Played", False)})
    res["nouveautes"] = lst[:14]
except Exception as e:   # noqa: BLE001
    print("new", e, file=sys.stderr)
    res["nouveautes"] = old.get("nouveautes", [])

# ---------- This week: episodes (Sonarr) + expected movies (Radarr), then "later" ----------
try:
    today = datetime.datetime.now(TZ).date()
    cal = get(f"{SONARR}/api/v3/calendar?start={today - datetime.timedelta(days=1)}&end={today + datetime.timedelta(days=8)}&includeSeries=true", HSO)
    days = {}
    for e in cal:
        d = datetime.datetime.fromisoformat(e["airDateUtc"].replace("Z", "+00:00")).astimezone(TZ).date()
        if d < today:
            continue
        s = e["series"]
        k = (d, s["title"])
        if k not in days:
            days[k] = {"titre": s["title"], "eps": [], "poster": arr_poster(s), "dispo": True}
        days[k]["eps"].append(e["episodeNumber"])
        days[k]["saison"] = e["seasonNumber"]
        days[k]["dispo"] = days[k]["dispo"] and e.get("hasFile", False)
    week = {}
    for (d, _), v in sorted(days.items()):
        eps = v.pop("eps")
        v["ep"] = "S%02dE%02d" % (v.pop("saison"), eps[0]) + (f"-{eps[-1]:02d}" if len(eps) > 1 else "")
        week.setdefault(d, []).append(v)
    res["semaine"] = [{"jour": when(d).capitalize(), "items": it, "_d": str(d)} for d, it in sorted(week.items())][:7]

    # next episode of each monitored series, 9 to 60 days ahead
    later = get(f"{SONARR}/api/v3/calendar?start={today + datetime.timedelta(days=8)}&end={today + datetime.timedelta(days=60)}&includeSeries=true", HSO)
    seen_s, series_later = set(), []
    for e in sorted(later, key=lambda e: e["airDateUtc"]):
        s = e["series"]
        if s["title"] in seen_s or not e.get("monitored", True):
            continue
        seen_s.add(s["title"])
        d = datetime.datetime.fromisoformat(e["airDateUtc"].replace("Z", "+00:00")).astimezone(TZ).date()
        series_later.append({"_d": str(d), "titre": s["title"], "quand": when(d), "poster": arr_poster(s),
                             "ep": ("New season · " if e["episodeNumber"] == 1 else "") + "S%02dE%02d" % (e["seasonNumber"], e["episodeNumber"])})

    movies = get(f"{RADARR}/api/v3/calendar?start={today}&end={today + datetime.timedelta(days=150)}", HRA)
    expected = []
    for f in movies:
        if f.get("hasFile") or not f.get("monitored", True):
            continue
        dr = f.get("digitalRelease") or f.get("physicalRelease")
        if not dr:
            continue
        d = datetime.datetime.fromisoformat(dr.replace("Z", "+00:00")).astimezone(TZ).date()
        if d >= today:
            expected.append({"titre": f["title"], "quand": when(d), "_d": str(d), "poster": arr_poster(f)})
    expected.sort(key=lambda x: x["_d"])
    # movies out within 7 days also go into "This week", mixed by day
    for a in expected:
        d = datetime.date.fromisoformat(a["_d"])
        if (d - today).days <= 7:
            day = when(d).capitalize()
            block = next((j for j in res["semaine"] if j["jour"] == day), None)
            item = {"titre": a["titre"], "poster": a["poster"], "dispo": False, "ep": "digital release", "film": True}
            if block:
                block["items"].append(item)
            else:
                res["semaine"].append({"jour": day, "items": [item], "_d": a["_d"]})
    res["semaine"].sort(key=lambda j: j.get("_d") or "")
    for j in res["semaine"]:
        j.pop("_d", None)
    # "Later": one list sorted by date, without the movies already in "This week"
    tard = [dict(a, ep="movie · digital release") for a in expected if (datetime.date.fromisoformat(a["_d"]) - today).days > 7]
    tard = sorted(tard + [dict(x) for x in series_later], key=lambda x: x["_d"])[:10]
    for x in tard:
        x.pop("_d", None)
    res["plus_tard"] = tard
except Exception as e:   # noqa: BLE001
    print("week", e, file=sys.stderr)
    res["semaine"], res["plus_tard"] = old.get("semaine", []), old.get("plus_tard", [])

# ---------- Downloads in progress (only used to show the state of your requests) ----------
downloads = []
try:
    for base, h, field in ((SONARR, HSO, "series"), (RADARR, HRA, "movie")):
        q = get(f"{base}/api/v3/queue?pageSize=50&include{'Series' if field == 'series' else 'Movie'}=true&includeEpisode=true", h)
        groups = {}
        for r in q.get("records", []):
            name = (r.get(field) or {}).get("title") or r.get("title", "?")
            g = groups.setdefault(name, {"titre": name, "size": 0, "left": 0})
            g["size"] += r.get("size") or 0
            g["left"] += r.get("sizeleft") or 0
        for g in groups.values():
            downloads.append({"titre": g["titre"], "pct": round((g["size"] - g["left"]) * 100 / g["size"]) if g["size"] else 0})
except Exception as e:   # noqa: BLE001
    print("downloads", e, file=sys.stderr)


# ---------- Slow parts: every 6 h ----------
SLOW = ("tendances", "series_a_venir", "films_a_venir", "animes")
slow_ok = old.get("_lent", 0) > time.time() - 6 * 3600 and "--all" not in sys.argv


def unreadable(t):
    """Title with no Latin letter but with letters (Korean, Japanese, Thai...). "1917" stays readable."""
    t = t or ""
    return not re.search(r"[A-Za-zÀ-ÿ]", t) and bool(re.search(r"[^\W\d_]", t))


def readable_title(x, kind):
    """Title in MEDIA_LANGUAGE; when it stayed in the original language, the English title."""
    t = x.get("title") or x.get("name") or ""
    if unreadable(t) and kind in ("movie", "tv") and x.get("id"):
        try:
            en = get(f"{SEERR}/api/v1/{kind}/{x['id']}?language=en", HSE)
            t2 = en.get("title") or en.get("name") or ""
            if t2 and not unreadable(t2):
                return t2
        except Exception as e:   # noqa: BLE001
            print("english title", x.get("id"), e, file=sys.stderr)
    return t


def card(x, kind=None):
    kind = kind or x.get("mediaType")
    d = x.get("releaseDate") or x.get("firstAirDate") or ""
    st = (x.get("mediaInfo") or {}).get("status") or 0
    n = ratings.get(f"{kind}:{x.get('id')}", {})
    return {"titre": readable_title(x, kind), "poster": tmdb(x.get("posterPath")), "lien": f"{SEERR_WEB}/{kind}/{x.get('id')}",
            "kind": kind, "tmdb": x.get("id") or 0,
            "date": (f"{d[8:10]}/{d[5:7]}/{d[2:4]}" if len(d) >= 10 else ""), "annee": d[:4],
            "note": n.get("imdb") or (f"{x.get('voteAverage'):.1f}" if x.get("voteAverage") else ""),
            "source": "IMDb" if n.get("imdb") else "TMDB",
            "statut": "library" if st >= 4 else ("requested" if st >= 2 else "")}


if slow_ok:
    for k in SLOW:
        res[k] = old.get(k, [])
        for c in res[k]:
            if c.get("poster"):
                used.add(os.path.basename(c["poster"]))
    res["_lent"] = old["_lent"]
else:
    for k, url in (("tendances", f"/api/v1/discover/trending?language={LANG}"),
                   ("series_a_venir", f"/api/v1/discover/tv/upcoming?language={LANG}"),
                   ("films_a_venir", f"/api/v1/discover/movies/upcoming?language={LANG}"),
                   ("animes", "/api/v1/discover/tv?genre=16&language=ja&sortBy=popularity.desc&firstAirDateGte="
                              + str(datetime.date.today() - datetime.timedelta(days=365)))):
        try:
            r = get(SEERR + url, HSE)["results"]
            kind = "tv" if ("tv/" in url or "/tv?" in url) else ("movie" if "movies" in url else None)
            if k == "animes":   # no adult content, no title left in Japanese
                r = [x for x in r if not x.get("adult") and re.search(r"[A-Za-z]", x.get("name") or "")]
            res[k] = [card(x, kind) for x in r if x.get("posterPath") and x.get("mediaType") != "person"][:14]
        except Exception as e:   # noqa: BLE001
            print(k, e, file=sys.stderr)
            res[k] = old.get(k, [])
    for k in SLOW:
        res[k] = [c for c in res.get(k, []) if not unreadable(c.get("titre"))]
    res["_lent"] = time.time()

# Seerr's state is only re-read every 6 h: on every run, align it with Radarr (file present) and Sonarr
movies_by_tmdb, series_by_tmdb = {}, {}
try:
    movies_by_tmdb = {m.get("tmdbId"): m for m in get(f"{RADARR}/api/v3/movie", HRA)}
    series_by_tmdb = {x.get("tmdbId"): x for x in get(f"{SONARR}/api/v3/series", HSO)}
    for k in SLOW:
        for c in res.get(k, []):
            m = (movies_by_tmdb if c.get("kind") == "movie" else series_by_tmdb).get(c.get("tmdb"))
            if not m:
                continue
            has = m.get("hasFile") if c.get("kind") == "movie" else (m.get("statistics") or {}).get("episodeFileCount", 0) > 0
            c["statut"] = "library" if has else (c.get("statut") or "requested")
except Exception as e:   # noqa: BLE001
    print("status", e, file=sys.stderr)

# ---------- Your requests: asked in Seerr, not arrived yet, in the order they should arrive ----------
try:
    today = datetime.datetime.now(TZ).date()
    dls = {d["titre"].lower(): d for d in downloads}
    reqs = get(f"{SEERR}/api/v1/request?take=100&skip=0&filter=all&sort=added", HSE).get("results", [])
    seen_l, lst = set(), []
    for r in reqs:
        m = r.get("media") or {}
        kind, tm = m.get("mediaType") or r.get("type"), m.get("tmdbId")
        if not tm or (kind, tm) in seen_l or r.get("status") == 3 or (m.get("status") or 0) >= 4:   # declined / available
            continue
        seen_l.add((kind, tm))
        try:
            x = get(f"{SEERR}/api/v1/{kind}/{tm}?language={LANG}", HSE)
        except Exception:   # noqa: BLE001
            continue
        title = x.get("title") or x.get("name") or "?"
        arr = (movies_by_tmdb if kind == "movie" else series_by_tmdb).get(tm) or {}
        if kind == "movie":
            out = arr.get("digitalRelease") or arr.get("physicalRelease") or x.get("releaseDate") or ""
        else:
            out = arr.get("nextAiring") or x.get("firstAirDate") or ""
        d = datetime.date.fromisoformat(out[:10]) if len(out) >= 10 else None
        dl = dls.get((arr.get("title") or title).lower())
        if dl:
            state, cls, order = f"downloading · {dl['pct']} %", "dl", 0
        elif d and d > today:
            state, cls, order = f"out on {d.day} {MONTHS[d.month - 1]}", "sortie", 2
        else:
            state, cls, order = "waiting", "attente", 1
        n = x.get("voteAverage")
        lst.append({"titre": title, "poster": tmdb(x.get("posterPath")), "lien": f"{SEERR_WEB}/{kind}/{tm}",
                    "kind": kind, "annee": (x.get("releaseDate") or x.get("firstAirDate") or "")[:4],
                    "note": f"{n:.1f}" if n else "", "etat": state, "cls": cls, "pct": dl["pct"] if dl else 0,
                    "_tri": (order, str(d or "9999"))})
    lst.sort(key=lambda z: z.pop("_tri"))
    res["liste"] = lst[:20]
except Exception as e:   # noqa: BLE001
    print("requests", e, file=sys.stderr)
    res["liste"] = old.get("liste", [])

C.write_json(OUT, res)

# Cleanup: posters unused for more than 30 days (downloads.py and media_news.py touch the ones they use)
for f in os.listdir(C.POSTERS_DIR):
    p = os.path.join(C.POSTERS_DIR, f)
    if f in used:
        os.utime(p)
    elif os.path.getmtime(p) < time.time() - 30 * 86400:
        os.remove(p)
C.log("ok", {k: len(v) for k, v in res.items() if isinstance(v, list)})
