"""trinityelectronic.boutir.com — Trinity Electronic, a Hong Kong phone shop on Boutir.

Prices are set in HKD and the cart charges HKD (`buy_currency` HKD even when the site
shows EUR; its EUR display uses its own rate, ~0.3% above the ECB rate in Oct 2026). So,
like geekwills, we take the HKD prices and convert them at the ECB reference rate of the
crawl day. Products and all their variants come from the storefront JSON API
(/apis/storefront/products). Stock is not tracked per variant ("unlimited quantity"):
a product is in stock when the shop lists it under stock_type=in_stock.

One guest cart per crawl (/apis/storefront/orders, step 0, never checked out) gives:
- shipping to the buyer: one international method, "Air Mail" by FedEx Priority / DHL
  Express from Hong Kong, a flat HKD 500 per order (the seller can change it per order);
- the automatic storefront campaign ("Enjoy 3% Summer Order Discount", no code), which
  every guest cart gets: included in the price while the cart applies it.
VAT/customs: the shop says nothing (terms, product texts). A phone is over €150, so
import VAT (21% in LT) and the courier's clearance fee are due on delivery: vat
"not_stated", as at wonda."""

from __future__ import annotations

import asyncio
import json
import re

from .. import config, details
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter

NAME = "trinity"
BASE = "https://trinityelectronic.boutir.com"
STORE = "trinityelectronic"
PRODUCTS_API = f"{BASE}/apis/storefront/products"
ORDERS_API = f"{BASE}/apis/storefront/orders"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
JSON = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
PAGE_SIZE = 100
FALLBACK_RATE = 0.11323  # EUR per HKD, ECB 2026-10-16, for raw runs without the ECB file
# Not phones: the shop sells extra shipping as a product ("SHIPPING COST by FedEx/DHL priority").
SKIP_RE = r"shipping cost"
STORAGE_SIZES = {32, 64, 128, 256, 512, 1024, 2048}
# Title fixes so models match the other shops' names (typos, missing series words, word order).
TITLE_TYPOS = [
    (r"^hwawei\b", "Huawei"), (r"^tenco\b", "Tecno"), (r"^mi (?=\d)", "Xiaomi "), (r"^xiaomi mi\s?(?=\d)", "Xiaomi "),
    (r"^oppo (?=x\d)", "OPPO Find "),  # "OPPO X10 PRO MAX" = Find X10 Pro Max
    (r"\bxfold\s*(?=\d)", "X Fold "), (r"\bmate (\d+)rs\b", r"Mate \1 RS"), (r"\bx90pro\b", "X90 Pro"),
    (r"^porsche design\s*\|?\s*(honor .*)$", r"\1 Porsche Design"),
    (r"\s*\(?\bready (?:stock )?to ship\b\)?", ""),
]


async def _list(client: BrowserClient, raw: RawWriter, kind: str, **params) -> list[dict]:
    """Every product of the storefront listing (one variant each), following pages."""
    out: list[dict] = []
    page = 0
    while True:
        r = await client.get(PRODUCTS_API, params={**params, "limit": PAGE_SIZE, "page": page, "lang": "en"}, headers=JSON)
        raw.save(kind, str(r.url), r.text, r.status_code, meta={"page": page, **params})
        data = r.json()
        out += data["products"]
        if not data.get("has_more") or not data["products"]:
            return out
        page += 1


