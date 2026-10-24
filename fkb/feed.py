"""Farside API fetch, filter classification and kill embeds."""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request

BASE = "https://playfarside.com"
API = BASE + "/api/killmails"
UA = "farside-killboard/0.2 (discord killboard; polite polling)"
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6


def load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def http_json(url, data=None, timeout=20):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, "Accept": "application/json",
                                                          **({"Content-Type": "application/json"} if body else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def fetch_kills(pages=1, limit=30, params=None, fetch=http_json):
    out, cursor = [], None
    for _ in range(pages):
        q = {"limit": str(limit), **(params or {})}
        if cursor:
            q["cursor"] = cursor
        d = fetch(API + "?" + urllib.parse.urlencode(q))
        out += d.get("killmails", [])
        cursor = d.get("nextCursor")
        if not cursor:
            break
    return out


# ---------- formatting ----------
def _num(v):
    try:
        return f"{int(float(v)):,}"
    except (TypeError, ValueError):
        return "?"


def _pilot(p):
    p = p or {}
    name = p.get("callsign") or "Unknown"
    corp = p.get("corporation") or {}
    return f"{name} [{corp['ticker']}]" if corp.get("ticker") else name


def _ship(s):
    s = s or {}
    n = s.get("customName") or s.get("name") or "Unknown ship"
    return f"{n} ({s['shipClass']})" if s.get("shipClass") else n


def _names(p):
    p = p or {}
    c = p.get("corporation") or {}
    return {x.lower() for x in (p.get("callsign"), c.get("name"), c.get("ticker"), p.get("pilotId"), c.get("id")) if x}


def classify(km, filters):
    """Return 'kill', 'loss', or None (not relevant) for a filter set; 'neutral' when no filter."""
    if not filters:
        return "neutral"
    if _names(km.get("victim", {}).get("pilot")) & filters:
        return "loss"
    for a in km.get("attackers", []) or []:
        if _names(a.get("pilot")) & filters:
            return "kill"
    if _names((km.get("finalBlow") or {}).get("pilot")) & filters:
        return "kill"
    return None


def build_embed(km, kind="neutral"):
    v = km.get("victim", {})
    fb = km.get("finalBlow") or {}
    loc = km.get("location") or {}
    attackers = km.get("attackers") or []
    top = max(attackers, key=lambda a: a.get("damage") or 0, default={})
    color = {"kill": GREEN, "loss": RED}.get(kind, GREY)
    tag = {"kill": "KILL", "loss": "LOSS"}.get(kind, "Kill")
    killer = _pilot(fb.get("pilot") or top.get("pilot"))
    victim = _pilot(v.get("pilot"))
    where = " / ".join(x for x in (loc.get("poiName"), loc.get("systemName"), loc.get("regionName")) if x) or "Unknown"
    e = {
        "title": f"{tag}: {victim} lost a {(v.get('ship') or {}).get('shipClass') or 'ship'}",
        "url": f"{BASE}/kills/{km['id']}",
        "description": f"📍 **{loc.get('systemName') or 'Unknown'}**"
                       + "".join(f" · {x}" for x in (loc.get("poiName"), loc.get("regionName")) if x)
                       + (f" · `{loc['securityBand']}`" if loc.get("securityBand") else "")
                       + f"\n**{killer}** destroyed **{victim}**",
        "color": color,
        "fields": [
            {"name": "Victim ship", "value": _ship(v.get("ship")), "inline": True},
            {"name": "Final blow", "value": f"{killer}\n{_ship(fb.get('ship') or top.get('ship'))}", "inline": True},
            {"name": "Value", "value": f"{_num(km.get('totalValue'))} credits", "inline": True},
            {"name": "Location", "value": where + (f" ({loc['securityBand']})" if loc.get("securityBand") else ""), "inline": True},
            {"name": "Attackers", "value": str(km.get("attackerCount", len(attackers))), "inline": True},
        ],
        "footer": {"text": "Farside killboard"},
    }
    if km.get("occurredAt"):
        e["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(km["occurredAt"] / 1000))
    sid = (v.get("ship") or {}).get("shipId")
    if sid:
        e["thumbnail"] = {"url": f"{BASE}/assets/portraits/killmails/feed/{sid}.webp"}
    return e



def post_webhook(webhook, embed, poster=http_json):
    for _ in range(5):
        try:
            poster(webhook + ("&" if "?" in webhook else "?") + "wait=true", {"username": "Farside Killboard", "embeds": [embed]})
            time.sleep(1.0)
            return True
        except urllib.error.HTTPError as e:
            if e.code != 429:
                print(f"webhook error HTTP {e.code}", file=sys.stderr)
                return False
            try:
                wait = float(json.loads(e.read() or b"{}").get("retry_after", 2))
            except Exception:
                wait = 2
            time.sleep(wait + 0.5)
    return False


def fetch_page(cursor=None, limit=30, fetch=http_json):
    q = {"limit": str(limit)}
    if cursor:
        q["cursor"] = cursor
    return fetch(API + "?" + urllib.parse.urlencode(q))
