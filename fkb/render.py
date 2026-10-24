"""Embed dicts (discord.Embed.from_dict-compatible) and plain-text renderings for stats."""
import time
from . import feed, stats as S

BLUE, GOLD = 0x3498DB, 0xF1C40F


def cr(v):
    v = float(v or 0)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if abs(v) >= div:
            return f"{v/div:.2f}{suf}"
    return f"{v:.0f}"


def _top(lst):
    return "\n".join(f"{n} ×{c}" for n, c in lst) or "—"


def entity_lines(title, s, period):
    L = [f"{title}  ({period})",
         f"Kills {s['kills']}  Losses {s['losses']}  K/D {s['kd']:.2f}  Efficiency {s['efficiency']:.1f}%",
         f"Destroyed {cr(s['destroyed'])} cr  Lost {cr(s['lost'])} cr  Final blows {s['final_blows']}",
         f"Solo {s['solo']}  Gang {s['gang']}"]
    for k, lab in (("top_pilots", "Top pilots"), ("used_ships", "Most used ships"), ("lost_ships", "Most lost ships"), ("systems", "Favourite systems")):
        if k in s:
            L.append(f"{lab}: " + (", ".join(f"{n} ×{c}" for n, c in s[k]) or "—"))
    return "\n".join(L)


def entity_embed(title, s, period, url=None):
    color = feed.GREEN if s["efficiency"] >= 50 else feed.RED
    if not s["kills"] and not s["losses"]:
        color = feed.GREY
    e = {"title": f"{title} — {period}", "color": color,
         "description": f"**{s['kills']}** kills / **{s['losses']}** losses · K/D **{s['kd']:.2f}** · Efficiency **{s['efficiency']:.1f}%**",
         "fields": [
             {"name": "Destroyed", "value": f"{cr(s['destroyed'])} cr", "inline": True},
             {"name": "Lost", "value": f"{cr(s['lost'])} cr", "inline": True},
             {"name": "Solo / Gang", "value": f"{s['solo']} / {s['gang']}", "inline": True}],
         "footer": {"text": "Farside killboard · values in credits"}}
    if "top_pilots" in s:
        e["fields"].append({"name": "Top pilots", "value": _top(s["top_pilots"]), "inline": True})
    e["fields"] += [{"name": "Most used ships", "value": _top(s["used_ships"]), "inline": True},
                    {"name": "Most lost ships", "value": _top(s["lost_ships"]), "inline": True},
                    {"name": "Favourite systems", "value": _top(s["systems"]), "inline": False}]
    if url:
        e["url"] = url
    return e


def corp_label(r):
    return f"{r['name']} [{r['ticker']}]" if r["ticker"] else r["name"]


def leaderboard_lines(rows, kind, period, sort):
    L = [f"Top {kind} — {period} — by {sort}"]
    for i, r in enumerate(rows, 1):
        L.append(f"{i:>2}. {r['name']:<34} K {r['kills']:>4}  L {r['losses']:>4}  {cr(r['dv']):>8} cr  eff {r['efficiency']:5.1f}%")
    return "\n".join(L)


def leaderboard_embed(rows, kind, period, sort):
    body = "\n".join(f"`{i:>2}` **{r['name']}** — {r['kills']}K/{r['losses']}L · {cr(r['dv'])} cr · {r['efficiency']:.0f}%"
                     for i, r in enumerate(rows, 1)) or "No data for this period."
    return {"title": f"Leaderboard: {kind} · {period} · by {sort}", "description": body[:4000], "color": GOLD}


def compare_embed(la, sa, lb, sb, h, period):
    def col(s):
        return (f"Kills **{s['kills']}** / Losses **{s['losses']}**\nK/D {s['kd']:.2f} · Eff {s['efficiency']:.1f}%\n"
                f"Destroyed {cr(s['destroyed'])} cr\nLost {cr(s['lost'])} cr\nSolo {s['solo']} / Gang {s['gang']}")
    return {"title": f"{la} vs {lb} — {period}", "color": BLUE, "fields": [
        {"name": la, "value": col(sa), "inline": True}, {"name": lb, "value": col(sb), "inline": True},
        {"name": "Head-to-head", "value": f"{la} killed {lb}: **{h['a_kills_b']}** ({cr(h['a_value_b'])} cr)\n"
                                          f"{lb} killed {la}: **{h['b_kills_a']}** ({cr(h['b_value_a'])} cr)", "inline": False}]}


def search_embed(c, text, total, ids, page, per):
    lines = []
    for kid in ids:
        km = feed_raw(c, kid)
        v = km.get("victim", {})
        fb = (km.get("finalBlow") or {}).get("pilot")
        when = time.strftime("%m-%d %H:%M", time.gmtime((km.get("occurredAt") or 0) / 1000))
        lines.append(f"[{when}]({feed.BASE}/kills/{kid}) **{feed._pilot(v.get('pilot'))}** lost {feed._ship(v.get('ship'))} to "
                     f"{feed._pilot(fb)} · {(km.get('location') or {}).get('systemName','?')} · {cr(km.get('totalValue'))} cr · `{kid[:8]}`")
    pages = max(1, -(-total // per))
    return {"title": f"Search: {text}", "description": "\n".join(lines) or "No matching kills.", "color": BLUE,
            "footer": {"text": f"{total} kills · page {page+1}/{pages}"}}


def feed_raw(c, kid):
    from .db import get_raw
    return get_raw(c, kid) or {}


def server_embed(s, period):
    span = ""
    if s["first"]:
        f = lambda ms: time.strftime("%Y-%m-%d", time.gmtime(ms / 1000))
        span = f"{f(s['first'])} → {f(s['last'])}"
    return {"title": f"Farside server stats — {period}", "color": BLUE, "description": span, "fields": [
        {"name": "Kills", "value": str(s["kills"]), "inline": True},
        {"name": "Destroyed", "value": f"{cr(s['value'])} cr", "inline": True},
        {"name": "Active pilots / corps", "value": f"{s['pilots']} / {s['corps']}", "inline": True},
        {"name": "Hottest systems", "value": _top(s["top_systems"]), "inline": True},
        {"name": "Most lost ships", "value": _top(s["top_lost_ships"]), "inline": True}]}
