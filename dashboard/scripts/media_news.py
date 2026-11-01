#!/usr/bin/env python3
"""Series news for the Media page -> <DATA_DIR>/media-news.json (cron every 30 min).

  actus.items  recent news (4 days) that mention one of YOUR series (titles from Sonarr), with summary and photo
  actus.une    the latest general TV news
  actus.tes_series  true when "items" is not empty (the widget then shows a "Your series" tab first)
Feeds: NEWS_FEEDS ("Name|url;Name|url"), English TV news sites by default. Photos are cached small in
<ASSETS_DIR>/posters. --print: print, write nothing.
"""
import datetime
import email.utils
import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import common as C

OUT = C.data_path("media-news.json")
SONARR = C.env("SONARR_URL", "http://192.168.1.10:8989").rstrip("/")
TZ = C.TZ
UA = {"User-Agent": "Mozilla/5.0 (glance-homelab-dashboard)"}
FEEDS = [f.split("|", 1) for f in C.env_list("NEWS_FEEDS", "Screen Rant|https://screenrant.com/feed/tv/;TVLine|https://tvline.com/feed/;"
                                             "Rotten Tomatoes|https://editorial.rottentomatoes.com/feed/;Decider|https://decider.com/feed/;"
                                             "CBR|https://www.cbr.com/feed/category/tv/", sep=";")]
NS = {"media": "http://search.yahoo.com/mrss/", "content": "http://purl.org/rss/1.0/modules/content/"}
now = datetime.datetime.now(TZ)


def text(h):
    t = html.unescape(re.sub(r"<[^>]+>", " ", h or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= 190 else t[:190].rsplit(" ", 1)[0] + "…"


def photo(it):
    url = ""
    enc = it.find("enclosure")
    if enc is not None and (enc.get("type") or "").startswith("image"):
        url = enc.get("url") or ""
    for tag in ("media:content", "media:thumbnail"):
        e = it.find(tag, NS)
        if not url and e is not None:
            url = e.get("url") or ""
    if not url:
        m = re.search(r'<img[^>]+src="([^"]+)"', (it.findtext("content:encoded", namespaces=NS) or "") + (it.findtext("description") or ""))
        url = m.group(1) if m else ""
    return html.unescape(urllib.parse.unquote(url))


def image(url):
    """Download the news photo small (240 px) once; Glance serves it from /assets/posters."""
    if not url:
        return ""
    base = url.split("?")[0]
    if "srcdn.com" in url or "cbrimages.com" in url:
        url = base + "?q=60&fit=crop&w=240&h=300&dpr=1"
    elif "/wp-content/" in url:
        url = base + "?w=240"
    name = "news-" + hashlib.md5(base.encode()).hexdigest()[:16] + ".jpg"
    path = os.path.join(C.POSTERS_DIR, name)
    if not os.path.exists(path):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                data = r.read()
            if len(data) < 500:
                return ""
            os.makedirs(C.POSTERS_DIR, exist_ok=True)
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.chmod(path + ".tmp", 0o644)
            os.replace(path + ".tmp", path)
        except Exception as e:   # noqa: BLE001
            print("photo", url[:70], str(e)[:60])
            return ""
    else:
        os.utime(path)
    return "/assets/posters/" + name


def read_feeds():
    items = []
    for src, url in FEEDS:
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
                root = ET.fromstring(r.read())
        except Exception as e:   # noqa: BLE001
            print(src, "unreadable:", str(e)[:60])
            continue
        for it in root.iter("item"):
            t, link, d = it.findtext("title") or "", it.findtext("link") or "", it.findtext("pubDate") or ""
            try:
                dt = email.utils.parsedate_to_datetime(d).astimezone(TZ)
            except Exception:   # noqa: BLE001
                dt = None
            if t and link and dt:
                title = re.sub(r"\s+", " ", t).strip()
                summary = text(it.findtext("description") or "")
                items.append({"titre": title, "lien": link, "source": src, "dt": dt, "photo": photo(it),
                              "resume": "" if summary.lower().startswith(title.lower()[:40]) else summary})
    items.sort(key=lambda x: x["dt"], reverse=True)
    return items


def ago(dt):
    m = int((now - dt).total_seconds() // 60)
    if m < 60:
        return "%d min ago" % max(m, 1)
    if m < 24 * 60:
        return "%d h ago" % (m // 60)
    return "yesterday" if m < 48 * 60 else "%d days ago" % (m // 1440)


def news():
    try:
        series = C.http_json(SONARR + "/api/v3/series", {"X-Api-Key": C.env("SONARR_KEY")}, timeout=30)
    except Exception as e:   # noqa: BLE001
        print("Sonarr unreadable:", str(e)[:60])
        series = []
    names = {}
    for s in series:
        t = re.sub(r"\s*\(\d{4}\)$", "", s.get("title", "")).strip()
        if len(t) >= 4:
            names[t] = re.compile(r"(?<![\w])%s(?![\w])" % re.escape(t), re.I)
    allnews = read_feeds()
    recent = [x for x in allnews if now - x["dt"] < datetime.timedelta(days=4)]
    mine, seen = [], set()
    for x in recent:
        for t, rx in names.items():
            if rx.search(x["titre"]) and x["lien"] not in seen:
                seen.add(x["lien"])
                mine.append({"tag": t if len(t) <= 16 else t[:15] + "…", "titre": x["titre"], "lien": x["lien"],
                             "quand": ago(x["dt"]).capitalize(), "source": x["source"], "resume": x["resume"], "img": image(x["photo"])})
                break
        if len(mine) >= 4:
            break
    top = [{"titre": x["titre"], "lien": x["lien"], "quand": ago(x["dt"]).capitalize(), "source": x["source"],
            "resume": x["resume"], "img": image(x["photo"])} for x in allnews[:8]]
    return {"tes_series": bool(mine), "items": mine, "une": top}


out = {"maj": now.strftime("%d/%m %H:%M")}
try:
    out["actus"] = news()
except Exception as e:   # noqa: BLE001
    print("news failed:", str(e)[:120])
    out["actus"] = C.read_json(OUT).get("actus")
if C.PRINT:
    print(json.dumps(out, ensure_ascii=False, indent=1))
else:
    C.write_json(OUT, out)
    C.log("ok", len((out.get("actus") or {}).get("items", [])), "news about your series")
