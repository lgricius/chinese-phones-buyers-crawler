"use strict";
// Catalog view. Static app: data comes from ../data/catalog.js (window.CATALOG, works
// from file://) or else ../data/index.json (any static web server). Price history
// (../data/trends.js) is loaded when a chart or the Updates view needs it.
// Filter state lives in the URL hash.

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const eur = (p) => "€" + p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const mem = (r, s) => `${r ?? "?"}/${s == null ? "?" : s >= 1024 ? s / 1024 + "TB" : s + "GB"}`;
const PAGE = 200;

const EDITIONS = { cn: "CN", cn_global_rom: "CN + Global ROM", global: "Global" };
const CONDITIONS = { new: "New", new_unsealed: "New, unsealed", used: "Used", refurbished: "Refurbished" };
const EDITION_ORDER = Object.keys(EDITIONS);

// Multi-select facets: name -> value of an offer.
const FACETS = {
  editions: (o) => o.edition,
  brands: (o) => o.brand,
  sites: (o) => o.site,
  rams: (o) => String(o.ram_gb ?? "?"),
  storages: (o) => String(o.storage_gb ?? "?"),
  conds: (o) => o.condition || "new",
  colors: (o) => o.color_family || "none",
  chips: (o) => o.chip || "not stated",
  batts: (o) => battBucket(o.batt),
  gplay: (o) => (o.google_play === true ? "yes" : o.google_play === false ? "no" : "not stated"),
  types: (o) => o.device_type || "phone",
};

// Battery ranges (mAh): [label, from]; an offer falls in the last range it reaches.
const BATTS = [["< 5000", 0], ["5000–5999", 5000], ["6000–6999", 6000], ["7000–7999", 7000], ["8000–9999", 8000], ["10000+", 10000]];
const battBucket = (mah) => (mah ? BATTS.filter(([, from]) => mah >= from).pop()[0] : "not stated");

const DEFAULTS = () => ({
  q: "", view: "models", sort: "price", dir: 1, stock: true, eu: false, bank: false, coupon: false, ship: false, split: true, pmin: "", pmax: "",
  trend: "", ev: "",
  ...Object.fromEntries(Object.keys(FACETS).map((f) => [f, new Set(f === "types" ? ["phone"] : [])])),
});

// Not filters: "Reset filters" keeps these.
const KEEP = ["view", "sort", "dir", "split", "bank", "coupon", "ship", "ev"];
const filtered = () => Object.entries(DEFAULTS()).some(([k, d]) =>
  !KEEP.includes(k) && (d instanceof Set ? S[k].size !== d.size || [...S[k]].some((x) => !d.has(x)) : S[k] !== d));

let S = DEFAULTS();
let OFFERS = [];
let UPDATED = "";
const BY_OID = new Map(); // offer id (trends) -> offers
const BY_CFG = new Map(); // model|ram|storage|edition -> offers
const MODEL_SPECS = new Map(); // model_key -> {chip, batt} (for removed offers in Updates)
let shown = PAGE;
const open = new Set();
const sel = new Map(); // open model row -> variant shown in its price chart

// ---------- data ----------

async function load() {
  const idx = window.CATALOG ? window.CATALOG.index : await fetch("../data/index.json").then((r) => r.json());
  UPDATED = idx.updated_at || "";
  const norm = (parts) => " " + parts.join(" ").toLowerCase().replace(/[()\-,]/g, " ").replace(/\s+/g, " ") + " ";
  OFFERS = idx.offers.map((o, i) => ({
    ...o,
    id: i,
    price_card: o.price_eur,
    cfg: `${o.model_key}|${o.ram_gb}|${o.storage_gb}|${o.edition}`,
    name: norm([o.brand, o.model, o.model_key, o.title]),
    hay: " " + [o.brand, o.model, o.title, o.site, o.chipset, o.os, o.color, o.color_family, EDITIONS[o.edition], o.edition,
      CONDITIONS[o.condition], o.ships_from === "eu" ? "eu europe" : "", o.sku].join(" ").toLowerCase().replace(/\s+/g, " ") + " ",
  }));
  // Chipset/battery for filters and grouping: what the shops agree on for the model and
  // edition (store.agree_specs), so a shop naming the chip differently does not drop out.
  for (const o of OFFERS) {
    o.chip = o.model_chipset ?? o.chipset;
    o.batt = o.model_battery_mah ?? o.battery_mah;
    if (!MODEL_SPECS.has(o.model_key)) MODEL_SPECS.set(o.model_key, { chip: o.chip, batt: o.batt });
  }
  const push = (map, k, o) => (map.get(k) || map.set(k, []).get(k)).push(o);
  for (const o of OFFERS) {
    if (o.oid) push(BY_OID, o.oid, o);
    push(BY_CFG, o.cfg, o);
  }
  $("#fresh").innerHTML = Object.entries(idx.sites).map(([s, v]) => `${esc(s)} ${esc(v.crawled_at.slice(0, 16).replace("T", " "))}`).join("<br>") + " UTC";
  for (const [s, why] of Object.entries(idx.disabled || {})) $("#fresh").innerHTML += `<br><span title="${esc(why)}"><s>${esc(s)}</s> off</span>`;
  readHash();
  bind();
  render();
}

// ---------- search & filters ----------

function toGb(n, unit) {
  return unit === "tb" || +n <= 2 ? +n * 1024 : +n;
}

// Free text; "16/512", "12+256gb", "1tb", "16gb" become RAM/storage filters.
function parseQuery(q) {
  const out = { words: [], ram: null, storage: null };
  for (const t of q.toLowerCase().split(/\s+/).filter(Boolean)) {
    let m;
    if ((m = t.match(/^(\d{1,2})(?:gb)?[/+](\d{1,4})(gb|tb)?$/))) {
      out.ram = +m[1];
      out.storage = toGb(m[2], m[3]);
    } else if ((m = t.match(/^(\d+)tb$/))) out.storage = +m[1] * 1024;
    else if ((m = t.match(/^(\d+)gb$/))) +m[1] <= 32 ? (out.ram = +m[1]) : (out.storage = +m[1]);
    else out.words.push(t);
  }
  return out;
}

let Q = parseQuery("");

// Words match the start of a word ("find" ~ "finder"); bare numbers must match a
// whole word ("9" does not match "90"). Names are searched first; when that finds
// nothing (with the other filters applied) all fields are searched: chipset, OS, colours...
const wordHit = (text, w) => (/^\d+$/.test(w) ? text.includes(` ${w} `) : text.includes(" " + w));

function pass(o, except) {
  const upd = S.view === "updates"; // changes include sold-out offers; the change type replaces the trend filter
  if (S.stock && !o.in_stock && !upd) return false;
  if (S.trend && !upd && except !== "trend" && !TREND_FILTERS[S.trend][1](o.trend)) return false;
  if (S.eu && o.ships_from !== "eu") return false;
  if (S.pmin !== "" && o.price_eur < +S.pmin) return false;
  if (S.pmax !== "" && o.price_eur > +S.pmax) return false;
  if (Q.ram != null && o.ram_gb !== Q.ram) return false;
  if (Q.storage != null && o.storage_gb !== Q.storage) return false;
  if (Q.field) for (const w of Q.words) if (!wordHit(o[Q.field], w)) return false;
  for (const [name, fn] of Object.entries(FACETS)) if (name !== except && S[name].size && !S[name].has(fn(o))) return false;
  return true;
}

// ---------- facets ----------

