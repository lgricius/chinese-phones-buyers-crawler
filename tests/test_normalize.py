import unittest

from phones import details
from phones import normalize as n
from phones.models import make_offer
from phones.query import parse_storage


class MemoryTest(unittest.TestCase):
    def test_formats_from_all_sites(self):
        cases = {
            "12+512GB": (12, 512),  # wonda
            "12GB - 256GB": (12, 256),  # giztop
            "16GB / 1TB": (16, 1024),
            "Vivo IQOO 16 - 16GB / 1024GB": (16, 1024),  # tradingshenzhen
            "Honor Magic 7 - 12GB/256GB - Snapdragon Elite": (12, 256),  # model number is not RAM
            "Oppo Find N6 - 12GB/256GB - Foldable": (12, 256),
            "12 GB / 256 GB UFS 4.1": (12, 256),  # data sheet
            "16+512G": (16, 512),  # gshopper
            "12G+512GB": (12, 512),
            "16+1T": (16, 1024),
            "Redmi 15 5G": (None, None),  # network, not memory
        }
        for text, expected in cases.items():
            self.assertEqual(n.parse_memory(text), expected, text)


class ModelKeyTest(unittest.TestCase):
    def key(self, title):
        return n.model_key(*n.split_brand(title))

    def test_same_phone_same_key_across_sites(self):
        for titles in [
            ("OPPO Find N6", "Oppo Find N6 5G Dual SIM", "Oppo Find N6 - 12GB/256GB - Foldable", "Oppo Find N6 5G (Global Version) Dual SIM"),
            ("Honor Magic 9 Pro Max", "Honor Magic 9 Pro Max 5G Dual SIM", "Honor Magic 9 Pro Max - 12GB / 256GB"),
            ("vivo iQOO 16", "Vivo IQOO 16 - 16GB / 1024GB"),
            ("Xiaomi Redmi K90 Ultra", "Redmi K90 Ultra - 16GB/256GB"),
            ("HUAWEI HW Pura 80 Ultra Dual SIM", "Huawei Pura 80 Ultra"),
            ("Honor Magic 8 Pro White", "Honor Magic 8 Pro"),
            ("OPPO Find N5 Global Edition-Black-16GB - 512GB", "Oppo Find N5 5G (Global Version) Dual SIM", "OPPO Find N5"),
            ("Realme GT 7 Pro-Grey-12GB - 256GB", "Realme GT 7 Pro"),
            ("OnePlus 15 Global", "Oneplus 15 5G (Global Rom) Dual SIM", "OnePlus 15 - 16GB/1TB"),
        ]:
            self.assertEqual(len({self.key(t) for t in titles}), 1, titles)

    def test_red_magic_is_a_manufacturer(self):
        for title in ("Red Magic 11 Air", "RedMagic 11 Air", "Nubia Red Magic 11 Air"):
            self.assertEqual(n.split_brand(title), ("Red Magic", "11 Air"), title)
        self.assertEqual(n.split_brand("Nubia Z80 Ultra")[0], "Nubia")

    def test_title_colour_and_shipping_notes(self):
        title = "OPPO Find N6 Global Stellar Titanium （ Only US Warehouses）"
        self.assertEqual(self.key(title), "oppo find n6")
        self.assertEqual((n.title_color(title), n.title_ships_from(title)), ("Stellar Titanium", "us"))
        self.assertEqual(self.key("Honor Magic 9 Pro Max"), "honor magic 9 pro max")  # model words are not colours

    def test_pro_max_is_not_pro(self):
        self.assertNotEqual(self.key("Honor Magic 9 Pro Max"), self.key("Honor Magic 9 Pro"))


