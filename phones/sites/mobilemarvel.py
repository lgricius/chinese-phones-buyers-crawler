"""themobilemarvel.com — The Mobile Marvel, a Hong Kong phone shop on Shopify (Shopify Markets).

Prices are set in HKD; the Lithuania/EUR market charges EUR at the shop's own rate (~2.5% over
ECB, rounded up to x.95), so like wonda we set the market (localization + cart_currency cookies)
and take products.json prices as-is, after checking the storefront really is in that market.
The base HKD prices (one extra products.json request before the market is set) are kept as
price_orig, so price trends compare the shop's own prices, not moves of its EUR rate.

One guest cart per crawl (never checked out) gives:
- shipping to the buyer: we use "EU Tax-Included Shipping (Duties & VAT Included)", UPS via the
  shop's EU warehouse, 6-12 days: DDP, VAT/duties prepaid, and the shop refunds 100% of any
  charge asked on delivery (shipping policy). Its other EU option, FedEx Express, is duties
  unpaid and declared low; the cart does not offer it to LT anyway;
- the automatic discount every cart gets, no code ("Summer Sale 2% Off" in Nov 2026; it was
  on a new phone, a used one and a photo kit alike): included in the price while the cart
  applies it, measured from the cart line.
Bank transfer: 4.75% off all orders (config.BANK_TRANSFER_DISCOUNT). No public codes.
The shop rate-limits fast page requests ("local_rate_limited"), so we fetch no product pages:
products.json has every variant, its stock and the description."""

from __future__ import annotations

import json
import re

from .. import config, details
from .. import normalize as n
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter

NAME = "mobilemarvel"
BASE = "https://www.themobilemarvel.com"
DOMAIN = "www.themobilemarvel.com"
COLLECTION = "smartphones"
# The DDP method: VAT and duties prepaid, nothing due on delivery.
DDP_RE = re.compile(r"tax.?included|\bddp\b|duties\s*(?:&|and)\s*(?:vat|taxes)\s*(?:included|paid)", re.I)
# "This is the Chinese version with global rom!", "USED Silver (has global rom installed on it)";
# not "This is with chinese rom not global rom".
GLOBAL_ROM_TEXT_RE = re.compile(r"\b(?:with|has|installed|flashed)\b(?:(?!\bnot\b)[^.!()]){0,20}\bglobal\s*rom\b", re.I)


def set_market(client: BrowserClient) -> None:
    client.session.cookies.set("localization", config.COUNTRY, domain=DOMAIN)
    client.session.cookies.set("cart_currency", config.CURRENCY, domain=DOMAIN)


async def _products(client: BrowserClient, raw: RawWriter, kind: str, query: str = "") -> dict:
    """Every product of the collection (all variants), following pages; returns the first variant in stock."""
    page, first = 1, None
    while True:
        url = f"{BASE}/collections/{COLLECTION}/products.json?limit=250&page={page}{query}"
        r = await client.get(url)
        products = r.json().get("products") or []
        if not products:
            return {"pages": page - 1, "variant": first}
        raw.save(kind, url, r.text)
        first = first or next((v["id"] for p in products for v in p["variants"] if v.get("available")), None)
        page += 1


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    # The shop's own HKD prices. The market follows the visitor's IP unless asked for, so ask for HK.
    await _products(client, raw, "products_base", "&country=HK&currency=HKD")
    set_market(client)
    q = f"&country={config.COUNTRY}&currency={config.CURRENCY}"
    url = f"{BASE}/collections/{COLLECTION}?{q[1:]}"
    raw.save("market", url, (await client.get(url)).text)
    got = await _products(client, raw, "products", q)
    log(f"{NAME}: {got['pages']} product pages fetched ({config.COUNTRY}/{config.CURRENCY} market)")
    await quote_cart(client, raw, got["variant"], log)


async def quote_cart(client: BrowserClient, raw: RawWriter, variant_id: int | None, log) -> None:
    """One-item cart: shipping rates to the buyer country + the automatic discount on the line."""
    if variant_id is None:
        return
    try:
        await client.post(f"{BASE}/cart/add.js", json={"items": [{"id": variant_id, "quantity": 1}]})
        url = f"{BASE}/cart/shipping_rates.json?shipping_address[country]={config.COUNTRY_NAME}&shipping_address[zip]={config.POSTCODE}"
        r = await client.get(url)
        raw.save("shipping", url, r.text, r.status_code, meta={"country": config.COUNTRY, "variant": variant_id})
        rates = r.json().get("shipping_rates") or []
        log(f"{NAME}: shipping to {config.COUNTRY}: " + (", ".join(f"{x['name']} €{x['price']}" for x in rates) or r.text[:200]))
        r = await client.get(f"{BASE}/cart.js")
        raw.save("cart", f"{BASE}/cart.js", r.text, r.status_code, meta={"variant": variant_id})
    except Exception as e:
        log(f"{NAME}: cart quote failed: {e}")


def market(page: str) -> tuple[str | None, str | None, float | None]:
    """(country, currency, EUR per HKD) shown by the storefront."""
    cur = re.search(r"Shopify\.currency\s*=\s*(\{[^}]*\})", page)
    country = re.search(r'Shopify\.country\s*=\s*"(\w+)"', page)
    cur = json.loads(cur.group(1)) if cur else {}
    return country.group(1) if country else None, cur.get("active"), float(cur["rate"]) if cur.get("rate") else None


