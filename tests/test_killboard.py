import json, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from fkb import db, feed, logic, service, stats as S, render as R

P1 = json.load(open(os.path.join(HERE, "fixtures", "killmails_page1.json")))
P2 = json.load(open(os.path.join(HERE, "fixtures", "killmails_page2.json")))
ALL = P1["killmails"] + P2["killmails"]
NOW = max(k["occurredAt"] for k in ALL) + 1000


def fresh():
    c = db.connect(":memory:")
    db.ingest(c, ALL, posted=1)
    return c


def oracle(ticker, since=0):
    """Independent pure-Python stats from raw JSON."""
    tk = lambda p: ((p or {}).get("corporation") or {}).get("ticker")
    kills = [k for k in ALL if k["occurredAt"] >= since and tk(k["victim"]["pilot"]) != ticker
             and any(tk(a["pilot"]) == ticker for a in k["attackers"])]
    losses = [k for k in ALL if k["occurredAt"] >= since and tk(k["victim"]["pilot"]) == ticker]
    dv = sum(float(k["totalValue"]) for k in kills)
    lv = sum(float(k["totalValue"]) for k in losses)
    return len(kills), len(losses), dv, lv, sum(1 for k in kills if k["attackerCount"] == 1)


def top_ticker():
    from collections import Counter
    return Counter(((a["pilot"].get("corporation") or {}).get("ticker")) for k in ALL for a in k["attackers"]).most_common(1)[0][0]


class Ingest(unittest.TestCase):
    def test_dedupe(self):
        c = db.connect(":memory:")
        self.assertEqual(len(db.ingest(c, ALL)), len(ALL))
        self.assertEqual(db.ingest(c, ALL), [])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM kills").fetchone()[0], len(ALL))
        fb = c.execute("SELECT SUM(final_blow) FROM attackers").fetchone()[0]
        self.assertEqual(fb, sum(1 for k in ALL if (k.get("finalBlow") or {}).get("pilot")))

    def test_backfill_cursor_and_resume(self):
        c = db.connect(":memory:")
        pages = {None: P1, P1["nextCursor"]: P2}
        sleeps = []
        n = db.backfill(c, lambda cur: pages[cur], now_ms=NOW, sleep=sleeps.append, log=lambda *_: None)
        self.assertEqual(n, len(ALL))
        self.assertEqual(sleeps, [2.5])
        self.assertEqual(db.backfill(c, lambda cur: 1 / 0), 0)  # done flag
        self.assertEqual(c.execute("SELECT COUNT(*) FROM kills WHERE posted=0").fetchone()[0], 0)

    def test_backfill_age_cap(self):
        c = db.connect(":memory:")
        db.backfill(c, lambda cur: {None: P1, P1["nextCursor"]: P2}[cur], max_age_days=0, now_ms=NOW, sleep=lambda s: None)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM kills").fetchone()[0], len(P1["killmails"]))

    def test_poll_posts_only_new_filtered(self):
        c = db.connect(":memory:")
        db.ingest(c, ALL[1:], posted=1)
        tk = top_ticker().lower()
        items = service.poll_once(c, {tk}, fetch_page=lambda cur: P1)
        involved = feed.classify(ALL[0], {tk})
        self.assertEqual(len(items), 1 if involved else 0)
        self.assertEqual(service.poll_once(c, {tk}, fetch_page=lambda cur: P1), [])