class EditionTest(unittest.TestCase):
    def offer(self, title, **kw):
        return make_offer(site="x", title=title, price_eur=1, in_stock=True, url="u", **kw)

    def test_statement_ignores_cross_sell(self):
        text = "This is a Chinese Edition, only supports multi-language.\n* Buy OPPO Find N6 Global version, please visit it HERE."
        self.assertEqual(details.detect_statement_edition(text)[0], "cn")

    def test_global_statement(self):
        self.assertEqual(details.detect_statement_edition("Global Version (Hong Kong model)")[0], "global")

    def test_global_rom_is_cn_hardware(self):
        text = "Chinese version : Unsealed and flashed with Global rom (CPH2745)"
        self.assertEqual(details.detect_statement_edition(text)[0], "cn_global_rom")
        self.assertEqual(self.offer("Oneplus 15 5G (Global Rom) Dual SIM").edition, "cn_global_rom")
        self.assertEqual(self.offer("Xiaomi 17 - 12GB/256GB", firmware="Global ROM - EN / DE").edition, "cn_global_rom")

    def test_title_beats_details(self):
        o = self.offer("Oppo Find N6 5G (Global Version)", info={"edition_note": "cn"})
        self.assertEqual((o.edition, o.edition_source), ("global", "title"))

    def test_not_stated_is_cn(self):
        o = self.offer("Samsung Galaxy S26 5G SM-S9420 Dual SIM")
        self.assertEqual((o.edition, o.edition_source), ("cn", "default"))
        o = self.offer("Vivo IQOO 16 - 16GB / 1024GB", firmware="Origin OS (see supported languages)")
        self.assertEqual(o.edition, "cn")

    def test_global_and_cn_are_one_phone(self):
        a, b = self.offer("Oppo Find N6 5G Dual SIM"), self.offer("OPPO Find N6 Global")
        self.assertEqual(a.model_key, b.model_key)
        self.assertNotEqual(a.config_key, b.config_key)


class ConditionShippingTest(unittest.TestCase):
    def test_unsealed(self):
        self.assertEqual(details.detect_condition("Chinese version, unsealed and installed Google play, 100% Brand new."), "new_unsealed")

    def test_title_condition(self):
        o = make_offer(site="x", title="Xiaomi 15 Pro - Used", price_eur=1, in_stock=True, url="u")
        self.assertEqual(o.condition, "used")

    def test_ships_from(self):
        self.assertEqual(details.detect_shipping("In transit\nEU Priority · 2–5 working days")[0], "eu")
        self.assertEqual(details.detect_shipping("Global Priority · 10–14 working days")[0], "cn")
        o = make_offer(site="x", title="Vivo X300 FE - 12GB/512GB - Global HK Edition - EU Warehouse", price_eur=1, in_stock=True, url="u")
        self.assertEqual((o.model_key, o.edition, o.ships_from), ("vivo x300 fe", "global", "eu"))


class DetailsTest(unittest.TestCase):
    def test_spec_table_dl(self):
        h = '<dl class="data-sheet"><dt>RAM</dt><dd>12 GB</dd><dt>Internal Storage</dt><dd>256 GB UFS 4.1</dd><dt>Battery</dt><dd>8800 mAh - 100 W Charge</dd><dt>Google Play Store</dt><dd>Yes (pre-installed)</dd></dl>'
        info = details.extract(h)
        self.assertEqual((info["battery_mah"], info["charging_w"], info["google_play"]), (8800, 100, True))
        self.assertEqual(n.parse_memory(f"{info['spec_ram']} / {info['spec_storage']}"), (12, 256))

    def test_without_google(self):
        self.assertIs(details.extract('<p>Chinese version</p><p>"WITHOUT" Google play store</p>')["google_play"], False)

    def test_device_type(self):
        self.assertEqual(details.device_type("Lenovo Yoga Pad Pro 14.5", None), "tablet")
        self.assertEqual(details.device_type("OPPO Find N6", 8.12), "phone")


class PricingTest(unittest.TestCase):
    def test_wonda_market_and_bank_discount(self):
        from phones.sites import wonda

        page = 'Shopify.country = "LT";\nShopify.currency = {"active":"EUR","rate":"0.896681"};'
        self.assertEqual(wonda.market(page), ("LT", "EUR", 0.896681))

    def test_bank_transfer_wordings(self):
        for text in (
            "Enjoy 4.5% discount by using local bank transfer payment",  # wonda banner
            '<span><b>4.5%<\\/b> Discount for Bank Transfer.<\\/span>',  # gshopper EU terms (escaped JSON)
            "Bank Transfer <span>Get 4.5% off instantly</span>",  # gshopper payment step
        ):
            self.assertEqual(details.bank_transfer_discount(text), 4.5, text)
        self.assertIsNone(details.bank_transfer_discount("SIGN UP & GET 5% OFF. Register your email"))

    def test_bank_price(self):
        # wondamobile Oppo Find N6 Global 16/512 in the LT/EUR market: card 1702.80, bank transfer 1626.17
        from phones import config
        from phones.models import apply_bank_discount

        o = make_offer(site="wonda", title="Oppo Find N6 5G (Global Version) Dual SIM", price_eur=1702.80, in_stock=True, url="u")
        apply_bank_discount([o], config.BANK_TRANSFER_DISCOUNT["wonda"])
        self.assertEqual((o.price_eur, o.price_eur_bank), (1702.80, 1626.17))

    def test_giztop_usd_rate(self):
        from phones.sites import giztop

        box = '<div data-price-box="product-id-{id}"><span data-price-amount="{p}"></span></div>'
        usd = "".join(box.format(id=i, p=p) for i, p in ((1, 1199), (2, 999)))
        eur = "".join(box.format(id=i, p=p) for i, p in ((1, 1049.72), (2, 874.62)))
        rate = giztop.usd_rate([{"kind": "listing_usd", "url": "x", "body": usd}, {"kind": "listing", "url": giztop.CATEGORY + "?p=1", "body": eur}])
        self.assertAlmostEqual(1049.72 / rate, 1199, places=1)


