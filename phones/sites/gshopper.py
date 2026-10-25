"""gshopper.com — Magento 2 marketplace (Singapore company). Since 2026-10 the storefront
is a Next.js app (no Magento markup left), but Magento still runs behind it: products come
from its GraphQL API, priced in EUR via the Content-Currency header (base currency USD;
the shop's rate from /rest/V1/directory/currency). Configurable products have one child
per warehouse x RAM/storage x colour, each with its own price and stock.

Ship To matters: the destination country is stored in the session
(ship/country/update/...), and per product ship/country/index/... says whether
it can be shipped there plus the shipping options and delivery time. Item prices
are the same for every EU country and include VAT ("Inclusive of VAT"). The
bank-transfer discount is marketplace-wide (config.py).

Discount codes are in USD (the base currency) and are applied by rule, without cart
requests: cart-wide codes from config.COUPON_RULES (gshopper150: $150 off orders over
$1000, also on products that do not mention it), else the code printed in the product
description, worth the number in its name. Checked in the cart on 2026-10-10, see README."""

from __future__ import annotations

import asyncio
import html
import json
import re

from .. import config, details
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter
from . import magento

NAME = "gshopper"
BASE = "https://www.gshopper.com"
CATEGORY_PATH = "phones-telecommunications/mobile-phones"
GRAPHQL = f"{BASE}/graphql"
CURRENCY_URL = f"{BASE}/rest/V1/directory/currency"
XHR = {"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"}
CODE_RE = r"(?:coupon|discount)\s*code\s*[:：]\s*([A-Za-z0-9_-]{4,})"
FALLBACK_RATE = 0.88  # EUR per USD in the shop on 2026-10-10 (cart: $150 off = €132), for raw runs without a rate record
PAGE_SIZE = 50

_PRICE = "price_range { minimum_price { final_price { value currency } } }"
CATEGORY_QUERY = "query($path: String) { categoryList(filters: {url_path: {eq: $path}}) { id name product_count } }"
PRODUCTS_QUERY = """query($cat: String, $page: Int, $size: Int) {
  products(filter: {category_id: {eq: $cat}}, pageSize: $size, currentPage: $page) {
    total_count page_info { current_page total_pages }
    items {
      __typename id sku name url_key url_suffix stock_status %(price)s
      description { html } short_description { html }
      ... on CustomizableProductInterface { options { title required } }
      ... on ConfigurableProduct {
        configurable_options { attribute_code label }
        variants { attributes { code label } product { sku stock_status %(price)s } }
      }
    }
  }
}""" % {"price": _PRICE}

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


def shop_rate(records: list[dict]) -> float | None:
    """EUR per USD as used by the shop (base currency USD), from /rest/V1/directory/currency."""
    for r in records:
        if r["kind"] == "currency":
            try:
                rates = json.loads(r["body"]).get("exchange_rates") or []
            except ValueError:
                return None
            return next((float(x["rate"]) for x in rates if x.get("currency_to") == config.CURRENCY), None)
    return None


def product_url(item: dict) -> str:
    return f"{BASE}/{item['url_key']}{item.get('url_suffix') or '.html'}"


def items(records: list[dict]) -> list[dict]:
    """Products of the stored GraphQL pages, once each."""
    out: dict[int, dict] = {}
    for r in records:
        if r["kind"] == "graphql":
            for it in json.loads(r["body"])["data"]["products"]["items"]:
                out.setdefault(it["id"], it)
    return list(out.values())


def _price(p: dict) -> tuple[float | None, str | None]:
    fp = ((p.get("price_range") or {}).get("minimum_price") or {}).get("final_price") or {}
    return fp.get("value"), fp.get("currency")


def variants(item: dict) -> list[dict]:
    """Every purchasable variant: magento.variants() dicts plus 'in_stock'."""
    title = item["name"].strip()
    if item.get("variants"):
        labels = {o["attribute_code"]: o["label"].lower() for o in item.get("configurable_options") or []}
        out = []
        for v in item["variants"]:
            price, _ = _price(v["product"])
            if price is None:
                continue
            values = {labels.get(a["code"], a["code"]): a["label"] for a in v["attributes"]}
            out.append({**magento.configured_variant(values, price), "in_stock": v["product"].get("stock_status") == "IN_STOCK"})
        return out
    price, _ = _price(item)
    return [] if price is None else [{**magento.single_variant(title, price), "in_stock": True}]


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


