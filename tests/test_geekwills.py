import json
import unittest

from phones import config
from phones.sites import geekwills

ECB = "<Cube time='2026-10-13'><Cube currency='USD' rate='1.1188'/><Cube currency='JPY' rate='170.1'/></Cube>"
SHIP = [{"carrier_title": "5-8 days", "method_title": "Express", "amount": 29.351, "base_amount": 29.95, "available": True}]
PAGE = """<meta property="og:title" content="ONEPLUS 16"/><meta itemprop="priceCurrency" content="USD"/>
<div class="stock available"><span>In stock</span></div><div itemprop="sku">OP1600</div>
<form id="product_addtocart_form"><input type="hidden" name="product" value="6718"/></form>
<script>{"spConfig": {"productId": "6718", "attributes": {
  "93": {"label": "Color", "options": [{"id": "1", "label": "Black"}]},
  "150": {"label": "Storage", "options": [{"id": "2", "label": "12GB - 256GB"}]}},
  "index": {"501": {"93": "1", "150": "2"}},
  "optionPrices": {"501": {"finalPrice": {"amount": 1039}}}}}</script>
<div class="product attribute overview"><div class="value"><p>* Don't support Google Mobile Service, no Google Play store.</p></div></div>"""


def records(ecb=True, rate=True):
    out = [{"kind": "product", "url": "https://www.geekwills.com/oneplus-16.html", "body": PAGE},
           {"kind": "shipping", "url": "x", "body": json.dumps(SHIP)}]
    if ecb:
        out.append({"kind": "ecb", "url": geekwills.ECB_URL, "body": ECB})
    if rate:
        out.append({"kind": "shop_rate", "url": "x", "body": json.dumps({"base_currency_code": "USD", "quote_currency_code": "EUR", "base_to_quote_rate": "0.9800"})})
    return out


class GeekwillsTest(unittest.TestCase):
    def test_usd_prices_at_the_ecb_rate(self):
        (o,) = geekwills.parse(records(), lambda *_: None)
        self.assertEqual((o.price_orig, o.currency_orig, o.price_eur), (1039.0, "USD", round(1039 / 1.1188, 2)))
        self.assertEqual((o.ram_gb, o.storage_gb, o.color, o.edition), (12, 256, "Black", "cn"))
        self.assertIs(o.google_play, False)
        self.assertEqual((o.shipping_eur, o.shipping_method, o.vat, o.delivers), (round(29.95 / 1.1188, 2), "Express (5-8 days)", "shop", True))
        self.assertIn("0.98, 10% more", o.payment_note)

    def test_fallback_rate(self):
        (o,) = geekwills.parse(records(ecb=False, rate=False), lambda *_: None)
        self.assertEqual(o.price_eur, round(1039 * config.USD_EUR_FALLBACK, 2))
        self.assertNotIn("more", o.payment_note)


if __name__ == "__main__":
    unittest.main()
