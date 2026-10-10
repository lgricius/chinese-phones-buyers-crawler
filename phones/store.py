"""Storage. Crawling and parsing are separate steps:

  fetch  -> data/raw/<run>/<site>.jsonl.gz       every HTTP response (gzip), last N runs kept
  parse  -> data/snapshots/<run>/<site>.json.gz  normalized offers = price archive (gzip, kept forever)
  index  -> data/index.json                      latest snapshot of every site (what queries read),
                                                 each offer with its price statistics (trends.py)
            data/trends.json                     compact price/stock change points of every offer

`reindex` re-parses all stored raw responses offline, so parser fixes apply to
past crawls without hitting the shops again. A site that fails keeps its
previous snapshot in the index.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from . import config, details, trends
from . import normalize as n
from . import raw
from .models import apply_bank_discount
from .http import BrowserClient
from .sites import ENABLED, SITES

DATA = Path(__file__).resolve().parent.parent / "data"
RAW = DATA / "raw"
SNAPSHOTS = DATA / "snapshots"
INDEX = DATA / "index.json"
TRENDS = DATA / "trends.json"
HISTORY_OLD = DATA / "history.json"
CATALOG_JS = DATA / "catalog.js"  # same data as a script, so web/index.html works from file://
TRENDS_JS = DATA / "trends.js"  # price history for the web view, loaded on demand
KEEP_RAW = 10


def _write_json(path: Path, obj, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    text = json.dumps(obj, ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
    if path.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            f.write(text)
    else:
        tmp.write_text(text)
    tmp.replace(path)


def _read_json(path: Path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    return json.loads(path.read_text())


def snapshot_files() -> list[tuple[str, str, Path]]:
    """[(run, site, path)] of every stored snapshot, oldest first (plain .json from before gzip too)."""
    out = {}
    if SNAPSHOTS.exists():
        for f in sorted(SNAPSHOTS.glob("*/*.json*")):
            if f.name.endswith((".json", ".json.gz")):
                out[(f.parent.name, f.name.split(".")[0])] = f  # .json.gz sorts after .json and wins
    return [(run, site, f) for (run, site), f in sorted(out.items())]


def compress_snapshots() -> int:
    """Gzip snapshots still stored as plain JSON (about 10x smaller)."""
    n = 0
    for _, _, f in snapshot_files():
        if f.suffix == ".json":
            _write_json(f.with_name(f.name + ".gz"), json.loads(f.read_text()))
            f.unlink()
            n += 1
    return n


def load_trends() -> dict:
    return _read_json(TRENDS) if TRENDS.exists() else trends.empty()


def load_index() -> dict:
    if not INDEX.exists():
        return {"updated_at": None, "sites": {}, "offers": []}
    return json.loads(INDEX.read_text())


def _run_crawled_at(run_dir: Path) -> str:
    meta = run_dir / "run.json"
    if meta.exists():
        return json.loads(meta.read_text())["crawled_at"]
    return datetime.strptime(run_dir.name, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()


def _parse_to_snapshot(site: str, run: str, crawled_at: str, raw_path: Path, log) -> int:
    records = raw.read(raw_path)
    offers = SITES[site].parse(records, log)
    if not offers:
        raise RuntimeError("no offers parsed")
    pct = config.BANK_TRANSFER_DISCOUNT.get(site)
    apply_bank_discount(offers, pct)
    stated = next((d for d in (details.bank_transfer_discount(r["body"]) for r in records[:400]) if d), None)
    if stated is not None and stated != pct:
        log(f"WARNING {site}: site states a {stated}% bank-transfer discount, config.BANK_TRANSFER_DISCOUNT has {pct}")
    rows = [{**o.to_dict(), "crawled_at": crawled_at} for o in offers]
    _write_json(
        SNAPSHOTS / run / f"{site}.json.gz",
        {"site": site, "crawled_at": crawled_at, "currency": "EUR", "count": len(rows), "offers": rows},
    )
    (SNAPSHOTS / run / f"{site}.json").unlink(missing_ok=True)
    return len(rows)


def config_key(o: dict) -> str:
    return f"{o['model_key']}|{o['ram_gb']}|{o['storage_gb']}|{o['edition']}"


SPECS = {"chipset": "model_chipset", "battery_mah": "model_battery_mah"}


def agree_specs(offers: list[dict]) -> None:
    """One chipset and battery per model and hardware edition (CN incl. CN + global ROM, or global),
    as `model_chipset` / `model_battery_mah`: filters use these, so shops naming a chip differently
    ("8 Elite Extreme Gen 6" vs "8 Elite Gen 6") or a page with a stray value do not split a model.
    Each shop votes with its most common value; ties go to more offers, then the value more common
    in the whole catalog. A model + edition no shop states takes the model's value."""
    overall = {f: Counter(o.get(f) for o in offers if o.get(f)) for f in SPECS}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for o in offers:
        groups[(o["model_key"], "global" if o["edition"] == "global" else "cn")].append(o)
        groups[(o["model_key"], None)].append(o)

    def vote(group: list[dict], f: str):
        by_site: dict[str, Counter] = defaultdict(Counter)
        for o in group:
            if o.get(f):
                by_site[o["site"]][o[f]] += 1
        if not by_site:
            return None
        shops = Counter(c.most_common(1)[0][0] for c in by_site.values())
        offers_n = sum(by_site.values(), Counter())
        return max(shops, key=lambda v: (shops[v], offers_n[v], overall[f][v], str(v)))

    agreed = {(k, f): vote(g, f) for k, g in groups.items() for f in SPECS}
    for o in offers:
        hw = "global" if o["edition"] == "global" else "cn"
        for f, field in SPECS.items():
            o[field] = agreed[((o["model_key"], hw), f)] or agreed[((o["model_key"], None), f)]


