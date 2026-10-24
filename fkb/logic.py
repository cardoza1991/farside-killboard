"""Command handlers, Discord-free. Each returns (embed_dict, extra) for testing and the bot."""
import time
from . import stats as S, render as R, feed
from .db import get_raw

PER_PAGE = 5


class NotFound(Exception):
    pass


def corp(c, q, period="7d", now_ms=None):
    r = S.find_corp(c, q)
    if not r:
        raise NotFound(f"No corp matching '{q}'.")
    s = S.entity_stats(c, "corp", r["id"], period, now_ms)
    return R.entity_embed(R.corp_label(r), s, period, f"{feed.BASE}/kills/corporation/{r['id']}"), s


def pilot(c, q, period="all", now_ms=None):
    r = S.find_pilot(c, q)
    if not r:
        raise NotFound(f"No pilot matching '{q}'.")
    s = S.entity_stats(c, "pilot", r["id"], period, now_ms)
    return R.entity_embed(r["callsign"], s, period, f"{feed.BASE}/kills/pilot/{r['id']}"), s


def leaderboard(c, kind="corps", period="7d", sort="kills", now_ms=None):
    rows = S.leaderboard(c, kind, period, sort, now_ms=now_ms)
    return R.leaderboard_embed(rows, kind, period, sort), rows


def compare(c, a, b, period="all", now_ms=None):
    ra, rb = S.find_corp(c, a), S.find_corp(c, b)
    if not ra or not rb:
        raise NotFound(f"No corp matching '{a if not ra else b}'.")
    sa = S.entity_stats(c, "corp", ra["id"], period, now_ms)
    sb = S.entity_stats(c, "corp", rb["id"], period, now_ms)
    h = S.head_to_head(c, ra["id"], rb["id"], period, now_ms)
    return R.compare_embed(R.corp_label(ra), sa, R.corp_label(rb), sb, h, period), h


def search(c, text, page=0):
    total, ids = S.search(c, text, PER_PAGE, page * PER_PAGE)
    return R.search_embed(c, text, total, ids, page, PER_PAGE), total


def kill(c, kid):
    km = get_raw(c, kid)
    if not km:
        r = c.execute("SELECT id FROM kills WHERE id LIKE ? LIMIT 1", (kid + "%",)).fetchone()
        km = get_raw(c, r[0]) if r else None
    if not km:
        raise NotFound(f"No kill '{kid}' in the local database.")
    return feed.build_embed(km), km


def server(c, period="all", now_ms=None):
    s = S.server_stats(c, period, now_ms)
    return R.server_embed(s, period), s


def hunt(c, period="24h", corp=None, now_ms=None):
    rows = S.hunt(c, period, corp, now_ms)
    return R.hunt_embed(rows, period, corp, now_ms), rows


def targets(c, period="24h", home="DXD", now_ms=None):
    rows = S.targets(c, period, home, now_ms)
    return R.targets_embed(rows, period, home, now_ms), rows


def where(c, q, now_ms=None):
    w = S.where(c, q)
    if not w:
        raise NotFound(f"No pilot or corp matching '{q}'.")
    return R.where_embed(w, now_ms), w


def card_for(c, kid, filters=None, cache_dir=None, fetch=None):
    """(png_bytes, short_embed, km) for /kill."""
    from . import card
    _, km = kill(c, kid)
    kind = feed.classify(km, filters or set()) or "neutral"
    return card.render_card(km, kind, cache_dir, **({} if fetch is None else {"fetch": fetch})), short_embed(km, kind), km


def short_embed(km, kind="neutral"):
    loc = km.get("location") or {}
    v = km.get("victim") or {}
    tag = {"kill": "KILL", "loss": "LOSS"}.get(kind, "Kill")
    e = {"title": f"{tag}: {feed._pilot(v.get('pilot'))} lost a {(v.get('ship') or {}).get('shipClass') or 'ship'}",
         "url": f"{feed.BASE}/kills/{km['id']}", "color": {"kill": feed.GREEN, "loss": feed.RED}.get(kind, feed.GREY),
         "description": f"📍 **{loc.get('systemName') or 'Unknown'}**" + "".join(f" · {x}" for x in (loc.get("poiName"), loc.get("regionName")) if x)
                        + (f" · `{loc['securityBand']}`" if loc.get("securityBand") else ""),
         "image": {"url": "attachment://kill.png"}}
    if km.get("occurredAt"):
        e["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(km["occurredAt"] / 1000))
    return e
