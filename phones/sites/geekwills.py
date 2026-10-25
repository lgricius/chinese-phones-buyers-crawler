"""geekwills.com — Magento 2, like giztop. The shop's base currency is USD and it takes
payment in USD only ("Our payments are accepted only in US dollars, although this system
shows other currencies"); its EUR display uses its own rate (0.98 EUR per USD in Oct 2026,
~10% above the ECB rate). So we crawl the USD prices and convert them at the ECB
reference rate of the crawl day: what a card charge in USD costs, before the card's own
fee. The shop's EUR rate is still measured from the cart quote and logged."""

from __future__ import annotations

import asyncio
import json
import re

from .. import config, details
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter
from . import magento

NAME = "geekwills"
BASE = "https://www.geekwills.com"
CATEGORY = f"{BASE}/phones/smartphones.html"
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
# Shipping: one flat method to LT ("Express", 5-8 days), quoted from a cart once per crawl
# (config.SHIPPING["geekwills"] is the fallback). VAT: paid by the shop via IOSS (customs page).
EXPRESS_RE = r"express"


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    try:
        raw.save("ecb", ECB_URL, (await client.get(ECB_URL)).text)
    except Exception as e:
        log(f"geekwills: ECB rate not fetched ({e}), using config.USD_EUR_FALLBACK")
    # The store opens in USD.
    if magento.page_currency_symbol((await client.get(CATEGORY)).text) != "$":
        await magento.switch_currency(client, CATEGORY, "USD")
    urls = await magento.list_products(client, raw, CATEGORY, "product_list_limit=60")
    log(f"geekwills: {len(urls)} products, fetching product pages")

    async def one(u: str) -> str:
        try:
            text = (await client.get(u)).text
            raw.save("product", u, text)
            return text
        except Exception as e:  # one broken page must not kill the crawl
            log(f"geekwills: skip {u}: {e}")
            return ""

    pages = await asyncio.gather(*(one(u) for u in urls))
    for u, text in [(u, t) for u, t in zip(urls, pages) if '<div class="stock available"' in t][:3]:
        try:
            methods = await magento.quote_shipping(client, BASE, u, text, config.COUNTRY, config.POSTCODE)
        except Exception as e:
            log(f"geekwills: shipping quote failed on {u}: {e}")
            continue
        if methods:
            raw.save("shipping", u, json.dumps(methods), meta={"country": config.COUNTRY})
            log(f"geekwills: shipping to {config.COUNTRY}: " + ", ".join(f"{m['method_title']} ${m['base_amount']:.2f}" for m in methods))
            await save_shop_rate(client, raw, log)
            return
    log("geekwills: no shipping quote, using config.SHIPPING")


async def save_shop_rate(client: BrowserClient, raw: RawWriter, log) -> None:
    """The shop's own EUR per USD, from the cart shown in EUR (for the price note only)."""
    try:
        await magento.switch_currency(client, CATEGORY, "EUR")
        cart = (await client.get(f"{BASE}/checkout/cart/")).text
        m = re.search(r"window\.checkoutConfig\s*=\s*(\{.*?\});\s*\n", cart, re.S)
        q = json.loads(m.group(1))["quoteData"]
        raw.save("shop_rate", f"{BASE}/checkout/cart/", json.dumps({k: q.get(k) for k in ("base_currency_code", "quote_currency_code", "base_to_quote_rate")}))
    except Exception as e:
        log(f"geekwills: shop's EUR rate not read: {e}")


def ecb_rate(records: list[dict]) -> tuple[float | None, str | None]:
    """(EUR per USD, ECB reference date) from the crawl's ECB daily file."""
    for r in records:
        if r["kind"] == "ecb":
            usd = re.search(r"currency='USD' rate='([\d.]+)'", r["body"])
            day = re.search(r"time='([\d-]+)'", r["body"])
            if usd:
                return round(1 / float(usd.group(1)), 6), day.group(1) if day else None
    return None, None


def shop_rate(records: list[dict]) -> float | None:
    """The shop's own EUR per USD (its EUR display), from the cart."""
    for r in records:
        if r["kind"] == "shop_rate":
            q = json.loads(r["body"])
            if q.get("quote_currency_code") == "EUR" and q.get("base_currency_code") == "USD":
                return float(q["base_to_quote_rate"])
    return None


def express_shipping(records: list[dict]) -> tuple[float | None, str | None]:
    """(USD, method title) of the Express method from the cart quote."""
    for r in records:
        if r["kind"] == "shipping":
            for m in json.loads(r["body"]):
                if re.search(EXPRESS_RE, m.get("method_title", ""), re.I) and m.get("available", True):
                    return float(m["base_amount"]), f"{m['method_title']} ({m.get('carrier_title') or ''})".replace(" ()", "")
    return None, None


def product_html(text: str) -> str:
    """Short description ("* Don't support Google Mobile Service") + the Details tab."""
    m = re.search(r'class="product attribute overview">.*?</div>\s*</div>', text, re.S)
    return (m.group(0) if m else "") + magento.details_html(text)


def parse_product(url: str, text: str) -> list[Offer]:
    if magento.page_currency(text) not in (None, "USD"):
        raise magento.WrongCurrency(f"geekwills: expected USD, got {magento.page_currency(text)} on {url}")
    title, in_stock, sku = magento.product_basics(text, url)
    info = details.extract(product_html(text))
    return [
        make_offer(
            site=NAME,
            title=title,
            memory_text=v["mem"],
            color=v["color"],
            edition_hints=v["versions"],
            firmware=v["versions"][-1] if v["versions"] else None,
            info=info,
            price_eur=v["price"],  # USD here; converted in parse()
            in_stock=in_stock,
            url=url,
            sku=sku,
            attrs=v["attrs"],
        )
        for v in magento.variants(text, title)
    ]


def parse(records: list[dict], log) -> list[Offer]:
    rate, day = ecb_rate(records)
    if rate is None:
        rate, day = config.USD_EUR_FALLBACK, "fallback"
    ship_usd, ship_method = express_shipping(records)
    own = shop_rate(records)
    log(f"geekwills: ECB rate {rate} EUR per USD ({day}); the shop's own EUR rate {own}")
    ship_eur = round(ship_usd * rate, 2) if ship_usd is not None else None
    log(f"geekwills: Express shipping to {config.COUNTRY}: " + (f"${ship_usd} = €{ship_eur} (cart quote)" if ship_usd is not None else "not quoted, using config"))
    offers: list[Offer] = []
    for r in records:
        if r["kind"] != "product":
            continue
        try:
            offers += parse_product(r["url"], r["body"])
        except Exception as e:
            log(f"geekwills: cannot parse {r['url']}: {e}")
    note = f"Charged in USD; EUR at the ECB rate of {day}" + (f" (the site's EUR prices use {own:g}, {(own / rate - 1) * 100:.0f}% more)" if own else "")
    for o in offers:
        usd = o.price_eur
        o.price_orig, o.currency_orig = round(usd, 2), "USD"
        o.price_eur = round(usd * rate, 2)
        o.payment_note = note
        apply_shipping(o, config.SHIPPING[NAME], ship_eur, ship_method, delivers=True if ship_eur is not None else None)
    return offers
