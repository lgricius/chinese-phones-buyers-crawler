import json
import unittest

from phones.sites import gshopper

URL = "https://www.gshopper.com/global-version-poco-f8-ultra.html"


def price(v, cur="EUR"):
    return {"minimum_price": {"final_price": {"value": v, "currency": cur}}}


def variant(color, mem, warehouse, value, stock="IN_STOCK"):
    return {"attributes": [{"code": "color", "label": color}, {"code": "type", "label": mem}, {"code": "warehouse", "label": warehouse}],
            "product": {"sku": f"MP1-{mem}-{color}", "stock_status": stock, "price_range": price(value)}}


ITEM = {
    "__typename": "ConfigurableProduct", "id": 713014, "sku": "MP1", "name": "Global Version POCO F8 Ultra",
    "url_key": "global-version-poco-f8-ultra", "url_suffix": ".html", "stock_status": "IN_STOCK", "price_range": price(616.0),
    "short_description": {"html": "<p><strong>Coupon Code: </strong>GPPOCO100</p>"},
    # escaped spec block, as some gshopper descriptions have it
    "description": {"html": "<p>&lt;strong&gt;Platform:&lt;/strong&gt; Snapdragon® 8 Elite Gen 5 Mobile Platform&lt;br&gt;</p>"},
    "options": [],
    "configurable_options": [{"attribute_code": "color", "label": "Color"}, {"attribute_code": "type", "label": "Type"},
                             {"attribute_code": "warehouse", "label": "Warehouse"}],
    "variants": [variant("Black", "16G+512GB", "Spain", 616.0), variant("Black", "16G+1TB", "Hong Kong", 704.0, "OUT_OF_STOCK")],
}
SHIP = {"status": True, "data": {"country_code": "LT", "verify_product_is_ship": 1,
        "shipment_data": {"title": "Standard Shipping", "amount": "Free Shipping", "time": "3-7 Business Days"}}}
RATE = {"base_currency_code": "USD", "exchange_rates": [{"currency_to": "EUR", "rate": 0.88}, {"currency_to": "GBP", "rate": 0.76}]}


def records(ship=SHIP):
    page = {"data": {"products": {"total_count": 1, "page_info": {"current_page": 1, "total_pages": 1}, "items": [ITEM]}}}
    return [{"kind": "currency", "url": gshopper.CURRENCY_URL, "body": json.dumps(RATE)},
            {"kind": "graphql", "url": gshopper.GRAPHQL, "body": json.dumps(page)},
            {"kind": "shipto", "url": URL, "body": json.dumps(ship)}]


class GshopperTest(unittest.TestCase):
    def test_variants_from_graphql(self):
        a, b = gshopper.parse(records(), lambda *_: None)
        self.assertEqual((a.url, a.price_eur, a.price_orig, a.currency_orig), (URL, 616.0, 700.0, "USD"))
        self.assertEqual((a.ram_gb, a.storage_gb, a.color, a.edition, a.sku), (16, 512, "Black", "global", "MP1"))
        self.assertEqual((a.ships_from, b.ships_from), ("eu", "hk"))
        self.assertEqual((a.in_stock, b.in_stock), (True, False))  # stock per variant
        self.assertEqual((a.shipping_eur, a.vat, a.delivers), (0.0, "included", True))
        self.assertEqual(a.chipset, "Snapdragon 8 Elite Gen 5")
        self.assertEqual((a.coupon_code, a.coupon_eur), ("GPPOCO100", 88.0))

    def test_not_shippable_to_buyer_country(self):
        no = {"data": {**SHIP["data"], "verify_product_is_ship": 0}}
        offers = gshopper.parse(records(no), lambda *_: None)
        self.assertTrue(all(not o.in_stock and o.delivers is False for o in offers))

    def test_old_storefront_raw_runs_are_reported(self):
        with self.assertRaisesRegex(RuntimeError, "old Magento storefront"):
            gshopper.parse([{"kind": "product", "url": URL, "body": "<html>"}], lambda *_: None)


if __name__ == "__main__":
    unittest.main()
