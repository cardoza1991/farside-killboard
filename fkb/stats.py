"""Stats/search queries. Values are game credits."""
import time

PERIODS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "all": None}


def since_ms(period, now_ms=None):
    s = PERIODS.get(period or "all")
    if s is None:
        return 0
    return (now_ms or int(time.time() * 1000)) - s * 1000


def find_corp(c, q):
    q = (q or "").strip()
    r = c.execute("SELECT * FROM corps WHERE lower(ticker)=lower(?) OR lower(name)=lower(?) OR id=?", (q, q, q)).fetchone()
    return r or c.execute("SELECT * FROM corps WHERE name LIKE ? ORDER BY length(name) LIMIT 1", (f"%{q}%",)).fetchone()


def find_pilot(c, q):
    q = (q or "").strip()
    r = c.execute("SELECT * FROM pilots WHERE lower(callsign)=lower(?) OR id=?", (q, q)).fetchone()
    return r or c.execute("SELECT * FROM pilots WHERE callsign LIKE ? ORDER BY length(callsign) LIMIT 1", (f"%{q}%",)).fetchone()


def autocomplete(c, kind, q, limit=25):
    q = f"%{q or ''}%"
    if kind == "corp":
        rows = c.execute("""SELECT co.name, co.ticker, COUNT(a.kill_id) n FROM corps co LEFT JOIN attackers a ON a.corp_id=co.id
            WHERE co.name LIKE ? OR co.ticker LIKE ? GROUP BY co.id ORDER BY n DESC LIMIT ?""", (q, q, limit)).fetchall()
        return [(f"{r['name']} [{r['ticker']}]"[:100], r["ticker"] or r["name"]) for r in rows]
    rows = c.execute("""SELECT p.callsign, COUNT(a.kill_id) n FROM pilots p LEFT JOIN attackers a ON a.pilot_id=p.id
        WHERE p.callsign LIKE ? GROUP BY p.id ORDER BY n DESC LIMIT ?""", (q, limit)).fetchall()
    return [(r["callsign"][:100], r["callsign"][:100]) for r in rows]


def entity_stats(c, kind, eid, period="all", now_ms=None):
    """kind: 'corp' or 'pilot'. Kills exclude friendly fire (victim in same entity)."""
    t = since_ms(period, now_ms)
    acol, vcol = ("corp_id", "victim_corp_id") if kind == "corp" else ("pilot_id", "victim_pilot_id")
    kq = f"""SELECT DISTINCT k.* FROM kills k JOIN attackers a ON a.kill_id=k.id
             WHERE a.{acol}=? AND k.occurred_at>=? AND coalesce(k.{vcol},'')<>?"""
    kills = c.execute(kq, (eid, t, eid)).fetchall()
    losses = c.execute(f"SELECT * FROM kills WHERE {vcol}=? AND occurred_at>=?", (eid, t)).fetchall()
    dv = sum(k["total_value"] for k in kills)
    lv = sum(k["total_value"] for k in losses)
    nk, nl = len(kills), len(losses)
    solo = sum(1 for k in kills if k["attacker_count"] == 1)
    sub = f"SELECT k.id FROM kills k JOIN attackers a ON a.kill_id=k.id WHERE a.{acol}=? AND k.occurred_at>=? AND coalesce(k.{vcol},'')<>?"

    def top(sql, args, n=5):
        return [(r[0] or "?", r[1]) for r in c.execute(sql, args).fetchall()][:n]

    out = {
        "kills": nk, "losses": nl, "destroyed": dv, "lost": lv,
        "efficiency": (100.0 * dv / (dv + lv)) if (dv + lv) else 0.0,
        "kd": (nk / nl) if nl else float(nk),
        "solo": solo, "gang": nk - solo,
        "final_blows": c.execute(f"SELECT COUNT(*) FROM attackers a JOIN kills k ON k.id=a.kill_id WHERE a.{acol}=? AND a.final_blow=1 AND k.occurred_at>=? AND coalesce(k.{vcol},'')<>?", (eid, t, eid)).fetchone()[0],
        "used_ships": top(f"""SELECT coalesce(s.ship_class,a.ship_id), COUNT(*) n FROM attackers a JOIN kills k ON k.id=a.kill_id
            LEFT JOIN ships s ON s.ship_id=a.ship_id WHERE a.{acol}=? AND k.occurred_at>=? AND coalesce(k.{vcol},'')<>?
            GROUP BY 1 ORDER BY n DESC""", (eid, t, eid)),
        "lost_ships": top(f"""SELECT coalesce(s.ship_class,k.victim_ship_id), COUNT(*) n FROM kills k LEFT JOIN ships s ON s.ship_id=k.victim_ship_id
            WHERE k.{vcol}=? AND k.occurred_at>=? GROUP BY 1 ORDER BY n DESC""", (eid, t)),
        "systems": top(f"""SELECT sy.name, COUNT(*) n FROM kills k LEFT JOIN systems sy ON sy.id=k.system_id
            WHERE k.id IN ({sub}) OR (k.{vcol}=? AND k.occurred_at>=?) GROUP BY k.system_id ORDER BY n DESC""", (eid, t, eid, eid, t)),
    }
    if kind == "corp":
        out["top_pilots"] = top(f"""SELECT p.callsign, COUNT(DISTINCT a.kill_id) n FROM attackers a JOIN kills k ON k.id=a.kill_id
            JOIN pilots p ON p.id=a.pilot_id WHERE a.corp_id=? AND k.occurred_at>=? AND coalesce(k.victim_corp_id,'')<>?
            GROUP BY a.pilot_id ORDER BY n DESC""", (eid, t, eid))
    return out


