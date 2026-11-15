import json
import unittest

from phones.sites import mobilemarvel as mm

MARKET = 'Shopify.currency = {"active":"EUR","rate":"0.1179273"};\nShopify.country = "LT";'


def variant(vid, o1, o2, eur, available=True):
    return {"id": vid, "option1": o1, "option2": o2, "price": eur, "available": available, "sku": None}


PHONE = {"id": 1, "title": "Xiaomi 18 Pro Global version", "handle": "xiaomi-18-pro-global-version", "vendor": "The Mobile Marvel",
         "tags": ["smartphones", "global version"], "options": [{"name": "Color"}, {"name": "Ram+Storage"}],
         "body_html": "<p>Starts shipping on 20th November</p>",
         "variants": [variant(11, "White", "12GB+256GB", "1026.95"), variant(12, "Black", "16GB RAM + 1TB", "1250.95", False)]}
USED = {"id": 2, "title": "RedMagic 11S Pro Chinese version Used", "handle": "rm-used", "vendor": "The Mobile Marvel",
        "tags": ["smartphones", "used"], "options": [{"name": "Color"}, {"name": "Storage/RAM"}],
        "body_html": "<p>USED Silver (has global rom installed on it)</p>", "variants": [variant(21, "Silver", "16GB+512GB", "700.95")]}
CN = {"id": 3, "title": "Redmagic 12 Pro+ Chinese version", "handle": "rm12", "vendor": "The Mobile Marvel", "tags": ["smartphones"],
      "options": [{"name": "Ram+Strorage"}, {"name": "Color"}],
      "body_html": "<p>This is with chinese rom not global rom</p><p>Google services can be pre-installed before shipping</p>",
      "variants": [variant(31, "16GB+1TB Collector's Edition", "Black", "1300.95")]}
BASE = {"products": [{**p, "variants": [{**v, "price": str(round(float(v["price"]) / 0.1179273))} for v in p["variants"]]} for p in (PHONE, USED, CN)]}
RATES = {"shipping_rates": [{"name": "Express (FedEx, Duties & Taxes Unpaid)", "price": "45.00"},
                            {"name": "EU Tax‑Included Shipping (Duties & VAT Included) 6-12 Days", "price": "53.95"}]}
CART = {"currency": "EUR", "items": [{"original_line_price": 102695, "discounts": [{"amount": 2053, "title": "Summer Sale 2% Off"}]}]}


def records(rates=RATES, cart=CART):
    out = [{"kind": "products_base", "url": f"{mm.BASE}/collections/smartphones/products.json?limit=250&page=1&country=HK&currency=HKD", "body": json.dumps(BASE)},
           {"kind": "market", "url": "x", "body": MARKET},
           {"kind": "products", "url": "x", "body": json.dumps({"products": [PHONE, USED, CN]})}]
    if rates is not None:
        out.append({"kind": "shipping", "url": "x", "body": json.dumps(rates)})
    if cart is not None:
        out.append({"kind": "cart", "url": "x", "body": json.dumps(cart)})
    return out


class MobileMarvelTest(unittest.TestCase):
    def test_market_prices_with_the_automatic_discount(self):
        a, b, used, cn = mm.parse(records(), lambda *_: None)
        self.assertEqual((a.price_eur, a.price_orig, a.currency_orig), (round(1026.95 * 0.98, 2), round(8708 * 0.98, 2), "HKD"))
        self.assertIn("2% 'Summer Sale 2% Off'", a.payment_note)
        self.assertEqual((a.model_key, a.ram_gb, a.storage_gb, a.color, a.edition, a.ships_from), ("xiaomi 18 pro", 12, 256, "White", "global", "hk"))
        self.assertEqual((b.ram_gb, b.storage_gb, b.in_stock), (16, 1024, False))
        self.assertEqual((used.condition, used.edition, used.ram_gb, used.storage_gb), ("used", "cn_global_rom", 16, 512))
        # "not global rom" stays CN; the memory option comes first; the edition text becomes part of the model
        self.assertEqual((cn.edition, cn.ram_gb, cn.storage_gb, cn.color, cn.google_play), ("cn", 16, 1024, "Black", True))
        self.assertEqual(cn.model_key, "red magic 12 pro plus collector s")

    def test_ddp_shipping_even_when_another_rate_is_cheaper(self):
        a = mm.parse(records(), lambda *_: None)[0]
        self.assertEqual((a.shipping_eur, a.vat, a.delivers, a.shipping_estimated), (53.95, "shop", True, False))
        self.assertIn("Duties & VAT Included", a.shipping_method)

    def test_no_ddp_rate_or_no_cart(self):
        a = mm.parse(records(rates={"shipping_rates": RATES["shipping_rates"][:1]}, cart=None), lambda *_: None)[0]
        self.assertEqual((a.shipping_eur, a.shipping_estimated, a.delivers), (53.95, True, None))  # config fallback
        self.assertEqual((a.price_eur, a.payment_note), (1026.95, None))

    def test_wrong_market_is_an_error(self):
        recs = records()
        recs[1] = {"kind": "market", "url": "x", "body": MARKET.replace('"LT"', '"HK"').replace("EUR", "HKD")}
        with self.assertRaises(RuntimeError):
            mm.parse(recs, lambda *_: None)


if __name__ == "__main__":
    unittest.main()
