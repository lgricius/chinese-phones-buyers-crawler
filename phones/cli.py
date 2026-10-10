"""Command line interface.

  phones crawl                         crawl all sites concurrently, update index
  phones best "oppo find n6"           best price per RAM/storage/edition, per site
  phones find --min-ram 16 --max-price 900 --edition global
  phones models [text]                 list known models
  phones history "honor magic 9 pro max" --storage 512
  phones changes --since 7d --kind drop  price/stock changes between crawls
  phones reindex                       re-parse stored raw pages offline
  phones serve                         web catalog at http://127.0.0.1:8787/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import query as q
from . import store, trends
from .config import COUNTRY, DISABLED_SITES
from .sites import ENABLED, SITES


def _fmt_mem(ram, storage) -> str:
    s = "?" if storage is None else (f"{storage // 1024}TB" if storage >= 1024 else f"{storage}GB")
    return f"{ram or '?'}/{s}"


def _fmt_price(o: dict | None) -> str:
    if not o:
        return "-"
    flags = " ".join(f for f, on in (("u", o.get("condition") == "new_unsealed"), ("E", o.get("ships_from") == "eu"), ("US", o.get("ships_from") == "us"), ("b", o.get("bank_price")), ("c", o.get("coupon_price")), ("+s", o.get("with_shipping") and not o.get("shipping_estimated")), ("~s", o.get("with_shipping") and o.get("shipping_estimated")), ("VAT?", o.get("vat") == "not_stated"), (f"no{COUNTRY}", o.get("delivers") is False)) if on)
    return f"€{o['price_eur']:,.2f}" + (f" {flags}" if flags else "") + ("" if o["in_stock"] else " (oos)")


def _fmt_trend(o: dict | None) -> str:
    """Last price change and the lowest price seen (standard price, from data/trends.json)."""
    tr = (o or {}).get("trend") or {}
    parts = []
    if "chg" in tr:
        parts.append(f"{'▼' if tr['chg'] < 0 else '▲'}€{abs(tr['chg']):,.0f} {tr['chg_at'][5:10]}")
    if tr.get("at_low"):
        parts.append("lowest seen")
    elif "low" in tr:
        parts.append(f"low €{tr['low']:,.0f} {tr['low_at'][5:]}")
    return ", ".join(parts) or "-"


LEGEND = "u = new but unsealed by the shop (e.g. to install Google Play), E = ships from EU warehouse, US = ships from US (EU import costs likely), b = bank-transfer price, c = with discount code (--links shows it), +s = incl. shipping (~s estimated), VAT? = shop does not say who pays import VAT, * = best"
if DISABLED_SITES:
    LEGEND += "\nnot shown: " + "; ".join(f"{k} ({v})" for k, v in DISABLED_SITES.items())


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    line = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip()
    return "\n".join([line(headers), line(["-" * w for w in widths]), *map(line, rows)])


def _filters(a) -> q.Filters:
    gp = {"yes": True, "no": False}.get(a.google_play) if a.google_play else None
    return q.Filters(
        ram=a.ram,
        min_ram=a.min_ram,
        storage=q.parse_storage(a.storage),
        min_storage=q.parse_storage(a.min_storage),
        edition=a.edition,
        condition=a.condition,
        ships_from=a.ships_from,
        brand=getattr(a, "brand", None),
        chipset=a.chipset,
        google_play=gp,
        min_battery=a.min_battery,
        min_display=a.min_display,
        max_display=a.max_display,
        min_price=a.min_price,
        max_price=a.max_price,
        sites=a.site,
        include_oos=a.include_oos,
        delivers_only=not a.no_delivery,
        device_type=None if a.type == "any" else a.type,
        bank=a.bank,
        shipping=a.shipping,
        coupon=a.coupon,
        color=a.color,
    )


def _freshness(index: dict) -> str:
    parts = [f"{s} {v['crawled_at'][:16].replace('T', ' ')}" for s, v in sorted(index["sites"].items())]
    return "data: " + ", ".join(parts) + " UTC" if parts else "no data yet - run: phones crawl"


def cmd_crawl(a) -> int:
    names = a.site or ENABLED
    for n in set(names) & set(DISABLED_SITES):
        print(f"note: {n} is disabled ({DISABLED_SITES[n]}); crawled and stored, but left out of the index")
    print(f"crawling {', '.join(names)} concurrently...")
    log = (lambda m: print("  " + m)) if a.verbose else (lambda m: None)
    summary = asyncio.run(store.crawl(names, log=log, keep_raw=a.keep_raw))
    for site, s in summary.items():
        print(f"  {site:16} " + (f"{s['offers']} offers in {s['seconds']}s" if s["ok"] else f"FAILED after {s['seconds']}s: {s['error']}"))
    return 0 if all(s["ok"] for s in summary.values()) else 1


def cmd_reindex(a) -> int:
    r = store.reindex(log=print)
    print(f"re-parsed {r['reparsed_offers']} offers from {r['runs_with_raw']} stored raw runs; "
          f"renamed {r['renamed_without_raw']} offers in snapshots without raw data; index has {r['index_offers']} offers")
    return 0


def cmd_serve(a) -> int:
    from .serve import serve

    if not store.INDEX.exists():
        print("no data yet - run: phones crawl")
        return 1
    serve(a.host, a.port)
    return 0


def cmd_best(a) -> int:
    f = _filters(a)
    index, offers = q.latest(f)
    keys, suggestions = q.resolve_models(a.model, index["offers"], family=a.family)
    if not keys:
        print(f"no model matches '{a.model}'." + (f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""))
        return 1
    offers = [o for o in offers if o["model_key"] in keys]
    if a.json:
        print(json.dumps(sorted(offers, key=lambda o: o["price_eur"]), indent=1, ensure_ascii=False))
        return 0
    table = q.best_by_config(offers, by_color=a.by_color)
    sites = sorted({o["site"] for o in offers}) or sorted(index["sites"])
    print(_freshness(index))
    if not table:
        all_offers = [o for o in index["offers"] if o["model_key"] in keys]
        have = sorted({f"{o['edition']}{'' if o['in_stock'] else ' (oos)'}" for o in all_offers})
        print(f"{', '.join(keys)}: no offers match the filters. Available: {', '.join(have) or 'none'}")
        return 1
    by_model = defaultdict(list)
    for k, v in table.items():
        by_model[k[0]].append((k, v))
    for model_key, configs in sorted(by_model.items()):
        name = next(o for v in configs[0][1:] for o in v.values())
        print(f"\n{name['brand']} {name['model']}  (prices in EUR, best per site)")
        rows = []
        order = lambda c: (c[0][1] or 0, c[0][2] or 0, c[0][3], c[0][4] or "")
        for (mk, ram, storage, edition, family), per_site in sorted(configs, key=order):
            best = q.cheapest(per_site)
            rows.append([_fmt_mem(ram, storage), edition] + ([family or "-"] if a.by_color else [])
                        + [_fmt_price(per_site.get(s)) + (" *" if per_site.get(s) is best else "") for s in sites]
                        + [f"{best['site']} ({best.get('color') or '-'})", _fmt_trend(best)])
        print(_table(["RAM/Storage", "Edition", *(["Colour"] if a.by_color else []), *sites, "Best (colour)", "Trend (best)"], rows))
        print(LEGEND)
        top = min((q.cheapest(v) for _, v in configs), key=lambda o: (not o["in_stock"], o["price_eur"]))
        print(f"cheapest: {_fmt_mem(top['ram_gb'], top['storage_gb'])} {top['edition']} {top.get('color') or ''} at {top['site']} {_fmt_price(top)}  {top['url']}")
        if a.links:
            for (mk, ram, storage, edition, family), per_site in sorted(configs, key=order):
                b = q.cheapest(per_site)
                same = sorted((o for o in offers if o["site"] == b["site"] and o["model_key"] == mk and o["ram_gb"] == ram
                               and o["storage_gb"] == storage and o["edition"] == edition and (family is None or o.get("color_family") == family)),
                              key=lambda o: o["price_eur"])
                colours = ", ".join(f"{o.get('color') or '-'} €{o['price_eur']:,.2f}" + ("" if o["in_stock"] else " oos") for o in same)
                code = f"\n      discount code: {b['coupon_code']} (-€{b['coupon_eur']:,.2f})" if b.get("coupon_price") else ""
                print(f"  {_fmt_mem(ram, storage):10} {edition:14} {b['site']:16} {b['url']}\n      colours: {colours}{code}")
    return 0


def cmd_find(a) -> int:
    f = _filters(a)
    index, offers = q.latest(f)
    if a.model:
        keys, _ = q.resolve_models(a.model, index["offers"], family=True)
        offers = [o for o in offers if o["model_key"] in keys]
    best = [q.cheapest(v) for v in q.best_by_config(offers, by_color=a.by_color).values()]
    best.sort(key=lambda o: o["price_eur"], reverse=a.desc)
    best = best[: a.limit]
    if a.json:
        print(json.dumps(best, indent=1, ensure_ascii=False))
        return 0
    print(_freshness(index))
    rows = [
        [
            f"{o['brand']} {o['model']}",
            _fmt_mem(o["ram_gb"], o["storage_gb"]),
            o["edition"],
            _fmt_price(o),
            o["site"],
            o.get("chipset") or "-",
            f"{o['display_in']}\"" if o.get("display_in") else "-",
            f"{o['battery_mah']}" if o.get("battery_mah") else "-",
            {True: "yes", False: "no", None: "?"}[o.get("google_play")],
            o.get("condition", "new"),
            o.get("ships_from") or "-",
            o.get("color") or "-",
        ]
        for o in best
    ]
    print(_table(["Model", "RAM/Storage", "Edition", "Best price", "Site", "Chipset", "Screen", "mAh", "GPlay", "Condition", "Ships", "Colour"], rows))
    print(LEGEND)
    print(f"{len(rows)} configurations (cheapest site each)")
    return 0


def cmd_models(a) -> int:
    index = store.load_index()
    offers = [o for o in index["offers"] if a.type == "any" or o.get("device_type", "phone") == a.type]
    if a.text:
        keys, sugg = q.resolve_models(a.text, offers, family=True)
        offers = [o for o in offers if o["model_key"] in (keys or sugg)]
    agg = defaultdict(lambda: {"sites": set(), "min": None, "name": None})
    for o in offers:
        g = agg[o["model_key"]]
        g["sites"].add(o["site"])
        g["name"] = g["name"] or f"{o['brand']} {o['model']}"
        if o["in_stock"] and (g["min"] is None or o["price_eur"] < g["min"]):
            g["min"] = o["price_eur"]
    rows = [[g["name"], k, ", ".join(sorted(g["sites"])), f"€{g['min']:,.2f}" if g["min"] else "oos"] for k, g in sorted(agg.items())]
    print(_table(["Model", "Key", "Sites", "From"], rows))
    print(f"{len(rows)} models")
    return 0


def cmd_history(a) -> int:
    f = _filters(a)
    index = store.load_index()
    keys, sugg = q.resolve_models(a.model, index["offers"], family=a.family)
    if not keys:
        print(f"no model matches '{a.model}'." + (f" Did you mean: {', '.join(sugg)}?" if sugg else ""))
        return 1
    rows = q.history(keys, f)
    if a.json:
        print(json.dumps([dict(crawled_at=t, config=list(c), site=s, price_eur=p) for t, c, s, p in rows], indent=1))
        return 0
    sites = sorted({s for _, _, s, _ in rows})
    by_config = defaultdict(lambda: defaultdict(dict))
    for t, c, s, p in rows:
        by_config[c][t][s] = p
    for (mk, ram, storage, edition), runs in sorted(by_config.items(), key=lambda x: (x[0][0], x[0][1] or 0, x[0][2] or 0)):
        print(f"\n{mk}  {_fmt_mem(ram, storage)}  {edition}")
        print(_table(["Crawled (UTC)", *sites], [[t[:16].replace("T", " ")] + [f"€{v[s]:,.2f}" if s in v else "-" for s in sites] for t, v in sorted(runs.items())]))
    if not rows:
        print("no history for that selection")
    return 0


KINDS = ["drop", "rise", "new", "gone", "in", "out"]


def _since(v: str) -> str:
    m = re.fullmatch(r"(\d+)\s*([hdw])", v.strip())
    if m:
        unit = {"h": "hours", "d": "days", "w": "weeks"}[m.group(2)]
        return (datetime.now(timezone.utc) - timedelta(**{unit: int(m.group(1))})).isoformat(timespec="seconds")
    try:
        return datetime.fromisoformat(v).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        raise ValueError(f"bad --since value: {v} (use 24h, 7d, 4w or a date)") from None


def _fmt_change(e: dict) -> str:
    eur = lambda v: f"€{v:,.2f}"
    k = e["kind"]
    if k in ("drop", "rise"):
        stock = {(True, False): ", sold out", (False, True): ", back in stock"}.get(tuple(e["stock"] or ()), "")
        return f"{eur(e['old_eur'])} -> {eur(e['new_eur'])} ({e['pct']:+.1f}%){stock}"
    if k == "new":
        return ("listed again " if e.get("relisted") else "new ") + eur(e["new_eur"]) + ("" if e["in_stock"] else " (oos)")
    if k == "gone":
        return f"removed (was {eur(e['old_eur'])})"
    return ("back in stock " if k == "in" else "sold out ") + eur(e["new_eur"])


def cmd_changes(a) -> int:
    t = store.load_trends()
    index = store.load_index()
    current = {o["oid"]: o for o in index["offers"] if o.get("oid")}
    desc = lambda e: current.get(e["id"]) or t["desc"].get(e["id"]) or {}
    ev = trends.events(t, _since(a.since))
    ev = [e for e in ev if (not a.kind or e["kind"] in a.kind) and (not a.site or e["site"] in a.site)
          and (a.type == "any" or (desc(e).get("device_type") or "phone") == a.type)
          and (a.min_pct is None or abs(e.get("pct") or 0) >= a.min_pct)]
    if a.model:
        keys, sugg = q.resolve_models(a.model, [desc(e) for e in ev if desc(e).get("model_key")], family=True)
        if not keys:
            print(f"no changes for '{a.model}'." + (f" Models with changes: {', '.join(sugg)}" if sugg else ""))
            return 1
        ev = [e for e in ev if desc(e).get("model_key") in keys]
    if a.json:
        print(json.dumps([{**e, **{k: desc(e).get(k) for k in trends.DESC_FIELDS}} for e in ev[: a.limit]], indent=1, ensure_ascii=False))
        return 0
    runs = sorted({r[1] for v in t["sites"].values() for r in v["runs"]})
    print(f"{len(runs)} crawls tracked since {runs[0][:16].replace('T', ' ') if runs else '-'} UTC; standard prices in EUR")
    if not ev:
        print(f"no changes since {a.since}" + (" for that selection" if a.kind or a.site or a.model or a.min_pct else ""))
        return 0
    rows = []
    for e in ev[: a.limit]:
        d = desc(e)
        rows.append([e["at"][:16].replace("T", " "), f"{d.get('brand', '')} {d.get('model', '')}", _fmt_mem(d.get("ram_gb"), d.get("storage_gb")),
                     d.get("edition") or "-", d.get("color") or "-", e["site"], _fmt_change(e)])
        if a.links:
            rows.append(["", d.get("url", ""), "", "", "", "", ""])
    print(_table(["Crawled (UTC)", "Model", "RAM/Storage", "Edition", "Colour", "Shop", "Change"], rows))
    more = len(ev) - a.limit
    print(f"{len(ev)} change" + ("" if len(ev) == 1 else "s") + (f" ({more} more: --limit)" if more > 0 else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="phones", description="Chinese phone shop price crawler (prices in EUR)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_filters(sp, model_arg: str | None = None):
        if model_arg == "required":
            sp.add_argument("model", help='model name, e.g. "oppo find n6"')
        elif model_arg == "optional":
            sp.add_argument("--model", help="restrict to models containing these words")
        sp.add_argument("--ram", type=int, help="exact RAM in GB")
        sp.add_argument("--min-ram", type=int)
        sp.add_argument("--storage", help="exact storage: 256, 512, 1tb")
        sp.add_argument("--min-storage")
        sp.add_argument("--edition", choices=["cn", "cn_global_rom", "global"])
        sp.add_argument("--condition", choices=["new", "new_unsealed", "used", "refurbished"])
        sp.add_argument("--ships-from", choices=["eu", "cn", "hk", "us"])
        sp.add_argument("--color", help="colour family (black, white, titanium, transparent, two-tone...) or part of a colour name")
        sp.add_argument("--by-color", action="store_true", help="separate rows per colour family")
        sp.add_argument("--chipset", help="substring, e.g. 'elite gen 5', 'dimensity'")
        sp.add_argument("--google-play", choices=["yes", "no"])
        sp.add_argument("--min-battery", type=int, help="mAh")
        sp.add_argument("--min-display", type=float, help="inches")
        sp.add_argument("--max-display", type=float, help="inches")
        sp.add_argument("--min-price", type=float, help="EUR")
        sp.add_argument("--max-price", type=float, help="EUR")
        sp.add_argument("--site", action="append", choices=list(SITES), help="repeatable")
        sp.add_argument("--include-oos", action="store_true", help="include out-of-stock offers")
        sp.add_argument("--no-delivery", action="store_true", help="include offers the shop does not deliver to the buyer country (shown as noLT)")
        sp.add_argument("--bank", action="store_true", help="use bank-transfer prices where a shop gives a discount (wonda -4.5%%)")
        sp.add_argument("--coupon", action="store_true", help="apply discount codes (gshopper150: $150 off orders over $1000; codes printed on pages)")
        sp.add_argument("--shipping", action="store_true", help="compare item + shipping to the buyer country (method with import VAT covered, see config.SHIPPING)")
        sp.add_argument("--type", default="phone", choices=["phone", "tablet", "accessory", "any"])
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    c = sub.add_parser("crawl", help="crawl sites concurrently and update the index")
    c.add_argument("--site", action="append", choices=list(SITES), help="repeatable; default all")
    c.add_argument("--keep-raw", type=int, default=store.KEEP_RAW, help="raw crawls to keep for reindex (default %(default)s)")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_crawl)

    r = sub.add_parser("reindex", help="re-parse stored raw crawls offline (after parser changes)")
    r.set_defaults(func=cmd_reindex)

    w = sub.add_parser("serve", help="web catalog at http://127.0.0.1:8787/")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8787)
    w.set_defaults(func=cmd_serve)

    b = sub.add_parser("best", help="best price per variant for a model, compared across sites")
    add_filters(b, "required")
    b.add_argument("--family", action="store_true", help="include related models (e.g. 'magic 9' -> Pro, Pro Max...)")
    b.add_argument("--links", action="store_true", help="print the link of each best offer")
    b.set_defaults(func=cmd_best)

    f = sub.add_parser("find", help="search all models by attributes, cheapest first")
    add_filters(f, "optional")
    f.add_argument("--brand")
    f.add_argument("--limit", type=int, default=40)
    f.add_argument("--desc", action="store_true", help="most expensive first")
    f.set_defaults(func=cmd_find)

    m = sub.add_parser("models", help="list known models")
    m.add_argument("text", nargs="?")
    m.add_argument("--type", default="phone", choices=["phone", "tablet", "accessory", "any"])
    m.set_defaults(func=cmd_models)

    h = sub.add_parser("history", help="price history across crawls")
    add_filters(h, "required")
    h.add_argument("--family", action="store_true")
    h.set_defaults(func=cmd_history)

    ch = sub.add_parser("changes", help="price and stock changes between crawls, newest first")
    ch.add_argument("model", nargs="?", help="only models containing these words")
    ch.add_argument("--since", default="7d", help="24h, 7d, 4w or a date (default %(default)s)")
    ch.add_argument("--kind", action="append", choices=KINDS, help="drop, rise, new, gone, in (back in stock), out (sold out); repeatable")
    ch.add_argument("--site", action="append", choices=list(SITES), help="repeatable")
    ch.add_argument("--min-pct", type=float, help="price changes of at least this many %%")
    ch.add_argument("--type", default="phone", choices=["phone", "tablet", "accessory", "any"])
    ch.add_argument("--limit", type=int, default=60)
    ch.add_argument("--links", action="store_true", help="print the link of each offer")
    ch.add_argument("--json", action="store_true")
    ch.set_defaults(func=cmd_changes)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO if getattr(a, "verbose", False) else logging.ERROR, format="  %(message)s")
    try:
        return a.func(a)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