// Facet counts use the same unit as the result list: model-colour lines, models, or offers.
function countUnit() {
  if (S.view === "offers") return ["offer", (o) => o.id];
  if (S.view === "updates") return ["changed offer", (o) => o.oid];
  return S.split ? ["model colour", (o) => `${o.model_key}|${o.color_family || "none"}`] : ["model", (o) => o.model_key];
}

const plural = (n, unit) => `${n} ${unit}${n === 1 ? "" : "s"}`;

function facetValues(name) {
  const [, unitKey] = countUnit();
  const models = new Map(); // value -> Set(unit), counted with all *other* filters applied
  for (const o of S.view === "updates" && TR ? eventOffers() : OFFERS) {
    if (!pass(o, name)) continue;
    const v = FACETS[name](o);
    if (!models.has(v)) models.set(v, new Set());
    models.get(v).add(unitKey(o));
  }
  const all = new Set(OFFERS.map(FACETS[name]));
  const vals = [...all].map((v) => [v, models.get(v)?.size || 0]);
  const num = (v) => (v === "?" ? 1e9 : +v);
  if (name === "rams" || name === "storages") vals.sort((a, b) => num(a[0]) - num(b[0]));
  else if (name === "batts") vals.sort((a, b) => BATTS.findIndex((x) => x[0] === a[0]) - BATTS.findIndex((x) => x[0] === b[0]) || (a[0] === "not stated") - (b[0] === "not stated"));
  else if (name === "editions") vals.sort((a, b) => EDITION_ORDER.indexOf(a[0]) - EDITION_ORDER.indexOf(b[0]));
  else vals.sort((a, b) => b[1] - a[1] || String(a[0]).localeCompare(b[0]));
  return vals;
}

function label(name, v) {
  if (name === "editions") return EDITIONS[v] || v;
  if (name === "conds") return CONDITIONS[v] || v;
  if (name === "rams") return v === "?" ? "?" : v + " GB";
  if (name === "storages") return v === "?" ? "?" : +v >= 1024 ? +v / 1024 + " TB" : v + " GB";
  if (name === "batts") return v === "not stated" ? "?" : v;
  return v;
}

const swatch = (family, cls = "", tip = "") => `<span class="sw sw-${esc(family || "none")} ${cls}"${tip ? ` title="${esc(tip)}"` : ""}></span>`;

function colorNames(family) {
  return [...new Set(OFFERS.filter((o) => (o.color_family || "none") === family).map((o) => o.color))].filter(Boolean).sort().join(", ");
}

// Colour dots for a set of offers (same variant, same shop): one per colour, tooltip with its price.
function colorDots(offers, cellPrice) {
  const byColor = new Map();
  for (const o of offers) {
    const k = o.color || "?";
    if (!byColor.has(k) || o.price_eur < byColor.get(k).price_eur) byColor.set(k, o);
  }
  return [...byColor.values()].sort((a, b) => a.price_eur - b.price_eur).map((o) => {
    const more = o.price_eur > cellPrice + 0.009;
    const tip = `${o.color || "colour not stated"}: ${eur(o.price_eur)}${o.in_stock ? "" : " (out of stock)"}${o.color_verified === false ? " - shop shows one price for all colours" : ""}`;
    return swatch(o.color_family, (more ? "more " : "") + (o.in_stock ? "" : "oos"), tip);
  }).join("");
}

function renderFacets() {
  const [unit, unitKey] = countUnit();
  $("#f-trend").innerHTML = S.view === "updates"
    ? `<span class="muted">Pick change types above the list.</span>`
    : Object.entries(TREND_FILTERS).map(([k, [text, fn, tip]]) => {
      const n = new Set(OFFERS.filter((o) => pass(o, "trend") && fn(o.trend)).map(unitKey)).size;
      return `<button class="chip${S.trend === k ? " on" : ""}${n ? "" : " zero"}" data-trend="${k}" title="${esc(tip)}: ${plural(n, unit)}">${esc(text)}</button>`;
    }).join("");
  for (const name of Object.keys(FACETS)) {
    const el = $(`#f-${name}`);
    let vals = facetValues(name);
    const search = { brands: "#brandq", chips: "#chipq" }[name];
    if (search) {
      const words = $(search).value.trim().toLowerCase().split(/\s+/).filter(Boolean);
      if (words.length) vals = vals.filter(([v]) => S[name].has(v) || words.every((w) => v.toLowerCase().includes(w)));
    }
    if (name === "colors") {
      el.innerHTML = vals.map(([v, n]) =>
        `<button class="chip${S[name].has(v) ? " on" : ""}${n ? "" : " zero"}" data-f="${name}" data-v="${esc(v)}" title="${plural(n, unit)}: ${esc(colorNames(v))}">${swatch(v)}${esc(v)}</button>`).join("");
    } else if (name === "rams" || name === "storages" || name === "batts") {
      el.innerHTML = vals.map(([v, n]) =>
        `<button class="chip${S[name].has(v) ? " on" : ""}${n ? "" : " zero"}" data-f="${name}" data-v="${esc(v)}" title="${plural(n, unit)}">${esc(label(name, v))}</button>`).join("");
    } else {
      el.innerHTML = vals.map(([v, n]) =>
        `<label class="facet${n ? "" : " zero"}" title="${plural(n, unit)}"><input type="checkbox" data-f="${name}" data-v="${esc(v)}"${S[name].has(v) ? " checked" : ""}> ${esc(label(name, v))}<span class="n">${n}</span></label>`).join("");
    }
  }
}

// ---------- models view ----------

function cheapest(list) {
  return list.reduce((b, o) => (!b || (o.in_stock && !b.in_stock) || (o.in_stock === b.in_stock && o.price_eur < b.price_eur) ? o : b), null);
}