def head_to_head(c, a, b, period="all", now_ms=None):
    t = since_ms(period, now_ms)
    q = """SELECT COUNT(DISTINCT k.id), coalesce(SUM(k.total_value),0) FROM kills k
           WHERE k.victim_corp_id=? AND k.occurred_at>=? AND EXISTS(SELECT 1 FROM attackers a WHERE a.kill_id=k.id AND a.corp_id=?)"""
    ab = c.execute(q, (b, t, a)).fetchone()
    ba = c.execute(q, (a, t, b)).fetchone()
    return {"a_kills_b": ab[0], "a_value_b": ab[1], "b_kills_a": ba[0], "b_value_a": ba[1]}


def leaderboard(c, kind="corps", period="7d", sort="kills", limit=10, now_ms=None, min_kills=3):
    t = since_ms(period, now_ms)
    acol, vcol, tbl, name = (("corp_id", "victim_corp_id", "corps", "coalesce(name,'?')||' ['||coalesce(ticker,'')||']'")
                             if kind == "corps" else ("pilot_id", "victim_pilot_id", "pilots", "callsign"))
    rows = c.execute(f"""
      WITH kd AS (SELECT a.{acol} e, COUNT(DISTINCT k.id) kills, 0 losses, 0.0 dv, 0.0 lv FROM attackers a JOIN kills k ON k.id=a.kill_id
                  WHERE k.occurred_at>=? AND a.{acol} IS NOT NULL AND coalesce(k.{vcol},'')<>a.{acol} GROUP BY a.{acol}),
           dv AS (SELECT e, SUM(v) dv FROM (SELECT DISTINCT a.{acol} e, k.id, k.total_value v FROM attackers a JOIN kills k ON k.id=a.kill_id
                  WHERE k.occurred_at>=? AND a.{acol} IS NOT NULL AND coalesce(k.{vcol},'')<>a.{acol}) GROUP BY e),
           ls AS (SELECT {vcol} e, COUNT(*) losses, SUM(total_value) lv FROM kills WHERE occurred_at>=? AND {vcol} IS NOT NULL GROUP BY {vcol})
      SELECT x.id e, {name} name, coalesce(kd.kills,0) kills, coalesce(ls.losses,0) losses,
             coalesce(dv.dv,0) dv, coalesce(ls.lv,0) lv
      FROM {tbl} x LEFT JOIN kd ON kd.e=x.id LEFT JOIN dv ON dv.e=x.id LEFT JOIN ls ON ls.e=x.id
      WHERE coalesce(kd.kills,0)+coalesce(ls.losses,0)>0""", (t, t, t)).fetchall()
    res = []
    for r in rows:
        d = dict(r)
        tot = d["dv"] + d["lv"]
        d["efficiency"] = 100.0 * d["dv"] / tot if tot else 0.0
        res.append(d)
    if sort == "efficiency":
        res = [r for r in res if r["kills"] >= min_kills]
    key = {"kills": lambda r: (r["kills"], r["dv"]), "value": lambda r: (r["dv"], r["kills"]),
           "efficiency": lambda r: (r["efficiency"], r["kills"])}[sort]
    return sorted(res, key=key, reverse=True)[:limit]


