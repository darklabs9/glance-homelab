#!/usr/bin/env python3
"""IMDb / Rotten Tomatoes / Metacritic ratings (OMDb) for the titles shown on the Media page -> <DATA_DIR>/ratings.json

Titles come from Jellyseerr/Overseerr (trending, upcoming series, upcoming movies). Output:
  {"ratings": {"tv:<tmdb>": {...}, "movie:<tmdb>": {...}, "imdb:<tt>": {...}}, "echecs": {...}}
Each IMDb id is asked again at most once a week; ids OMDb refuses are skipped for 7 days ("Incorrect IMDb ID": 1 year).
A free OMDb key allows 1000 requests a day: run it every 6 hours.
"""
import datetime
import sys
import time

import common as C

OUT = C.data_path("ratings.json")
OMDB = C.env("OMDB_KEY")
SEERR = C.env("SEERR_URL", "http://192.168.1.10:5055").rstrip("/")
HSE = {"X-Api-Key": C.env("SEERR_KEY")}
LANG = C.env("MEDIA_LANGUAGE", "en")
if not OMDB:
    print("no OMDB_KEY", file=sys.stderr)
    sys.exit(1)


def seerr(path):
    return C.http_json(SEERR + "/api/v1/" + path, HSE, timeout=30)


_o = C.read_json(OUT)
old = _o.get("ratings", {})
failed = {k: v for k, v in (_o.get("echecs") or {}).items() if v > time.time() - 7 * 86400}
ratings = {}


def omdb(imdb):
    if not imdb or imdb in failed:
        return None
    if "imdb:" + imdb in old and old["imdb:" + imdb].get("_t", 0) > time.time() - 7 * 86400:
        return old["imdb:" + imdb]
    try:
        d = C.http_json("https://www.omdbapi.com/?i=%s&apikey=%s" % (imdb, OMDB), timeout=30)
    except Exception as e:   # noqa: BLE001
        print("omdb error", imdb, e, file=sys.stderr)
        return None
    if d.get("Response") != "True":
        # "Incorrect IMDb ID" is final -> skipped for a year (timestamp in the future); other errors 7 days
        if "incorrect imdb id" in (d.get("Error") or "").lower():
            failed[imdb] = time.time() + 358 * 86400
        else:
            failed[imdb] = time.time()
        print("omdb:", imdb, d.get("Error"), file=sys.stderr)
        return None
    rt = next((r["Value"] for r in d.get("Ratings", []) if r.get("Source") == "Rotten Tomatoes"), None)
    mc, ir = d.get("Metascore"), d.get("imdbRating")
    return {"imdb": ir if ir not in (None, "N/A") else None, "votes": d.get("imdbVotes"), "rt": rt,
            "mc": mc if mc not in (None, "N/A") else None, "id": imdb, "_t": time.time()}


titles = []
for path in ("discover/trending", "discover/tv/upcoming", "discover/movies/upcoming"):
    try:
        for r in seerr(path + "?language=" + LANG).get("results", [])[:14]:
            k = r.get("mediaType") or ("movie" if "title" in r else "tv")
            if k in ("tv", "movie"):
                titles.append((k, r["id"]))
    except Exception as e:   # noqa: BLE001
        print("seerr error", path, e, file=sys.stderr)

n_ok = 0
for kind, tid in dict.fromkeys(titles):
    key = "%s:%d" % (kind, tid)
    try:
        d = seerr("%s/%d" % (kind, tid))
        imdb = d.get("imdbId") if kind == "movie" else (d.get("externalIds") or {}).get("imdbId")
    except Exception as e:   # noqa: BLE001
        print("detail error", key, e, file=sys.stderr)
        continue
    r = omdb(imdb)
    if r:
        ratings[key] = r
        ratings["imdb:" + r["id"]] = r
        n_ok += 1

C.write_json(OUT, {"generated": datetime.datetime.now().isoformat(timespec="minutes"), "count": n_ok,
                   "ratings": ratings, "echecs": failed})
C.log("ok", n_ok, "titles rated")