class ColorTest(unittest.TestCase):
    def test_families(self):
        cases = {"Pink": "pink", "Phantom Black": "black", "Rose Gold": "gold", "Turquoise": "blue", "Icyblue": "blue",
                 "Black & White": "two-tone", "Titanium Charcoal": "titanium", "Deuterium Front Transparent": "transparent",
                 "Ling Yun (Special Gray)": "gray"}
        for name, fam in cases.items():
            self.assertEqual(n.color_family(name), fam, name)

    def test_giztop_custom_options(self):
        from phones.sites import magento

        page = (
            '<label class="label" for="select_8681"> <span>Software</span> </label>'
            '<select name="options[8681]" id="select_8681" class=" required product-custom-option"></select>'
            '<label class="label" for="select_9000"> <span>Accessories</span> </label>'
            '<select name="options[9000]" id="select_9000" class=" product-custom-option"></select>'
            '"optionConfig":{"8681":{"1":{"prices":{"finalPrice":{"amount":0}},"name":"China ROM (Original)"},'
            '"2":{"prices":{"finalPrice":{"amount":8.755}},"name":"Global ROM (Flashed)"}},'
            '"9000":{"3":{"prices":{"finalPrice":{"amount":60.4}},"name":"Case"}}}'
        )
        self.assertEqual(magento.custom_options(page), [("Software", [("China ROM (Original)", 0.0), ("Global ROM (Flashed)", 8.755)])])


class GshopperTest(unittest.TestCase):
    def key(self, title):
        return n.model_key(*n.split_brand(title))

    def test_marketplace_titles(self):
        cases = {
            "Global Version POCO M7 Pro 5G": "xiaomi poco m7 pro",
            "【2025 New】Ulefone Armor 34 Pro 5G AI Rugged Phone 25500mAh Up to 32GB+512GB": "ulefone armor 34 pro",
            "CN Version HONOR Magic9 Pro Max": "honor magic 9 pro max",
            "Global Version OPPO Find X9 Ultra-12G+512GB-Tundra Umber": "oppo find x9 ultra",
            "Global Version Xiaomi 17T Pro  12G+1TB Black": "xiaomi 17t pro",
            "Samsung Galaxy S26 Ultra SM-S948B 5G Global Version (Middle East Spec)": "samsung galaxy s26 ultra",
            "CN Version Xiaomi Xiaomi 17 Ultra Leica Edition": "xiaomi 17 ultra leica",
            "CN Version ACE 6T": "oneplus ace 6t",
            "EU Versin OnePlus  Nord CE6": "oneplus nord ce6",
        }
        for title, key in cases.items():
            self.assertEqual(self.key(title), key, title)

    def test_editions(self):
        self.assertEqual(n.title_edition("CN Version Oneplus 15 flashed with the international system OxygenOS"), "cn_global_rom")
        self.assertEqual(n.title_edition("EU Versin OnePlus Nord CE6"), "global")
        o = make_offer(site="x", title="Ulefone Armor X31 Pro 5G Rugged smartphone", price_eur=1, in_stock=True, url="u")
        self.assertEqual((o.edition, o.edition_source), ("global", "brand"))

    def test_shipping_and_warehouse(self):
        from phones.sites import gshopper

        data = {"data": {"country_code": "LT", "verify_product_is_ship": 1, "shipment_data": {
            "title": "Standard Shipping", "amount": "Free Shipping", "time": "10-20 Business Days",
            "self_funded_title": "Fast Delivery(DHL)", "self_funded_amount": "€50.15", "self_funded_time": "5-10 Business Days"}}}
        ok, cost, note, method = gshopper.shipping(data)
        self.assertEqual((ok, cost, method), (True, 0.0, "Standard Shipping, 10-20 Business Days"))
        self.assertIn("DHL", note)
        self.assertEqual([gshopper.warehouse_region(w) for w in ("Hong Kong", "Spain", "China", "USA")], ["hk", "eu", "cn", "us"])


