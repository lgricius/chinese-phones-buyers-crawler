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
SALE = {"name": "11-11 Crazy Sale", "status": "active", "type": "SelectedMemberDiscountCampaign", "target_type": "all_customer",
        "discount_type": "single_discount", "discount_amount": 0.05, "cash_coupon": None, "has_min_spend_amount": True,
        "min_spend_amount": 10000.0, "started_at": "2026-11-09T16:00:00Z", "ended_at": "2026-11-19T15:59:59Z",
        "excluded_product_ids": [], "excluded_category_ids": [], "excluded_hashtags": []}
MEMBERS = {**SALE, "name": "VIP", "target_type": "selected_members", "discount_amount": 0.2}
CART = {"customer_country_code": "LT", "buy_currency": "HKD",
        "store_shipping_options": [{"enabled": True, "courier_name": "FEDEX Piority , DHL Express", "option_price": "500.0", "shipping_option": "Air Mail", "delivery_areas": []},
                                   {"enabled": True, "courier_name": "DHL/UPS/FEDEX US UK", "option_price": "400.0", "shipping_option": "Air Mail",
                                    "delivery_areas": [{"country_code": "GB"}, {"country_code": "US"}]},
                                   {"enabled": True, "option_price": "0", "shipping_option": "Store Pickup"}],
        "all_can_see_campaigns": [SALE, MEMBERS],
        "order": {"currency": "HKD", "created_at": "2026-11-12T19:02:21Z", "subtotal": 11800.0, "discount": 590.0, "campaigns": [{"name": "11-11 Crazy Sale"}]}}
# Raw runs from before the campaign list was read (Oct 2026): the cart's own discount.
OLD_CART = {"store_shipping_options": CART["store_shipping_options"][:1],
            "order": {"currency": "HKD", "subtotal": 8100.0, "discount": 243.0, "campaigns": [{"name": "summer discount"}]}}


def records(cart=CART):
    out = [{"kind": "ecb", "url": trinity.ECB_URL, "body": ECB},
           {"kind": "listing_in_stock", "url": "x", "body": json.dumps({"products": [{"id": "1"}, {"id": "2"}], "has_more": False})}]
    out += [{"kind": "product", "url": p["product_url"], "body": json.dumps(p)} for p in (PHONE, KIT, FEE)]
    if cart:
        out.append({"kind": "cart", "url": trinity.ORDERS_API, "body": json.dumps(cart)})
    return out


class TrinityTest(unittest.TestCase):
    def test_hkd_at_ecb_rate_with_the_campaign_rules(self):
        a, b, kit = trinity.parse(records(), lambda *_: None)
        rate = round(1 / 8.8316, 6)
        # HKD 8100 is under the HKD 10000 minimum spend: no discount; the members-only 20% is not for guests
        self.assertEqual((a.price_orig, a.currency_orig, a.price_eur), (8100.0, "HKD", round(8100.0 * rate, 2)))
        self.assertEqual((b.price_orig, b.price_eur), (11210.0, round(11210.0 * rate, 2)))  # 11800 - 5%
        self.assertIn("5% '11-11 Crazy Sale' discount (no code, until 2026-11-19)", b.payment_note)
        self.assertNotIn("discount", a.payment_note)
        self.assertEqual((a.ram_gb, a.storage_gb, a.color, a.edition, a.ships_from), (12, 256, "Black", "cn", "hk"))
        self.assertEqual((b.ram_gb, b.storage_gb, b.color, b.in_stock), (16, 1024, "Blue", False))  # tracked stock at 0
        # the worldwide HKD 500 option: the HKD 400 one is GB/US only
        self.assertEqual((a.shipping_eur, a.vat, a.delivers), (round(500 * rate, 2), "not_stated", True))
        self.assertEqual(a.shipping_method, "Air Mail (FEDEX Piority / DHL Express)")
        # campaign_handling 1: excluded from the campaign; no RAM/storage + "kit": accessory
        self.assertEqual((kit.price_orig, kit.device_type), (2100.0, "accessory"))

    def test_campaign_exclusions(self):
        c = trinity.cart_campaigns(records())
        self.assertEqual([x["name"] for x in c], ["11-11 Crazy Sale"])
        v = {"price": 12000.0, "original_price": 12000.0}
        p = {"id": "9", "campaign_handling": 0, "category_ids": ["C1"], "hashtags": ["Sale"]}
        self.assertEqual(trinity.best_campaign(p, v, c)["pct"], 0.05)
        self.assertIsNone(trinity.best_campaign(p, {**v, "original_price": 13000.0}, c))  # already discounted
        self.assertIsNone(trinity.best_campaign({**p, "campaign_handling": 1}, v, c))
        self.assertIsNone(trinity.best_campaign(p, v, [{**c[0], "excluded_hashtags": {"sale"}}]))
        ended = {**CART, "order": {**CART["order"], "created_at": "2026-11-20T00:00:00Z"}}
        self.assertEqual(trinity.cart_campaigns(records(ended)), [])

    def test_old_cart_discount(self):
        a = trinity.parse(records(OLD_CART), lambda *_: None)[0]
        self.assertEqual(a.price_orig, 7857.0)
        self.assertIn("3% 'summer discount'", a.payment_note)

    def test_no_courier_to_country(self):
        cart = {**CART, "store_shipping_options": CART["store_shipping_options"][1:]}
        a = trinity.parse(records(cart), lambda *_: None)[0]
        self.assertEqual((a.delivers, a.shipping_eur), (False, None))

    def test_without_cart_quote(self):
        a = trinity.parse(records(cart=None), lambda *_: None)[0]
        self.assertEqual(a.price_orig, 8100.0)
        self.assertEqual(a.shipping_eur, trinity.config.SHIPPING["trinity"]["eur"])  # config fallback
        self.assertIsNone(a.delivers)

    def test_option_text(self):
        from phones import normalize as n
        for value, mem, color in [("12+256 black", (12, 256), "black"), ("256 purple", (None, 256), "purple"),
                                  ("1T blue", (None, 1024), "blue"), ("12GB RAM | 512GB ROM (Black)", (12, 512), "Black")]:
            self.assertEqual((n.parse_memory(trinity.memory_text(value)), trinity.color_text(value)), (mem, color), value)
        self.assertEqual(trinity.clean_title("OPPO X10 PRO MAX china version"), "OPPO Find X10 PRO MAX china version")
        self.assertEqual(trinity.clean_title("Porsche Design | Honor magic 6 rsr"), "Honor magic 6 rsr Porsche Design")
        for title, clean in [("OnePlus 15 Global Edition (16-November start shipping ）", "OnePlus 15 Global Edition"),
                             ("VIVO X300 China version（ship on 17-October）", "VIVO X300 China version"),
                             ("VIVO X300 Global Edition (14 Nov ship）", "VIVO X300 Global Edition")]:
            self.assertEqual(trinity.clean_title(title), clean)


if __name__ == "__main__":
    unittest.main()