def update_trends(full: bool = False, log=print) -> dict:
    """Add new snapshots to data/trends.json (full=True: rebuild from all snapshots)."""
    t = trends.empty() if full or not TRENDS.exists() else _read_json(TRENDS)
    t, rebuilt = trends.update(t, [x for x in snapshot_files() if x[1] in ENABLED], _read_json)
    if rebuilt:
        log("trends: an older snapshot changed, rebuilt from all snapshots")
    _write_json(TRENDS, t, indent=None)
    return t


def rebuild_index(full_trends: bool = False, log=print) -> dict:
    """Index = latest snapshot of every site, with price statistics per offer.
    Also refreshes data/trends.json and the web view's data files."""
    if n := compress_snapshots():
        log(f"gzipped {n} snapshots")
    latest: dict[str, Path] = {}
    for _, site, f in snapshot_files():
        if site in ENABLED:
            latest[site] = f  # oldest first -> last one wins
    index = {"updated_at": None, "sites": {}, "offers": [], "disabled": dict(config.DISABLED_SITES)}
    for site, f in sorted(latest.items()):
        d = _read_json(f)
        index["offers"] += d["offers"]
        index["sites"][site] = {"crawled_at": d["crawled_at"], "count": d["count"], "snapshot": str(f.relative_to(DATA))}
    index["updated_at"] = max((s["crawled_at"] for s in index["sites"].values()), default=None)
    agree_specs(index["offers"])
    t = update_trends(full_trends, log)
    for o in index["offers"]:
        o["oid"] = trends.offer_id(o)
        o["trend"] = trends.stats(t, o)
    _write_json(INDEX, index)
    write_catalog_js(index)
    _write_js(TRENDS_JS, "TRENDS", trends.web_payload(t, {o["oid"] for o in index["offers"]}))
    HISTORY_OLD.unlink(missing_ok=True)  # replaced by trends.json
    return index


def _write_js(path: Path, name: str, obj) -> None:
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_suffix(".tmp")
    tmp.write_text(f"window.{name} = " + payload.replace("</", "<\\/") + ";\n")
    tmp.replace(path)