class ShippingTest(unittest.TestCase):
    def test_tradingshenzhen_widget_and_warehouse(self):
        from phones.sites import tradingshenzhen as ts

        self.assertEqual(ts.widget_cost("Estimated delivery | Steps | from €16.00 | Order placed"), 16.0)
        self.assertEqual(ts.widget_cost("Estimated delivery | Steps | Free | Order placed"), 0.0)
        cn = make_offer(site="tradingshenzhen", title="Redmi Turbo 5 12GB/256GB", price_eur=300, in_stock=True, url="u")
        ts.set_shipping(cn, "from €16.00", [])
        self.assertEqual((cn.shipping_eur, cn.shipping_estimated, cn.vat), (16.0, True, "shop"))
        eu = make_offer(site="tradingshenzhen", title="vivo X300 FE 12GB/512GB EU Warehouse", price_eur=500, in_stock=True, url="u")
        ts.set_shipping(eu, "", [])
        self.assertEqual((eu.ships_from, eu.shipping_eur, eu.vat), ("eu", 0.0, "included"))

    def test_tradingshenzhen_delivery_countries(self):
        from phones.sites import tradingshenzhen as ts

        page = '<select name="acodvd[id_country]" class="acodvd-select"><option value="1">Austria</option><option value="125" selected>Latvia</option></select>'
        self.assertEqual(ts.delivery_countries(page), ["Austria", "Latvia"])
        o = make_offer(site="tradingshenzhen", title="vivo X300 FE 12GB/512GB EU Warehouse", price_eur=500, in_stock=True, url="u")
        ts.set_shipping(o, "from €16.00", ts.delivery_countries(page))  # Lithuania not listed
        self.assertEqual((o.delivers, o.shipping_eur, o.vat), (False, None, None))
        ts.set_shipping(o, "from €16.00", ["Latvia", "Lithuania"])
        self.assertEqual((o.delivers, o.shipping_eur), (None, 0.0))

    def test_cart_quotes(self):
        import json

        from phones.sites import giztop, wonda

        quote = [{"method_title": "Registered Post Air Mail (12-25 days)", "amount": 26.17745}, {"method_title": "Express (6-10 days)", "amount": 35.011245}]
        self.assertEqual(giztop.express_shipping([{"kind": "shipping", "body": json.dumps(quote)}]), (35.01, "Express (6-10 days) (EU Priority)"))
        rates = {"shipping_rates": [{"name": "EU Standard - (8-10 Working Days)", "price": "53.80"}]}
        self.assertEqual(wonda.cheapest_rate([{"kind": "shipping", "body": json.dumps(rates)}]), (53.8, "EU Standard - (8-10 Working Days)"))
        self.assertEqual(giztop.express_shipping([]), (None, None))  # old raw runs: config fallback


class ChipsetTest(unittest.TestCase):
    def test_one_name_per_chip(self):
        same = ["3nm Qualcomm Snapdragon 8 Elite Octa-core", "SM8750-AB Snapdragon 8 Elite (3 Nm)", "Snapdragon 8 Elite Soc / 4", "Snapdragon® 8 Elite"]
        self.assertEqual({n.norm_chipset(v) for v in same}, {"Snapdragon 8 Elite"})
        self.assertIsNone(n.norm_chipset("Snapdragon 8 Elite Gen"))  # cut off: unknown, not a wrong chip
        self.assertEqual(n.norm_chipset("Xring 03"), "Xring O3")
        self.assertEqual(n.norm_chipset("Kirin 9030s"), "Kirin 9030S")
        self.assertEqual(n.norm_chipset("Snapdragon 8 Elite Gen 5 Leading"), "Snapdragon 8 Elite Gen 5 Leading Edition")
        self.assertEqual(n.norm_chipset("Snapdragon\ufe0f 8 Elite Gen 5"), "Snapdragon 8 Elite Gen 5")  # emoji variation selector (gshopper)

    def test_shops_agree_per_model_and_edition(self):
        from phones.store import agree_specs

        def o(site, ed, chip, bat=None, model="honor magic 9 pro max"):
            return {"site": site, "edition": ed, "model_key": model, "chipset": chip, "battery_mah": bat}

        offers = [o("giztop", "cn", "Snapdragon 8 Elite Extreme Gen 6", 8800), o("giztop", "cn", "Snapdragon 8 Gen 3", 6000),
                  o("giztop", "cn", "Snapdragon 8 Elite Extreme Gen 6", 8800), o("wonda", "cn_global_rom", "Snapdragon 8 Elite Extreme Gen 6"),
                  o("tradingshenzhen", "cn", "Snapdragon 8 Elite Gen 6", 8800), o("gshopper", "cn", None),
                  o("wonda", "global", "Snapdragon 8 Elite Gen 6", 7100), o("gshopper", "global", None),
                  o("giztop", "cn", "Dimensity 8550", model="honor 600 pro"), o("wonda", "global", None, model="honor 600 pro")]
        agree_specs(offers)
        cn = {x["model_chipset"] for x in offers[:6]}
        self.assertEqual(cn, {"Snapdragon 8 Elite Extreme Gen 6"})  # 2 shops (giztop, wonda) vs 1; stray giztop page outvoted
        self.assertEqual({x["model_battery_mah"] for x in offers[:6]}, {8800})
        self.assertEqual([x["model_chipset"] for x in offers[6:8]], ["Snapdragon 8 Elite Gen 6"] * 2)  # global hardware votes apart
        self.assertEqual(offers[7]["model_battery_mah"], 7100)
        self.assertEqual(offers[9]["model_chipset"], "Dimensity 8550")  # edition nobody states: the model's value


