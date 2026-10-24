import io, json, os, sys, unittest
HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from PIL import Image
from fkb import card, db, logic, stats as S
from test_killboard import ALL, NOW, fresh


def boom(url):
    raise OSError("no network in tests")


class Card(unittest.TestCase):
    def test_renders_every_fixture_offline(self):
        for k in ALL:
            for kind in ("kill", "loss", "neutral"):
                png = card.render_card(k, kind, cache_dir=None, fetch=boom)
                im = Image.open(io.BytesIO(png))
                self.assertEqual(im.width, card.W)
                self.assertGreater(im.height, 500)
                self.assertEqual(im.getpixel((10, 100))[:3], card.RULE[kind])

    def test_ship_cache_used(self):
        d = "/tmp/fkb_shipcache_test"
        os.makedirs(d, exist_ok=True)
        buf = io.BytesIO()
        Image.new("RGBA", (40, 20), (255, 0, 0, 255)).save(buf, "WEBP")
        calls = []
        f = lambda url: calls.append(url) or buf.getvalue()
        for p in os.listdir(d):
            os.remove(os.path.join(d, p))
        self.assertIsNotNone(card.ship_image("destroyer5", d, f))
        self.assertIsNotNone(card.ship_image("destroyer5", d, boom))  # served from cache
        self.assertEqual(len(calls), 1)
        self.assertIsNone(card.ship_image("../etc", d, f))
        self.assertIsNone(card.ship_image("x1", None, boom))

    def test_empty_attackers(self):
        k = dict(ALL[0], attackers=[], finalBlow=None, location={})
        self.assertTrue(card.render_card(k, fetch=None))

    def test_card_for_and_short_embed(self):
        c = fresh()
        png, e, km = logic.card_for(c, ALL[0]["id"][:8], {"dxd"}, None, fetch=boom)
        self.assertEqual(e["image"]["url"], "attachment://kill.png")
        self.assertIn(ALL[0]["location"]["systemName"], e["description"])
        self.assertTrue(png.startswith(b"\x89PNG"))


class Hunt(unittest.TestCase):
    def setUp(self):
        self.c = fresh()

    def test_counts_match_raw(self):
        from collections import Counter
        rows = S.hunt(self.c, "all", None, NOW, limit=100)
        raw = Counter(k["location"]["systemId"] for k in ALL)
        self.assertEqual({r["system_id"]: r["kills"] for r in rows}, dict(raw))
        for r in rows:
            ks = [k for k in ALL if k["location"]["systemId"] == r["system_id"]]
            self.assertEqual(r["last"], max(k["occurredAt"] for k in ks))
            self.assertAlmostEqual(r["avg_value"], sum(float(k["totalValue"]) for k in ks) / len(ks))
            self.assertGreaterEqual(r["heat"], r["kills"])
            self.assertLessEqual(r["heat"], 2 * r["kills"])
        self.assertEqual(rows, sorted(rows, key=lambda r: (r["heat"], r["last"]), reverse=True))

    def test_recency_weighting(self):
        c = db.connect(":memory:")
        base = dict(ALL[0])
        mk = lambda i, sid, t: dict(base, id=f"k{i}", occurredAt=t, location=dict(base["location"], systemId=sid, systemName=sid))
        day = 86400000
        db.ingest(c, [mk(1, "old", NOW - 23 * 3600000), mk(2, "old", NOW - 23 * 3600000), mk(3, "new", NOW - 1000),
                      mk(4, "new", NOW - 2000)])
        rows = S.hunt(c, "24h", None, NOW)
        self.assertEqual(rows[0]["system"], "new")
        self.assertEqual(S.hunt(c, "1h", None, NOW)[0]["kills"], 2)
        self.assertEqual(len(S.hunt(c, "1h", None, NOW)), 1)
        self.assertEqual(S.hunt(c, "7d", "nope-corp", NOW), [])
        _ = day

    def test_corp_filter_and_targets(self):
        rows = S.hunt(self.c, "all", "DXD", NOW, limit=100)
        dxd = [k for k in ALL if (k["victim"]["pilot"].get("corporation") or {}).get("ticker") == "DXD"
               or any((a["pilot"].get("corporation") or {}).get("ticker") == "DXD" for a in k["attackers"])]
        self.assertEqual(sum(r["kills"] for r in rows), len(dxd))
        t = S.targets(self.c, "all", "DXD", NOW, limit=100)
        non = [k for k in ALL if (k["victim"]["pilot"].get("corporation") or {}).get("ticker") != "DXD"]
        self.assertEqual(sum(r["losses"] for r in t), len(non))

    def test_where(self):
        name = ALL[0]["attackers"][0]["pilot"]["callsign"]
        w = S.where(self.c, name)
        self.assertEqual(w["kind"], "pilot")
        self.assertEqual(w["last_seen"], w["events"][0]["occurred_at"])
        self.assertTrue(all(e["role"] in ("kill", "loss") for e in w["events"]))
        self.assertEqual(S.where(self.c, "DXD")["kind"], "corp")
        with self.assertRaises(logic.NotFound):
            logic.where(self.c, "zz-nobody-zz")
        for e, _ in (logic.hunt(self.c, "all", None, NOW), logic.targets(self.c, "all", "DXD", NOW), logic.where(self.c, name, NOW)):
            self.assertLessEqual(len(json.dumps(e)), 6000)
            self.assertIn("API", e["footer"]["text"])


if __name__ == "__main__":
    unittest.main()