async def quote_cart(client: BrowserClient, raw: RawWriter, product: dict, log) -> None:
    """Guest cart with one unit of `product` (not checked out): shipping options + campaigns."""
    body = {"step": 0, "line_items": [{"product_id": product["id"], "variant_id": product["variants"][0]["id"], "quantity": 1, "type": "order"}],
            "customer": {"language": "en", "timezone": "Europe/Vilnius"}}
    r = await client.post(f"{ORDERS_API}?s={STORE}", json=body, headers={**JSON, "Content-Type": "application/json"})
    raw.save("cart", ORDERS_API, r.text, r.status_code, meta={"product_id": product["id"]})
    d = r.json()
    if "order" not in d:
        log(f"trinity: cart quote failed: {(d.get('meta') or {}).get('error_message')}")
        return
    opts = ", ".join(f"{o['shipping_option']} ({o.get('courier_name') or '-'}) HKD {o['option_price']}" for o in d.get("store_shipping_options") or [])
    log(f"trinity: cart for {d.get('customer_country_code')} in {d['order']['currency']}: {opts}; discount HKD {d['order'].get('discount')}")


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    try:
        raw.save("ecb", ECB_URL, (await client.get(ECB_URL)).text)
    except Exception as e:
        log(f"trinity: ECB rate not fetched ({e}), using FALLBACK_RATE")
    listed = await _list(client, raw, "listing")
    in_stock = await _list(client, raw, "listing_in_stock", stock_type="in_stock")
    log(f"trinity: {len(listed)} products ({len(in_stock)} in stock), fetching product details")

    async def one(p: dict) -> None:
        try:
            r = await client.get(f"{PRODUCTS_API}/{p['id']}", params={"lang": "en"}, headers=JSON)
            raw.save("product", p["product_url"], r.text, r.status_code, meta={"id": p["id"]})
        except Exception as e:  # one broken product must not kill the crawl
            log(f"trinity: skip {p['product_url']}: {e}")

    await asyncio.gather(*(one(p) for p in listed))
    phone = next((p for p in in_stock if not re.search(SKIP_RE, p["title"], re.I) and p.get("variants")), None)
    if phone:
        try:
            await quote_cart(client, raw, phone, log)
        except Exception as e:
            log(f"trinity: cart quote failed: {e}")


def ecb_rate(records: list[dict]) -> tuple[float | None, str | None]:
    """(EUR per HKD, ECB reference date) from the crawl's ECB daily file."""
    for r in records:
        if r["kind"] == "ecb":
            hkd = re.search(r"currency='HKD' rate='([\d.]+)'", r["body"])
            day = re.search(r"time='([\d-]+)'", r["body"])
            if hkd:
                return round(1 / float(hkd.group(1)), 6), day.group(1) if day else None
    return None, None


def cart_terms(records: list[dict]) -> tuple[float | None, str | None, float, str | None]:
    """(shipping HKD, method, automatic campaign discount as a fraction, campaign name) from the cart quote."""
    for r in records:
        if r["kind"] != "cart":
            continue
        try:
            d = json.loads(r["body"])
        except ValueError:
            return None, None, 0.0, None
        if "order" not in d:
            return None, None, 0.0, None
        # The courier method; "Store Pickup" is in Hong Kong.
        ship = next((o for o in d.get("store_shipping_options") or [] if o.get("enabled") and o.get("courier_name")), None)
        o = d["order"]
        pct = (o.get("discount") or 0) / o["subtotal"] if o.get("subtotal") else 0.0
        names = ", ".join(c.get("name") or c.get("kind") for c in o.get("campaigns") or []) or None
        method = f"{ship['shipping_option']} ({re.sub(r'\s*,\s*', ' / ', ship['courier_name'].strip())})" if ship else None
        return (float(ship["option_price"]) if ship else None), method, round(pct, 4), names
    return None, None, 0.0, None


def option_value(v: dict) -> str:
    """The variant's option values as one string, with a space between a size and a colour
    ("16+512white")."""
    return re.sub(r"(\d)([a-z]{3,})", r"\1 \2", " ".join(o["value"] for o in v["options"]), flags=re.I).strip()


def memory_text(value: str) -> str:
    """'12+256 black' -> '12GB+256GB black', '256 purple' -> '256GB purple': the shop writes
    sizes without units. '12GB RAM | 512GB ROM' -> '12GB RAM 512GB'."""
    value = re.sub(r"\s*\|\s*|\bROM\b", " ", value, flags=re.I)
    value = re.sub(r"\b(\d{1,2})\s*\+\s*(\d{2,4})(?!\d)(?!\s*(?:gb?|tb?)\b)", r"\1GB+\2GB", value, flags=re.I)
    value = re.sub(r"(?<![+\d])\b([12])\s*T\b", r"\1TB", value, flags=re.I)  # "1T blue"
    return re.sub(r"(?<![+\d])\b(64|128|256|512)\b(?!\s*(?:gb?|tb?)\b)", r"\1GB", value, flags=re.I)