function mode(values) {
  const c = new Map();
  for (const v of values) if (v != null) c.set(v, (c.get(v) || 0) + 1);
  return [...c.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
}

// One result row per model, or per model + colour family when colours are separate rows.
function groupModels(list) {
  const m = new Map();
  for (const o of list) {
    const family = o.color_family || "none";
    const key = S.split ? `${o.model_key}|${family}` : o.model_key;
    if (!m.has(key)) m.set(key, { key, brand: o.brand, model: o.model, family: S.split ? family : null, offers: [] });
    m.get(key).offers.push(o);
  }
  return [...m.values()].map((g) => {
    const best = cheapest(g.offers);
    return {
      ...g,
      best,
      from: best.price_eur,
      editions: EDITION_ORDER.filter((e) => g.offers.some((o) => o.edition === e)),
      sites: [...new Set(g.offers.map((o) => o.site))].sort(),
      variants: new Set(g.offers.map((o) => o.cfg)).size,
      families: [...new Set(g.offers.map((o) => o.color_family || "none"))].sort(),
      colorNames: [...new Set(g.offers.map((o) => o.color).filter(Boolean))].sort(),
      chipset: mode(g.offers.map((o) => o.chip)),
      display: mode(g.offers.map((o) => o.display_in)),
      battery: mode(g.offers.map((o) => o.batt)),
    };
  });
}

const MODEL_COLS = [
  ["brand", "Brand", (g) => g.brand],
  ["name", "Model", (g) => [`${g.brand} ${g.model}`, g.family || ""]],
  ["editions", "Editions", (g) => g.editions.length],
  ["variants", "Variants", (g) => g.variants],
  ["sites", "Shops", (g) => g.sites.length],
  ["colors", "Colour", (g) => (g.family ? g.family : g.families.length)],
  ["chipset", "Chipset", (g) => g.chipset || "~"],
  ["display", "Screen", (g) => g.display ?? 0],
  ["battery", "Battery", (g) => g.battery ?? 0],
  ["price", "From", (g) => g.from],
  ["chg", "Change", (g) => recentPct(g.best)],
];
const priceTitle = (t) => {
  const tags = [S.coupon && "code", S.bank && "bank", S.ship && "+ship"].filter(Boolean).join(" ");
  return tags ? `${t} <span class="muted">${tags}</span>` : t;
};

function sortBy(list, cols) {
  const col = cols.find((c) => c[0] === S.sort) || cols.find((c) => c[0] === "price");
  const cmp = (x, y) => (typeof x === "number" ? x - y : String(x).localeCompare(String(y)));
  return list.sort((a, b) => {
    const x = col[2](a), y = col[2](b);
    if (Array.isArray(x)) { // e.g. [model, colour]: model first, then colour
      for (let i = 0; i < x.length; i++) { const c = cmp(x[i], y[i]); if (c) return c * S.dir; }
      return 0;
    }
    return cmp(x, y) * S.dir;
  });
}

function header(cols, numeric) {
  return "<tr>" + cols.map(([k, t]) =>
    `<th data-sort="${k}" class="${numeric.includes(k) ? "num " : ""}${S.sort === k ? "sorted" + (S.dir < 0 ? " desc" : "") : ""}">${k === "price" ? priceTitle(t) : t}</th>`).join("") + "</tr>";
}

function shipLine(o) {
  if (o.shipping_eur == null) return o.vat_note ? `Shipping: not quoted. ${o.vat_note}` : "";
  const cost = o.shipping_eur ? (o.shipping_estimated ? "~" : "") + eur(o.shipping_eur) : "free";
  return `Shipping to LT: ${cost}, ${o.shipping_method || ""}${o.shipping_estimated ? " (estimate)" : ""}` + (o.vat_note ? `\nVAT: ${o.vat_note}` : "");
}

function cell(o, isBest, siblings) {
  if (!o) return `<td class="num muted">–</td>`;
  const flags = (o.condition === "new_unsealed" ? '<span class="flag" title="new, unsealed by the shop">u</span>' : "") +
    (o.ships_from === "eu" ? '<span class="flag" title="ships from EU warehouse">E</span>' : "") +
    (o.ships_from === "us" ? '<span class="flag" title="ships from US warehouses only: EU import costs likely">US</span>' : "") +
    (o.bank_price ? '<span class="flag" title="bank-transfer price">b</span>' : "") +
    (o.coupon_price ? `<span class="flag" title="${esc(`code ${o.coupon_code}: -${eur(o.coupon_eur)}`)}">c</span>` : "") +
    (S.ship && o.shipping_estimated ? '<span class="flag" title="shipping cost estimated: the shop does not quote it for Lithuania">~</span>' : "") +
    (S.ship && o.shipping_eur == null ? '<span class="flag" title="shipping cost unknown, not included">?</span>' : "") +
    (o.vat === "not_stated" ? `<span class="flag warn" title="${esc(o.vat_note || "")}">VAT?</span>` : "") +
    (o.delivers === false ? '<span class="flag warn" title="the shop does not deliver to Lithuania">noLT</span>' : "");
  const tip = [o.title,
    `${o.color || "Colour not stated"}: ${eur(o.price_card)} standard` + (o.price_orig ? ` (site shows ${o.currency_orig} ${o.price_orig.toLocaleString("en-US")})` : ""),
    o.coupon_eur ? `Discount code ${o.coupon_code}: -${eur(o.coupon_eur)} (${o.coupon_note})${o.coupon_price ? "" : " - tick Discount codes"}` : "",
    o.price_eur_bank ? `Bank transfer: ${o.payment_note}${o.coupon_price && o.bank_price ? ", taken after the code (stacking not confirmed)" : ""}` : o.payment_note || "",
    o.coupon_price || o.bank_price ? `Item after discounts: ${eur(o.price_item)}` : "",
    shipLine(o),
    S.ship ? `Total: ${eur(o.price_eur)}` : "",
    o.delivery_note || "",
    o.edition_evidence ? "Edition: " + o.edition_evidence : "",
    trendTip(o)].filter(Boolean).join("\n");
  const dots = siblings ? `<span class="dots">${colorDots(siblings, o.price_eur)}</span>` : "";
  return `<td class="num${isBest ? " best-cell" : ""}"><a href="${esc(o.url)}" target="_blank" rel="noopener" title="${esc(tip)}" class="${o.in_stock ? "" : "oos"}">${eur(o.price_eur)}</a>${flags}${dots}</td>`;
}

// Shops whose own chipset/battery differs from what the others agree on (filters use the agreed value).
function specDiffs(offers) {
  const diffs = new Map();
  for (const o of offers) {
    if (o.chipset && o.chip && o.chipset !== o.chip) diffs.set(`${o.site}|${o.chipset}`, `${o.site} says ${o.chipset}`);
    if (o.battery_mah && o.batt && o.battery_mah !== o.batt) diffs.set(`${o.site}|${o.battery_mah}`, `${o.site} says ${o.battery_mah} mAh`);
  }
  return diffs.size ? `<br><span title="Filters use what most shops say for this model and edition">Shops differ: ${esc([...diffs.values()].join("; "))}</span>` : "";
}

// Offer's own spec value; marked when it differs from the agreed one, filled in (muted) when not stated.
function specCell(own, agreed, unit = "") {
  if (own != null && agreed != null && own !== agreed) return `${esc(own)}${unit} <span class="flag warn" title="${esc(`Other shops say ${agreed}${unit}; filters use that`)}">≠</span>`;
  if (own == null && agreed != null) return `<span class="muted" title="Not stated by this shop: what other shops say">${esc(agreed)}${unit}</span>`;
  return own == null ? "" : esc(own) + unit;
}

function detail(g) {
  const sites = g.sites;
  const chosen = sel.get(g.key) || g.best.cfg;
  const rowsMap = new Map();
  for (const o of g.offers) {
    const key = o.cfg;
    if (!rowsMap.has(key)) rowsMap.set(key, { ram: o.ram_gb, storage: o.storage_gb, edition: o.edition, cfg: o.cfg, bySite: {} });
    (rowsMap.get(key).bySite[o.site] ||= []).push(o);
  }
  const rows = [...rowsMap.values()].sort((a, b) => (a.ram ?? 0) - (b.ram ?? 0) || (a.storage ?? 0) - (b.storage ?? 0) ||
    EDITION_ORDER.indexOf(a.edition) - EDITION_ORDER.indexOf(b.edition));
  const body = rows.map((c) => {
    const cheapestBySite = Object.fromEntries(Object.entries(c.bySite).map(([s, list]) => [s, cheapest(list)]));
    const best = cheapest(Object.values(cheapestBySite));
    return `<tr class="vrow${c.cfg === chosen ? " chosen" : ""}" data-g="${esc(g.key)}" data-cfg="${esc(c.cfg)}" title="Click for the price history of this variant">` +
      `<td>${mem(c.ram, c.storage)}</td><td><span class="badge ${c.edition}">${EDITIONS[c.edition]}</span></td>` +
      sites.map((s) => cell(cheapestBySite[s], cheapestBySite[s] === best, c.bySite[s])).join("") +
      `<td>${esc(best.site)} <span class="muted">${esc(best.color || "")}</span></td><td class="num">${trendBadge(best)}</td></tr>`;
  }).join("");
  const perEdition = (field) => { // "Dimensity 8550 (CN) / Snapdragon 8 Elite (Global)" when the hardware differs
    const vals = new Map();
    for (const o of g.offers) if (o[field] != null) (vals.get(o[field]) || vals.set(o[field], new Set()).get(o[field])).add(o.edition === "global" ? "Global" : "CN");
    return vals.size > 1 ? [...vals].map(([v, eds]) => `${v}${field === "batt" ? " mAh" : ""} (${[...eds].join(", ")})`).join(" / ") : null;
  };
  const specs = [
    perEdition("chip") || g.chipset, g.display && g.display + '"', perEdition("batt") || (g.battery && g.battery + " mAh"),
    mode(g.offers.map((o) => o.charging_w)) && mode(g.offers.map((o) => o.charging_w)) + " W",
    mode(g.offers.map((o) => o.os)),
    { true: "Google Play: yes", false: "Google Play: no" }[mode(g.offers.map((o) => o.google_play))],
  ].filter(Boolean).map(esc).join(" · ");
  const evidence = EDITION_ORDER.map((e) => g.offers.find((o) => o.edition === e && o.edition_evidence))
    .filter(Boolean).map((o) => `<b>${EDITIONS[o.edition]}</b> (${esc(o.site)}): “${esc(o.edition_evidence)}”`).join("<br>");
  return `<table class="matrix"><tr><th>Variant</th><th>Edition</th>${sites.map((s) => `<th class="num">${esc(s)}</th>`).join("")}<th>Best</th><th class="num" title="last price change of the best offer (30 days)">Change</th></tr>${body}</table>
    <div class="meta">Dots = ${g.family ? "shop colour names in this colour family" : "colours at that shop"} (hover for price; red ring = costs more than shown, faded = out of stock).<br>${specs}${specDiffs(g.offers)}${evidence ? "<br>" + evidence : ""}</div>
    ${trendPanel(g, chosen)}`;
}

function renderModels(list) {
  const groups = sortBy(groupModels(list), MODEL_COLS);
  const models = new Set(groups.map((g) => g.offers[0].model_key)).size;
  $("#count").textContent = S.split ? `${groups.length} model colours · ${models} models · ${list.length} offers` : `${models} models · ${list.length} offers`;
  const page = groups.slice(0, shown);
  $("#more").hidden = groups.length <= shown;
  if (!groups.length) return `<div class="empty">Nothing matches. Try removing a filter.</div>`;
  const rows = page.map((g) => {
    const isOpen = open.has(g.key);
    const r = `<tr class="model${isOpen ? " open" : ""}" data-key="${esc(g.key)}">
      <td>${esc(g.brand)}</td><td>${esc(g.model)}</td>
      <td>${g.editions.map((e) => `<span class="badge ${e}">${EDITIONS[e]}</span>`).join("")}</td>
      <td class="num">${g.variants}</td><td class="num" title="${esc(g.sites.join(", "))}">${g.sites.length}</td>
      <td class="nowrap">${g.family
        ? `${swatch(g.family)}${esc(g.family)}${g.colorNames.length && g.colorNames.join(", ").toLowerCase() !== g.family ? ` <span class="muted">${esc(g.colorNames.join(", "))}</span>` : ""}`
        : g.families.map((f) => swatch(f, "", f)).join("")}</td>
      <td>${esc(g.chipset || "")}</td><td class="num">${g.display ? g.display + '"' : ""}</td><td class="num">${g.battery || ""}</td>
      <td class="num"><span class="${g.best.in_stock ? "" : "oos"}">${eur(g.from)}</span> <span class="muted">${esc(g.best.site)}</span></td>
      <td class="num">${trendBadge(g.best)}</td></tr>`;
    return isOpen ? r + `<tr class="detail"><td colspan="${MODEL_COLS.length}">${detail(g)}</td></tr>` : r;
  }).join("");
  return `<table>${header(MODEL_COLS, ["variants", "sites", "display", "battery", "price", "chg"])}${rows}</table>`;
}

// ---------- offers view ----------

const OFFER_COLS = [
  ["brand", "Brand", (o) => o.brand],
  ["name", "Model", (o) => `${o.brand} ${o.model}`],
  ["variant", "Variant", (o) => (o.ram_gb ?? 0) * 1e5 + (o.storage_gb ?? 0)],
  ["edition", "Edition", (o) => EDITION_ORDER.indexOf(o.edition)],
  ["site", "Shop", (o) => o.site],
  ["price", "Price", (o) => o.price_eur],
  ["chg", "Change", (o) => recentPct(o)],
  ["shipping", "Shipping", (o) => o.shipping_eur ?? 1e9],
  ["orig", "Site price", (o) => o.price_orig ?? 0],
  ["condition", "Condition", (o) => o.condition || "new"],
  ["ships", "Ships", (o) => o.ships_from || "~"],
  ["chipset", "Chipset", (o) => o.chip || "~"],
  ["display", "Screen", (o) => o.display_in ?? 0],
  ["battery", "mAh", (o) => o.batt ?? 0],
  ["charging", "W", (o) => o.charging_w ?? 0],
  ["gplay", "GPlay", (o) => String(o.google_play)],
  ["colors", "Colour", (o) => o.color || "~"],
];

function renderOffers(list) {
  const rows = sortBy([...list], OFFER_COLS);
  $("#count").textContent = `${rows.length} offers · ${new Set(rows.map((o) => o.model_key)).size} models`;
  $("#more").hidden = rows.length <= shown;
  if (!rows.length) return `<div class="empty">Nothing matches. Try removing a filter.</div>`;
  const body = rows.slice(0, shown).map((o) => `<tr>
    <td>${esc(o.brand)}</td><td><a href="${esc(o.url)}" target="_blank" rel="noopener" title="${esc(o.title)}">${esc(o.model)}</a></td>
    <td class="num">${mem(o.ram_gb, o.storage_gb)}</td><td><span class="badge ${o.edition}" title="${esc(o.edition_evidence || "not stated: CN by default")}">${EDITIONS[o.edition]}</span></td>
    <td>${esc(o.site)}</td>${cell(o, false)}<td class="num">${trendBadge(o)}</td><td class="num" title="${esc(shipLine(o))}">${o.shipping_eur == null ? "?" : o.shipping_eur ? (o.shipping_estimated ? "~" : "") + eur(o.shipping_eur) : "free"}</td><td class="num muted">${o.price_orig ? esc(o.currency_orig + " " + o.price_orig.toLocaleString("en-US")) : ""}</td><td>${esc(CONDITIONS[o.condition] || "")}</td><td>${esc(o.ships_from || "")}</td>
    <td>${specCell(o.chipset, o.chip)}</td><td class="num">${o.display_in ? o.display_in + '"' : ""}</td><td class="num">${specCell(o.battery_mah, o.batt)}</td>
    <td class="num">${o.charging_w || ""}</td><td>${{ true: "yes", false: "no" }[o.google_play] || ""}</td><td>${swatch(o.color_family)}${esc(o.color || "")}${o.color_verified === false ? ' <span class="muted" title="shop shows one price for all colours">*</span>' : ""}</td></tr>`).join("");
  return `<table>${header(OFFER_COLS, ["variant", "price", "chg", "shipping", "orig", "display", "battery", "charging"])}${body}</table>`;
}

// ---------- price trends ----------
// Offers carry precomputed statistics (o.trend, see phones/trends.py) for badges, sorting
// and filters. The full history, ../data/trends.js, is loaded for charts and Updates:
// per offer id only the crawls where its price or stock changed, [run, EUR, in stock(, shop price)];
// shops priced in USD are compared in USD, so exchange-rate moves are not price changes.

const DAY = 864e5;
const SAME = 0.005;
const SHOPS = ["giztop", "wonda", "tradingshenzhen", "gshopper", "geekwills", "trinity"]; // fixed chart colour per shop
const shopColor = (site) => (SHOPS.includes(site) ? `var(--s${SHOPS.indexOf(site) + 1})` : "var(--muted)");
const within = (iso, days) => !!iso && Date.now() - Date.parse(iso) <= days * DAY;
const ago = (iso, short) => {
  const d = Math.floor((Date.now() - Date.parse(iso)) / DAY);
  return short ? (d < 1 ? "today" : `${d}d`) : d < 1 ? "today" : d === 1 ? "yesterday" : `${d} days ago`;
};
const signed = (v, unit = "%") => `${v > 0 ? "+" : "−"}${unit === "€" ? "€" + Math.abs(Math.round(v)).toLocaleString("en-US") : Math.abs(v).toFixed(1) + "%"}`;
const utc = (iso) => iso.slice(0, 16).replace("T", " ");
const shortDate = (t, withTime) => new Date(t).toLocaleString("en-GB", { day: "numeric", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit", timeZone: "UTC" } : { timeZone: "UTC" }) });

// Sidebar "Price trend": [chip text, test on o.trend, tooltip]
const TREND_FILTERS = {
  drop7: ["▼ 7 days", (t) => t?.chg < 0 && within(t.chg_at, 7), "price dropped in the last 7 days"],
  drop30: ["▼ 30 days", (t) => t?.chg < 0 && within(t.chg_at, 30), "price dropped in the last 30 days"],
  low: ["lowest seen", (t) => !!t?.at_low, "in stock at the lowest price seen (after a price change)"],
  new7: ["new 7 days", (t) => !!t?.new && within(t.since, 7), "listed for the first time in the last 7 days"],
};

const recentPct = (o) => (o.trend?.chg != null && within(o.trend.chg_at, 30) ? o.trend.chg_pct : 0);

function trendTip(o) {
  const t = o.trend;
  if (!t) return "";
  return [
    t.chg != null ? `Last price change ${ago(t.chg_at)}: ${eur(t.prev)} → ${eur(t.prev + t.chg)} (${signed(t.chg_pct)})` : "No price change seen",
    t.low != null ? `Lowest seen: ${eur(t.low)} (${t.low_at})${t.at_low ? ", that is now" : ""}` : "",
    t.low30 != null ? `Last 30 days: ${eur(t.low30)} – ${eur(t.high30)}, ${t.drops30} drops, ${t.rises30} rises` : "",
    `Listed since ${t.since}${t.new ? " (new)" : ""}, in stock at ${t.stock_pct}% of ${t.n} crawls (standard price)`,
  ].filter(Boolean).join("\n");
}

// Last price change (30 days) and "lowest seen".
function trendBadge(o) {
  const t = o?.trend;
  if (!t) return "";
  const tip = esc(trendTip(o));
  const low = t.at_low ? ` <span class="flag good" title="${tip}">low</span>` : "";
  if (t.chg != null && within(t.chg_at, 30)) {
    return `<span class="nowrap ${t.chg < 0 ? "delta-down" : "delta-up"}" title="${tip}">${t.chg < 0 ? "▼" : "▲"} ${signed(t.chg, "€").slice(1)} <span class="muted">${ago(t.chg_at, true)}</span></span>${low}`;
  }
  if (low) return low.trim();
  return t.new && within(t.since, 7) ? `<span class="flag" title="${tip}">new</span>` : "";
}

let TR = null;
let trLoading = null;
let trError = "";

function loadTrends() {
  return (trLoading ||= new Promise((resolve, reject) => {
    const done = () => (window.TRENDS ? resolve((TR = prepTrends(window.TRENDS))) : reject(new Error("no price history in data/trends.js")));
    if (window.TRENDS) return done();
    const s = document.createElement("script");
    s.src = "../data/trends.js?v=" + encodeURIComponent(UPDATED);
    s.onload = done;
    s.onerror = () => reject(new Error("data/trends.js is missing: run ./phones.sh reindex"));
    document.head.append(s);
  }));
}

// Placeholder until the history is loaded, then render again.
function needTrends(what) {
  if (!trError) loadTrends().then(() => render(), (e) => { trError = e.message; render(); });
  return `<div class="empty">${trError ? esc(trError) : `Loading ${what}…`}</div>`;
}

function prepTrends(raw) {
  for (const st of Object.values(raw.sites)) st.t = st.runs.map(([at]) => Date.parse(at));
  return { ...raw, events: null, gone: new Map() };
}

const eurAt = (pt, runs, r = pt[0]) => (pt.length > 3 ? Math.round(pt[3] * runs[r][1] * 100) / 100 : pt[1]);
const moved = (a, b) => (a.length > 3 && b.length > 3 ? Math.abs(a[3] - b[3]) > SAME : Math.abs(a[1] - b[1]) > SAME);

// Every change between crawls, newest first (same rules as trends.events in Python).
function trendEvents() {
  if (TR.events) return TR.events;
  const out = [];
  for (const [id, [site, pts]] of Object.entries(TR.series)) {
    const runs = TR.sites[site].runs;
    let prev = null;
    pts.forEach((pt, k) => {
      const [ri, e, s] = pt;
      let kind = null, relisted = false;
      if (k === 0) kind = ri > 0 ? "new" : null;
      else if (e == null) kind = "gone";
      else if (pts[k - 1][1] == null) { kind = "new"; relisted = true; }
      else if (moved(prev, pt)) kind = e < prev[1] ? "drop" : "rise";
      else if (s !== prev[2]) kind = s ? "in" : "out";
      if (kind) {
        const rate = runs[ri][1];
        const old = prev ? (prev.length > 3 && pt.length > 3 ? Math.round(prev[3] * rate * 100) / 100 : prev[1]) : null;
        const now = e == null ? null : eurAt(pt, runs);
        out.push({ id, site, at: runs[ri][0], kind, relisted, old, now, inStock: !!s, wasInStock: prev ? !!prev[2] : null,
          pct: old && now != null && (kind === "drop" || kind === "rise") ? ((now - old) / old) * 100 : null });
      }
      if (e != null) prev = pt;
    });
  }
  // Newest crawl first; within a crawl biggest drops, then rises, then the rest.
  const order = { drop: 0, rise: 1, in: 2, new: 3, out: 4, gone: 5 };
  return (TR.events = out.sort((a, b) => b.at.localeCompare(a.at) || order[a.kind] - order[b.kind] || Math.abs(b.pct || 0) - Math.abs(a.pct || 0)));
}

// The offer an event is about: the current one, or what it was when it was removed.
function evOffer(e) {
  const cur = BY_OID.get(e.id);
  if (cur) return cur[0];
  if (TR.gone.has(e.id)) return TR.gone.get(e.id);
  const d = TR.desc[e.id];
  let o = null;
  if (d) {
    const specs = MODEL_SPECS.get(d.model_key) || {};
    o = { ...d, oid: e.id, id: "gone-" + e.id, gone: true, in_stock: false, price_eur: e.old ?? 0, price_card: e.old ?? 0,
      cfg: `${d.model_key}|${d.ram_gb}|${d.storage_gb}|${d.edition}`, chip: specs.chip || d.chipset, batt: specs.batt || d.battery_mah,
      name: " " + [d.brand, d.model, d.model_key, d.title].join(" ").toLowerCase().replace(/[()\-,]/g, " ").replace(/\s+/g, " ") + " ",
      hay: " " + [d.brand, d.model, d.title, d.site, d.chipset, d.color, d.color_family, EDITIONS[d.edition], d.edition].join(" ").toLowerCase().replace(/\s+/g, " ") + " " };
  }
  TR.gone.set(e.id, o);
  return o;
}

function eventOffers() {
  return [...new Set(trendEvents().filter((e) => !S.ev || e.kind === S.ev).map(evOffer).filter(Boolean))];
}

const KINDS = { drop: "Price drops", rise: "Price rises", new: "New", in: "Back in stock", out: "Sold out", gone: "Removed" };

function changeText(e) {
  switch (e.kind) {
    case "drop":
    case "rise": {
      const stock = e.inStock !== e.wasInStock ? (e.inStock ? " · back in stock" : " · sold out") : "";
      return `<span class="muted">${eur(e.old)} →</span> ${eur(e.now)} <span class="${e.kind === "drop" ? "delta-down" : "delta-up"}">${e.kind === "drop" ? "▼" : "▲"} ${signed(e.now - e.old, "€").slice(1)} (${signed(e.pct)})</span>${stock}`;
    }
    case "new": return `${e.relisted ? "Listed again" : "New listing"} ${eur(e.now)}${e.inStock ? "" : " · out of stock"}`;
    case "gone": return `Removed <span class="muted">· was ${eur(e.old)}</span>`;
    case "in": return `Back in stock ${eur(e.now)}`;
    default: return `Sold out <span class="muted">· ${eur(e.now)}</span>`;
  }
}

function renderUpdates() {
  if (!TR) {
    $("#count").textContent = "Updates";
    $("#more").hidden = true;
    return needTrends("price history");
  }
  const all = trendEvents();
  const base = all.map((e) => [e, evOffer(e)]).filter(([, o]) => o && pass(o));
  const counts = {};
  for (const [e] of base) counts[e.kind] = (counts[e.kind] || 0) + 1;
  const list = S.ev ? base.filter(([e]) => e.kind === S.ev) : base;
  const runs = [...new Set(Object.values(TR.sites).flatMap((st) => st.runs.map(([at]) => at)))].sort();
  $("#count").textContent = `${plural(list.length, "update")} · ${new Set(list.map(([, o]) => o.model_key)).size} models`;
  $("#more").hidden = list.length <= shown;
  const chips = `<div class="kinds">` + [["", "All", base.length], ...Object.entries(KINDS).map(([k, t]) => [k, t, counts[k] || 0])]
    .map(([k, t, n]) => `<button class="chip${S.ev === k ? " on" : ""}${n ? "" : " zero"}" data-ev="${k}">${t} <span class="n">${n}</span></button>`).join("") +
    `<span class="muted">${runs.length} crawls tracked since ${runs.length ? esc(utc(runs[0])) : "-"} UTC · standard prices</span></div>`;
  if (!list.length) {
    return chips + `<div class="empty">${all.length ? "No changes match. Try another change type or remove a filter." : "No price or stock changes yet. Changes show up here after crawls that find any."}</div>`;
  }
  let lastAt = "";
  const rows = list.slice(0, shown).map(([e, o]) => {
    const head = e.at !== lastAt ? `<tr class="group"><td colspan="7">${esc(utc(e.at))} UTC <span class="muted">· ${plural(list.filter(([x]) => x.at === e.at).length, "change")}</span></td></tr>` : "";
    lastAt = e.at;
    const now = o.gone ? `<span class="muted">removed</span>` : `<span class="${o.in_stock ? "" : "oos"}">${eur(o.price_card)}</span>`;
    return head + `<tr><td><a href="${esc(o.url)}" target="_blank" rel="noopener" title="${esc(o.title || "")}">${esc(o.brand)} ${esc(o.model)}</a></td>
      <td class="num">${mem(o.ram_gb, o.storage_gb)}</td><td><span class="badge ${o.edition}">${EDITIONS[o.edition] || esc(o.edition)}</span></td>
      <td class="nowrap">${swatch(o.color_family)}${esc(o.color || "")}</td><td>${esc(e.site)}</td><td class="nowrap">${changeText(e)}</td>
      <td class="num" title="${esc(trendTip(o))}">${now}</td></tr>`;
  }).join("");
  return chips + `<table><tr><th>Model</th><th class="num">Variant</th><th>Edition</th><th>Colour</th><th>Shop</th><th>Change</th><th class="num">Now</th></tr>${rows}</table>`;
}

// ----- price history of one variant (model detail) -----

// One entry per crawl of the shop: the variant's cheapest in-stock colour (else cheapest
// listed), EUR at that crawl's rate; null when not listed.
function shopRuns(site, ids) {
  const st = TR.sites[site];
  const n = st.runs.length;
  const out = new Array(n).fill(null);
  for (const id of ids) {
    const pts = TR.series[id]?.[1] || [];
    pts.forEach((pt, k) => {
      if (pt[1] == null) return;
      const end = k + 1 < pts.length ? pts[k + 1][0] : n;
      for (let r = pt[0]; r < end; r++) {
        const v = { p: eurAt(pt, st.runs, r), o: pt.length > 3 ? pt[3] : null, s: !!pt[2] };
        const c = out[r];
        if (!c || (v.s && !c.s) || (v.s === c.s && v.p < c.p)) out[r] = v;
      }
    });
  }
  return out.map((v, r) => v && { ...v, t: st.t[r], at: st.runs[r][0] });
}

function shopStats(rs, lastT) {
  const since30 = lastT - 30 * DAY;
  let low = null, prev = null, chg = null, drops = 0, rises = 0, listed = 0, inStock = 0, l30 = null, h30 = null;
  for (const v of rs) {
    if (!v) continue;
    listed++;
    if (v.s) inStock++;
    const changed = prev && (v.o != null && prev.o != null ? Math.abs(v.o - prev.o) > SAME : Math.abs(v.p - prev.p) > SAME);
    if (changed) {
      const from = v.o != null && prev.o != null ? Math.round(prev.o * (v.p / v.o) * 100) / 100 : prev.p;
      chg = { from, to: v.p, at: v.at };
      if (v.t >= since30) v.p < from ? drops++ : rises++;
    }
    if (v.s) {
      if (!low || v.p < low.p - SAME || (changed && Math.abs(v.p - low.p) <= SAME)) low = v;
      if (v.t >= since30) { l30 = l30 == null ? v.p : Math.min(l30, v.p); h30 = h30 == null ? v.p : Math.max(h30, v.p); }
    }
    prev = v;
  }
  return { now: rs[rs.length - 1], low, chg, drops, rises, l30, h30, listed, stockPct: listed ? Math.round((inStock / listed) * 100) : 0, since: rs.find(Boolean)?.at };
}

// Offer ids of a variant per shop: current offers (all colours, any stock) and removed ones.
function variantIds(g, cfg) {
  const bySite = new Map();
  const add = (site, id) => (bySite.get(site) || bySite.set(site, new Set()).get(site)).add(id);
  const fam = (f) => !g.family || (f || "none") === g.family;
  for (const o of BY_CFG.get(cfg) || []) if (o.oid && fam(o.color_family)) add(o.site, o.oid);
  for (const [id, d] of Object.entries(TR.desc)) {
    if (`${d.model_key}|${d.ram_gb}|${d.storage_gb}|${d.edition}` === cfg && fam(d.color_family)) add(d.site, id);
  }
  return bySite;
}

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * p).find((s) => s >= raw) || 10 * p;
}

const CHARTS = new Map();

// Step lines (a price holds until the next crawl), one per shop; gaps = out of stock or not listed.
function trendChart(series) {
  const W = 720, H = 190, L = 58, R = 14, T = 10, B = 24;
  const listed = series.flatMap((s) => s.rs.filter(Boolean));
  const inStock = listed.filter((v) => v.s);
  if (!inStock.length) return `<div class="muted">Not in stock at any crawl.</div>`;
  let t0 = Math.min(...listed.map((v) => v.t)), t1 = Math.max(...listed.map((v) => v.t));
  if (t1 - t0 < 3600e3) { t0 -= 12 * 3600e3; t1 += 12 * 3600e3; }
  let y0 = Math.min(...inStock.map((v) => v.p)), y1 = Math.max(...inStock.map((v) => v.p));
  const step = niceStep(Math.max((y1 - y0) / 3, 5));
  y0 = Math.floor(y0 / step) * step - (y1 - y0 < step ? step : 0);
  y1 = Math.ceil(y1 / step) * step + (Math.ceil(y1 / step) * step === y1 ? step / 2 : 0);
  const x = (t) => L + ((t - t0) / (t1 - t0)) * (W - L - R);
  const y = (p) => T + ((y1 - p) / (y1 - y0)) * (H - T - B);
  const grid = [];
  for (let v = y0; v <= y1 + 1e-9; v += step) {
    grid.push(`<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text class="ax" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">€${v.toLocaleString("en-US")}</text>`);
  }
  const short = t1 - t0 < 3 * DAY;
  const ticks = [t0, t1].map((t, i) => `<text class="ax" x="${x(t)}" y="${H - 6}" text-anchor="${i ? "end" : "start"}">${esc(shortDate(t, short))}</text>`);
  const lines = series.map(({ site, rs }) => {
    let d = "", prev = null;
    const dots = [];
    rs.forEach((v, i) => {
      if (v && v.s) {
        d += prev ? `H${x(v.t)}V${y(v.p)}` : `M${x(v.t)},${y(v.p)}`;
        const shopMove = prev && (v.o != null && prev.o != null ? Math.abs(v.o - prev.o) > SAME : Math.abs(v.p - prev.p) > SAME);
        if (!prev || shopMove || i === rs.length - 1) dots.push(v); // dots where the shop changed its price
        prev = v;
      } else prev = null;
    });
    return `<path d="${d}" style="stroke:${shopColor(site)}" class="ln"/>` +
      dots.map((v) => `<circle cx="${x(v.t)}" cy="${y(v.p)}" r="4" style="fill:${shopColor(site)}" class="dot"/>`).join("");
  }).join("");
  const id = CHARTS.size + 1;
  const xs = [...new Set(listed.map((v) => v.t))].sort((a, b) => a - b);
  CHARTS.set(id, { series, x, xs, W, short });
  return `<div class="chart" data-chart="${id}"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Price history per shop">
    ${grid.join("")}${ticks.join("")}<line class="xh" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>${lines}</svg><div class="ctip" hidden></div></div>`;
}

// Crosshair snapped to the nearest crawl; the tooltip lists every shop at that time.
function chartHover(e) {
  const el = e?.target.closest?.(".chart");
  for (const c of document.querySelectorAll(".chart")) {
    if (c === el) continue;
    c.querySelector(".ctip").hidden = true;
    c.querySelector(".xh").setAttribute("visibility", "hidden");
  }
  const c = el && CHARTS.get(+el.dataset.chart);
  if (!c) return;
  const r = el.querySelector("svg").getBoundingClientRect();
  const px = ((e.clientX - r.left) / r.width) * c.W;
  const t = c.xs.reduce((b, v) => (Math.abs(c.x(v) - px) < Math.abs(c.x(b) - px) ? v : b), c.xs[0]);
  const xh = el.querySelector(".xh");
  xh.setAttribute("x1", c.x(t));
  xh.setAttribute("x2", c.x(t));
  xh.setAttribute("visibility", "visible");
  const tip = el.querySelector(".ctip");
  const head = document.createElement("div");
  head.className = "muted";
  head.textContent = shortDate(t, true) + " UTC";
  const rows = c.series.map(({ site, rs }) => {
    const v = rs.filter((x) => x && x.t <= t).pop();
    const row = document.createElement("div");
    const key = document.createElement("span");
    key.className = "lk";
    key.style.background = shopColor(site);
    const val = document.createElement("b");
    val.textContent = !v ? "not listed" : v.s ? eur(v.p) : `sold out (${eur(v.p)})`;
    const name = document.createElement("span");
    name.className = "muted";
    name.textContent = " " + site;
    row.append(key, val, name);
    return row;
  });
  tip.replaceChildren(head, ...rows);
  tip.hidden = false;
  const left = (c.x(t) / c.W) * r.width;
  tip.style.left = (left > r.width / 2 ? left - tip.offsetWidth - 12 : left + 12) + "px";
}

function trendPanel(g, cfg) {
  const [model, ram, storage, edition] = cfg.split("|");
  const title = `<h4>Price history · ${mem(ram === "null" ? null : +ram, storage === "null" ? null : +storage)} ${EDITIONS[edition] || ""}${g.family ? " · " + esc(g.family) : ""}</h4>`;
  if (!TR) return `<div class="trend">${title}${needTrends("price history")}</div>`;
  const ids = variantIds(g, cfg);
  const series = [...ids].sort((a, b) => SHOPS.indexOf(a[0]) - SHOPS.indexOf(b[0])).map(([site, set]) => ({ site, rs: shopRuns(site, set) }));
  if (!series.length) return `<div class="trend">${title}<div class="muted">No history for this variant yet.</div></div>`;
  const stats = series.map((s) => ({ site: s.site, ...shopStats(s.rs, TR.sites[s.site].t.at(-1)) }));
  const nowBest = stats.filter((s) => s.now?.s).sort((a, b) => a.now.p - b.now.p)[0];
  const lowBest = stats.filter((s) => s.low).sort((a, b) => a.low.p - b.low.p)[0];
  const drops = stats.reduce((n, s) => n + s.drops, 0), rises = stats.reduce((n, s) => n + s.rises, 0);
  const first = stats.map((s) => s.since).sort()[0];
  let verdict = !nowBest ? "Not in stock at any shop now."
    : !stats.some((s) => s.chg) ? `Now <b>${eur(nowBest.now.p)}</b> at ${esc(nowBest.site)}. No price changes seen since ${esc(utc(first))} UTC.`
    : nowBest.now.p <= lowBest.low.p + SAME
    ? `Now <b>${eur(nowBest.now.p)}</b> at ${esc(nowBest.site)}: the lowest price seen.`
    : `Now <b>${eur(nowBest.now.p)}</b> at ${esc(nowBest.site)}: ${((nowBest.now.p / lowBest.low.p - 1) * 100).toFixed(1)}% above the lowest seen (${eur(lowBest.low.p)} at ${esc(lowBest.site)}, ${esc(utc(lowBest.low.at).slice(0, 10))}).`;
  if (drops || rises) verdict += ` Last 30 days: ${plural(drops, "price drop")}, ${plural(rises, "rise")}.`;
  const legend = series.map((s) => `<span class="lg"><span class="lk" style="background:${shopColor(s.site)}"></span>${esc(s.site)}</span>`).join("");
  const rows = stats.map((s) => `<tr><td class="nowrap"><span class="lk" style="background:${shopColor(s.site)}"></span>${esc(s.site)}</td>
    <td class="num">${!s.now ? `<span class="muted">not listed</span>` : `<span class="${s.now.s ? "" : "oos"}">${eur(s.now.p)}</span>`}</td>
    <td class="num">${s.low ? `${eur(s.low.p)} <span class="muted">${esc(utc(s.low.at).slice(0, 10))}</span>` : "–"}</td>
    <td class="num">${s.l30 != null ? (Math.abs(s.h30 - s.l30) > SAME ? `${eur(s.l30)} – ${eur(s.h30)}` : eur(s.l30)) : "–"}</td>
    <td class="num">${s.chg ? `<span class="${s.chg.to < s.chg.from ? "delta-down" : "delta-up"}">${s.chg.to < s.chg.from ? "▼" : "▲"} ${signed(s.chg.to - s.chg.from, "€").slice(1)}</span> <span class="muted">${ago(s.chg.at)}</span>` : `<span class="muted">none</span>`}</td>
    <td class="num">${s.drops} / ${s.rises}</td><td class="num">${s.stockPct}% <span class="muted">of ${s.listed}</span></td>
    <td class="num muted">${esc(utc(s.since).slice(0, 10))}</td></tr>`).join("");
  return `<div class="trend">${title}<p>${verdict}</p><div class="legend-row">${legend}</div>${trendChart(series)}
    <table class="matrix stats"><tr><th>Shop</th><th class="num">Now</th><th class="num">Lowest seen</th><th class="num">30-day range</th><th class="num">Last change</th><th class="num" title="price drops / rises in 30 days">Drops / rises</th><th class="num" title="share of crawls where it was in stock">In stock</th><th class="num">Tracked since</th></tr>${rows}</table>
    <div class="meta">Standard prices (no discount code, bank-transfer discount or shipping) of the cheapest colour, in EUR at each crawl's rate; USD shops count only their own price changes. Click another variant row to switch.</div></div>`;
}

// ---------- render & state ----------

function render() {
  for (const o of OFFERS) {
    // Code first, then the bank-transfer % on what is left (stacking not confirmed by the shops).
    const pct = o.bank_discount_pct ?? (o.price_eur_bank ? (1 - o.price_eur_bank / o.price_card) * 100 : 0);
    o.coupon_price = !!(S.coupon && o.coupon_eur);
    o.bank_price = !!(S.bank && pct);
    o.price_item = Math.round((o.price_card - (o.coupon_price ? o.coupon_eur : 0)) * (o.bank_price ? 1 - pct / 100 : 1) * 100) / 100;
    o.price_eur = S.ship ? Math.round((o.price_item + (o.shipping_eur ?? 0)) * 100) / 100 : o.price_item;
  }
  Q = parseQuery(S.q);
  Q.field = Q.words.length ? "name" : null;
  let list = OFFERS.filter((o) => pass(o));
  if (!list.length && Q.field) {
    Q.field = "hay";
    list = OFFERS.filter((o) => pass(o));
  }
  CHARTS.clear();
  $("#out").innerHTML = S.view === "models" ? renderModels(list) : S.view === "offers" ? renderOffers(list) : renderUpdates();
  renderFacets();
  document.querySelectorAll(".views button").forEach((b) => b.classList.toggle("on", b.dataset.view === S.view));
  document.body.dataset.view = S.view;
  $("#reset").disabled = !filtered();
  writeHash();
}

function writeHash() {
  const p = new URLSearchParams();
  const d = DEFAULTS();
  for (const [k, v] of Object.entries(S)) {
    if (v instanceof Set) {
      const same = v.size === d[k].size && [...v].every((x) => d[k].has(x));
      if (!same) p.set(k, [...v].join("~"));
    } else if (v !== d[k]) p.set(k, typeof v === "boolean" ? (v ? "1" : "0") : v);
  }
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const d = DEFAULTS();
  for (const k of Object.keys(d)) {
    if (!p.has(k)) continue;
    const v = p.get(k);
    if (d[k] instanceof Set) S[k] = new Set(v ? v.split("~") : []);
    else if (typeof d[k] === "boolean") S[k] = v === "1";
    else if (typeof d[k] === "number") S[k] = +v;
    else S[k] = v;
  }
  syncInputs();
}

function syncInputs() {
  $("#q").value = S.q;
  $("#stock").checked = S.stock;
  $("#eu").checked = S.eu;
  $("#bank").checked = S.bank;
  $("#ship").checked = S.ship;
  $("#coupon").checked = S.coupon;
  $("#split").checked = S.split;
  $("#pmin").value = S.pmin;
  $("#pmax").value = S.pmax;
}

function bind() {
  let t;
  const rerender = () => { shown = PAGE; render(); };
  $("#q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { S.q = e.target.value; rerender(); }, 120); });
  $("#stock").addEventListener("change", (e) => { S.stock = e.target.checked; rerender(); });
  $("#eu").addEventListener("change", (e) => { S.eu = e.target.checked; rerender(); });
  $("#bank").addEventListener("change", (e) => { S.bank = e.target.checked; rerender(); });
  $("#ship").addEventListener("change", (e) => { S.ship = e.target.checked; rerender(); });
  $("#coupon").addEventListener("change", (e) => { S.coupon = e.target.checked; rerender(); });
  $("#split").addEventListener("change", (e) => { S.split = e.target.checked; render(); });
  $("#pmin").addEventListener("input", (e) => { S.pmin = e.target.value; rerender(); });
  $("#pmax").addEventListener("input", (e) => { S.pmax = e.target.value; rerender(); });
  $("#brandq").addEventListener("input", renderFacets);
  $("#chipq").addEventListener("input", renderFacets);
  $("#reset").addEventListener("click", () => {
    S = { ...DEFAULTS(), ...Object.fromEntries(KEEP.map((k) => [k, S[k]])) };
    $("#brandq").value = $("#chipq").value = "";
    syncInputs();
    rerender();
  });
  $("#more").addEventListener("click", () => { shown += PAGE; render(); });
  document.querySelectorAll(".views button").forEach((b) => b.addEventListener("click", () => { S.view = b.dataset.view; rerender(); }));
  $("#side").addEventListener("click", (e) => {
    const tb = e.target.closest("[data-trend]");
    if (tb) { S.trend = S.trend === tb.dataset.trend ? "" : tb.dataset.trend; rerender(); return; }
    const el = e.target.closest("[data-f]");
    if (!el) return;
    const set = S[el.dataset.f];
    set.has(el.dataset.v) ? set.delete(el.dataset.v) : set.add(el.dataset.v);
    rerender();
  });
  $("#out").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (th) {
      S.dir = S.sort === th.dataset.sort ? -S.dir : 1;
      S.sort = th.dataset.sort;
      render();
      return;
    }
    const ev = e.target.closest("[data-ev]");
    if (ev) { S.ev = ev.dataset.ev; rerender(); return; }
    if (e.target.closest("a")) return;
    const vr = e.target.closest("tr.vrow");
    if (vr) { sel.set(vr.dataset.g, vr.dataset.cfg); render(); return; }
    const tr = e.target.closest("tr.model");
    if (tr) { open.has(tr.dataset.key) ? open.delete(tr.dataset.key) : open.add(tr.dataset.key); render(); }
  });
  $("#out").addEventListener("pointermove", chartHover);
  $("#out").addEventListener("pointerleave", () => chartHover(null));
  window.addEventListener("hashchange", () => { S = DEFAULTS(); readHash(); render(); });
}

load().catch((e) => {
  $("#out").innerHTML = `<div class="empty">Could not load the catalog data (${esc(e.message)}).<br>Run <code>./phones.sh crawl</code> to create data/catalog.js.</div>`;
});
