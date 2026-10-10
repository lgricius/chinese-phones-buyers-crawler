"""Turn each shop's naming into one shared attribute schema.

Every offer ends up with: brand, model, model_key, ram_gb, storage_gb, edition,
color, color_family. `model_key` is the join key across shops ("oppo find n6"), and
(model_key, ram_gb, storage_gb, edition) identifies a comparable configuration.
"""

from __future__ import annotations

import re
import unicodedata

# Canonical brand names. Sub-brands map to the parent brand but stay in the
# model name, so "Redmi K90" -> brand Xiaomi, model "Redmi K90".
BRANDS = {
    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "poco": "Xiaomi",
    "vivo": "Vivo",
    "iqoo": "Vivo",
    "oppo": "Oppo",
    "oneplus": "OnePlus",
    "realme": "Realme",
    "honor": "Honor",
    "huawei": "Huawei",
    "samsung": "Samsung",
    "motorola": "Motorola",
    "moto": "Motorola",
    "sony": "Sony",
    "google": "Google",
    "apple": "Apple",
    "iphone": "Apple",
    "nubia": "Nubia",
    "redmagic": "Red Magic",
    "zte": "ZTE",
    "meizu": "Meizu",
    "asus": "Asus",
    "rog": "Asus",
    "lenovo": "Lenovo",
    "legion": "Lenovo",
    "nothing": "Nothing",
    "tecno": "Tecno",
    "infinix": "Infinix",
    "blackview": "Blackview",
    "doogee": "Doogee",
    "ulefone": "Ulefone",
    "oukitel": "Oukitel",
    "cubot": "Cubot",
    "unihertz": "Unihertz",
    "umidigi": "Umidigi",
    "hotwav": "Hotwav",
    "fossibot": "Fossibot",
    "iiif150": "IIIF150",
    "oscal": "Blackview",
    "agm": "AGM",
    "fairphone": "Fairphone",
    "hmd": "HMD",
    "nokia": "Nokia",
}
# Sub-brands that are part of the model name, not a separate brand prefix.
# Brands that only sell international units: when a shop does not say, they are Global, not CN.
GLOBAL_ONLY_BRANDS = {"Ulefone", "Doogee", "Blackview", "Oukitel", "Cubot", "Unihertz", "Umidigi", "Hotwav",
                      "Fossibot", "IIIF150", "Oscal", "AGM", "Fairphone", "HMD", "Nokia", "Nothing", "Tecno", "Infinix"}
SUB_BRANDS = {"redmi", "poco", "iqoo", "rog", "legion", "iphone", "oscal"}
# Product lines that identify the brand when a title omits it ("CN Version ACE 6T").
FAMILY_BRANDS = {"ace": "OnePlus", "nord": "OnePlus", "armor": "Ulefone", "galaxy": "Samsung", "pixel": "Google",
                 "reno": "Oppo", "find": "Oppo", "mate": "Huawei", "pura": "Huawei", "nova": "Huawei", "magic": "Honor"}

# Phrases that never distinguish one phone model from another.
NOISE = [
    r"\(?\bglobal\s+(?:version|rom|edition)\b\)?",
    r"\(?\bchina\s+(?:version|rom|edition)\b\)?",
    r"\(?\bcn\s+(?:version|rom)\b\)?",
    r"\(?\bhk\s+version\b\)?",
    r"\(?\bhong\s+kong\s+(?:version|model)\b\)?",
    r"\bdual\s+sim\b",
    r"\b5g\b",
    r"\b4g\b",
    r"\bsmart\s*phones?\b",
    r"\bmobile\s+phones?\b",
    r"\bnew\b",
    r"\bofficial\b",
    r"\bunlocked\b",
    r"\bsm-[a-z0-9]+\b",  # Samsung model codes
    r"\bcph\d{4}\b",  # Oppo/OnePlus model codes
    r"\bxq-[a-z0-9]+\b",  # Sony model codes
    r"\bwith\s+google\s+play\b",
    r"\bgoogle\s+play\b",
    r"\bgms\b",
    r"\bota\b",
    r"\(?\b(?:global|china|chinese)\b\)?",  # bare edition word: "OnePlus 15 Global"
    r"\bhw\b",  # "HUAWEI HW Pura 80"
    r"\bhk\b",
    r"\beu\s+(?:warehouse|lager|stock)\b",
    r"\b(?:in\s+)?stock\b",
]
NOISE_RE = re.compile("|".join(NOISE), re.I)

