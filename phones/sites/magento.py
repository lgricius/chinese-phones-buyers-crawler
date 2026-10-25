"""Shared Magento 2 storefront logic (giztop, gshopper, geekwills).

Product pages carry configurable-product JSON with one entry per child product
(colour x storage x warehouse...), each with its own price in the display
currency: `spConfig` (configurable renderer) or `jsonConfig` (swatch renderer).
Required custom options ("Software: Global ROM + €8.76") multiply the variants.
"""

from __future__ import annotations

import html
import json
import re

from .. import normalize as n
from ..http import BrowserClient
from ..raw import RawWriter

# Required custom options that create real variants. Optional ones (accessories,
# insurance, lens kits) are add-ons and are ignored.
VARIANT_OPTION_RE = r"colou?r|storage|memory|capacity|version|software|rom\b|firmware|system|edition"
EDITION_OPTION_RE = r"version|software|rom\b|firmware|system|edition"
MEMORY_ATTR_RE = r"storage|memory|capacity|ram|rom size|type"


class WrongCurrency(RuntimeError):
    pass


def extract_json(text: str, key: str) -> dict | None:
    i = text.find(f'"{key}"')
    if i < 0:
        return None
    j = text.find("{", i)
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[j:])
        return obj
    except ValueError:
        return None


async def switch_currency(client: BrowserClient, page_url: str, currency: str) -> str:
    """Switch the storefront currency via its switcher; returns the page as it was before."""
    r = await client.get(page_url)
    m = re.search(rf"data-post='(\{{\"action\":\"[^']*currency[^']*\"{currency}\"[^']*)'", r.text)
    fk = re.search(r'name="form_key" type="hidden" value="([^"]+)"', r.text)
    if not m or not fk:
        raise RuntimeError(f"{page_url}: currency switcher not found")
    post = json.loads(m.group(1))
    domain = re.match(r"https?://([^/]+)", page_url).group(1)
    client.session.cookies.set("form_key", fk.group(1), domain=domain)
    await client.post(post["action"], data={**post["data"], "form_key": fk.group(1)})
    return r.text


async def list_products(client: BrowserClient, raw: RawWriter, category: str, params: str = "") -> list[str]:
    """Product URLs of a category, following ?p=N pages."""
    urls: dict[str, None] = {}
    page = 1
    total = None
    while True:
        url = f"{category}?{params + '&' if params else ''}p={page}"
        r = await client.get(url)
        raw.save("listing", url, r.text)
        if total is None:
            nums = re.findall(r'toolbar-number">(\d+)', r.text)
            total = int(nums[2]) if len(nums) >= 3 else None
        new = [u for u in re.findall(r'<a class="product-item-link"\s+href="([^"]+)"', r.text) if u not in urls]
        if not new:
            break
        urls.update(dict.fromkeys(new))
        if total and len(urls) >= total:
            break
        page += 1
    return list(urls)


def page_currency(text: str) -> str | None:
    cur = re.search(r'itemprop="priceCurrency" content="(\w+)"', text)
    return cur.group(1) if cur else None


def page_currency_symbol(text: str) -> str | None:
    m = re.search(r'class="price">\s*([^\d\s<]+)\s*[\d]', text)
    return m.group(1) if m else None


def listing_prices(text: str) -> dict[str, float]:
    """{product id: listed price} from a category page."""
    return {pid: float(p) for pid, p in re.findall(r'data-price-box="product-id-(\d+)".*?data-price-amount="([\d.]+)"', text, re.S)}


def product_basics(text: str, url: str) -> tuple[str, bool, str | None]:
    """(title, in stock, sku)"""
    title_m = re.search(r'<meta property="og:title"\s+content="([^"]*)"', text)
    title = html.unescape(title_m.group(1)).strip() if title_m else url
    stock_m = re.search(r'<div class="stock (available|unavailable)"', text)
    sku_m = re.search(r'itemprop="sku">([^<]+)<', text)
    return title, bool(stock_m and stock_m.group(1) == "available"), sku_m.group(1).strip() if sku_m else None


