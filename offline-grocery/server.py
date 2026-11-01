#!/usr/bin/env python3
"""Offline grocery list for Mealie.

A small server that:
  - serves the app (static/) under BASE_PATH (default /grocery/): installable on a phone, works without network
    thanks to the service worker;
  - exposes a simplified Mealie API (BASE_PATH/api/...) while the Mealie token stays on the server;
  - polls the Mealie shopping list every POLL_INTERVAL seconds and sends a Web Push to subscribed phones when it
    changes: the phone's service worker then refreshes the list in the background, even with the app closed.
There is no authentication of its own: keep it on your LAN / VPN, or put it behind your reverse proxy's auth.
"""
import base64, hashlib, json, os, threading, time, unicodedata, urllib.error, urllib.parse, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

MEALIE = os.environ.get("MEALIE_URL", "http://mealie:9000").rstrip("/")
TOKEN = os.environ["MEALIE_TOKEN"].strip()
LIST_ID = os.environ["MEALIE_LIST_ID"].strip()
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:admin@example.com")
PORT = int(os.environ.get("PORT", "8080"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "120"))
DATA = os.environ.get("DATA_DIR", "/data")
CATALOG = os.environ.get("CATALOG", "/config/catalog.json")
PREFIX = "/" + os.environ.get("BASE_PATH", "/grocery").strip("/")
PREFIX = "" if PREFIX == "/" else PREFIX
ADDED_NOTE = os.environ.get("ADDED_NOTE", "added from phone")
OTHER_AISLE = os.environ.get("OTHER_AISLE", "Other")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SUBSCRIBERS = os.path.join(DATA, "subscribers.json")
STATE = os.path.join(DATA, "state.json")
VAPID_PEM = os.path.join(DATA, "vapid_private.pem")
SEP = "  ·"   # item notes look like "Name  ·  detail": the part before the separator is the item name
lock = threading.Lock()

DEFAULT_ICONS = {"Produce": "🥕", "Fruit and vegetables": "🥕", "Bakery": "🥖", "Meat": "🥩", "Butcher": "🥩",
                 "Dairy": "🧀", "Pantry": "🥫", "Grocery": "🥫", "Frozen": "🧊", "Drinks": "🧃", "Beverages": "🧃",
                 "Household": "🧴", "Cleaning": "🧴"}
try:
    AISLE_ICONS = json.loads(os.environ.get("AISLE_ICONS") or "null") or DEFAULT_ICONS
except ValueError:
    AISLE_ICONS = DEFAULT_ICONS


def log(*a):
    print(time.strftime("%d/%m %H:%M:%S"), *a, flush=True)