# "12+512GB", "12GB/256GB", "12GB - 256GB", "16GB RAM 1TB". RAM must carry
# "GB" or be joined by "+" / "/" so "Magic 7 - 12GB/256GB" is not read as 7GB RAM.
MEM_PAIR_RE = re.compile(
    r"\b(\d{1,2})\s*(?:(?:\+|/)|gb\s*(?:ram)?\s*(?:\+|/|-|–|,|\s)?)\s*(\d{2,4}|1|2)\s*(gb|tb)\b", re.I
)
STORAGE_RE = re.compile(r"\b(\d{2,4}|1|2)\s*(gb|tb)\b", re.I)


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def to_gb(num: str, unit: str) -> int:
    n = int(num)
    return n * 1024 if unit.lower() == "tb" else n


def parse_memory(text: str | None) -> tuple[int | None, int | None]:
    """'12+512GB', '12GB - 256GB', '16GB / 1TB', '16GB RAM 512GB' -> (ram, storage)."""
    if not text:
        return None, None
    text = _bare_g(text)  # "16+512G", "12G+512GB", "16+1T"
    m = MEM_PAIR_RE.search(text)
    if m:
        ram, storage = int(m.group(1)), to_gb(m.group(2), m.group(3))
        if ram <= 32 and storage >= 32:
            return ram, storage
    m = STORAGE_RE.search(text)
    if m:
        gb = to_gb(m.group(1), m.group(2))
        if gb >= 32:
            return None, gb
    return None, None


def single_gb(text: str | None, min_gb: int = 0, max_gb: int = 1 << 20) -> int | None:
    """The size in a spec row that lists exactly one value ('16GB LPDDR5x'); None for '12GB/16GB'."""
    if not text:
        return None
    vals = [to_gb(a, b) for a, b in re.findall(r"\b(\d{1,4})\s*(gb|tb)\b", text, re.I)]
    return vals[0] if len(vals) == 1 and min_gb <= vals[0] <= max_gb else None


def _bare_g(text: str) -> str:
    """'16+512G' / '12G+1TB' -> '16GB+512GB' / '12GB+1TB'. Only inside a RAM+storage pair, so
    '17T Pro' or '5G' elsewhere in a title stay untouched."""

    def unit(u: str | None) -> str:
        return (u or "G").upper() + "B"

    return re.sub(
        r"\b(\d{1,2})\s*(?:([GT])B?)?\s*([+/])\s*(\d{1,4})\s*([GT])B?\b",
        lambda m: f"{m[1]}{unit(m[2])}{m[3]}{m[4]}{unit(m[5])}",
        text,
        flags=re.I,
    )


def strip_memory(text: str) -> str:
    text = MEM_PAIR_RE.sub(" ", _bare_g(text))
    text = STORAGE_RE.sub(" ", text)
    return text


EDITIONS = ("cn", "cn_global_rom", "global")
GLOBAL_ROM_RE = re.compile(r"global\s*rom\b|global\s+firmware|flashed\s+with\s+(?:the\s+)?(?:global|international)", re.I)


def title_edition(*texts: str | None) -> str | None:
    """Edition words in a title or variant option. 'Global ROM' is CN hardware."""
    blob = " ".join(t for t in texts if t)
    if GLOBAL_ROM_RE.search(blob):
        return "cn_global_rom"
    if re.search(r"\bglobal\b|\binternational\b|hong kong model|\beu vers(?:ion|in)\b", blob, re.I):
        return "global"
    if re.search(r"\bchina\b|\bchinese\b|\bcn (?:version|rom)\b", blob, re.I):
        return "cn"
    return None