class Stats(unittest.TestCase):
    def setUp(self):
        self.c = fresh()
        self.t = top_ticker()

    def test_corp_matches_oracle_all_and_7d(self):
        cid = S.find_corp(self.c, self.t)["id"]
        for period in ("all", "7d", "24h"):
            s = S.entity_stats(self.c, "corp", cid, period, NOW)
            k, l, dv, lv, solo = oracle(self.t, S.since_ms(period, NOW))
            self.assertEqual((s["kills"], s["losses"], s["solo"]), (k, l, solo), period)
            self.assertAlmostEqual(s["destroyed"], dv)
            self.assertAlmostEqual(s["lost"], lv)
            if dv + lv:
                self.assertAlmostEqual(s["efficiency"], 100 * dv / (dv + lv))
            self.assertAlmostEqual(s["kd"], k / l if l else k)

    def test_leaderboard_consistent_with_entity(self):
        rows = S.leaderboard(self.c, "corps", "all", "kills", now_ms=NOW)
        self.assertTrue(rows)
        self.assertEqual(rows, sorted(rows, key=lambda r: (r["kills"], r["dv"]), reverse=True))
        for r in rows[:3]:
            s = S.entity_stats(self.c, "corp", r["e"], "all", NOW)
            self.assertEqual((r["kills"], r["losses"]), (s["kills"], s["losses"]))
            self.assertAlmostEqual(r["dv"], s["destroyed"])
        self.assertTrue(S.leaderboard(self.c, "pilots", "all", "value", now_ms=NOW))

    def test_head_to_head_symmetry(self):
        rows = S.leaderboard(self.c, "corps", "all", now_ms=NOW)
        if len(rows) < 2:
            self.skipTest("need 2 corps")
        a, b = rows[0]["e"], rows[1]["e"]
        h1, h2 = S.head_to_head(self.c, a, b), S.head_to_head(self.c, b, a)
        self.assertEqual(h1["a_kills_b"], h2["b_kills_a"])

    def test_find_and_autocomplete(self):
        self.assertEqual(S.find_corp(self.c, self.t.lower())["ticker"], self.t)
        self.assertTrue(S.autocomplete(self.c, "corp", self.t[:1]))
        name = ALL[0]["victim"]["pilot"]["callsign"]
        self.assertEqual(S.find_pilot(self.c, name.upper())["callsign"], name)
        self.assertTrue(S.autocomplete(self.c, "pilot", name[:3]))


class SearchAndHandlers(unittest.TestCase):
    def setUp(self):
        self.c = fresh()

    def test_search_system_and_paging(self):
        sysname = ALL[0]["location"]["systemName"]
        expect = sum(1 for k in ALL if k["location"]["systemName"] == sysname)
        total, ids = S.search(self.c, sysname, 5, 0)
        self.assertGreaterEqual(total, expect)
        self.assertLessEqual(len(ids), 5)
        _, ids2 = S.search(self.c, sysname, 5, 5)
        self.assertFalse(set(ids) & set(ids2))
        e, t = logic.search(self.c, sysname)
        self.assertIn("page 1/", e["footer"]["text"])
        self.assertEqual(logic.search(self.c, "zzzz-nothing")[1], 0)

    def test_kill_prefix_and_notfound(self):
        e, km = logic.kill(self.c, ALL[3]["id"][:8])
        self.assertEqual(km["id"], ALL[3]["id"])
        with self.assertRaises(logic.NotFound):
            logic.kill(self.c, "nope")
        with self.assertRaises(logic.NotFound):
            logic.corp(self.c, "nope-corp-xyz")

    def test_handlers_build_valid_embeds(self):
        t = top_ticker()
        pilot = ALL[0]["attackers"][0]["pilot"]["callsign"]
        rows = S.leaderboard(self.c, "corps", "all", now_ms=NOW)
        other = rows[1]["name"].split(" [")[0] if len(rows) > 1 else t
        for e, _ in (logic.corp(self.c, t, "all", NOW), logic.pilot(self.c, pilot, "all", NOW),
                     logic.leaderboard(self.c, "corps", "all", "efficiency", NOW), logic.compare(self.c, t, other, "all", NOW),
                     logic.server(self.c, "all", NOW)):
            self.assertLessEqual(len(json.dumps(e)), 6000)
            self.assertIn("title", e)
            for f in e.get("fields", []):
                self.assertTrue(f["value"])
                self.assertLessEqual(len(f["value"]), 1024)

    def test_feed_embed_colors(self):
        k = ALL[0]
        att = k["attackers"][0]["pilot"]["corporation"]["ticker"].lower()
        self.assertEqual(feed.classify(k, {att}), "kill")
        self.assertEqual(feed.build_embed(k, "kill")["color"], feed.GREEN)
        self.assertEqual(feed.build_embed(k, "loss")["color"], feed.RED)
        self.assertIn("thumbnail", feed.build_embed(k))
        self.assertEqual(R.cr(1234567), "1.23M")


class Lock(unittest.TestCase):
    def test_single_instance(self):
        p = "/tmp/fkb_test_lock/l.lock"
        h = service.acquire_lock(p)
        import subprocess
        r = subprocess.run([sys.executable, "-c", f"import sys;sys.path.insert(0,{os.path.join(HERE,'..')!r});"
                            f"from fkb import service;service.acquire_lock({p!r})"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        h.close()


if __name__ == "__main__":
    unittest.main()
