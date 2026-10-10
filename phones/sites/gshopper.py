"""gshopper.com — Magento 2 marketplace (Singapore company), swatch variants in
`jsonConfig`: one child per warehouse x RAM/storage x colour, each with its own
price. EUR via the shop's currency switcher.

Ship To matters: the destination country is stored in the session
(ship/country/update/...), and per product ship/country/index/... says whether
it can be shipped there plus the shipping options and delivery time. Item prices
are the same for every EU country and include VAT ("Inclusive of VAT"). The
bank-transfer discount is marketplace-wide (config.py).

Discount codes are in USD (the base currency) and are applied by rule, without cart
requests: cart-wide codes from config.COUPON_RULES (gshopper150: $150 off orders over
$1000, also on products that do not mention it), else the code printed on the page,
worth the number in its name. Checked in the cart on 2026-10-10, see README."""

from __future__ import annotations

import asyncio
import json
import re

from .. import config, details
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter
from . import magento

NAME = "gshopper"
BASE = "https://www.gshopper.com"
CATEGORY = f"{BASE}/phones-telecommunications/mobile-phones.html"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
CODE_RE = r"(?:coupon|discount)\s*code\s*[:：]\s*([A-Za-z0-9_-]{4,})"
FALLBACK_RATE = 0.88  # EUR per USD in the shop on 2026-10-10 (cart: $150 off = €132), for raw runs without a USD listing

WAREHOUSES = [
    ("hk", r"hong\s*kong|\bhk\b"),
    ("cn", r"china|mainland|shenzhen|\bcn\b"),
    ("us", r"\bus\b|usa|united states|america"),
    ("eu", r"\beu\b|europe|spain|germany|poland|czech|france|italy|netherlands|belgium|hungary|lithuania"),
]


def warehouse_region(name: str | None) -> str | None:
    if not name:
        return None
    return next((code for code, rx in WAREHOUSES if re.search(rx, name, re.I)), None)


def product_id(text: str) -> str | None:
    m = re.search(r'"productShipTo":\s*\{[^}]*?"product_id":\s*(\d+)', text) or re.search(r'name="product" value="(\d+)"', text)
    return m.group(1) if m else None


def europe_countries(text: str) -> set[str]:
    m = re.search(r'"europe_list":\s*"([A-Z,]+)"', text)
    return set(m.group(1).split(",")) if m else set()


def page_codes(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(CODE_RE, text, re.I)))


def page_coupon(text: str) -> tuple[str, float] | None:
    """(code, USD off) printed on the page. The amount is taken from the code's name
    (gshopper30 -> $30): in cart checks it matched every time, the page text did not."""
    for code in page_codes(text):
        m = re.search(r"(\d+)$", code)
        if m:
            return code, float(m.group(1))
    return None


def best_coupon(price_eur: float, rate: float, page: tuple[str, float] | None) -> tuple[str, float, str] | None:
    """(code, EUR off, note) of the best code for a variant: the cart-wide rules in
    config.COUPON_RULES, or the code printed on the product page."""
    usd = round(price_eur / rate, 2)
    options = [(r["code"], r["usd_off"], r["note"]) for r in config.COUPON_RULES.get(NAME, []) if usd > r.get("min_usd", 0)]
    if page:
        options.append((page[0], page[1], "code printed on the product page; amount from the code name, not checked in the cart"))
    if not options:
        return None
    code, off, note = max(options, key=lambda x: x[1])
    return code, round(off * rate, 2), note


