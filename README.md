# chine-phones

On-demand price crawler for Chinese phone shops. Prices are compared in **EUR**,
with one shared attribute schema for every shop so offers can be compared like for like.

| Site | Platform | How it is crawled | EUR price source |
|---|---|---|---|
| giztop.com | Magento | category pages + every product page (variant JSON) | shop's EUR switcher (cart quotes EUR; base currency USD, kept as `price_orig`) |
| wondamobile.com | Shopify Markets | `products.json` (all variants + description) | the Lithuania/EUR market price list (not USD x rate - the market price differs) |
| tradingshenzhen.com | PrestaShop + Cloudflare | category pages + every product page (data sheet, firmware options) | native EUR |
| gshopper.com | Magento (swatch variants) | category pages + every product page + ship-to check per product | shop's EUR switcher; Ship To = Lithuania set in the session (deliverability, shipping, EU VAT included) |

## Setup

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Buyer market

Prices are per market, so the crawler shops as a buyer from `phones/config.py`
(`COUNTRY = "LT"`, `CURRENCY = "EUR"`) instead of relying on IP geolocation. Item prices only;
shipping and taxes differ per shop: giztop pays EU VAT via IOSS (free express); tradingshenzhen
ships free from its EU warehouse, from €15 from China; gshopper prices include EU VAT, standard
shipping to LT is free (`shipping_eur`, `delivery_note`), and items it cannot ship to LT are
marked unavailable.

## Usage

```sh
./phones.sh crawl                       # all sites concurrently (~5-6 min; tradingshenzhen asks per colour)
./phones.sh crawl --site wonda          # one site

./phones.sh best "oppo find n6"         # every RAM/storage/edition, price per site, cheapest marked *
./phones.sh best "honor magic 9 pro max" --storage 512 --links
./phones.sh best "magic 9" --family     # all Magic 9 models
./phones.sh best "oppo find n6" --edition global --include-oos
./phones.sh best "oneplus 15" --edition cn_global_rom
./phones.sh best "oppo find n6" --bank   # use bank-transfer prices (wonda, gshopper: -4.5%)
./phones.sh best "oppo find n6" --shipping   # compare item + shipping to LT
./phones.sh best "oppo find n6" --coupon --links   # apply discount codes, show which code to enter
./phones.sh best "xiaomi 18 pro" --by-color      # one row per colour family
./phones.sh best "oppo find x10 pro max" --color titanium --links

./phones.sh find --brand honor --min-ram 16 --max-price 1200
./phones.sh find --chipset "elite gen 5" --edition global
./phones.sh find --model "find" --min-storage 1tb --google-play yes
./phones.sh find --ships-from eu                 # EU warehouse stock (fast, no customs)
./phones.sh find --condition new --edition global

./phones.sh models huawei               # what model names exist
./phones.sh history "honor magic 9 pro max" --storage 512
./phones.sh changes                     # price/stock changes of the last 7 days, biggest first per crawl
./phones.sh changes "find n6" --since 30d --kind drop --links
./phones.sh changes --kind in --site wonda --since 24h   # back in stock
./phones.sh reindex                     # re-parse stored raw pages offline (~2 s), e.g. after parser fixes
```

## Web catalog

No server or Python needed to view it - open the file:

```sh
open web/index.html                     # macOS; or double-click it
./phones.sh serve                       # optional: http://127.0.0.1:8787/
```

`web/` (`index.html`, `app.js`, `style.css`) is a fixed static app; the data lives in `data/`
and is rewritten by `crawl` and `reindex`:

| File | Used for |
|---|---|
| `data/index.json` | latest offers of every site (canonical data), each with its price statistics |
| `data/catalog.js` | the same as `window.CATALOG = {...}`; lets the page work from `file://`, where browsers block `fetch()` of JSON |
| `data/trends.js` | price history (change points only) as `window.TRENDS`, loaded when a chart or the Updates view needs it |

The app uses `catalog.js` when present, else fetches `index.json`, so the project folder can
also be put on any static host (nginx, GitHub Pages, S3, `npx serve`). Exclude `data/raw/` from
anything you publish.

