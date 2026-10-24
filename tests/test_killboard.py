import json, os, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import killboard as kb

FIX = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "killmails_page1.json")))
KILLS = FIX["killmails"]


class T(unittest.TestCase):
    def test_first_run_no_flood(self):
        st = {"seen": [], "initialized": False}
        self.assertEqual(kb.process(KILLS, st, set()), [])
        self.assertEqual(len(st["seen"]), len(KILLS))
        self.assertEqual(kb.process(KILLS, st, set()), [])  # dedupe

    def test_backfill(self):
        st = {"seen": [], "initialized": False}
        out = kb.process(KILLS, st, set(), backfill=3)
        self.assertEqual(len(out), 3)
        ts = [k["occurredAt"] for k, _ in out]
        self.assertEqual(ts, sorted(ts))

    def test_new_kill_posted(self):
        st = {"seen": [k["id"] for k in KILLS[1:]], "initialized": True}
        out = kb.process(KILLS, st, set())
        self.assertEqual([k["id"] for k, _ in out], [KILLS[0]["id"]])

    def test_filter_kill_and_loss(self):
        k = KILLS[0]
        att = k["attackers"][0]["pilot"]["corporation"]["ticker"].lower()
        self.assertEqual(kb.classify(k, {att}), "kill")
        self.assertEqual(kb.classify(k, {k["victim"]["pilot"]["callsign"].lower()}), "loss")
        self.assertIsNone(kb.classify(k, {"nobody-xyz"}))
        self.assertEqual(kb.build_embed(k, "kill")["color"], kb.GREEN)
        self.assertEqual(kb.build_embed(k, "loss")["color"], kb.RED)

    def test_embed_fields(self):
        e = kb.build_embed(KILLS[0])
        self.assertTrue(e["url"].endswith("/kills/" + KILLS[0]["id"]))
        self.assertIn("thumbnail", e)
        self.assertTrue(e["timestamp"].endswith("Z"))
        self.assertIn("credits", e["fields"][2]["value"])
        self.assertLessEqual(len(json.dumps(e)), 6000)

    def test_all_fixture_embeds_build(self):
        for k in KILLS:
            kb.build_embed(k)

    def test_pagination(self):
        calls = []
        def fake(url):
            calls.append(url)
            return {"killmails": KILLS[:2], "nextCursor": "c1" if len(calls) == 1 else None}
        out = kb.fetch_kills(pages=3, fetch=fake)
        self.assertEqual(len(out), 4)
        self.assertIn("cursor=c1", calls[1])

    def test_state_roundtrip(self):
        p = "/tmp/fkb_state_test.json"
        kb.save_state(p, {"seen": ["a", "b"], "initialized": True})
        self.assertEqual(kb.load_state(p)["seen"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