async def graphql(client: BrowserClient, query: str, variables: dict) -> dict:
    r = await client.post(
        GRAPHQL,
        json={"query": query, "variables": variables},
        headers={"Content-Type": "application/json", "Accept": "application/json", "Content-Currency": config.CURRENCY},
    )
    data = r.json()
    if data.get("errors"):
        raise RuntimeError(f"gshopper graphql: {data['errors'][0].get('message')}")
    return {"text": r.text, "status": r.status_code, "data": data["data"]}


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    r = await client.get(CURRENCY_URL, headers=XHR)
    raw.save("currency", CURRENCY_URL, r.text, r.status_code)
    cats = (await graphql(client, CATEGORY_QUERY, {"path": CATEGORY_PATH}))["data"]["categoryList"]
    if not cats:
        raise RuntimeError(f"gshopper: category {CATEGORY_PATH} not found")
    cat = str(cats[0]["id"])
    found: list[dict] = []
    page, pages = 1, 1
    while page <= pages:
        res = await graphql(client, PRODUCTS_QUERY, {"cat": cat, "page": page, "size": PAGE_SIZE})
        raw.save("graphql", GRAPHQL, res["text"], res["status"], meta={"category": cat, "page": page, "currency": config.CURRENCY})
        products = res["data"]["products"]
        pages = products["page_info"]["total_pages"]
        found += products["items"]
        page += 1
    currencies = {_price(it)[1] for it in found} - {None}
    if currencies != {config.CURRENCY}:
        raise magento.WrongCurrency(f"gshopper: expected {config.CURRENCY} prices, got {currencies}")
    log(f"gshopper: {len(found)} products in category {cat}, fetching ship-to data")
    if not found:
        return
    # Ship To: set the buyer country once for the session, then ask per product.
    r = await client.get(f"{BASE}/ship/country/update/country_code/{config.COUNTRY}/product_id/{found[0]['id']}", headers=XHR)
    raw.save("shipto_set", r.url, r.text, r.status_code, meta={"country": config.COUNTRY})

    async def ship(it: dict) -> None:
        u = product_url(it)
        try:
            rr = await client.get(f"{BASE}/ship/country/index/product_id/{it['id']}", headers=XHR)
            raw.save("shipto", u, rr.text, rr.status_code, meta={"product_id": it["id"]})
        except Exception as e:
            log(f"gshopper: no ship-to data for {u}: {e}")

    await asyncio.gather(*(ship(it) for it in found))


def parse_product(item: dict, ship_data: dict | None, rate: float = FALLBACK_RATE) -> list[Offer]:
    _, currency = _price(item)
    if currency not in (None, config.CURRENCY):
        raise magento.WrongCurrency(f"gshopper: expected {config.CURRENCY}, got {currency} for {item['url_key']}")
    url = product_url(item)
    title = item["name"].strip()
    description = (item.get("description") or {}).get("html") or ""
    if "&lt;" in description:  # spec blocks pasted as escaped HTML ("&lt;strong&gt;Platform:&lt;/strong&gt; ...")
        description = html.unescape(description)
    info = details.extract(description)
    can_ship, ship_cost, ship_note, ship_method = shipping(ship_data)
    country = ((ship_data or {}).get("data") or {}).get("country_code")
    if country and country != config.COUNTRY:
        raise RuntimeError(f"gshopper: ship-to is {country}, expected {config.COUNTRY}")
    # "<strong>Coupon Code: </strong>GPPOCO100", mostly in the short description
    page_code = page_coupon(details.html_to_text(((item.get("short_description") or {}).get("html") or "") + "\n" + description))
    in_stock = item.get("stock_status") == "IN_STOCK"
    offers = []
    for v in variants(item):
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
            in_stock=in_stock and v["in_stock"] and can_ship is not False,
            url=url,
            sku=item.get("sku"),
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
    products = items(records)
    if not products and any(r["kind"] == "product" for r in records):
        raise RuntimeError("raw run of the old Magento storefront (before 2026-10-16), not re-parsed; its snapshot is kept")
    log(f"gshopper: {len(products)} products, ship-to data for {len(ship)}")
    rate = shop_rate(records)
    log(f"gshopper: shop rate {round(rate or FALLBACK_RATE, 5)} EUR per USD" + ("" if rate else " (fallback, no rate record)"))
    rate = rate or FALLBACK_RATE
    offers: list[Offer] = []
    for it in products:
        # Required custom options ("Software: Global ROM + €8.76") would multiply the variants;
        # none on gshopper so far, and GraphQL option prices are not in the display currency.
        required = [o["title"] for o in it.get("options") or [] if o.get("required") and re.search(magento.VARIANT_OPTION_RE, o["title"], re.I)]
        if required:
            log(f"gshopper: {product_url(it)}: required options {required} not expanded into variants")
        try:
            offers += parse_product(it, ship.get(product_url(it)), rate)
        except Exception as e:
            log(f"gshopper: cannot parse {product_url(it)}: {e}")
    return offers