- **Global search**: model names first, then chipset/OS/colour/shop; understands `16/512`, `12+256`, `1tb`, `16gb`, `global`, `eu`
- **Price trend** filter: price dropped in the last 7 / 30 days, at the lowest price seen, new in the last 7 days
- **Facets with counts** (counted within the current search): manufacturer and chipset (with filter boxes), edition, shop, RAM, storage, colour, battery range, condition, Google Play, type; plus price range, in stock, ships from EU. Chipset names are unified across shops (`normalize.norm_chipset`). Chipset and battery filters (web and `--chipset` / `--min-battery`) use one value per model and hardware edition (CN or Global) that the shops agree on (`store.agree_specs`, one vote per shop), so a shop naming the chip differently ("8 Elite Gen 6" vs "8 Elite Extreme Gen 6"), a page with a stray value, or a shop stating nothing does not drop out of the results. The model details list shops that say otherwise; the Offers view marks them with ≠
- **Models view**: one line per phone and colour, with editions, variants, shops, specs and from-price (untick "Colours as separate lines" to group colours per phone); click to open the variant x shop price grid with the best price marked, links to each shop, the last price change, and the sentence each edition was read from. Below the grid: the price history of one variant (click a row to pick it) - a chart with a line per shop, "now vs lowest seen", and per shop the lowest price, 30-day range, last change, drops/rises and how often it was in stock. The Change column sorts by the last price change (30 days), biggest drop first
- **Offers view**: every offer, sortable by any column, with its last price change; hover a price for its history in numbers
- **Updates view**: what changed between crawls, newest first - price drops and rises, new listings, back in stock, sold out, removed - filtered by the search and sidebar like everything else
- Filters live in the URL, so a view can be bookmarked or shared

Every query command accepts `--json`. Out-of-stock offers are stored but hidden unless `--include-oos`.
Queries default to phones; use `--type tablet|any` for the rest.

## Shipping and VAT (Lithuania)

Each offer carries the shipping cost to `COUNTRY` for the method where no import VAT/customs
is left to the buyer (`config.SHIPPING`). In the web catalog, **Include shipping** (above the
results) adds it to every price; on the CLI use `--shipping`. Bank discounts apply to the item only.

| Shop | Method | Cost to LT | VAT / customs |
|---|---|---|---|
| giztop | Express (EU Priority), 6-10 days | €35.01 flat, quoted from a cart each crawl | paid by Giztop (IOSS). Air Mail is declared low: avoid it |
| tradingshenzhen | **no delivery to LT**: not in the delivery widget's country list (checked each crawl, offers get `delivers: false`) | - | - |
| gshopper | Standard (per product ship-to check) | usually free | included in the price |
| wonda | EU Standard, 8-10 working days | €53.80 flat, quoted from a cart each crawl | **not stated** (`VAT?`): 21% import VAT + carrier fee may be due on delivery |

Offers a shop does not deliver to `COUNTRY` (`delivers: false`) are hidden from queries;
`--no-delivery` shows them, flagged `noLT`.

### Disabled shops

`config.DISABLED_SITES` takes a shop out of crawls, the index, the web view and price trends,
with the reason shown in the web view's header and the CLI legend. Its code and stored data
are kept: remove the entry and crawl it to bring it back (`crawl --site <name>` still works
while disabled, for checking). Currently disabled: **tradingshenzhen** (does not deliver to
Lithuania; Latvia and Poland are in its list).

## Discount codes (gshopper)

Gshopper's base currency is USD, so a "$150" code takes €132 off a EUR cart (shop rate 0.88).
Codes are applied by rule, with no extra requests to the shop. The rules come from cart checks on 2026-10-10:

- **gshopper150**: $150 off any order over $1000 (`config.COUPON_RULES`). It's printed on only a few pages,
  but the cart accepted it on all 12 phones over $1000 tried and refused it on every cheaper one.
- **Codes printed on a product page** ("Save $30 With Coupon Code: gshopper30") work on that product.
  The amount is the number in the code's name: it matched the cart 7 of 7 times, while the page text didn't always (one page said $20 for a $30 code).
- An order takes one code; the best one is used. Typos on pages ("gshoppr150") are refused by the cart,
  and the cart-wide rule covers those products anyway.
- The bank-transfer 4.5% is taken after the code. Whether they stack isn't confirmed: the cart shows the
  4.5% only when the order is placed.

The web toggle **Discount codes** and the CLI `--coupon` apply them. Codes expire, so re-check gshopper150 in the cart now and then.

## Shared attributes (one schema for all sites)