def search(c, text, limit=5, offset=0):
    q = f"%{(text or '').strip()}%"
    where = """FROM kills k LEFT JOIN pilots vp ON vp.id=k.victim_pilot_id LEFT JOIN corps vc ON vc.id=k.victim_corp_id
      LEFT JOIN systems sy ON sy.id=k.system_id LEFT JOIN ships s ON s.ship_id=k.victim_ship_id
      WHERE vp.callsign LIKE :q OR vc.name LIKE :q OR vc.ticker LIKE :q OR sy.name LIKE :q OR s.ship_class LIKE :q
        OR k.victim_ship_name LIKE :q OR EXISTS(SELECT 1 FROM attackers a LEFT JOIN pilots p ON p.id=a.pilot_id
          LEFT JOIN corps co ON co.id=a.corp_id LEFT JOIN ships s2 ON s2.ship_id=a.ship_id WHERE a.kill_id=k.id AND
          (p.callsign LIKE :q OR co.name LIKE :q OR co.ticker LIKE :q OR s2.ship_class LIKE :q OR a.ship_name LIKE :q))"""
    total = c.execute("SELECT COUNT(*) " + where, {"q": q}).fetchone()[0]
    ids = [r[0] for r in c.execute("SELECT k.id " + where + " ORDER BY k.occurred_at DESC LIMIT :l OFFSET :o",
                                   {"q": q, "l": limit, "o": offset})]
    return total, ids


def server_stats(c, period="all", now_ms=None):
    t = since_ms(period, now_ms)
    r = c.execute("SELECT COUNT(*), coalesce(SUM(total_value),0), MIN(occurred_at), MAX(occurred_at) FROM kills WHERE occurred_at>=?", (t,)).fetchone()
    top_sys = c.execute("""SELECT sy.name, COUNT(*) n FROM kills k LEFT JOIN systems sy ON sy.id=k.system_id WHERE occurred_at>=?
        GROUP BY k.system_id ORDER BY n DESC LIMIT 5""", (t,)).fetchall()
    top_ship = c.execute("""SELECT coalesce(s.ship_class,k.victim_ship_id), COUNT(*) n FROM kills k LEFT JOIN ships s ON s.ship_id=k.victim_ship_id
        WHERE occurred_at>=? GROUP BY 1 ORDER BY n DESC LIMIT 5""", (t,)).fetchall()
    return {"kills": r[0], "value": r[1], "first": r[2], "last": r[3],
            "pilots": c.execute("SELECT COUNT(DISTINCT pilot_id) FROM attackers a JOIN kills k ON k.id=a.kill_id WHERE k.occurred_at>=?", (t,)).fetchone()[0],
            "corps": c.execute("SELECT COUNT(DISTINCT corp_id) FROM attackers a JOIN kills k ON k.id=a.kill_id WHERE k.occurred_at>=?", (t,)).fetchone()[0],
            "top_systems": [tuple(x) for x in top_sys], "top_lost_ships": [tuple(x) for x in top_ship]}
