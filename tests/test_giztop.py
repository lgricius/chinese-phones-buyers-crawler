import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from phones import store
from phones.sites import giztop


def page(currency: str, price: float) -> str:
    return (f'<meta property="og:title" content="OPPO Find N6 Global Edition-Black-16GB - 512GB">'
            f'<div class="stock available"><span>In stock</span></div><div itemprop="sku">FN6</div>'
            f'<div class="product-info-main"><meta itemprop="priceCurrency" content="{currency}">'
            f'<meta itemprop="price" content="{price}"></div>')


URL = "https://www.giztop.com/oppo-find-n6.html"
SHIPPING = [{"method_title": "Express", "amount": 40.0, "base_amount": 40.0, "available": True}]


def rec(kind, body, url=URL, meta=None):
    return {"kind": kind, "url": url, "meta": meta or {}, "body": body}


class FallbackRateTest(unittest.TestCase):
    def test_usd_pages_are_converted_at_the_stored_rate(self):
        records = [rec("fallback_rate", json.dumps({"currency": "USD", "rate": 0.9, "date": "2026-11-15"})),
                   rec("product", page("USD", 1500)),
                   rec("shipping", json.dumps(SHIPPING), meta={"currency": "USD"})]
        (o,) = giztop.parse(records, lambda *_: None)
        self.assertEqual((o.price_eur, o.price_orig, o.currency_orig), (1350.0, 1500, "USD"))
        self.assertEqual(o.shipping_eur, 36.0)
        self.assertIn("rate of 2026-11-15", o.payment_note)

    def test_eur_pages_keep_their_price_and_no_note(self):
        records = [rec("fallback_rate", json.dumps({"currency": "USD", "rate": 0.9, "date": "2026-11-15"})),
                   rec("product", page("EUR", 1350)), rec("shipping", json.dumps(SHIPPING), meta={"currency": "EUR"})]
        (o,) = giztop.parse(records, lambda *_: None)
        self.assertEqual((o.price_eur, o.price_orig, o.shipping_eur, o.payment_note), (1350, 1500.0, 40.0, None))

    def test_usd_page_without_any_rate_is_skipped(self):
        logs = []
        self.assertEqual(giztop.parse([rec("product", page("USD", 1500))], logs.append), [])
        self.assertTrue(any("expected EUR" in m for m in logs))

    def test_failed_switch_stores_the_last_rate_and_warns(self):
        class Raw:
            saved = []

            def save(self, *a, **k):
                self.saved.append(a)

        raw, logs = Raw(), []
        with mock.patch.object(store, "last_rate", return_value=(0.86, "2026-11-15")):
            giztop.save_fallback_rate(raw, TimeoutError("timed out"), False, logs.append)
        self.assertEqual(json.loads(raw.saved[0][2]), {"currency": "USD", "rate": 0.86, "date": "2026-11-15"})
        self.assertTrue(logs[0].startswith("WARNING giztop: currency switch failed"))
        with mock.patch.object(store, "last_rate", return_value=(None, None)), self.assertRaises(RuntimeError):
            giztop.save_fallback_rate(raw, TimeoutError("timed out"), False, logs.append)

    def test_last_rate_from_the_latest_snapshot(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(store, "SNAPSHOTS", Path(d)):
            for run, rate in (("2026-11-14T000000Z", 0.85), ("2026-11-15T000000Z", 0.87)):
                offers = [{"price_eur": round(100 * rate, 2), "price_orig": 100.0, "currency_orig": "USD"}, {"price_eur": 50, "price_orig": None}]
                (Path(d) / run).mkdir()
                with gzip.open(Path(d) / run / "giztop.json.gz", "wt") as f:
                    json.dump({"crawled_at": run[:10] + "T00:00:00+00:00", "offers": offers}, f)
            self.assertEqual(store.last_rate("giztop", "USD"), (0.87, "2026-11-15"))
            self.assertEqual(store.last_rate("wonda", "USD"), (None, None))


if __name__ == "__main__":
    unittest.main()
