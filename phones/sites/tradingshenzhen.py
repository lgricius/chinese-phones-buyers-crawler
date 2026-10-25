"""tradingshenzhen.com — PrestaShop, prices natively in EUR, behind Cloudflare.
Each listing item is one RAM/storage configuration; colour and firmware are
combinations on the product page. The page only shows the default combination's
price and no per-combination stock, so we ask PrestaShop's refresh endpoint for
every colour (x firmware option): colours can cost more (Xiaomi 18 Pro
Transparent +€350) or be sold out. The delivery widget tells EU-warehouse
stock ("EU Priority") from China stock ("Global Priority")."""

from __future__ import annotations

import asyncio
import html
import itertools
import json
import re

from .. import config, details
from .. import normalize as n
from ..http import BrowserClient
from ..models import Offer, apply_shipping, make_offer
from ..raw import RawWriter

NAME = "tradingshenzhen"
BASE = "https://tradingshenzhen.com"
CATEGORY = f"{BASE}/en/smartphones"
MAX_COMBOS = 24


async def list_products(client: BrowserClient, raw: RawWriter) -> list[str]:
    urls: dict[str, None] = {}
    page = 1
    while True:
        url = f"{CATEGORY}?page={page}"
        r = await client.get(url)
        if page == 1:
            m = re.search(r'"currency":\{[^}]*"iso_code":"(\w+)"', r.text)
            if m and m.group(1) != "EUR":
                raise RuntimeError(f"tradingshenzhen: expected EUR, got {m.group(1)}")
        new = [u for u in re.findall(r'<h3 class="h3 product-title"><a href="([^"]+)">', r.text) if u not in urls]
        if not new:
            break
        raw.save("listing", url, r.text)
        urls.update(dict.fromkeys(new))
        page += 1
    return list(urls)


def _groups(text: str) -> dict[str, tuple[str, list[tuple[str, str]], str | None]]:
    """{group label: (group id, [(value id, title)], selected value id)}"""
    out = {}
    pattern = r'<span class="control-label">([^<]+?)\s*</span>\s*<select[^>]*data-product-attribute="(\d+)"[^>]*>(.*?)</select>'
    for label, gid, body in re.findall(pattern, text, re.S):
        opts = [(v, html.unescape(t)) for v, t in re.findall(r'<option value="(\d+)" title="([^"]+)"', body)]
        sel = re.search(r'<option value="(\d+)"[^>]*selected', body)
        out[html.unescape(label).strip()] = (gid, opts, sel.group(1) if sel else None)
    # Colour swatches / radio groups
    for gid, body in re.findall(r'<ul id="group_(\d+)">(.*?)</ul>', text, re.S):
        label_m = re.search(rf'<span class="control-label">([^<]+?)\s*</span>\s*<ul id="group_{gid}"', text)
        label = html.unescape(label_m.group(1)).strip() if label_m else f"group {gid}"
        opts = re.findall(r'value="(\d+)"[^>]*(?:checked)?[^>]*>\s*<span[^>]*>(?:<span class="sr-only">)?([^<]+)<', body)
        sel = re.search(r'value="(\d+)"[^>]*checked', body)
        out[label] = (gid, [(v, html.unescape(t).strip()) for v, t in opts], sel.group(1) if sel else None)
    return out


def _split_groups(groups: dict) -> tuple[dict, dict, dict]:
    """(colour groups, other groups, other groups with several options)"""
    color = {k: v for k, v in groups.items() if "colo" in k.lower()}
    other = {k: v for k, v in groups.items() if k not in color}
    multi = {k: v for k, v in other.items() if len(v[1]) > 1}
    return color, other, multi


def details_html(text: str) -> str:
    """Short description, 'Read before you buy' panel, long description and data sheet."""
    i = text.find('class="description-short"')
    sheet = text.find('class="data-sheet"')
    j = text.find("</dl>", sheet) if sheet > 0 else -1
    if i < 0:
        return text[sheet : j + 5] if sheet > 0 else ""
    return text[i : (j + 5) if j > i else i + 120000]


def delivery_text(text: str) -> str:
    """The estimated-delivery widget ('EU Priority · 2–5 working days')."""
    i = text.find('class="acodvd-main"')
    return details.html_to_text(text[i : i + 6000]) if i > 0 else ""


def widget_cost(delivery: str) -> float | None:
    """Shipping cost shown by the delivery widget ('from €16.00', 'Free'). The widget quotes the
    shop's default country; the buyer country is not in its list, so this is an estimate."""
    if re.search(r"\|\s*Free\s*\|", delivery):
        return 0.0
    m = re.search(r"from\s*€\s*([\d.,]+)", delivery)
    return float(m.group(1).replace(",", "")) if m else None


def delivery_countries(text: str) -> list[str]:
    """Countries in the delivery widget's "Delivery country" list (empty if the page has none)."""
    m = re.search(r'<select name="acodvd\[id_country\]"[^>]*>(.*?)</select>', text, re.S)
    return [html.unescape(t).strip() for t in re.findall(r"<option[^>]*>([^<]+)", m.group(1))] if m else []


def set_shipping(o: Offer, delivery: str, countries: list[str]) -> None:
    if countries and config.COUNTRY_NAME not in countries:
        apply_shipping(o, config.SHIPPING[NAME], delivers=False)
        o.delivery_note = f"Does not deliver to {config.COUNTRY_NAME}: not in the shop's delivery countries. " + (o.delivery_note or "")
    elif o.ships_from == "eu":
        apply_shipping(o, config.SHIPPING["tradingshenzhen_eu"])
    else:
        apply_shipping(o, config.SHIPPING[NAME], widget_cost(delivery) or None, estimated=True)