def details_html(text: str) -> str:
    """The Details tab (description, 'Things To Know', spec table)."""
    i = text.find('class="product info detailed"')
    if i < 0:
        return ""
    j = text.find('class="block related"', i)
    return text[i : j if j > 0 else i + 80000]


def custom_options(text: str) -> list[tuple[str, list[tuple[str, float]]]]:
    """[(title, [(value name, price adjustment)])] for required variant-type custom options."""
    cfg = extract_json(text, "optionConfig") or {}
    titles = {oid: html.unescape(t).strip() for oid, t in re.findall(r'<label class="label" for="select_(\d+)">\s*<span>([^<]+)</span>', text)}
    required = set(re.findall(r'name="options\[(\d+)\]"[^>]*class="[^"]*\brequired\b', text))
    out = []
    for oid, values in cfg.items():
        title = titles.get(oid, "")
        if oid in required and re.search(VARIANT_OPTION_RE, title, re.I) and isinstance(values, dict):
            out.append((title, [(v["name"].strip(), float(v["prices"]["finalPrice"]["amount"])) for v in values.values()]))
    return out


def main_product_id(text: str) -> str | None:
    """Id of the product the page is about (its add-to-cart form)."""
    m = re.search(r'id="product_addtocart_form".{0,4000}?name="product" value="(\d+)"', text, re.S) or re.search(
        r'name="product" value="(\d+)"', text
    )
    return m.group(1) if m else None


def main_price(text: str) -> float | None:
    """Final price of the page's own product. Pages show other products' prices too (header
    widgets, "sold together"), so the first itemprop="price" is not necessarily ours."""
    pid = main_product_id(text)
    if pid:
        box = re.search(rf'data-price-box="product-id-{pid}"(.{{0,3000}}?)</div>', text, re.S)
        if box:
            m = re.search(r'data-price-amount="([\d.]+)"\s+data-price-type="finalPrice"', box.group(1))
            if m:
                return float(m.group(1))
    i = text.find('class="product-info-main"')
    m = re.search(r'itemprop="price" content="([\d.]+)"', text[i:] if i >= 0 else text)
    return float(m.group(1)) if m else None


def product_config(text: str) -> dict | None:
    """The page product's own configurable JSON. Pages also embed configs of other products
    ("Sold together" widgets list a Realme 15 Pro on an Oppo page), so match by productId."""
    pid = main_product_id(text)
    configs = []
    for key in ("spConfig", "jsonConfig"):
        for m in re.finditer(f'"{key}"', text):
            try:
                cfg, _ = json.JSONDecoder().raw_decode(text[text.find("{", m.start()) :])
            except ValueError:
                continue
            if isinstance(cfg, dict) and cfg.get("attributes"):
                configs.append(cfg)
    if pid is None:
        return configs[0] if configs else None
    return next((c for c in configs if str(c.get("productId")) == pid), None)  # none: simple product


def configured_variant(values: dict[str, str | None], price: float) -> dict:
    """Variant of a configurable product from its attribute values (lower-case label -> value)."""
    return {
        "price": price,
        "mem": next((v for k, v in values.items() if re.search(MEMORY_ATTR_RE, k) and v and n.parse_memory(v)[1]), None),
        "color": next((v for k, v in values.items() if "colo" in k), None),
        "versions": [v for k, v in values.items() if re.search(EDITION_OPTION_RE, k)],
        "attrs": values,
    }


def single_variant(title: str, price: float) -> dict:
    """Variant of a single-configuration product, e.g. "OPPO Find N5 Global Edition-Black-16GB - 512GB"."""
    color_m = re.search(rf"-\s*((?:{n.COLOR_WORDS})(?:\s+\w+)?)\s*-", title, re.I) or re.search(
        r"\d\s*(?:gb|tb|g|t)\s*-\s*([a-z][a-z ]{2,30})$", title, re.I  # "...-12G+512GB-Canyon Orange"
    )
    return {
        "price": price,
        # Title first; else make_offer uses the single-value RAM/ROM spec rows.
        "mem": title if re.search(r"\d+\s*(gb|tb)", title, re.I) else None,
        "color": color_m.group(1) if color_m else n.title_color(title),
        "versions": [],
        "attrs": {},
    }