# ---------- Mealie ----------
def api(method, path, data=None):
    r = urllib.request.Request(MEALIE + path, method=method,
                               data=json.dumps(data).encode() if data is not None else None,
                               headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    b = urllib.request.urlopen(r, timeout=30).read()
    return json.loads(b) if b else None


def simple(t):
    """Lowercase, accents stripped: used to compare item names."""
    t = unicodedata.normalize("NFD", (t or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn").replace("œ", "oe").strip()


def simplified_list():
    """The Mealie list reduced to what the phone shows, sorted by the store's aisle order (label order in Mealie)."""
    d = api("GET", "/api/households/shopping/lists/" + LIST_ID)
    order = {ls["label"]["id"]: (ls["position"], ls["label"]["name"]) for ls in d.get("labelSettings", [])}
    items = []
    for i in d["listItems"]:
        text = (i.get("food") or {}).get("name") or i.get("note") or ""
        parts = [m.strip() for m in text.split(SEP) if m.strip()]
        name = parts[0] if parts else "?"
        detail = " · ".join(parts[1:])
        if (i.get("food") or {}).get("name") and i.get("note"):
            detail = i["note"]
        pos, aisle = order.get(i.get("labelId"), (999, (i.get("label") or {}).get("name") or OTHER_AISLE))
        q = i.get("quantity") or 0
        items.append({"id": i["id"], "name": name, "detail": detail, "qty": q if q and q != 1 else None,
                      "unit": (i.get("unit") or {}).get("name"), "checked": bool(i.get("checked")),
                      "aisle": aisle, "aislePos": pos, "pos": i.get("position") or 0,
                      "color": (i.get("label") or {}).get("color") or "#9aa5a0"})
    items.sort(key=lambda x: (x["aislePos"], x["aisle"], simple(x["name"])))
    h = hashlib.sha1(json.dumps([(x["id"], x["name"], x["detail"], x["checked"], x["aisle"], x["qty"]) for x in items],
                                ensure_ascii=False).encode()).hexdigest()[:12]
    return {"name": d["name"], "items": items, "version": h, "at": int(time.time()),
            "remaining": sum(1 for x in items if not x["checked"]), "icons": AISLE_ICONS}


def set_checked(item_id, checked):
    i = api("GET", "/api/households/shopping/items/" + item_id)
    if i is None:
        return False
    body = {k: i.get(k) for k in ("id", "shoppingListId", "note", "quantity", "position", "isFood", "foodId", "labelId", "unitId", "extras")}
    body["checked"] = bool(checked)
    body["recipeReferences"] = i.get("recipeReferences") or []
    api("PUT", "/api/households/shopping/items/" + item_id, body)
    return True


def catalog():
    """Optional list of known items ({"items": [{"name", "aisle", "keywords"}]}) for suggestions and aisle guessing."""
    if not os.path.isfile(CATALOG):
        return []
    try:
        return json.load(open(CATALOG, encoding="utf-8"))["items"]
    except Exception as e:
        log("catalog unreadable:", e)
        return []


def add_item(text):
    text = (text or "").strip()[:80]
    if not text:
        return "nothing to add"
    known = catalog()
    s = simple(text)
    hit = next((a for a in known if simple(a["name"]) == s), None)
    if hit is None:
        hit = next((a for a in known if s and s in simple(a["name"])), None) or \
              next((a for a in known if s and s in simple(a.get("keywords", "")).split()), None)
    name = hit["name"] if hit else text[:1].upper() + text[1:]
    label = None
    if hit and hit.get("aisle"):
        labels = {l["name"]: l["id"] for l in api("GET", "/api/groups/labels?perPage=100")["items"]}
        label = labels.get(hit["aisle"])
    current = api("GET", "/api/households/shopping/lists/" + LIST_ID)
    for i in current["listItems"]:
        if simple((i.get("note") or "").split(SEP)[0]) == simple(name):
            if i.get("checked"):      # already there but checked: uncheck it instead of adding a duplicate
                set_checked(i["id"], False)
                return "back on the list: " + name
            return "already on the list: " + name
    item = {"shoppingListId": LIST_ID, "note": name + (SEP + "  " + ADDED_NOTE if ADDED_NOTE else ""),
            "quantity": 1, "checked": False, "isFood": False}
    if label:
        item["labelId"] = label
    api("POST", "/api/households/shopping/items", item)
    return "added: " + name


# ---------- Web Push ----------
def load_json(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def save_json(path, d):
    tmp = path + ".tmp"
    json.dump(d, open(tmp, "w"), ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def vapid():
    """VAPID key pair, generated on first start and kept in DATA_DIR."""
    if not os.path.exists(VAPID_PEM):
        v = Vapid()
        v.generate_keys()
        v.save_key(VAPID_PEM)
        os.chmod(VAPID_PEM, 0o600)
        log("VAPID keys generated")
    return Vapid.from_file(VAPID_PEM)


def public_key():
    raw = vapid().public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def subscribe(sub):
    with lock:
        subs = load_json(SUBSCRIBERS, [])
        subs = [a for a in subs if a.get("endpoint") != sub.get("endpoint")]
        sub["since"] = int(time.time())
        subs.append(sub)
        save_json(SUBSCRIBERS, subs)
    log("subscribed:", sub.get("endpoint", "")[:60], "->", len(subs), "device(s)")


def push(message):
    with lock:
        subs = load_json(SUBSCRIBERS, [])
    dead = []
    for a in subs:
        try:
            webpush(subscription_info={"endpoint": a["endpoint"], "keys": a["keys"]}, data=json.dumps(message),
                    vapid_private_key=VAPID_PEM, vapid_claims={"sub": VAPID_SUBJECT}, ttl=6 * 3600,
                    headers={"Urgency": "normal"})
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            log("push refused", code, a["endpoint"][:60])
            if code in (404, 410):
                dead.append(a["endpoint"])
        except Exception as e:
            log("push error:", e)
    if dead:
        with lock:
            subs = [a for a in load_json(SUBSCRIBERS, []) if a["endpoint"] not in dead]
            save_json(SUBSCRIBERS, subs)
    return len(subs) - len(dead)


def watch():
    """Every POLL_INTERVAL seconds: if the Mealie list changed, tell the phones."""
    state = load_json(STATE, {})
    while True:
        try:
            l = simplified_list()
            if l["version"] != state.get("version"):
                n = push({"type": "update", "version": l["version"], "remaining": l["remaining"], "name": l["name"]})
                log("list changed (%s, %d left) -> %d device(s) notified" % (l["version"], l["remaining"], n))
                state = {"version": l["version"], "at": int(time.time())}
                save_json(STATE, state)
        except Exception as e:
            log("watch:", e)
        time.sleep(POLL_INTERVAL)


# ---------- HTTP ----------
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
         ".webmanifest": "application/manifest+json", ".png": "image/png", ".svg": "image/svg+xml", ".css": "text/css"}


class Handler(BaseHTTPRequestHandler):
    server_version = "offline-grocery/1"

    def log_message(self, fmt, *a):
        if "/api/" in str(a[0] if a else ""):
            log(self.address_string(), fmt % a)

    def do_HEAD(self):
        self.send_response(200); self.end_headers()

    def reply(self, code, body, ctype="application/json", cache="no-store", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        """Path without BASE_PATH. Requests without the prefix also work (proxies that strip it)."""
        p = self.path.split("?")[0]
        if PREFIX and (p == PREFIX or p.startswith(PREFIX + "/")):
            p = p[len(PREFIX):]
        return p or "/"

    def json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        p = self.route()
        try:
            if PREFIX and self.path.split("?")[0] == PREFIX:
                # the app uses relative URLs: it must be opened with the trailing slash
                return self.reply(301, "", "text/plain", extra={"Location": PREFIX + "/"})
            if p == "/api/list":
                return self.reply(200, simplified_list())
            if p == "/api/catalog":
                return self.reply(200, {"items": [{"name": a["name"], "aisle": a.get("aisle"), "keywords": a.get("keywords", "")}
                                                  for a in catalog()]}, cache="max-age=3600")
            if p == "/api/vapid":
                return self.reply(200, {"key": public_key()})
            if p == "/api/health":
                return self.reply(200, {"ok": True, "subscribers": len(load_json(SUBSCRIBERS, []))})
            if p.startswith("/api/"):
                return self.reply(404, {"error": "unknown"})
            # app files
            if p == "/":
                p = "/index.html"
            path = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
            if not path.startswith(STATIC + os.sep) or not os.path.isfile(path):
                return self.reply(404, "not found", "text/plain")
            ext = os.path.splitext(path)[1]
            cache = "no-cache" if ext in (".html", ".js", ".webmanifest") else "max-age=86400"
            return self.reply(200, open(path, "rb").read(), TYPES.get(ext, "application/octet-stream"), cache)
        except urllib.error.HTTPError as e:
            return self.reply(502, {"error": "Mealie %s" % e.code})
        except Exception as e:
            log("GET", p, "->", e)
            return self.reply(500, {"error": str(e)})

    def cross_site(self):
        """A POST must come from the app itself: JSON only (another page can't send that without a CORS preflight,
        which this server never approves) and, when the browser sends Origin, the same host."""
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            return "Content-Type %r" % self.headers.get("Content-Type")
        o = self.headers.get("Origin")
        if o is not None:
            hosts = {h.strip().lower() for v in (self.headers.get("Host"), self.headers.get("X-Forwarded-Host")) if v
                     for h in v.split(",")}
            if urllib.parse.urlsplit(o).netloc.lower() not in hosts:
                return "Origin %r (hosts %s)" % (o, sorted(hosts))
        return None

    def do_POST(self):
        p = self.route()
        refused = self.cross_site()
        if refused:
            log("POST", p, "refused:", refused)
            return self.reply(403, {"error": "request refused"})
        try:
            d = self.json_body()
            if p == "/api/ops":
                # operations made offline, replayed in order: [{type:"check", id, checked} | {type:"add", text}]
                results = []
                for op in d.get("ops", []):
                    if op.get("type") == "check":
                        results.append({"op": op, "ok": set_checked(op["id"], op.get("checked", True))})
                    elif op.get("type") == "add":
                        results.append({"op": op, "ok": True, "message": add_item(op.get("text"))})
                    else:
                        results.append({"op": op, "ok": False})
                return self.reply(200, {"results": results, "list": simplified_list()})
            if p == "/api/subscribe":
                if not d.get("endpoint") or not d.get("keys"):
                    return self.reply(400, {"error": "incomplete subscription"})
                subscribe({"endpoint": d["endpoint"], "keys": d["keys"], "device": (d.get("device") or "")[:80]})
                return self.reply(200, {"ok": True})
            if p == "/api/test-push":
                n = push({"type": "test", "remaining": simplified_list()["remaining"]})
                return self.reply(200, {"sent": n})
            return self.reply(404, {"error": "unknown"})
        except urllib.error.HTTPError as e:
            log("POST", p, "-> Mealie", e.code, e.read()[:300])
            return self.reply(502, {"error": "Mealie %s" % e.code})
        except BrokenPipeError:
            pass
        except Exception as e:
            log("POST", p, "->", e)
            return self.reply(500, {"error": str(e)})


if __name__ == "__main__":
    os.makedirs(DATA, exist_ok=True)
    vapid()
    threading.Thread(target=watch, daemon=True).start()
    log("offline grocery on :%d%s/, Mealie %s, list %s" % (PORT, PREFIX, MEALIE, LIST_ID[:8]))
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
