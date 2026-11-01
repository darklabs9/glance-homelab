"""Scheduled tasks helpers for nas-state.py.

next_run(fields, after) -> datetime of the next run of a cron line (5 fields), None if nothing within 35 days.
script_of(command)     -> path of the script a cron command runs (python3 X, bash X, or X directly).
description(path)      -> first sentences of the script header (docstring or leading comments), 260 chars max.
"""
import ast, datetime, re

BOUNDS = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
NAMES = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11,
         "dec": 12, "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
          "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}


def _values(field, i):
    lo, hi = BOUNDS[i]
    out = set()
    for part in field.lower().split(","):
        step = 1
        if "/" in part:
            part, p = part.split("/", 1)
            step = int(p)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(NAMES.get(x, x)) for x in part.split("-", 1))
        else:
            a = int(NAMES.get(part, part))
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    if i == 4 and 7 in out:
        out.add(0)
    return out


def next_run(fields, after):
    """Next run strictly after `after` (whole minute)."""
    try:
        mi, h, dom, mon, dow = (_values(c, i) for i, c in enumerate(fields))
    except (ValueError, KeyError):
        return None
    dom_any, dow_any = fields[2] == "*", fields[4] == "*"
    d = after.replace(second=0, microsecond=0) + datetime.timedelta(minutes=1)
    end = d + datetime.timedelta(days=35)
    while d < end:
        if d.month not in mon:
            d = (d.replace(day=1, hour=0, minute=0) + datetime.timedelta(days=32)).replace(day=1)
            continue
        # cron rule: when both day-of-month and day-of-week are restricted, either one matches
        day_ok = (d.day in dom) if dow_any else ((d.isoweekday() % 7) in dow) if dom_any else \
            (d.day in dom or (d.isoweekday() % 7) in dow)
        if not day_ok:
            d = d.replace(hour=0, minute=0) + datetime.timedelta(days=1)
            continue
        if d.hour not in h:
            d = d.replace(minute=0) + datetime.timedelta(hours=1)
            continue
        if d.minute in mi:
            return d
        d += datetime.timedelta(minutes=1)
    return None


def script_of(cmd):
    for m in re.finditer(r"(/[\w./@+-]+\.(?:py|sh|mjs|js))\b", cmd):
        return m.group(1)
    m = re.match(r"^(?:\S+=\S+\s+)*(/\S+)", cmd)
    return m.group(1) if m else None


def _clean(t):
    t = re.sub(r"\s+", " ", t).strip()
    # a leading date ("27/10/2026: ...") does not help to understand what the script does
    t = re.sub(r"^\d{1,4}[/-]\d{1,2}(?:[/-]\d{2,4})?\s*(?:\((?:[^()]|\([^()]*\))*\))?\s*[:.-]\s*", "", t)
    t = t[:1].upper() + t[1:]
    if len(t) > 260:
        t = t[:260].rsplit(" ", 1)[0] + "…"
    return t


def description(path):
    try:
        src = open(path, errors="replace").read(6000)
    except Exception:
        return ""
    if path.endswith(".py"):
        try:
            doc = ast.get_docstring(ast.parse(open(path, errors="replace").read()))
        except Exception:
            doc = None
        if doc:
            return _clean(doc.split("\n\n")[0])
    lines = []
    for l in src.splitlines()[:40]:
        s = l.strip()
        if s.startswith("#!") or s.startswith("# -*-") or s.startswith("# shellcheck"):
            continue
        if s.startswith("#") or s.startswith("//"):
            s = s.lstrip("#/ ").strip()
            if s and not set(s) <= set("-=*_ "):
                lines.append(s)
            elif lines:
                break
        elif lines or s:
            break
    return _clean(" ".join(lines))