| Field | Meaning |
|---|---|
| `brand`, `model`, `model_key` | normalized name; `model_key` (e.g. `oppo find n6`) joins the same phone across shops |
| `ram_gb`, `storage_gb` | from the variant option, else product data sheet, else title (1TB = 1024) |
| `edition` | `cn` / `cn_global_rom` / `global` (see below), plus `edition_source` and `edition_evidence` (the sentence it was read from) |
| `condition` | `new` / `new_unsealed` (shop opened it, e.g. to install Google Play) / `used` / `refurbished` |
| `ships_from`, `delivery_note` | `eu` / `cn` / `hk` when the shop says so (tradingshenzhen delivery widget, "EU Warehouse" titles) |
| `price_eur`, `in_stock` | standard checkout price (card/PayPal) in the buyer market |
| `shipping_eur`, `shipping_method`, `shipping_estimated`, `vat`, `vat_note` | shipping to the buyer country and who pays import VAT: `included` / `shop` / `not_stated` (see Shipping and VAT) |
| `coupon_code`, `coupon_eur`, `coupon_note`, `price_eur_coupon` | best discount code for this variant and its value in EUR (see Discount codes) |
| `price_eur_bank`, `payment_note` | price with the marketplace's bank-transfer discount, applied to all its products from `BANK_TRANSFER_DISCOUNT` in `phones/config.py` (wonda, gshopper: 4.5%); the crawl warns if a site states a different value |
| `price_orig`, `currency_orig` | price in the shop's own default currency (giztop USD), to check against the site |
| `price_base` | wonda: the EUR price divided by the storefront's daily Shopify Markets rate; used only by the price trends |
| `color`, `color_family` | one offer per colour: the shop's name ("Phantom Black") and a family comparable across shops (black, white, titanium, transparent, two-tone...) |
| `color_verified` | price/stock confirmed for that colour (all three shops price colours individually; false only for old crawls) |
| `firmware` | shop's ROM label (tradingshenzhen) |
| `google_play` | from product description (`null` = shop does not say) |
| `chipset`, `display_in`, `battery_mah`, `charging_w`, `os`, `languages` | from spec table / description |
| `device_type` | `phone` / `tablet` / `accessory` |
| `attrs` | raw site attributes, kept for reference |

A comparable configuration is `(model_key, ram_gb, storage_gb, edition)`; colour is a filter or an
extra split (`--by-color`, "Split by colour" in the web view), because colours can change price
(Transparent often +€350-440) and stock.

**Edition**: one phone, separate price rows per edition.

| Edition | Meaning |
|---|---|
| `cn` | China version (the default: these shops sell CN units unless they say otherwise) |
| `cn_global_rom` | China hardware flashed with a global ROM by the shop ("Global Rom", "we flash the Global ROM before shipping") |
| `global` | official global / international unit (incl. "Hong Kong model", "Global HK Edition") |

Decided in this order: title/variant words → product description statement → shop firmware
option → `cn`. Cross-sell lines such as "Buy the Global version HERE" are ignored.
Condition and ships-from are flags (shown as `u` / `E` in tables), not separate price rows.

## Storage and history

Crawling and parsing are separate steps:

```
data/raw/<run>/<site>.jsonl.gz          every HTTP response, gzip (~29 MB per run); last 10 runs kept (--keep-raw N)
data/snapshots/<run>/<site>.json.gz     normalized offers = full price archive (gzip, ~150 KB per run, kept forever)
data/trends.json                        price/stock change points of every offer (what charts and `changes` read)
data/index.json                         latest snapshot of every site + price statistics per offer (what queries read)
```

`reindex` re-parses all stored raw runs offline (no network, ~20 s) and rebuilds snapshots,
trends and the index, so parser fixes apply to past crawls too.

### Price trends

Each crawl is compared with the shop's previous one (`phones/trends.py`); only changes are kept:
per offer (shop + product link + chosen options) a point when its price or stock changed, or it
was listed / removed. Twenty days of daily crawls with a few percent of prices moving each day
come to ~250 KB, so the web page loads the whole history in a blink.

- Shops priced in USD (giztop, gshopper) are compared in USD: a move of the exchange rate alone is
  not a price change. Wonda's EUR prices are converted from its base prices at a daily rate
  (`Shopify.currency` on the page), so it is compared before that conversion (`price_base`).
  Charts show EUR at each crawl's rate.
- Statistics per offer are computed at index time (`trend` in `index.json`): last change (amount,
  %, when, previous price), lowest seen and since when, whether the price now is the lowest seen,
  30-day range and number of drops/rises, first seen, share of crawls in stock.
- Prices tracked are the standard ones (no discount code, bank-transfer discount or shipping).
- Crawls before `trends.FIRST_RUN` (2026-10-10 10:38) are not compared: they were made while the
  parsers were being written (no USD prices at giztop, one price for all colours at tradingshenzhen).
- Trends are only as good as the crawl frequency: crawl daily (cron / launchd) to see them. A site that fails keeps its previous
snapshot in the index; if parsing fails the raw data is still kept, so fix + `reindex` recovers it.

## Anti-bot

Requests go through `curl_cffi`, which impersonates real browsers at the TLS/HTTP2 fingerprint
level (Chrome, Safari, Firefox profiles) with matching User-Agent and headers. Each site gets a
random profile per crawl, randomized Accept-Language, 4 concurrent requests with random delays,
and on a block (403/429/Cloudflare page) it rotates to another browser profile and backs off.

## Adding a site

Create `phones/sites/<name>.py` with `NAME`, `async def fetch(client, raw, log)` (download and
`raw.save(kind, url, body)` every response) and `def parse(records, log) -> list[Offer]` (offline;
build offers with `make_offer(...)` and `details.extract(description_html)`), then register it in
`phones/sites/__init__.py`.

## Tests

```sh
.venv/bin/python -m unittest discover tests
```