def usd_rate(records: list[dict]) -> float | None:
    """EUR per USD as used by the shop (base currency USD), from the category page in both currencies."""
    usd = next((magento.listing_prices(r["body"]) for r in records if r["kind"] == "listing_usd"), {})
    eur = next((magento.listing_prices(r["body"]) for r in records if r["kind"] == "listing"), {})
    ratios = sorted(eur[k] / usd[k] for k in usd.keys() & eur.keys() if usd[k] > 0)
    return ratios[len(ratios) // 2] if ratios else None


def details_html(text: str) -> str:
    i = text.find('id="tab-details"')
    if i < 0:
        return ""
    j = text.find('<div class="tabs-content"', i + 20)
    return text[i : j if j > 0 else i + 80000]


def shipping(data: dict | None) -> tuple[bool | None, float | None, str | None, str | None]:
    """(ships to the buyer country, standard shipping cost EUR, delivery note, method) from ship/country/index."""
    if not data:
        return None, None, None, None
    d = data.get("data") or {}
    sd = d.get("shipment_data") or {}
    ok = d.get("verify_product_is_ship")
    amount = sd.get("amount") or ""
    cost = 0.0 if "free" in amount.lower() else (float(re.sub(r"[^\d.]", "", amount.replace(",", ""))) if re.search(r"\d", amount) else None)
    note = f"{sd.get('title', 'Shipping')} to {d.get('country_code')}: {amount}, {sd.get('time', '')}".strip(", ")
    if sd.get("self_funded_title"):
        note += f"; {sd['self_funded_title']} {sd.get('self_funded_amount', '')} ({sd.get('self_funded_time', '')})"
    method = ", ".join(x for x in (sd.get("title"), sd.get("time")) if x) or None
    return (None if ok is None else bool(int(ok))), cost, note, method


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    before = await magento.switch_currency(client, f"{CATEGORY}?product_list_limit=32", config.CURRENCY)
    if magento.page_currency_symbol(before) == "$":
        raw.save("listing_usd", CATEGORY, before)  # same page in USD -> shop's USD/EUR rate
    urls = await magento.list_products(client, raw, CATEGORY, params="product_list_limit=32")
    log(f"gshopper: {len(urls)} products, fetching product pages")

    async def page(u: str) -> tuple[str, str]:
        try:
            text = (await client.get(u)).text
            raw.save("product", u, text)
            return u, text
        except Exception as e:
            log(f"gshopper: skip {u}: {e}")
            return u, ""

    pages = await asyncio.gather(*(page(u) for u in urls))
    ids = {u: product_id(t) for u, t in pages if product_id(t)}
    if not ids:
        return
    # Ship To: set the buyer country once for the session, then ask per product.
    first = next(iter(ids.values()))
    r = await client.get(f"{BASE}/ship/country/update/country_code/{config.COUNTRY}/product_id/{first}", headers=XHR)
    raw.save("shipto_set", r.url, r.text, r.status_code, meta={"country": config.COUNTRY})

    async def ship(u: str, pid: str) -> None:
        try:
            rr = await client.get(f"{BASE}/ship/country/index/product_id/{pid}", headers=XHR)
            raw.save("shipto", u, rr.text, rr.status_code, meta={"product_id": pid})
        except Exception as e:
            log(f"gshopper: no ship-to data for {u}: {e}")

    await asyncio.gather(*(ship(u, pid) for u, pid in ids.items()))


def parse_product(url: str, text: str, ship_data: dict | None, rate: float = FALLBACK_RATE) -> list[Offer]:
    if magento.page_currency(text) not in (None, "EUR"):
        raise magento.WrongCurrency(f"gshopper: expected EUR, got {magento.page_currency(text)} on {url}")
    title, in_stock, sku = magento.product_basics(text, url)
    info = details.extract(details_html(text))
    can_ship, ship_cost, ship_note, ship_method = shipping(ship_data)
    country = ((ship_data or {}).get("data") or {}).get("country_code")
    if country and country != config.COUNTRY:
        raise RuntimeError(f"gshopper: ship-to is {country}, expected {config.COUNTRY}")
    page_code = page_coupon(text)
    offers = []
    for v in magento.variants(text, title):
        warehouse = next((val for k, val in v["attrs"].items() if "warehouse" in k), None)
        o = make_offer(
            site=NAME,
            title=title,
            memory_text=v["mem"],
            color=v["color"],
            edition_hints=v["versions"],
            firmware=v["versions"][-1] if v["versions"] else None,
            info=info,
            price_eur=v["price"],
            in_stock=in_stock and can_ship is not False,
            url=url,
            sku=sku,
            attrs=v["attrs"],
        )
        o.ships_from = warehouse_region(warehouse) or o.ships_from
        o.delivery_note = (f"Warehouse {warehouse}. " if warehouse else "") + (ship_note or "")
        if can_ship is False:
            o.delivery_note = f"Cannot be shipped to {config.COUNTRY}. " + o.delivery_note
        apply_shipping(o, config.SHIPPING[NAME], ship_cost, ship_method, delivers=can_ship)
        o.price_orig, o.currency_orig = round(o.price_eur / rate, 2), "USD"
        c = best_coupon(o.price_eur, rate, page_code)
        if c:
            o.coupon_code, o.coupon_eur, o.coupon_note = c
            o.price_eur_coupon = round(o.price_eur - o.coupon_eur, 2)
        offers.append(o)
    return offers


def parse(records: list[dict], log) -> list[Offer]:
    ship = {}
    for r in records:
        if r["kind"] == "shipto":
            try:
                ship[r["url"]] = json.loads(r["body"])
            except ValueError:
                pass
    # Shop-wide EU terms ("Inclusive of VAT", "4.5% Discount for Bank Transfer") appear only on some pages.
    products = [r for r in records if r["kind"] == "product"]
    europe = set().union(*(europe_countries(r["body"]) for r in products)) if products else set()
    log(f"gshopper: ship-to data for {len(ship)} products; {config.COUNTRY} in EU list (VAT included): {config.COUNTRY in europe}")
    rate = usd_rate(records)
    log(f"gshopper: shop rate {round(rate or FALLBACK_RATE, 5)} EUR per USD" + ("" if rate else " (fallback, no USD listing)"))
    rate = rate or FALLBACK_RATE
    offers: list[Offer] = []
    for r in products:
        try:
            offers += parse_product(r["url"], r["body"], ship.get(r["url"]), rate)
        except Exception as e:
            log(f"gshopper: cannot parse {r['url']}: {e}")
    return offers