def firmware_edition(firmware: str | None) -> str | None:
    """Shop firmware option. Anything 'Global' on a CN-hardware listing is a Global ROM;
    'Origin OS', 'Hyper OS - CH / EN' etc. carry no extra information (still CN)."""
    if firmware and "global" in firmware.lower():
        return "cn_global_rom"
    return None


def title_condition(title: str) -> str | None:
    t = title.lower()
    if re.search(r"\brefurbished\b|\brenewed\b", t):
        return "refurbished"
    if re.search(r"\bused\b|pre-?owned|second[- ]hand|open[- ]box|\bdemo\b", t):
        return "used"
    return None


def title_ships_from(title: str) -> str | None:
    t = _ascii(title)
    if re.search(r"\beu\s+(?:warehouses?|lager|stock)\b", t, re.I):
        return "eu"
    if re.search(r"\bus\s+(?:warehouses?|stock)\b", t, re.I):
        return "us"
    return None


COLOR_WORDS = (
    "black|white|silver|gold|green|blue|purple|violet|pink|red|orange|yellow|gray|grey|"
    "titanium|transparent|cyan|brown|beige|lavender|mint|graphite|champagne|bronze"
)
# Words that belong to model names and must never be taken as a colour adjective.
MODEL_WORDS = (
    r"pro|max|ultra|plus|mini|lite|fold|flip|air|neo|turbo|note|edge|prime|power|super|racing|explorer|"
    r"vision|design|edition|porsche|rsr|leica|global|gt|[a-z]?\d+[a-z]*\+?"
)
# Trailing colour, optionally with one descriptive word: "White", "Stellar Titanium", "Black and White".
TRAILING_COLOR_RE = re.compile(
    rf"(?:\s+(?!(?:{MODEL_WORDS})\b)[a-z]+)?(?:\s+(?:{COLOR_WORDS})(?:\s+(?:and|&)\s+(?:{COLOR_WORDS}))?)+$", re.I
)


SPEC_CUTOFF_RE = re.compile(
    r"\s(?=(?:\d+(?:\.\d+)?\s*(?:mah|mp|hz|w|inch|in|\"|”)\b|\d+\s*gb\s+ram|up to\b|ai\b|rugged|smart\s*phone|smartphone|"
    r"mobile phone|android|snapdragon|dimensity|helio|unisoc|octa|thermal|night vision|waterproof|with\b|nfc\b|amoled|lcd|"
    r"ips\b|dual (?:display|screen)|built-in|projector|camera|flashed|support(?:s|ing)?\b|via ota|ota\b))",
    re.I,
)


def _base_text(title: str) -> str:
    """Title without memory, noise and shipping notes; colour still attached."""
    t = re.sub(r"[【\[][^】\]]*[】\]]", " ", title)  # "【2025 New】", "【Pre-sale】" tags
    t = _ascii(t)  # NFKD also turns full-width brackets into ASCII ones
    t = re.sub(r"\([^)]*(?:warehouse|ship|stock)[^)]*\)", " ", t, flags=re.I)  # "(Only US Warehouses)"
    t = re.sub(r"^\s*(?:global|cn|china|chinese|eu|international)\s+vers(?:ion|in)\b\s*", "", t, flags=re.I)  # "Global Version POCO F8", "EU Versin"
    t = re.sub(r"\([^)]*\b(?:spec|model|version)\b[^)]*\)", " ", t, flags=re.I)  # "(Middle East Spec)", "(US Model)"
    t = re.sub(r"\binternational\s+version\b", " ", t, flags=re.I)
    t = re.sub(r"\s*-\s*\d+\s*g(?:b)?\s*\+\s*\d+\s*(?:gb|tb|g|t)\b.*$", "", t, flags=re.I)  # "Find X9 Ultra-12G+512GB-Tundra Umber"
    # Marketplace titles append specs: "...Armor 28 Ultra 5G AI Thermal Imaging 32GB RAM ... Rugged Smartphone".
    t = re.split(SPEC_CUTOFF_RE, t)[0]
    t = re.split(r"\s[-–|]\s", t)[0] if re.search(r"\d+\s*gb", t, re.I) else t  # "Name - 16GB/512GB"
    t = strip_memory(t)
    t = NOISE_RE.sub(" ", t)
    t = re.sub(r"[()\[\],]", " ", t)
    t = re.sub(r"\bplus\b", "+", t, flags=re.I)
    t = re.sub(r"\s+\+", "+", t)
    t = re.sub(r"\s+", " ", t).strip(" -")
    t = re.split(r"\s[-–|]\s", t)[0]  # "Oppo Find N6 - Foldable"
    t = re.sub(rf"\s*-\s*(?:{COLOR_WORDS})\b.*$", "", t, flags=re.I)  # giztop "Find N5 Global Edition-Black-16GB"
    return t


