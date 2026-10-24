#!/usr/bin/env python3
"""Farside killboard -> Discord webhook poster. Stdlib only.

Data source: public JSON API used by https://playfarside.com/kills
  GET https://playfarside.com/api/killmails?limit=30[&cursor=..][&corporationId=..|pilotId=..]
"""
import argparse, json, os, random, sys, time, urllib.error, urllib.parse, urllib.request

BASE = "https://playfarside.com"
API = BASE + "/api/killmails"
UA = "farside-killboard/0.1 (discord webhook; polite polling)"
GREEN, RED, GREY = 0x2ECC71, 0xE74C3C, 0x95A5A6
HERE = os.path.dirname(os.path.abspath(__file__))


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
        "description": f"**{killer}** destroyed **{victim}**",
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


# ---------- state ----------
def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"seen": [], "initialized": False}


def save_state(path, st, keep=5000):
    st["seen"] = st["seen"][-keep:]
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, path)


def process(kills, state, filters, backfill=0):
    """Return list of (km, kind) to post, oldest first; updates state['seen']."""
    seen = set(state["seen"])
    new = [k for k in kills if k.get("id") and k["id"] not in seen]
    new.sort(key=lambda k: k.get("occurredAt") or 0)
    if not state.get("initialized"):
        to_post = new[-backfill:] if backfill > 0 else []
        state["initialized"] = True
    else:
        to_post = new
    for k in new:
        state["seen"].append(k["id"])
    out = []
    for k in to_post:
        kind = classify(k, filters)
        if kind:
            out.append((k, kind))
    return out


def post(webhook, embed, dry_run, poster=http_json):
    if dry_run:
        print(json.dumps(embed, indent=2))
        return
    for attempt in range(5):
        try:
            poster(webhook + ("&" if "?" in webhook else "?") + "wait=true",
                   {"username": "Farside Killboard", "embeds": [embed]})
            time.sleep(1.0)  # stay well under webhook rate limit
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read() or b"{}").get("retry_after", 2))
                except Exception:
                    wait = 2
                time.sleep(wait + 0.5)
            else:
                print(f"webhook error HTTP {e.code}", file=sys.stderr)
                return


def main(argv=None):
    load_env(os.path.join(HERE, ".env"))
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print embeds instead of posting")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--backfill", type=int, default=int(os.environ.get("BACKFILL", "0")))
    ap.add_argument("--fixture", help="read killmails from a JSON file instead of the API")
    ap.add_argument("--state", default=os.environ.get("STATE_FILE", os.path.join(HERE, "state.json")))
    a = ap.parse_args(argv)
    interval = max(30, int(os.environ.get("POLL_SECONDS", "45")))
    filters = {x.strip().lower() for x in os.environ.get("FILTER_NAMES", "").split(",") if x.strip()}
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
    if not webhook and not a.dry_run:
        sys.exit("DISCORD_WEBHOOK_URL not set (put it in .env) or use --dry-run")
    params = {}
    if os.environ.get("CORPORATION_ID"):
        params["corporationId"] = os.environ["CORPORATION_ID"]
    state = load_state(a.state)
    backoff = interval
    while True:
        try:
            if a.fixture:
                kills = json.load(open(a.fixture))["killmails"]
            else:
                kills = fetch_kills(pages=1, params=params)
            for km, kind in process(kills, state, filters, a.backfill):
                post(webhook, build_embed(km, kind), a.dry_run)
            save_state(a.state, state)
            backoff = interval
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            backoff = min(backoff * 2, 900)
            print(f"poll failed ({type(e).__name__}); retry in {backoff}s", file=sys.stderr)
        if a.once:
            return
        time.sleep(backoff + random.uniform(0, 5))


if __name__ == "__main__":
    main()
