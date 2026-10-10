from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import config
from . import details as d
from . import normalize as n


@dataclass
class Offer:
    """One purchasable configuration at one shop. Same schema for every site."""

    site: str
    title: str
    brand: str
    model: str
    model_key: str  # join key across shops, e.g. "oppo find n6"
    ram_gb: int | None
    storage_gb: int | None
    edition: str  # cn | cn_global_rom | global
    edition_source: str  # title | details | firmware | default (not stated -> cn)
    price_eur: float  # standard checkout price (card / PayPal) in the buyer market
    in_stock: bool
    url: str
    device_type: str = "phone"  # phone | tablet | accessory
    color: str | None = None  # the shop's colour name; one offer per colour
    color_family: str | None = None  # black, white, titanium, transparent, two-tone... (comparable across shops)
    color_verified: bool = True  # price/stock confirmed for this colour (False: shop gave one price for all colours)
    firmware: str | None = None
    edition_evidence: str | None = None  # the sentence the edition was read from
    condition: str = "new"  # new | new_unsealed | used | refurbished
    ships_from: str | None = None  # eu | cn | hk | us, only when the shop says so
    delivers: bool | None = None  # delivers to the buyer country (config.COUNTRY); None = not known
    delivery_note: str | None = None
    shipping_eur: float | None = None  # shipping to the buyer country with shipping_method (config.SHIPPING)
    shipping_method: str | None = None
    shipping_estimated: bool = False  # not quoted by the shop for this country, see config.SHIPPING
    vat: str | None = None  # included | shop | not_stated (import VAT may be due on delivery)
    vat_note: str | None = None
    google_play: bool | None = None
    chipset: str | None = None
    display_in: float | None = None
    battery_mah: int | None = None
    charging_w: int | None = None
    os: str | None = None
    languages: str | None = None
    sku: str | None = None
    price_eur_bank: float | None = None  # with the shop's bank-transfer discount, if it offers one
    bank_discount_pct: float | None = None
    coupon_code: str | None = None  # best discount code accepted in the shop's cart for this variant
    coupon_eur: float | None = None
    coupon_note: str | None = None  # the cart's name for the discount ("Save $150 on orders over $1000!")
    price_eur_coupon: float | None = None
    payment_note: str | None = None
    price_orig: float | None = None  # price in the shop's base currency, for checking against the site
    currency_orig: str | None = None
    attrs: dict = field(default_factory=dict)  # raw site attributes, for reference

    @property
    def config_key(self) -> str:
        return f"{self.model_key}|{self.ram_gb}|{self.storage_gb}|{self.edition}"

    def to_dict(self) -> dict:
        return asdict(self)


def make_offer(
    *,
    site: str,
    title: str,
    price_eur: float,
    in_stock: bool,
    url: str,
    brand_hint: str | None = None,
    memory_text: str | None = None,
    model_suffix: str | None = None,
    color: str | None = None,
    color_verified: bool = True,
    edition_hints: list[str | None] | None = None,
    firmware: str | None = None,
    info: dict | None = None,
    condition: str | None = None,
    sku: str | None = None,
    price_orig: float | None = None,
    currency_orig: str | None = None,
    attrs: dict | None = None,
) -> Offer:
    """Build an Offer. `info` is the output of details.extract() for the product page."""
    info = info or {}
    brand, model = n.split_brand(title, brand_hint)
    if model_suffix:  # special edition carried in a variant option, e.g. "16+512GB Collector's Edition"
        model = f"{model} {n.clean_model_text(model_suffix)}".strip()

    # RAM/storage: variant option > product-page spec table > title.
    ram, storage = n.parse_memory(memory_text)
    for fallback in (f"{info.get('spec_ram') or ''} / {info.get('spec_storage') or ''}", title):
        if ram is None or storage is None:
            r2, s2 = n.parse_memory(fallback)
            ram, storage = ram or r2, storage or s2
    # Spec rows with a single value each: "RAM: 16GB LPDDR5x", "ROM: 512GB UFS 4.1".
    ram = ram or n.single_gb(info.get("spec_ram"), max_gb=32)
    storage = storage or n.single_gb(info.get("spec_storage"), min_gb=32)

    # Edition: title/variant words > description statement > firmware option > CN.
    # These shops sell CN units unless they explicitly say Global.
    edition, source, evidence = n.title_edition(title, *(edition_hints or [])), "title", title
    if edition is None:
        edition, source, evidence = info.get("edition_note"), "details", info.get("edition_evidence")
    if edition is None:
        edition, source, evidence = n.firmware_edition(firmware), "firmware", firmware
    if edition is None and brand in n.GLOBAL_ONLY_BRANDS:  # Ulefone, Doogee... have no China edition
        edition, source, evidence = "global", "brand", f"{brand} sells international units only"
    if edition is None:
        edition, source, evidence = "cn", "default", None

    # Condition: explicit shop field / title > description statement > new.
    condition = condition if condition and condition != "new" else None
    condition = condition or n.title_condition(title) or info.get("condition") or "new"
    ships_from = n.title_ships_from(title) or info.get("ships_from")

    return Offer(
        site=site,
        title=title.strip(),
        brand=brand,
        model=n.pretty_model(model),
        model_key=n.model_key(brand, model),
        ram_gb=ram,
        storage_gb=storage,
        edition=edition,
        edition_source=source,
        edition_evidence=evidence,
        condition=condition,
        ships_from=ships_from,
        delivery_note=info.get("delivery_note"),
        price_eur=round(float(price_eur), 2),
        in_stock=bool(in_stock),
        url=url,
        device_type=d.device_type(title, info.get("display_in")),
        color=n.norm_color(color),
        color_family=n.color_family(color),
        color_verified=color_verified,
        firmware=firmware,
        google_play=info.get("google_play"),
        chipset=n.norm_chipset(info.get("chipset")),
        display_in=info.get("display_in"),
        battery_mah=info.get("battery_mah"),
        charging_w=info.get("charging_w"),
        os=info.get("os"),
        languages=info.get("languages"),
        sku=sku,
        price_orig=price_orig,
        currency_orig=currency_orig,
        attrs=attrs or {},
    )


def apply_bank_discount(offers: list[Offer], pct: float | None) -> None:
    """Marketplace-wide bank-transfer discount (config.BANK_TRANSFER_DISCOUNT) on every offer."""
    for o in offers:
        o.price_eur_bank = round(o.price_eur * (1 - pct / 100), 2) if pct else None
        o.bank_discount_pct = pct or None
        o.payment_note = f"{pct:g}% off with bank transfer" if pct else None


def apply_shipping(o: Offer, rule: dict, eur: float | None = None, method: str | None = None, estimated: bool | None = None,
                   delivers: bool | None = None) -> None:
    """Shipping + VAT terms from a config.SHIPPING rule; `eur`/`method` override it with a live quote.
    delivers=False: the shop does not deliver to the buyer country, so there is no shipping cost."""
    o.delivers = delivers
    if delivers is False:
        o.shipping_eur, o.shipping_method, o.shipping_estimated = None, f"no delivery to {config.COUNTRY_NAME}", False
        o.vat, o.vat_note = None, None
        return
    o.shipping_eur = eur if eur is not None else rule.get("eur")
    o.shipping_method = method or rule.get("method")
    o.shipping_estimated = rule.get("estimated", False) if estimated is None else estimated
    o.vat, o.vat_note = rule.get("vat"), rule.get("vat_note")
