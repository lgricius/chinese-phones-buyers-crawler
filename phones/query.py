"""Queries over the merged index (latest offers) and snapshots (history)."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from . import normalize as n
from .store import iter_snapshots, load_index


@dataclass
class Filters:
    ram: int | None = None
    min_ram: int | None = None
    storage: int | None = None
    min_storage: int | None = None
    edition: str | None = None  # cn | cn_global_rom | global
    condition: str | None = None  # new | new_unsealed | used | refurbished
    ships_from: str | None = None  # eu | cn | hk
    color: str | None = None  # colour family ("titanium") or part of the shop's colour name
    brand: str | None = None
    chipset: str | None = None
    google_play: bool | None = None
    min_battery: int | None = None
    min_display: float | None = None
    max_display: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    sites: list[str] | None = None
    include_oos: bool = False
    device_type: str | None = "phone"
    bank: bool = False  # compare bank-transfer prices where a shop offers a discount
    coupon: bool = False  # apply the best discount code (gshopper: $150 off over $1000, page codes)
    shipping: bool = False  # compare item + shipping to the buyer country (unknown shipping counts as 0)
    delivers_only: bool = True  # hide offers the shop does not deliver to the buyer country

    def match(self, o: dict) -> bool:
        def lt(v, lim):  # value known and below the limit -> reject
            return lim is not None and (v is None or v < lim)

        def gt(v, lim):
            return lim is not None and (v is None or v > lim)

        return not (
            (not self.include_oos and not o["in_stock"])
            or (self.delivers_only and o.get("delivers") is False)
            or (self.device_type and o.get("device_type", "phone") != self.device_type)
            or (self.ram is not None and o["ram_gb"] != self.ram)
            or (self.storage is not None and o["storage_gb"] != self.storage)
            or lt(o["ram_gb"], self.min_ram)
            or lt(o["storage_gb"], self.min_storage)
            or (self.edition and o["edition"] != self.edition)
            or (self.condition and o.get("condition", "new") != self.condition)
            or (self.ships_from and o.get("ships_from") != self.ships_from)
            or (self.color and not color_match(o, self.color))
            or (self.brand and o["brand"].lower() != self.brand.lower())
            or (self.chipset and self.chipset.lower() not in (o.get("model_chipset") or o.get("chipset") or "").lower())
            or (self.google_play is not None and o.get("google_play") is not self.google_play)
            or lt(o.get("model_battery_mah") or o.get("battery_mah"), self.min_battery)
            or lt(o.get("display_in"), self.min_display)
            or gt(o.get("display_in"), self.max_display)
            or lt(o["price_eur"], self.min_price)
            or gt(o["price_eur"], self.max_price)
            or (self.sites and o["site"] not in self.sites)
        )


def color_match(o: dict, wanted: str) -> bool:
    w = wanted.lower().strip()
    return o.get("color_family") == w or w in (o.get("color") or "").lower()


def parse_storage(s: str | None) -> int | None:
    if not s:
        return None
    m = re.fullmatch(r"\s*(\d+)\s*(tb|gb)?\s*", s, re.I)
    if not m:
        raise ValueError(f"bad storage value: {s}")
    v = int(m.group(1))
    return v * 1024 if (m.group(2) or "").lower() == "tb" or v in (1, 2) else v


def resolve_models(query: str, offers: list[dict], family: bool = False) -> tuple[list[str], list[str]]:
    """Map free text to model_keys. Returns (matched keys, suggestions).

    Exact normalized match wins ("honor magic 9 pro max" does not pull in
    "honor magic 9"). With family=True, or when there is no exact match, every
    model containing all query words is returned ("magic 9" -> all Magic 9s).
    """
    keys = sorted({o["model_key"] for o in offers})
    q = n.query_key(query)
    q_plain = n.model_key("", n.clean_model_text(query))
    exact = [k for k in keys if k in (q, q_plain) or k.split(" ", 1)[-1] == q_plain]
    if exact and not family:
        return exact, []
    words = set(q_plain.split())
    contains = [k for k in keys if words <= set(k.split())]
    if contains:
        return contains, []
    suggestions = [k for k, score, _ in process.extract(q_plain, keys, scorer=fuzz.token_set_ratio, limit=6) if score >= 60]
    return [], suggestions


def with_payment(o: dict, bank: bool, shipping: bool = False, coupon: bool = False) -> dict:
    """Price to compare: item price, minus the discount code when asked, with the bank-transfer
    discount when asked (taken after the code; stacking is not confirmed by the shops), plus shipping when asked."""
    o = {**o}
    item = o["price_eur"]
    if coupon and o.get("coupon_eur"):
        item -= o["coupon_eur"]
        o["coupon_price"] = True
    pct = o.get("bank_discount_pct")
    if bank and pct:
        item *= 1 - pct / 100
        o["bank_price"] = True
    o["item_price_eur"] = round(item, 2)
    o["price_eur"] = round(item + ((o.get("shipping_eur") or 0) if shipping else 0), 2)
    o["with_shipping"] = shipping
    return o


def latest(filters: Filters) -> tuple[dict, list[dict]]:
    index = load_index()
    offers = [with_payment(o, filters.bank, filters.shipping, filters.coupon) for o in index["offers"]]
    return index, [o for o in offers if filters.match(o)]


def best_by_config(offers: list[dict], by_color: bool = False) -> dict[tuple, dict[str, dict]]:
    """{(model_key, ram, storage, edition, colour family or None): {site: cheapest offer}}.
    Without by_color the cheapest colour wins; the offer says which colour that is."""
    table: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for o in offers:
        k = (o["model_key"], o["ram_gb"], o["storage_gb"], o["edition"], o.get("color_family") if by_color else None)
        cur = table[k].get(o["site"])
        if cur is None or (o["in_stock"], -o["price_eur"]) > (cur["in_stock"], -cur["price_eur"]):
            table[k][o["site"]] = o
    return table


def cheapest(site_offers: dict[str, dict]) -> dict:
    return min(site_offers.values(), key=lambda o: (not o["in_stock"], o["price_eur"]))


def history(model_keys: list[str], filters: Filters) -> list[tuple[str, tuple, str, float]]:
    """[(crawled_at, config, site, min price)] over all snapshots."""
    f = Filters(**{**filters.__dict__, "include_oos": True})
    rows: dict[tuple, float] = {}
    for crawled_at, offers in iter_snapshots():
        for o in offers:
            o = with_payment(o, filters.bank, filters.shipping, filters.coupon)
            if o["model_key"] in model_keys and f.match(o) and (filters.include_oos or o["in_stock"]):
                k = (crawled_at, (o["model_key"], o["ram_gb"], o["storage_gb"], o["edition"]), o["site"])
                rows[k] = min(rows.get(k, o["price_eur"]), o["price_eur"])
    return [(*k, v) for k, v in sorted(rows.items())]
