"""wondamobile.com — Shopify with per-market pricing (Shopify Markets).

Prices depend on the buyer's market, not just a currency conversion: the
Lithuania/EUR market charges a different amount than USD x exchange rate. We
set the market explicitly (localization + cart_currency cookies, see config.py)
and take products.json prices as-is, after checking the storefront really is in
that market. The bank-transfer discount is marketplace-wide (config.py)."""

from __future__ import annotations

import json
import re

from .. import config, details
from .. import normalize as n
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter

NAME = "wonda"
BASE = "https://wondamobile.com"
COLLECTION = "mobile"


def set_market(client: BrowserClient) -> None:
    client.session.cookies.set("localization", config.COUNTRY, domain="wondamobile.com")
    client.session.cookies.set("cart_currency", config.CURRENCY, domain="wondamobile.com")


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    set_market(client)
    url = f"{BASE}/collections/{COLLECTION}?country={config.COUNTRY}&currency={config.CURRENCY}"
    raw.save("market", url, (await client.get(url)).text)
    page, variant = 1, None
    while True:
        url = f"{BASE}/collections/{COLLECTION}/products.json?limit=250&page={page}&country={config.COUNTRY}&currency={config.CURRENCY}"
        r = await client.get(url)
        if not r.json().get("products"):
            break
        raw.save("products", url, r.text)
        variant = variant or next((v["id"] for p in r.json()["products"] for v in p["variants"] if v.get("available")), None)
        page += 1
    log(f"wonda: {page - 1} product pages fetched ({config.COUNTRY}/{config.CURRENCY} market)")
    await quote_shipping(client, raw, variant, log)


async def quote_shipping(client: BrowserClient, raw: RawWriter, variant_id: int | None, log) -> None:
    """Shipping rates to the buyer country for a one-item cart (rates are flat per order)."""
    if variant_id is None:
        return
    try:
        await client.post(f"{BASE}/cart/add.js", json={"items": [{"id": variant_id, "quantity": 1}]})
        url = f"{BASE}/cart/shipping_rates.json?shipping_address[country]={config.COUNTRY_NAME}&shipping_address[zip]={config.POSTCODE}"
        r = await client.get(url)
        raw.save("shipping", url, r.text, r.status_code, meta={"country": config.COUNTRY, "variant": variant_id})
        rates = r.json().get("shipping_rates") or []
        log(f"wonda: shipping to {config.COUNTRY}: " + (", ".join(f"{x['name']} €{x['price']}" for x in rates) or r.text[:200]))
    except Exception as e:
        log(f"wonda: shipping quote failed: {e}")


def cheapest_rate(records: list[dict]) -> tuple[float | None, str | None]:
    """(EUR, name) of the cheapest shipping rate from the crawl's cart quote."""
    for r in records:
        if r["kind"] == "shipping":
            try:
                rates = json.loads(r["body"]).get("shipping_rates") or []
            except ValueError:
                continue
            if rates:
                best = min(rates, key=lambda x: float(x["price"]))
                return float(best["price"]), best["name"]
    return None, None


def market(page: str) -> tuple[str | None, str | None]:
    """(country, currency) shown by the storefront."""
    cur = re.search(r"Shopify\.currency\s*=\s*(\{[^}]*\})", page)
    country = re.search(r'Shopify\.country\s*=\s*"(\w+)"', page)
    return (
        country.group(1) if country else None,
        json.loads(cur.group(1)).get("active") if cur else None,
    )


def parse(records: list[dict], log) -> list[Offer]:
    page = next(r["body"] for r in records if r["kind"] in ("market", "rate"))
    country, currency = market(page)
    if currency != "EUR":
        raise RuntimeError(f"wonda: storefront currency is {currency}, expected EUR")
    products = [p for r in records if r["kind"] == "products" for p in json.loads(r["body"])["products"]]
    log(f"wonda: {len(products)} products, market {country}/{currency}")
    ship_eur, ship_method = cheapest_rate(records)
    log(f"wonda: shipping to {config.COUNTRY}: " + (f"{ship_method} €{ship_eur} (cart quote)" if ship_eur is not None else "not quoted, using config"))

    offers: list[Offer] = []
    for p in products:
        opts = [o["name"].lower() for o in p.get("options", [])]
        url = f"{BASE}/products/{p['handle']}"
        info = details.extract(p.get("body_html") or "")
        for v in p.get("variants", []):
            values = {opts[i]: v.get(f"option{i + 1}") for i in range(len(opts))}
            mem = values.get("size") or values.get("storage") or values.get("capacity") or v.get("title")
            # Text next to the memory in the size option is a special edition ("16+512GB Collector's Edition").
            extra = re.sub(r"[\s()\-+/]+", " ", n.strip_memory(mem or "")).strip() if values.get("size") else ""
            color = values.get("color") or values.get("colour")
            version = values.get("version") or values.get("edition")
            price = float(v["price"])  # already in the market currency (EUR)
            o = make_offer(
                site=NAME,
                title=p["title"],
                brand_hint=p.get("vendor"),
                memory_text=mem,
                model_suffix=extra if re.search(r"[a-z]{3}", extra, re.I) else None,
                color=color,
                edition_hints=[version],
                info=info,
                price_eur=price,
                in_stock=v.get("available", False),
                url=f"{url}?variant={v['id']}",
                sku=v.get("sku"),
                attrs={k: val for k, val in values.items() if val},
            )
            apply_shipping(o, config.SHIPPING[NAME], ship_eur, ship_method, delivers=True if ship_eur is not None else None)
            offers.append(o)
    return offers