def clean_model_text(title: str) -> str:
    t = _base_text(title)
    stripped = TRAILING_COLOR_RE.sub("", t)
    return stripped if len(stripped.split()) >= 2 else t


def title_color(title: str) -> str | None:
    """Colour named at the end of a single-configuration title: 'OPPO Find N6 Global Stellar Titanium'."""
    t = _base_text(title)
    m = TRAILING_COLOR_RE.search(t)
    return m.group(0).strip() if m and len(t[: m.start()].split()) >= 2 else None


def split_brand(title: str, brand_hint: str | None = None) -> tuple[str, str]:
    """Return (brand, model) where model excludes the parent brand prefix."""
    text = clean_model_text(title)
    # Two-word manufacturer names: "Red Magic 11 Pro", "Nubia Red Magic 10S", "RedMagic ..."
    text = re.sub(r"^(?:nubia\s+)?red\s*magic\b", "redmagic", text, flags=re.I)
    words = text.split()
    brand = None
    if words:
        first = words[0].lower()
        if first in BRANDS:
            brand = BRANDS[first]
            if first not in SUB_BRANDS:
                words = words[1:]
                # "Xiaomi Redmi K90" -> keep "Redmi K90"; "Vivo iQOO 16" -> "iQOO 16"
    if brand is None and brand_hint:
        brand = BRANDS.get(brand_hint.lower().split()[0], brand_hint.strip().title())
    while words and brand and words[0].lower() in BRANDS and BRANDS[words[0].lower()] == brand and words[0].lower() not in SUB_BRANDS:
        words = words[1:]  # "Xiaomi Xiaomi 17 Ultra"
    # "Magic8 Pro Air" == "Magic 8 Pro Air", "Fold6" == "Fold 6" (2-letter prefixes like "gt8", "x300" stay joined)
    words = [re.sub(r"^([A-Za-z]{3,})(\d+[A-Za-z]*\+?)$", r"\1 \2", w) for w in words]
    words = " ".join(words).split()
    if brand is None and words and words[0].lower() in FAMILY_BRANDS and not brand_hint:
        brand = FAMILY_BRANDS[words[0].lower()]
    model = " ".join(words)
    return brand or "Unknown", model


def pretty_model(model: str) -> str:
    out = []
    for w in model.split():
        lw = w.lower()
        if lw in ("iqoo",):
            out.append("iQOO")
        elif lw in ("poco", "rog", "gt", "se", "fe", "rsr", "ai"):
            out.append(lw.upper())
        elif re.fullmatch(r"[a-z]+\d*[a-z]*\+?", lw) and not re.fullmatch(r"[a-z]{1,3}\d+[a-z]*\+?", lw):
            out.append(w[0].upper() + w[1:].lower())
        else:
            out.append(w.upper() if re.fullmatch(r"[a-z]{1,3}\d+[a-z]*\+?", lw) else w)
    return " ".join(out)