class CouponTest(unittest.TestCase):
    def test_gshopper_codes(self):
        from phones import query as q
        from phones.sites import gshopper as g

        self.assertEqual(g.page_coupon("Save $20 With Coupon Code: gshopper30"), ("gshopper30", 30.0))  # the code name wins
        self.assertEqual(g.page_coupon("Discount code：gshoppr150"), ("gshoppr150", 150.0))
        self.assertEqual(g.best_coupon(1204.72, 0.88, None)[:2], ("gshopper150", 132.0))  # $1369: cart-wide code
        self.assertIsNone(g.best_coupon(703.12, 0.88, None))  # $799: under $1000
        self.assertEqual(g.best_coupon(703.12, 0.88, ("GPPOCO100", 100.0))[:2], ("GPPOCO100", 88.0))
        self.assertEqual(g.best_coupon(1438.8, 0.88, ("GPOPPO100", 100.0))[0], "gshopper150")  # one code per order: the best
        o = {"price_eur": 993.52, "coupon_eur": 132.0, "bank_discount_pct": 4.5, "shipping_eur": 0.0}
        self.assertEqual(q.with_payment(o, bank=False, coupon=True)["price_eur"], 861.52)
        self.assertEqual(q.with_payment(o, bank=True, coupon=True)["price_eur"], 822.75)
        self.assertEqual(q.with_payment(o, bank=False)["price_eur"], 993.52)


class MagentoConfigTest(unittest.TestCase):
    def test_ignores_other_products_configs(self):
        """'Sold together' widgets embed other products' variant JSON before the page's own."""
        from phones.sites import magento

        widget = '"jsonConfig": {"productId":"713635","attributes":{"1":{"id":"1","code":"type","label":"Type","options":[{"id":"9","label":"12G+512GB","products":["5"]}]}},"optionPrices":{"5":{"finalPrice":{"amount":365.2}}},"index":{"5":{"1":"9"}}}'
        page = ('<form id="product_addtocart_form"><input name="product" value="713638"></form>' + widget +
                '<meta itemprop="price" content="1679.92" />')
        v = magento.variants(page, "Global Version OPPO Find X9 Ultra-16G+1TB-Tundra Umber")
        self.assertEqual([(x["price"], x["color"]) for x in v], [(1679.92, "Tundra Umber")])
        self.assertEqual(n.parse_memory(v[0]["mem"]), (16, 1024))


class MagentoPriceTest(unittest.TestCase):
    def test_single_product_uses_its_own_price_box(self):
        """giztop Earth Explorer Master Set: an earlier itemprop="price" belongs to another product."""
        from phones.sites import magento

        page = ('<meta itemprop="price" content="1005.95" />'
                '<form id="product_addtocart_form"><input name="product" value="34693"></form>'
                '<div class="price-box" data-price-box="product-id-34693"><span data-price-amount="1662.57" data-price-type="finalPrice">'
                '<span data-price-amount="2187.87" data-price-type="oldPrice"></div>')
        self.assertEqual(magento.main_price(page), 1662.57)


class StorageArgTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual([parse_storage(x) for x in ("512", "1tb", "1TB", "1024", "1")], [512, 1024, 1024, 1024, 1024])


if __name__ == "__main__":
    unittest.main()