def _data_product(text: str) -> dict:
    m = re.search(r'data-product="([^"]+)"', text)
    return json.loads(html.unescape(m.group(1))) if m else {}


def _combos(groups: dict) -> tuple[list[str], list[tuple]]:
    """Groups to enumerate (every colour group, plus non-colour groups with several options)."""
    color, _, multi = _split_groups(groups)
    keys = [k for k in color if len(color[k][1]) > 1] + list(multi)
    return keys, list(itertools.islice(itertools.product(*(groups[k][1] for k in keys)), MAX_COMBOS)) if keys else []


async def fetch_product(client: BrowserClient, raw: RawWriter, url: str, log) -> None:
    r = await client.get(url)
    raw.save("product", url, r.text)
    if not _data_product(r.text):
        log(f"tradingshenzhen: no product data on {url}")
        return
    groups = _groups(r.text)
    keys, combos = _combos(groups)
    base = {f"group[{gid}]": sel for gid, opts, sel in groups.values() if sel}
    for combo in combos:
        data = {"ajax": "1", "action": "refresh", "quantity_wanted": "1", **base}
        for k, (vid, _) in zip(keys, combo):
            data[f"group[{groups[k][0]}]"] = vid
        rr = await client.post(url, data=data, headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"})
        selected = {k: next((t for vid, t in v[1] if vid == v[2]), "") for k, v in groups.items() if k not in keys and v[2]}
        selected.update({k: t for k, (_, t) in zip(keys, combo)})
        raw.save("refresh", url, rr.text, rr.status_code, meta={"selected": selected})


async def fetch(client: BrowserClient, raw: RawWriter, log) -> None:
    urls = await list_products(client, raw)
    log(f"tradingshenzhen: {len(urls)} products, fetching product pages")

    async def one(u: str) -> None:
        try:
            await fetch_product(client, raw, u, log)
        except Exception as e:
            log(f"tradingshenzhen: skip {u}: {e}")

    await asyncio.gather(*(one(u) for u in urls))


def parse_product(url: str, text: str, refreshes: list[dict], shop_countries: list[str] | None = None) -> list[Offer]:
    dp = _data_product(text)
    if not dp:
        return []
    groups = _groups(text)
    color, other, multi = _split_groups(groups)
    delivery = delivery_text(text)
    countries = delivery_countries(text) or shop_countries or []  # the list is shop-wide; out-of-stock pages lack the widget
    info = details.extract(details_html(text), delivery)
    title = html.unescape(dp.get("name") or url)

    def offer(price: float, available: bool, selected: dict[str, str], verified: bool = True) -> Offer:
        colour = next((v for k, v in selected.items() if k in color), None)
        rest = {k: v for k, v in selected.items() if k not in color}
        firmware = rest.get("Firmware") or next(iter(rest.values()), None)
        memory = next((v for v in rest.values() if n.parse_memory(v)[1]), None)  # "Version: 16GB / 512GB"
        return make_offer(
            site=NAME,
            title=title,
            brand_hint=dp.get("manufacturer_name"),
            memory_text=memory,
            color=colour,
            color_verified=verified,
            firmware=firmware,
            edition_hints=[v for k, v in rest.items() if k != "Firmware"],
            info=info,
            condition=dp.get("condition"),
            price_eur=price,
            in_stock=available,
            url=url,
            sku=dp.get("reference"),
            attrs={"category": dp.get("category"), **selected},
        )

    offers = []
    for r in refreshes:  # one per colour (x firmware) combination
        try:
            j = json.loads(r["body"])
        except ValueError:
            continue
        price_m = re.search(r'content="([\d.]+)"', j.get("product_prices", ""))
        if not price_m:
            continue
        btn = re.search(r"<button[^>]*add-to-cart[^>]*>", j.get("product_add_to_cart", ""))
        offers.append(offer(float(price_m.group(1)), bool(btn) and "disabled" not in btn.group(0), r["meta"]["selected"]))
    if offers:
        for o in offers:
            set_shipping(o, delivery, countries)
        return offers

    # No per-combination data (single colour, or an older crawl): the page's default price for every colour.
    default = {k: next((t for vid, t in v[1] if vid == v[2]), v[1][0][1] if v[1] else "") for k, v in other.items()}
    available = dp.get("availability") in ("available", "in_stock") or (dp.get("quantity") or 0) > 0
    colours = [(k, t) for k, v in color.items() for _, t in v[1]] or [(None, None)]
    single = len(colours) == 1
    offers = [offer(dp["price_amount"], available, {**default, **({k: t} if k else {})}, verified=single) for k, t in colours]
    for o in offers:
        set_shipping(o, delivery, countries)
    return offers


def parse(records: list[dict], log) -> list[Offer]:
    refreshes: dict[str, list[dict]] = {}
    for r in records:
        if r["kind"] == "refresh":
            refreshes.setdefault(r["url"], []).append(r)
    products = [r for r in records if r["kind"] == "product"]
    shop_countries = next((c for c in (delivery_countries(r["body"]) for r in products) if c), [])
    offers: list[Offer] = []
    for r in products:
        try:
            offers += parse_product(r["url"], r["body"], refreshes.get(r["url"], []), shop_countries)
        except Exception as e:
            log(f"tradingshenzhen: cannot parse {r['url']}: {e}")
    return offers