def clean_title(title: str) -> str:
    """Brand typos in the shop's titles ("Hwawei Pura 80", "TENCO PHANTOM", "mi 15 ultra")."""
    title = re.sub(r"\s+", " ", title).strip()
    for typo, brand in TITLE_TYPOS:
        title = re.sub(typo, brand, title, flags=re.I)
    return title


def color_text(value: str) -> str | None:
    """The colour part of an option value: '12+512 titan grey' -> 'titan grey'."""
    c = re.sub(r"\b\d{1,2}\s*(gb|g)?\s*[+/]\s*\d{1,4}\s*(gb|g|tb|t)?\b|\b\d{1,4}\s*(gb|tb|t)?\b", " ", value, flags=re.I)
    c = re.sub(r"\b(ram|rom|storage|memory)\b|\|", " ", c, flags=re.I)  # "12GB RAM | 512GB ROM (Black)"
    c = re.sub(r"\s+", " ", c).strip(" -/,()")
    return c if c and re.search(r"[a-z]", c, re.I) and not re.search(r"\d", c) else None


def variant_in_stock(v: dict) -> bool:
    return bool(v.get("unlimited_quantity") or (v.get("quantity") or 0) > 0 or (v.get("backorder_limit") or 0) > 0)


def parse_product(p: dict, listed_in_stock: bool) -> list[Offer]:
    title = clean_title(p["title"])
    if re.search(SKIP_RE, title, re.I):
        return []
    text = p.get("html_description") or (p.get("description") or "").replace("\n", "<br>")
    info = details.extract(text)
    offers = []
    for v in p["variants"]:
        value = option_value(v)
        o = make_offer(
            site=NAME,
            title=title,
            memory_text=memory_text(value),
            color=color_text(value),
            info=info,
            price_eur=v["price"],  # HKD here; converted in parse()
            in_stock=listed_in_stock and variant_in_stock(v),
            url=p["product_url"],
            attrs={o["name"].strip().lower(): o["value"].strip() for o in v["options"]},
        )
        if o.storage_gb is not None and o.storage_gb not in STORAGE_SIZES:  # typos: "16+52 Olive", "12+513GB"
            o.storage_gb = None
        # Camera kits, lenses, pens: no RAM/storage anywhere.
        if o.storage_gb is None and re.search(r"\b(kit|lens|set|pen|stylus|keyboard)\b", title, re.I):
            o.device_type = "accessory"
        offers.append(o)
    return offers


def parse(records: list[dict], log) -> list[Offer]:
    rate, day = ecb_rate(records)
    if rate is None:
        rate, day = FALLBACK_RATE, "fallback"
    ship_hkd, ship_method, discount, campaign = cart_terms(records)
    ship_eur = round(ship_hkd * rate, 2) if ship_hkd is not None else None
    log(f"trinity: ECB rate {rate} EUR per HKD ({day}); shipping to {config.COUNTRY}: "
        + (f"{ship_method} HKD {ship_hkd:g} = €{ship_eur}" if ship_hkd is not None else "not quoted, using config")
        + (f"; automatic discount {discount:.1%} ({campaign})" if discount else ""))
    in_stock = {p["id"] for r in records if r["kind"] == "listing_in_stock" for p in json.loads(r["body"])["products"]}
    products = [json.loads(r["body"]) for r in records if r["kind"] == "product" and r.get("status", 200) == 200]
    offers: list[Offer] = []
    note = f"Charged in HKD; EUR at the ECB rate of {day}"
    for p in products:
        try:
            got = parse_product(p, p["id"] in in_stock)
        except Exception as e:
            log(f"trinity: cannot parse {p.get('product_url')}: {e}")
            continue
        off = discount if p.get("campaign_handling") == 0 else 0.0  # 1: excluded from campaigns (checked in the cart)
        for o in got:
            hkd = round(o.price_eur * (1 - off), 2)
            o.price_orig, o.currency_orig = hkd, "HKD"
            o.price_eur = round(hkd * rate, 2)
            o.payment_note = note + (f"; includes the automatic {off:.0%} '{campaign}' discount (cart)" if off else "")
            apply_shipping(o, config.SHIPPING[NAME], ship_eur, ship_method, delivers=True if ship_eur is not None else None)
        offers += got
    return offers
