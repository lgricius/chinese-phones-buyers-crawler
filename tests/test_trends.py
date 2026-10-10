import unittest

from phones import trends


def offer(site, color, eur, stock=True, usd=None):
    o = {"site": site, "url": f"https://{site}/p", "color": color, "ram_gb": 16, "storage_gb": 512, "edition": "cn",
         "condition": "new", "price_eur": eur, "in_stock": stock, "brand": "Oppo", "model": "Find N6", "model_key": "oppo find n6"}
    if usd:
        o.update(price_orig=usd, currency_orig="USD")
    return o


RUNS = ["2026-11-01T080000Z", "2026-11-02T080000Z", "2026-11-03T080000Z", "2026-11-04T080000Z"]
AT = [f"{r[:10]}T08:00:00+00:00" for r in RUNS]


def build(snapshots):
    t = trends.empty()
    for run, at, site, offers in snapshots:
        trends.apply(t, site, run, at, offers)
    return t


class TrendsTest(unittest.TestCase):
    def setUp(self):
        self.t = build([
            (RUNS[0], AT[0], "giztop", [offer("giztop", "Black", 875.0, usd=1000)]),
            (RUNS[0], AT[0], "wonda", [offer("wonda", "Blue", 500.0), offer("wonda", "Red", 520.0)]),
            # giztop: only the exchange rate moved (USD 1000 both days) -> no change
            (RUNS[1], AT[1], "giztop", [offer("giztop", "Black", 900.0, usd=1000)]),
            (RUNS[1], AT[1], "wonda", [offer("wonda", "Blue", 480.0), offer("wonda", "Red", 520.0, stock=False)]),
            (RUNS[2], AT[2], "giztop", [offer("giztop", "Black", 855.0, usd=950)]),
            (RUNS[2], AT[2], "wonda", [offer("wonda", "Red", 520.0), offer("wonda", "Green", 499.0)]),
            (RUNS[3], AT[3], "wonda", [offer("wonda", "Blue", 470.0), offer("wonda", "Red", 520.0), offer("wonda", "Green", 499.0)]),
        ])
        self.ids = {c: trends.offer_id(offer(s, c, 0)) for s, c in [("giztop", "Black"), ("wonda", "Blue"), ("wonda", "Red"), ("wonda", "Green")]}

    def test_rate_move_is_not_a_change(self):
        pts = self.t["series"][self.ids["Black"]][1]
        self.assertEqual([p[0] for p in pts], [0, 2])  # nothing stored for the rate-only crawl
        self.assertEqual(pts[1][3], 950)

    def test_events(self):
        ev = trends.events(self.t)
        got = sorted((e["site"], e["kind"], e["at"][:10]) for e in ev)
        self.assertEqual(got, sorted([
            ("wonda", "drop", "2026-11-02"), ("wonda", "out", "2026-11-02"),
            ("giztop", "drop", "2026-11-03"), ("wonda", "gone", "2026-11-03"), ("wonda", "in", "2026-11-03"), ("wonda", "new", "2026-11-03"),
            ("wonda", "new", "2026-11-04"),  # Blue listed again
        ]))
        g = next(e for e in ev if e["site"] == "giztop")
        self.assertEqual((g["old_eur"], g["new_eur"], g["pct"]), (900.0, 855.0, -5.0))  # both at that day's rate
        self.assertTrue(next(e for e in ev if e["kind"] == "new" and e["at"].startswith("2026-11-04"))["relisted"])
        self.assertEqual([e["at"] for e in ev], sorted((e["at"] for e in ev), reverse=True))
        self.assertTrue(all(e["at"] >= AT[2] for e in trends.events(self.t, since=AT[2])))

    def test_stats(self):
        blue = {**offer("wonda", "Blue", 470.0), "oid": self.ids["Blue"]}
        s = trends.stats(self.t, blue)
        self.assertEqual((s["chg"], s["chg_pct"], s["prev"], s["chg_at"]), (-10.0, -2.1, 480.0, AT[3]))
        self.assertEqual((s["low"], s["low_at"], s["at_low"]), (470.0, "2026-11-04", True))
        self.assertEqual((s["low30"], s["high30"], s["drops30"], s["rises30"]), (470.0, 500.0, 2, 0))
        self.assertEqual((s["since"], s["n"], s["stock_pct"]), ("2026-11-01", 3, 100))  # not listed on 11-03
        red = trends.stats(self.t, {**offer("wonda", "Red", 520.0), "oid": self.ids["Red"]})
        self.assertEqual((red["n"], red["stock_pct"]), (4, 75))
        self.assertNotIn("chg", red)
        self.assertTrue(trends.stats(self.t, {**offer("wonda", "Green", 499.0), "oid": self.ids["Green"]})["new"])
        black = trends.stats(self.t, {**offer("giztop", "Black", 855.0, usd=950), "oid": self.ids["Black"]})
        self.assertEqual((black["chg"], black["low"], black["at_low"]), (-45.0, 855.0, True))

    def test_update_rebuilds_when_an_older_snapshot_appears(self):
        snaps = {(RUNS[1], "wonda"): {"crawled_at": AT[1], "offers": [offer("wonda", "Blue", 480.0)]}}
        t, rebuilt = trends.update(trends.empty(), list((r, s, (r, s)) for r, s in snaps), snaps.get)
        self.assertFalse(rebuilt)
        snaps[(RUNS[0], "wonda")] = {"crawled_at": AT[0], "offers": [offer("wonda", "Blue", 500.0)]}
        t, rebuilt = trends.update(t, list((r, s, (r, s)) for r, s in snaps), snaps.get)
        self.assertTrue(rebuilt)
        self.assertEqual([r[0] for r in t["sites"]["wonda"]["runs"]], [RUNS[0], RUNS[1]])

    def test_offer_id_ignores_price_and_stock(self):
        a, b = offer("wonda", "Blue", 500.0), offer("wonda", "Blue", 480.0, stock=False)
        self.assertEqual(trends.offer_id(a), trends.offer_id(b))
        self.assertNotEqual(trends.offer_id(a), trends.offer_id({**a, "attrs": {"bundle": "Photography Kit"}}))


if __name__ == "__main__":
    unittest.main()
