"""giztop.com — Magento 2. The storefront is switched to EUR through the shop's
own currency switcher; product pages then carry the configurable-product
spConfig JSON with per-variant EUR prices, plus the Details tab that states the
edition ("This is a Chinese Edition"). The shop's base currency is USD (the cart
quotes EUR from it); the USD price is kept as price_orig for checking against
the site's default display."""

from __future__ import annotations

import asyncio
import json
import re

from .. import config, details
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter
from . import magento

NAME = "giztop"
BASE = "https://www.giztop.com"
CATEGORY = f"{BASE}/phones.html"
# phones.html does not include the Global-version listings; they have their own category.
CATEGORIES = [CATEGORY, f"{BASE}/phones/global-version.html"]
# "* Buy OPPO Find N6 Global version, please visit it HERE." -> the other edition's page
# Shipping: Express is the method where Giztop pays VAT/customs (IOSS); flat rate, quoted
# from a cart once per crawl (config.SHIPPING["giztop"] is the fallback).
EXPRESS_RE = r"express"
CROSS_LINK_RE = r'href="(https://www\.giztop\.com/[a-z0-9-]+\.html)"[^>]*>\s*(?:<[^>]+>\s*)*HERE'


async def switch_to_eur(client: BrowserClient, raw: RawWriter | None = None) -> None:
    before = await magento.switch_currency(client, CATEGORY, "EUR")
    if raw is not None and magento.page_currency_symbol(before) == "$":
        raw.save("listing_usd", CATEGORY, before)  # same page in USD -> shop's USD/EUR rate


def usd_rate(records: list[dict]) -> float | None:
    """EUR per USD as used by the shop, from the same category page in both currencies."""
    usd = next((magento.listing_prices(r["body"]) for r in records if r["kind"] == "listing_usd"), {})
    eur = next((magento.listing_prices(r["body"]) for r in records if r["kind"] == "listing" and r["url"] == f"{CATEGORY}?p=1"), {})
    ratios = sorted(eur[k] / usd[k] for k in usd.keys() & eur.keys() if usd[k] > 0)
    return ratios[len(ratios) // 2] if ratios else None


def parse_product(url: str, text: str) -> list[Offer]:
    if magento.page_currency(text) not in (None, "EUR"):
        raise magento.WrongCurrency(f"giztop: expected EUR, got {magento.page_currency(text)} on {url}")
    title, in_stock, sku = magento.product_basics(text, url)
    info = details.extract(magento.details_html(text))
    return [
        make_offer(
            site=NAME,
            title=title,
            memory_text=v["mem"],
            color=v["color"],
            edition_hints=v["versions"],
            firmware=v["versions"][-1] if v["versions"] else None,
            info=info,
            price_eur=v["price"],
            in_stock=in_stock,
            url=url,
            sku=sku,
            attrs=v["attrs"],
        )
        for v in magento.variants(text, title)
    ]


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    await switch_to_eur(client, raw)
    urls: dict[str, None] = {}
    for category in CATEGORIES:
        found = await magento.list_products(client, raw, category)
        log(f"giztop: {category.removeprefix(BASE)}: {len(found)} products")
        urls.update(dict.fromkeys(found))
    log(f"giztop: {len(urls)} unique products, fetching product pages")

    async def one(u: str) -> str:
        try:
            text = (await client.get(u)).text
            if magento.page_currency(text) not in (None, "EUR"):  # session lost the currency: switch again
                await switch_to_eur(client)
                text = (await client.get(u)).text
            raw.save("product", u, text)
            return text
        except Exception as e:  # one broken page must not kill the crawl
            log(f"giztop: skip {u}: {e}")
            return ""

    pages = await asyncio.gather(*(one(u) for u in urls))
    await quote_shipping(client, raw, list(zip(urls, pages)), log)
    # Other editions linked only from a product page ("Buy the Global version HERE").
    extra = sorted({u for t in pages for u in re.findall(CROSS_LINK_RE, t, re.I)} - urls.keys())
    if extra:
        log(f"giztop: {len(extra)} products only linked from other product pages: {', '.join(u.removeprefix(BASE + '/') for u in extra)}")
        await asyncio.gather(*(one(u) for u in extra))


async def quote_shipping(client: BrowserClient, raw: RawWriter, pages: list[tuple[str, str]], log) -> None:
    """Shipping methods to the buyer country from a guest cart (rates are flat per order)."""
    for u, text in [(u, t) for u, t in pages if '<div class="stock available"' in t][:3]:
        try:
            methods = await magento.quote_shipping(client, BASE, u, text, config.COUNTRY, config.POSTCODE)
        except Exception as e:
            log(f"giztop: shipping quote failed on {u}: {e}")
            continue
        if methods:
            raw.save("shipping", u, json.dumps(methods), meta={"country": config.COUNTRY})
            log("giztop: shipping to " + config.COUNTRY + ": " + ", ".join(f"{m['method_title']} €{m['amount']:.2f}" for m in methods))
            return
    log(f"giztop: no shipping quote, using config.SHIPPING")


def express_shipping(records: list[dict]) -> tuple[float | None, str | None]:
    """(EUR, method title) of the Express method from the crawl's cart quote."""
    for r in records:
        if r["kind"] == "shipping":
            for m in json.loads(r["body"]):
                if re.search(EXPRESS_RE, m.get("method_title", ""), re.I) and m.get("available", True):
                    return round(m["amount"], 2), f"{m['method_title']} (EU Priority)"
    return None, None


def parse(records: list[dict], log) -> list[Offer]:
    rate = usd_rate(records)
    log(f"giztop: shop rate {rate and round(rate, 5)} EUR per USD")
    ship_eur, ship_method = express_shipping(records)
    log(f"giztop: Express shipping to {config.COUNTRY}: " + (f"€{ship_eur} (cart quote)" if ship_eur is not None else "not quoted, using config"))
    offers: list[Offer] = []
    for r in records:
        if r["kind"] != "product":
            continue
        try:
            offers += parse_product(r["url"], r["body"])
        except Exception as e:
            log(f"giztop: cannot parse {r['url']}: {e}")
    for o in offers:
        if rate:
            o.price_orig, o.currency_orig = round(o.price_eur / rate, 2), "USD"
        apply_shipping(o, config.SHIPPING[NAME], ship_eur, ship_method, delivers=True if ship_eur is not None else None)
    return offers
