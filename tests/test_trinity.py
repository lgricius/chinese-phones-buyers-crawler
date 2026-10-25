import json
import unittest

from phones.sites import trinity

ECB = "<Cube time='2026-10-16'><Cube currency='USD' rate='1.1636'/><Cube currency='HKD' rate='8.8316'/></Cube>"


def variant(vid, value, price, unlimited=True, qty=None):
    return {"id": vid, "options": [{"name": "colour and atorage", "value": value}], "price": price, "original_price": price,
            "unlimited_quantity": unlimited, "quantity": qty, "backorder_limit": 0}


PHONE = {"id": "1", "title": "Xiaomi 18 Pro ", "product_url": "https://trinityelectronic.boutir.com/i/X1", "campaign_handling": 0,
         "description": "-China Version,Ready stock to ship\n\n-google play services preinstall", "html_description": "",
         "variants": [variant("11", "12+256 black ", 8100.0), variant("12", "16+1T blue", 11800.0, unlimited=False, qty=0)]}
KIT = {"id": "2", "title": "VIVO X FOLD6 Camera Kit", "product_url": "https://trinityelectronic.boutir.com/i/X2", "campaign_handling": 1,
       "description": "", "html_description": "", "variants": [variant("21", "black", 2100.0)]}
FEE = {"id": "3", "title": "SHIPPING COST by FedEx/DHL priority", "product_url": "https://trinityelectronic.boutir.com/i/X3",
       "campaign_handling": 0, "description": "", "html_description": "", "variants": [variant("31", "HKD$500", 500.0)]}
CART = {"customer_country_code": "LT", "buy_currency": "HKD",
        "store_shipping_options": [{"enabled": True, "courier_name": "FEDEX Piority , DHL Express", "option_price": "500.0", "shipping_option": "Air Mail"},
                                   {"enabled": True, "option_price": "0", "shipping_option": "Store Pickup"}],
        "order": {"currency": "HKD", "subtotal": 8100.0, "discount": 243.0, "campaigns": [{"name": "summer discount"}]}}


def records(cart=True):
    out = [{"kind": "ecb", "url": trinity.ECB_URL, "body": ECB},
           {"kind": "listing_in_stock", "url": "x", "body": json.dumps({"products": [{"id": "1"}, {"id": "2"}], "has_more": False})}]
    out += [{"kind": "product", "url": p["product_url"], "body": json.dumps(p)} for p in (PHONE, KIT, FEE)]
    if cart:
        out.append({"kind": "cart", "url": trinity.ORDERS_API, "body": json.dumps(CART)})
    return out


class TrinityTest(unittest.TestCase):
    def test_hkd_at_ecb_rate_with_the_automatic_discount(self):
        a, b, kit = trinity.parse(records(), lambda *_: None)
        rate = round(1 / 8.8316, 6)
        self.assertEqual((a.price_orig, a.currency_orig, a.price_eur), (7857.0, "HKD", round(7857.0 * rate, 2)))
        self.assertEqual((a.ram_gb, a.storage_gb, a.color, a.edition), (12, 256, "Black", "cn"))
        self.assertEqual((b.ram_gb, b.storage_gb, b.color, b.in_stock), (16, 1024, "Blue", False))  # tracked stock at 0
        self.assertEqual((a.shipping_eur, a.vat, a.delivers), (round(500 * rate, 2), "not_stated", True))
        self.assertIn("3% 'summer discount'", a.payment_note)
        # campaign_handling 1: excluded from the campaign; no RAM/storage + "kit": accessory
        self.assertEqual((kit.price_orig, kit.device_type), (2100.0, "accessory"))

    def test_without_cart_quote(self):
        a = trinity.parse(records(cart=False), lambda *_: None)[0]
        self.assertEqual(a.price_orig, 8100.0)
        self.assertEqual(a.shipping_eur, 56.61)  # config fallback
        self.assertIsNone(a.delivers)

    def test_option_text(self):
        from phones import normalize as n
        for value, mem, color in [("12+256 black", (12, 256), "black"), ("256 purple", (None, 256), "purple"),
                                  ("1T blue", (None, 1024), "blue"), ("12GB RAM | 512GB ROM (Black)", (12, 512), "Black")]:
            self.assertEqual((n.parse_memory(trinity.memory_text(value)), trinity.color_text(value)), (mem, color), value)
        self.assertEqual(trinity.clean_title("OPPO X10 PRO MAX china version"), "OPPO Find X10 PRO MAX china version")
        self.assertEqual(trinity.clean_title("Porsche Design | Honor magic 6 rsr"), "Honor magic 6 rsr Porsche Design")


if __name__ == "__main__":
    unittest.main()
