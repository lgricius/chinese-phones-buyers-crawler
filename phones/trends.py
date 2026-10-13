"""Price trends: a compact change-point history of every offer, built from the snapshots.

  data/trends.json   {"sites":  {site: {"cur": "USD", "runs": [[run, crawled_at, rate], ...]}},
                      "series": {offer id: [site, [[run index, EUR, in stock(, shop price)], ...]]},
                      "desc":   {offer id: what the offer was (model, variant, colour, link...)}}
  data/trends.js     the same for the web view (window.TRENDS; descriptions only of offers
                     no longer listed), loaded when a chart or the Updates view needs it

A series stores a point only when the price or stock changed; EUR None = no longer
listed. Shops that convert from another currency (USD at giztop/gshopper, wonda's
Shopify Markets rate) are compared before the conversion, so a move of the exchange rate
is not a price change; `rate` is EUR per unit at that crawl, and the EUR price at any
crawl is shop price x rate. New snapshots are
added incrementally; `reindex` rebuilds everything from the snapshots.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timedelta

SAME = 0.005  # prices closer than this are equal
# Crawls before this were made while the parsers were being written (no USD prices at
# giztop, one price for all colours at tradingshenzhen): their differences are not price changes.
FIRST_RUN = "2026-10-10T103854Z"
WINDOW = timedelta(days=30)
DESC_FIELDS = ("site", "brand", "model", "model_key", "title", "ram_gb", "storage_gb", "edition", "color", "color_family",
               "condition", "device_type", "ships_from", "chipset", "battery_mah", "url")


def empty() -> dict:
    return {"sites": {}, "series": {}, "desc": {}}


def offer_id(o: dict) -> str:
    """Stable id of one purchasable option: shop, product link and the options picked on the page."""
    attrs = sorted((k, str(v)) for k, v in (o.get("attrs") or {}).items() if k != "category")
    key = [o["site"], o["url"], o.get("color"), o.get("ram_gb"), o.get("storage_gb"), o.get("edition"), o.get("condition"), o.get("firmware"), attrs]
    return hashlib.sha1(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:10]


def _points(offers: list[dict]) -> tuple[str, float, dict[str, tuple[list, dict]]]:
    """(currency, EUR per unit, {id: (point without run index, description)}) of one snapshot.
    Two offers with the same id (rare) keep the in-stock / cheaper one."""
    orig = [o for o in offers if base(o)]
    cur = next((o["currency_orig"] for o in orig if o.get("currency_orig")), "EUR")
    rate = statistics.median(o["price_eur"] / base(o) for o in orig) if orig else 1.0
    pts: dict[str, tuple[list, dict]] = {}
    for o in offers:
        i = offer_id(o)
        pt = [round(o["price_eur"], 2), int(bool(o["in_stock"]))] + ([round(base(o), 4)] if base(o) else [])
        if i not in pts or (pt[1], -pt[0]) > (pts[i][0][1], -pts[i][0][0]):
            pts[i] = (pt, {k: o.get(k) for k in DESC_FIELDS})
    return cur, rate, pts


def base(o: dict) -> float | None:
    """Price before the shop's currency conversion, if the shop converts."""
    return o.get("price_base") or (o.get("price_orig") if o.get("currency_orig") else None)


def price_changed(a: list, b: list) -> bool:
    """Points [run, EUR, stock(, shop price)]: compared in the shop's currency when both have it.
    A shop price worked back from a EUR price rounded to cents is off by up to ~1 cent,
    so it changed only by more than 0.01% (or a cent)."""
    if len(a) > 3 and len(b) > 3:
        return abs(a[3] - b[3]) > max(2 * SAME, a[3] * 1e-4)
    return abs(a[1] - b[1]) > SAME


def eur_at(pt: list, runs: list) -> float:
    """EUR price of a point at the crawl it was recorded (or any later crawl's run index)."""
    return round(pt[3] * runs[pt[0]][2], 2) if len(pt) > 3 else pt[1]


def apply(t: dict, site: str, run: str, crawled_at: str, offers: list[dict]) -> None:
    """Add one site snapshot (newer than the site's last one) to the trends."""
    cur, rate, pts = _points(offers)
    st = t["sites"].setdefault(site, {"cur": cur, "runs": []})
    st["cur"] = cur
    ri = len(st["runs"])
    st["runs"].append([run, crawled_at, round(rate, 6)])
    series = t["series"]
    for i, (pt, desc) in pts.items():
        new = [ri, *pt]
        if i not in series:
            series[i] = [site, [new]]
        else:
            last = series[i][1][-1]
            changed = last[1] is None or price_changed(last, new)
            if not changed and len(last) > 3 and len(new) > 3:
                new[3] = last[3]  # same shop price: keep it exactly, so the points compare equal
            if changed or last[2] != new[2]:
                series[i][1].append(new)
            elif len(last) == 3 and len(new) > 3:
                last.append(new[3])  # same price, now also known in the shop's currency
        t["desc"][i] = desc
    for i, (sname, ser) in series.items():
        if sname == site and i not in pts and ser[-1][1] is not None:
            ser.append([ri, None, 0])


