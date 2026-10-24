"""SQLite storage + ingest for Farside killmails."""
import json, os, sqlite3, time

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS corps(id TEXT PRIMARY KEY, name TEXT, ticker TEXT);
CREATE TABLE IF NOT EXISTS pilots(id TEXT PRIMARY KEY, callsign TEXT, corp_id TEXT, affiliation TEXT);
CREATE TABLE IF NOT EXISTS systems(id TEXT PRIMARY KEY, name TEXT, region TEXT, security TEXT);
CREATE TABLE IF NOT EXISTS ships(ship_id TEXT PRIMARY KEY, name TEXT, ship_class TEXT, hull_class TEXT, tier INT, unit_value REAL);
CREATE TABLE IF NOT EXISTS kills(
  id TEXT PRIMARY KEY, occurred_at INTEGER, total_value REAL, system_id TEXT, poi_name TEXT,
  attacker_count INT, victim_pilot_id TEXT, victim_corp_id TEXT, victim_ship_id TEXT,
  victim_ship_name TEXT, final_pilot_id TEXT, raw TEXT, posted INT DEFAULT 0);
CREATE TABLE IF NOT EXISTS attackers(
  kill_id TEXT, pilot_id TEXT, corp_id TEXT, ship_id TEXT, ship_name TEXT, damage REAL, final_blow INT,
  PRIMARY KEY(kill_id, pilot_id));
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_k_time ON kills(occurred_at);
CREATE INDEX IF NOT EXISTS ix_k_vc ON kills(victim_corp_id);
CREATE INDEX IF NOT EXISTS ix_k_vp ON kills(victim_pilot_id);
CREATE INDEX IF NOT EXISTS ix_a_c ON attackers(corp_id);
CREATE INDEX IF NOT EXISTS ix_a_p ON attackers(pilot_id);
"""


def connect(path):
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    c = sqlite3.connect(path, timeout=30)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


def meta_get(c, k, default=None):
    r = c.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return r[0] if r else default


def meta_set(c, k, v):
    c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (k, None if v is None else str(v)))
    c.commit()


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pilot(c, p):
    p = p or {}
    corp = p.get("corporation") or {}
    if corp.get("id"):
        c.execute("INSERT INTO corps VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, ticker=excluded.ticker",
                  (corp["id"], corp.get("name"), corp.get("ticker")))
    if p.get("pilotId"):
        c.execute("INSERT INTO pilots VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET callsign=excluded.callsign,"
                  " corp_id=excluded.corp_id, affiliation=excluded.affiliation",
                  (p["pilotId"], p.get("callsign"), corp.get("id"), p.get("affiliationId")))
    return p.get("pilotId"), corp.get("id")


def _ship(c, s):
    s = s or {}
    if s.get("shipId"):
        c.execute("INSERT OR REPLACE INTO ships VALUES(?,?,?,?,?,?)", (s["shipId"], s.get("name"), s.get("shipClass"),
                  s.get("hullClass"), s.get("tier"), _num(s.get("unitValue"))))
    return s.get("shipId"), (s.get("customName") or s.get("name"))


def ingest(c, kills, posted=0):
    """Insert killmails; returns list of ids that were new."""
    new = []
    for km in kills:
        kid = km.get("id")
        if not kid or c.execute("SELECT 1 FROM kills WHERE id=?", (kid,)).fetchone():
            continue
        loc = km.get("location") or {}
        if loc.get("systemId"):
            c.execute("INSERT OR REPLACE INTO systems VALUES(?,?,?,?)", (loc["systemId"], loc.get("systemName"),
                      loc.get("regionName"), loc.get("securityBand")))
        v = km.get("victim") or {}
        vp, vc = _pilot(c, v.get("pilot"))
        vs, vsn = _ship(c, v.get("ship"))
        fp = ((km.get("finalBlow") or {}).get("pilot") or {}).get("pilotId")
        atts = km.get("attackers") or []
        c.execute("INSERT INTO kills VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (kid, km.get("occurredAt"), _num(km.get("totalValue")),
                  loc.get("systemId"), loc.get("poiName"), km.get("attackerCount", len(atts)), vp, vc, vs, vsn, fp,
                  json.dumps(km, separators=(",", ":")), posted))
        for a in atts:
            ap, ac = _pilot(c, a.get("pilot"))
            s, sn = _ship(c, a.get("ship"))
            if ap:
                c.execute("INSERT OR IGNORE INTO attackers VALUES(?,?,?,?,?,?,?)",
                          (kid, ap, ac, s, sn, _num(a.get("damage")), int(ap == fp)))
        new.append(kid)
    c.commit()
    return new


def get_raw(c, kid):
    r = c.execute("SELECT raw FROM kills WHERE id=?", (kid,)).fetchone()
    return json.loads(r[0]) if r else None


def backfill(c, fetch_page, max_age_days=30, max_kills=20000, delay=2.5, now_ms=None, sleep=time.sleep, log=print):
    """Follow the cursor back once (resumable). fetch_page(cursor)->{killmails,nextCursor}."""
    if meta_get(c, "backfill_done") == "1":
        return 0
    now_ms = now_ms or int(time.time() * 1000)
    floor = now_ms - max_age_days * 86400000
    cursor = meta_get(c, "backfill_cursor")
    total = int(meta_get(c, "backfill_count", "0"))
    while True:
        d = fetch_page(cursor)
        kms = d.get("killmails") or []
        ingest(c, kms, posted=1)
        total += len(kms)
        cursor = d.get("nextCursor")
        oldest = min((k.get("occurredAt") or now_ms for k in kms), default=0)
        meta_set(c, "backfill_cursor", cursor)
        meta_set(c, "backfill_count", total)
        if not cursor or not kms or oldest < floor or total >= max_kills:
            break
        if total % 600 == 0:
            log(f"backfill: {total} kills, back to {time.strftime('%Y-%m-%d', time.gmtime(oldest/1000))}")
        sleep(delay)
    meta_set(c, "backfill_done", "1")
    return total