def variants(text: str, title: str) -> list[dict]:
    """Every purchasable variant: {price, mem, color, versions, attrs} (attrs: lower-case label -> value)."""
    out: list[dict] = []
    cfg = product_config(text)
    if cfg and cfg.get("attributes"):
        attrs = cfg["attributes"]
        for child_id, sel in cfg.get("index", {}).items():
            values = {}
            for attr_id, opt_id in sel.items():
                a = attrs.get(attr_id)
                if a:
                    values[a["label"].lower()] = next((o["label"] for o in a["options"] if o["id"] == opt_id), None)
            price = cfg["optionPrices"].get(child_id, {}).get("finalPrice", {}).get("amount")
            if price is None:
                continue
            out.append(configured_variant(values, price))
    else:
        price = main_price(text)
        if price is None:
            return []
        out.append(single_variant(title, price))

    # Required custom options multiply the variants: "Software: Global ROM (Flashed) + €8.76",
    # "Color-Storage: Black Gold-16GB-1TB + €26.27".
    for opt_title, values in custom_options(text):
        expanded = []
        for v in out:
            for name, adj in values:
                w = {**v, "price": v["price"] + adj, "attrs": {**v["attrs"], opt_title.lower(): name}, "versions": list(v["versions"])}
                if re.search(EDITION_OPTION_RE, opt_title, re.I):
                    w["versions"].append(name)
                else:
                    if n.parse_memory(name) != (None, None):
                        w["mem"] = name
                    color = re.sub(r"\s*-?\s*\d+\s*(gb|tb).*$", "", name, flags=re.I).strip(" -")
                    if color and not re.search(r"\d", color):
                        w["color"] = color
                expanded.append(w)
        out = expanded
    return out


async def quote_shipping(client: BrowserClient, base: str, url: str, text: str, country: str, postcode: str) -> list[dict] | None:
    """Shipping methods for `country`, from a guest cart holding this page's product:
    [{"method_title", "amount" (cart currency), "base_amount", ...}]. None if it cannot be carted."""
    form = re.search(r'action="([^"]+checkout/cart/add[^"]+)"', text)
    fk = re.search(r'name="form_key" type="hidden" value="([^"]+)"', text)
    if not form or not fk:
        return None
    data = {"product": main_product_id(text), "form_key": fk.group(1), "qty": "1"}
    # Cached pages carry different form keys; the cart checks the posted one against the cookie.
    client.session.cookies.set("form_key", fk.group(1), domain=re.match(r"https?://([^/]+)", base).group(1))
    cfg = product_config(text)
    if cfg and cfg.get("index"):
        for aid, oid in next(iter(cfg["index"].values())).items():
            data[f"super_attribute[{aid}]"] = oid
    for oid in re.findall(r'name="options\[(\d+)\]"[^>]*class="[^"]*\brequired\b', text):  # first value of required options
        first = re.search(rf'name="options\[{oid}\]".*?<option value="(\d+)"', text, re.S)
        if first:
            data[f"options[{oid}]"] = first.group(1)
    await client.post(html.unescape(form.group(1)), data=data, headers={"Referer": url})
    cart = (await client.get(f"{base}/checkout/cart/")).text
    m = re.search(r"window\.checkoutConfig\s*=\s*(\{.*?\});\s*\n", cart, re.S)
    if not m:
        return None
    qid = json.loads(m.group(1))["quoteData"]["entity_id"]
    r = await client.post(
        f"{base}/rest/default/V1/guest-carts/{qid}/estimate-shipping-methods",
        json={"address": {"country_id": country, "postcode": postcode, "region_id": 0}},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    methods = r.json()
    return methods if isinstance(methods, list) else None