def write_catalog_js(index: dict) -> None:
    """Browsers block fetch() of JSON from file:// pages but allow <script src>,
    so the web view also gets the data as `window.CATALOG = {...}`."""
    _write_js(CATALOG_JS, "CATALOG", {"index": index})


def prune_raw(keep: int) -> None:
    if not RAW.exists():
        return
    runs = sorted(p for p in RAW.iterdir() if p.is_dir())
    for old in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(old)


async def _fetch_site(name: str, run: str, log) -> tuple[str, Path | None, str | None, float]:
    start = time.monotonic()
    client = BrowserClient()
    path = RAW / run / f"{name}.jsonl.gz"
    writer = raw.RawWriter(path)
    log(f"{name}: start (browser profile {client.profile})")
    try:
        await SITES[name].fetch(client, writer, log)
        log(f"{name}: {writer.count} responses stored")
        return name, path, None, time.monotonic() - start
    except Exception as e:
        return name, None, f"{type(e).__name__}: {e}", time.monotonic() - start
    finally:
        writer.close()
        await client.close()


async def crawl(sites: list[str] | None = None, log=print, keep_raw: int = KEEP_RAW) -> dict:
    """Fetch the given sites (default: all) concurrently, then parse and index."""
    names = sites or ENABLED
    now = datetime.now(timezone.utc)
    run, crawled_at = now.strftime("%Y-%m-%dT%H%M%SZ"), now.isoformat(timespec="seconds")
    _write_json(RAW / run / "run.json", {"crawled_at": crawled_at, "sites": names})
    results = await asyncio.gather(*(_fetch_site(name, run, log) for name in names))

    summary = {}
    for name, path, err, secs in results:
        if err:
            summary[name] = {"ok": False, "error": err, "seconds": round(secs)}
            continue
        try:
            count = _parse_to_snapshot(name, run, crawled_at, path, log)
            summary[name] = {"ok": True, "offers": count, "seconds": round(secs)}
        except Exception as e:  # raw data is kept, so a parser fix + reindex recovers it
            summary[name] = {"ok": False, "error": f"parse failed: {e}", "seconds": round(secs)}
    rebuild_index(log=log)
    prune_raw(keep_raw)
    return summary


def _renormalize(o: dict) -> bool:
    brand, model = n.split_brand(o["title"], o["brand"])
    new = {"brand": brand, "model": n.pretty_model(model), "model_key": n.model_key(brand, model), "chipset": n.norm_chipset(o.get("chipset"))}
    changed = any(o.get(k) != v for k, v in new.items())
    o.update(new)
    return changed


def reindex(log=print) -> dict:
    """Re-parse every stored raw run offline and rebuild snapshots + index.
    Snapshots whose raw data was pruned only get model names re-normalized."""
    reparsed, renamed = 0, 0
    raw_runs = sorted(p for p in RAW.iterdir() if p.is_dir()) if RAW.exists() else []
    with_raw = set()
    for run_dir in raw_runs:
        crawled_at = _run_crawled_at(run_dir)
        for f in sorted(run_dir.glob("*.jsonl.gz")):
            site = f.name.removesuffix(".jsonl.gz")
            if site not in ENABLED:
                continue
            try:
                reparsed += _parse_to_snapshot(site, run_dir.name, crawled_at, f, log)
                with_raw.add((run_dir.name, site))
            except Exception as e:
                log(f"{run_dir.name}/{site}: {e}")
    for run, site, f in snapshot_files():
        if (run, site) in with_raw:
            continue
        d = _read_json(f)
        c = sum(_renormalize(o) for o in d["offers"])
        if c:
            _write_json(f, d)
            renamed += c
    index = rebuild_index(full_trends=True, log=log)
    return {"reparsed_offers": reparsed, "runs_with_raw": len(raw_runs), "renamed_without_raw": renamed, "index_offers": len(index["offers"])}


def iter_snapshots(site: str | None = None):
    """Yield (crawled_at, offers) for every stored snapshot, oldest first."""
    for _, s, f in snapshot_files():
        if site and s != site:
            continue
        d = _read_json(f)
        yield d["crawled_at"], d["offers"]