def update(t: dict, snapshots: list[tuple[str, str, object]], read) -> tuple[dict, bool]:
    """Add snapshots [(run, site, path)] not yet in `t`, oldest first. Returns (trends, rebuilt);
    a snapshot older than what a site already has means history changed: rebuild from scratch."""
    done = {s: {r[0] for r in v["runs"]} for s, v in t["sites"].items()}
    last = {s: v["runs"][-1][0] for s, v in t["sites"].items() if v["runs"]}
    snapshots = [x for x in snapshots if x[0] >= FIRST_RUN]
    new = sorted((run, site, path) for run, site, path in snapshots if run not in done.get(site, ()))
    if any(site in last and run < last[site] for run, site, _ in new):
        t, new = empty(), sorted(snapshots)
        rebuilt = True
    else:
        rebuilt = False
    for run, site, path in new:
        d = read(path)
        apply(t, site, run, d["crawled_at"], d["offers"])
    return t, rebuilt


def _day(ts: str) -> str:
    return ts[:10]


def stats(t: dict, o: dict) -> dict | None:
    """Price statistics of one current offer, in EUR at each crawl's rate:
    since/n: first seen, crawls listed; stock_pct: share of those crawls in stock;
    new: listed after the shop's first crawl; low/low_at: lowest in-stock price and since when;
    at_low: the price now is the lowest seen; low30/high30: in-stock range over 30 days;
    chg/chg_pct/chg_at/prev: last price change; drops30/rises30: changes in 30 days."""
    ser = t["series"].get(o.get("oid"))
    if not ser:
        return None
    runs = t["sites"][ser[0]]["runs"]
    pts = ser[1]
    times = [datetime.fromisoformat(r[1]) for r in runs]
    since30 = times[-1] - WINDOW

    listed = instock = drops = rises = 0
    low = low30 = high30 = prev = chg = None  # low: (EUR, point)
    for k, pt in enumerate(pts):
        ri, e, s = pt[:3]
        end = pts[k + 1][0] if k + 1 < len(pts) else len(runs)
        if e is None:
            continue
        listed += end - ri
        instock += (end - ri) if s else 0
        changed = prev is not None and price_changed(prev, pt)
        if changed:
            chg = (prev, pt)
            if times[ri] >= since30:
                drops += e < prev[1]
                rises += e > prev[1]
        if s:
            v = eur_at(pt, runs)
            if low is None or v < low[0] - SAME:
                low = (v, pt)
            elif changed and abs(v - low[0]) <= SAME:
                low = (v, pt)  # back at the low: the latest time it got there
            if times[end - 1] >= since30:
                low30 = v if low30 is None else min(low30, v)
                high30 = v if high30 is None else max(high30, v)
        prev = pt

    first = pts[0][0]
    out = {"since": _day(runs[first][1]), "n": listed, "stock_pct": round(100 * instock / listed) if listed else 0}
    if first > 0:
        out["new"] = True
    if chg:
        p0, p1 = chg
        rate = runs[p1[0]][2]
        old = round(p0[3] * rate, 2) if len(p0) > 3 and len(p1) > 3 else p0[1]  # same rate: only the shop's change
        new = eur_at(p1, runs)
        out.update(chg=round(new - old, 2), chg_pct=round((new - old) / old * 100, 1), chg_at=runs[p1[0]][1], prev=old)
        if low is not None:
            out.update(low=low[0], low_at=_day(runs[low[1][0]][1]), at_low=bool(o["in_stock"]) and o["price_eur"] <= low[0] + SAME)
        if low30 is not None:
            out.update(low30=low30, high30=high30, drops30=drops, rises30=rises)
    return out


def events(t: dict, since: str | None = None) -> list[dict]:
    """Changes between crawls, newest first: drop / rise (price), in / out (stock),
    new (listed after the shop's first crawl, or again after removal), gone (no longer listed).
    old_eur/new_eur: EUR at that crawl's rate, so only the shop's own change shows."""
    out = []
    for i, (site, pts) in t["series"].items():
        runs = t["sites"][site]["runs"]
        prev = None
        for k, pt in enumerate(pts):
            ri, e, s = pt[:3]
            at = runs[ri][1]
            ev = None
            if k == 0:
                if ri > 0:
                    ev = {"kind": "new"}
            elif e is None:
                ev = {"kind": "gone"}
            elif pts[k - 1][1] is None:
                ev = {"kind": "new", "relisted": True}
            elif price_changed(prev, pt):
                ev = {"kind": "drop" if e < prev[1] else "rise", "stock": [bool(prev[2]), bool(s)] if s != prev[2] else None}
            elif s != prev[2]:
                ev = {"kind": "in" if s else "out"}
            if ev and (since is None or at >= since):
                rate = runs[ri][2]
                old = None if prev is None else round(prev[3] * rate, 2) if len(prev) > 3 and len(pt) > 3 else prev[1]
                new = None if e is None else eur_at(pt, runs)
                ev.update(id=i, site=site, at=at, old_eur=old, new_eur=new, in_stock=bool(s) if e is not None else None)
                if ev["kind"] in ("drop", "rise"):
                    ev["pct"] = round((new - old) / old * 100, 1)
                out.append(ev)
            if e is not None:
                prev = pt
    out.sort(key=lambda e: e["at"], reverse=True)
    return out


def web_payload(t: dict, current_ids: set[str]) -> dict:
    """What the web view needs: runs and series; descriptions only of offers not in the index."""
    return {
        "sites": {s: {"cur": v["cur"], "runs": [[r[1], r[2]] for r in v["runs"]]} for s, v in t["sites"].items()},
        "series": t["series"],
        "desc": {i: d for i, d in t["desc"].items() if i not in current_ids},
    }