def ddp_rate(records: list[dict]) -> tuple[float | None, str | None, bool | None]:
    """(EUR, name, quoted) of the duties-paid rate from the cart quote. quoted=False: the cart
    gave rates but none is DDP (shipping then comes from config, flagged estimated)."""
    for r in records:
        if r["kind"] == "shipping":
            try:
                rates = json.loads(r["body"]).get("shipping_rates") or []
            except ValueError:
                return None, None, None
            ddp = [x for x in rates if DDP_RE.search(x.get("name") or "")]
            if ddp:
                best = min(ddp, key=lambda x: float(x["price"]))
                return float(best["price"]), best["name"], True
            return None, None, False if rates else None
    return None, None, None


def cart_discount(records: list[dict]) -> tuple[float, str | None]:
    """(automatic discount as a fraction, its title) from the cart line, e.g. (0.02, 'Summer Sale 2% Off')."""
    for r in records:
        if r["kind"] == "cart":
            try:
                item = (json.loads(r["body"]).get("items") or [None])[0]
            except ValueError:
                return 0.0, None
            if not item or not item.get("original_line_price"):
                return 0.0, None
            off = sum(d.get("amount") or 0 for d in item.get("discounts") or [])
            names = ", ".join(d.get("title") for d in item.get("discounts") or [] if d.get("title")) or None
            return round(off / item["original_line_price"], 4), names
    return 0.0, None


def option_values(p: dict, v: dict) -> tuple[str | None, str | None, dict]:
    """(memory, colour, all options) of a variant. Option names vary: "Ram+Storage", "Storage/RAM",
    "Ram+Strorage", "Storage", "Case color"."""
    values = {(o["name"] or "").strip().lower(): v.get(f"option{i + 1}") for i, o in enumerate(p.get("options") or [])}
    mem = next((val for k, val in values.items() if re.search(r"ram|stor", k)), None)
    color = next((val for k, val in values.items() if "colo" in k), None)
    return mem, color, {k: val for k, val in values.items() if val and val != "Default Title"}


def parse(records: list[dict], log) -> list[Offer]:
    page = next(r["body"] for r in records if r["kind"] == "market")
    country, currency, rate = market(page)
    if currency != config.CURRENCY or country != config.COUNTRY:
        raise RuntimeError(f"{NAME}: storefront market is {country}/{currency}, expected {config.COUNTRY}/{config.CURRENCY}")
    products = [p for r in records if r["kind"] == "products" for p in json.loads(r["body"])["products"]]
    base = {v["id"]: float(v["price"]) for r in records if r["kind"] == "products_base" and "currency=HKD" in r["url"]
            for p in json.loads(r["body"])["products"] for v in p["variants"]}  # older runs got the IP's market (EUR)
    ship_eur, ship_method, quoted = ddp_rate(records)
    off, campaign = cart_discount(records)
    log(f"{NAME}: {len(products)} products, market {country}/{currency} at {rate} EUR per HKD; shipping to {config.COUNTRY}: "
        + (f"{ship_method} €{ship_eur} (cart quote)" if quoted else "no DDP rate in the cart, using config" if quoted is False else "not quoted, using config")
        + (f"; automatic {off:.0%} '{campaign}'" if off else ""))
    note = f"includes the automatic {off:.0%} '{campaign}' discount (cart, no code)" if off else None

    offers: list[Offer] = []
    for p in products:
        url = f"{BASE}/products/{p['handle']}"
        text = p.get("body_html") or ""
        info = details.extract(text)
        plain = details.html_to_text(text)
        tags = {t.strip().lower() for t in p.get("tags") or []}
        rom = "Global ROM" if GLOBAL_ROM_TEXT_RE.search(plain) else None
        for v in p.get("variants", []):
            mem, color, attrs = option_values(p, v)
            # Text next to the memory is a special edition ("16GB+1TB Collector's Edition", "(Privacy Display)").
            extra = re.sub(r"[\s()\-+/]+", " ", n.strip_memory(re.sub(r"\bram\b", " ", mem or "", flags=re.I))).strip()
            o = make_offer(
                site=NAME,
                title=p["title"],
                brand_hint=p.get("vendor") if p.get("vendor") and "marvel" not in p["vendor"].lower() else None,
                memory_text=mem,
                model_suffix=extra if re.search(r"[a-z]{3}", extra, re.I) and not re.search(r"\b(?:version|kit)\b", extra, re.I) else None,
                color=color,
                edition_hints=[rom],
                info=info,
                condition="used" if "used" in tags else None,
                price_eur=round(float(v["price"]) * (1 - off), 2),
                in_stock=v.get("available", False),
                url=f"{url}?variant={v['id']}",
                sku=v.get("sku"),
                attrs=attrs,
            )
            if v["id"] in base:
                o.price_orig, o.currency_orig = round(base[v["id"]] * (1 - off), 2), "HKD"
            o.ships_from = "hk"  # dispatched from Hong Kong (DDP parcels go via its EU warehouse)
            o.payment_note = note
            apply_shipping(o, config.SHIPPING[NAME], ship_eur, ship_method, estimated=not quoted, delivers=True if quoted else None)
            offers.append(o)
    return offers
