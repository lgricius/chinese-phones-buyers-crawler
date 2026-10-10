"""Buyer context. Shops price per market (country + currency), so we crawl as a
buyer from this country instead of relying on IP geolocation."""

COUNTRY = "LT"
POSTCODE = "01100"  # Vilnius, for shipping quotes
COUNTRY_NAME = "Lithuania"
CURRENCY = "EUR"

# Bank-transfer discount per marketplace. It applies to every product of that shop
# ("Enjoy 4.5% discount by using bank transfer", "Get 4.5% off instantly"), so it
# is set here once; crawls only warn when the site states a different value.
BANK_TRANSFER_DISCOUNT = {"wonda": 4.5, "gshopper": 4.5}

# Cart-wide discount codes, in the shop's base currency. gshopper150 is printed on only a
# few pages but the cart took it on every phone over $1000 tried (12 of 12, 2026-10-10)
# and refused it below. Re-check in the cart now and then: codes expire.
COUPON_RULES = {"gshopper": [{"code": "gshopper150", "usd_off": 150, "min_usd": 1000, "note": "Save $150 on orders over $1000! (cart-wide code)"}]}

# Shipping to COUNTRY with the method where no import VAT/customs is left for the buyer
# (or, where the shop does not say, its standard method). Rates are flat per shop.
# giztop and wonda are re-quoted from a cart on every crawl; these values are the
# fallback for old raw data. vat: "included" (price incl. EU VAT, shipped within the EU
# or VAT collected by the shop), "shop" (shop pays import VAT/customs, e.g. via IOSS),
# "not_stated" (shop says nothing: import VAT + carrier fee may be due on delivery).
# Shops left out of crawls, the index and the web view (code and stored data are kept;
# remove the entry to bring a shop back, then crawl it).
DISABLED_SITES = {
    "tradingshenzhen": "does not deliver to Lithuania: not in its delivery countries (checked 2026-10-11)",
}

SHIPPING = {
    "giztop": {"eur": 35.01, "method": "Express (EU Priority), 6-10 days", "vat": "shop",
               "vat_note": "VAT/customs paid by Giztop (IOSS) with Express only; Air Mail is declared low and the buyer may pay VAT"},
    "wonda": {"eur": 53.80, "method": "EU Standard, 8-10 working days", "vat": "not_stated",
              "vat_note": "No duty/VAT statement; items are not taxed at checkout, so LT import VAT (21%) + carrier fee may be due on delivery"},
    # Lithuania is not in the shop's delivery countries (checked per crawl from the delivery widget,
    # offers get delivers=False); these apply to delivery countries. Global Priority €15-16 for
    # neighbouring EU countries ("from €10"); EU-warehouse stock ships free.
    "tradingshenzhen": {"eur": 15.0, "estimated": True, "method": "Global Priority, 10-14 days", "vat": "shop",
                        "vat_note": "Global Priority: import VAT/customs covered by the shop"},
    "tradingshenzhen_eu": {"eur": 0.0, "method": "EU warehouse, 2-4 days", "vat": "included", "vat_note": "Ships within the EU"},
    "gshopper": {"eur": None, "method": "Standard", "vat": "included",
                 "vat_note": "EU VAT included in the price and paid by Gshopper"},
}