def model_key(brand: str, model: str) -> str:
    k = f"{brand} {model}".lower()
    k = k.replace("+", " plus ")
    k = re.sub(r"[^a-z0-9 ]", " ", k)
    k = re.sub(r"^motorola moto (?=\w)", "motorola ", k)  # "Motorola Moto Razr" == "Moto Razr"
    k = re.sub(r"\b([a-z]{1,2}|gt)\s+(\d)", r"\1\2", k)  # "gt 8" == "gt8", "x 300" == "x300"
    k = re.sub(r"\bporsche design\b", "porsche", k)
    k = re.sub(r"\bedition\b", " ", k)  # "Power Edition" == "Power"
    return re.sub(r"\s+", " ", k).strip()


def query_key(text: str) -> str:
    """Normalize free text from the user the same way shop titles are."""
    brand, model = split_brand(text)
    if brand == "Unknown":
        return model_key("", model)
    return model_key(brand, model)


# Colour family -> words that indicate it (checked in this order; first hit wins).
COLOR_FAMILIES = [
    ("transparent", r"transparent|clear"),
    ("titanium", r"titanium"),
    ("black", r"black|midnight|obsidian|onyx|\bink\b|\bnight\b|carbon|fiber|charcoal"),
    ("white", r"white|ivory|pearl|snow|silk|cream|porcelain|moonlight"),
    ("silver", r"silver|platinum|chrome|deuterium"),
    ("gray", r"gr[ae]y|graphite|\bspace\b|slate|ling yun|\bash\b|smoke"),
    ("gold", r"gold|champagne|sand|dune|desert"),
    ("blue", r"blue|navy|ocean|\bsky\b|azure|\bice\b|cyan|teal|turquoise"),
    ("green", r"green|mint|jade|emerald|olive|sage|forest"),
    ("purple", r"purple|violet|lavender|lilac|plum|orchid"),
    ("pink", r"pink|rose|blush|peach"),
    ("red", r"red|ruby|crimson|wine|burgundy|scarlet"),
    ("orange", r"orange|coral|amber|copper|sunset"),
    ("yellow", r"yellow|lemon|\blime\b"),
    ("brown", r"brown|cocoa|coffee|mocha|bronze|\btan\b|camel|umber|tundra"),
]


def color_family(name: str | None) -> str | None:
    """'Phantom Black' -> black, 'Ling Yun (Special Gray)' -> gray; two colours ('Black & White') -> two-tone."""
    if not name:
        return None
    n = name.lower()
    hits = [fam for fam, rx in COLOR_FAMILIES if re.search(rx, n)]
    if len(hits) > 1 and re.search(r"[&/+]|\band\b", n):
        return "two-tone"
    return hits[0] if hits else "other"


def norm_color(c: str | None) -> str | None:
    if not c:
        return None
    return re.sub(r"\s+", " ", c).strip().title()


def norm_chipset(v: str | None) -> str | None:
    """One name per chip across shops: "3nm Qualcomm Snapdragon® 8 Elite Octa-core" and
    "SM8750-AB Snapdragon 8 Elite (3 Nm)" -> "Snapdragon 8 Elite". Cut-off names
    ("Snapdragon 8 Elite Gen") are unknown rather than a wrong chip."""
    if not v:
        return None
    s = re.sub(r"[™®\u200b-\u200f\u2060\ufe00-\ufe0f\ufeff]", "", v)  # also invisible characters (emoji variation selector)
    s = re.sub(r"\(\s*\d+\s*nm\s*\)|\b\d+\s*nm\b|\bqualcomm\b|\bmediatek\b|\bhisilicon\b|\bSM\d{4}[\w-]*", " ", s, flags=re.I)
    s = re.sub(r"\b(octa[- ]?core|soc(\s*/\s*\d+)?|chipset|processor|mobile platform|platform)\b", " ", s, flags=re.I)
    s = re.sub(r"\bleading(\s+(edition|version))?\b", "Leading Edition", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" -/,")
    if re.search(r"\bgen$", s, re.I):
        return None
    s = re.sub(r"\bxring 0(\d)\b", r"Xring O\1", s, flags=re.I)
    s = re.sub(r"\b(kirin \d+)([a-z])\b", lambda m: m.group(1) + m.group(2).upper(), s, flags=re.I)
    return s or None
