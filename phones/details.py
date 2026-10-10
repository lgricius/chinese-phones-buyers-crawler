"""Product-page details -> shared attributes.

Titles often omit the important facts (CN vs Global edition, Global ROM,
Google Play, unsealed, ships from EU), but every shop states them in the
product description, spec table or delivery widget. Each site adapter hands us the description HTML; we pull out a spec
table (th/td, dt/dd or table rows) plus plain-text statements and map them to
one set of fields.
"""

from __future__ import annotations

import html as htmllib
import re

FIELDS = (
    "edition_note",
    "edition_evidence",
    "condition",
    "ships_from",
    "delivery_note",
    "google_play",
    "chipset",
    "display_in",
    "battery_mah",
    "charging_w",
    "os",
    "languages",
)

# Sentences that advertise a *different* listing ("Buy the Global version HERE").
CROSS_SELL_RE = re.compile(r"\b(visit|click|here|also available|looking for|check out)\b", re.I)
CHINA_RE = re.compile(r"\b(chinese|china|cn)\s+(edition|version|rom|model)\b", re.I)
GLOBAL_RE = re.compile(r"\b(global|international|eu|european)\s+(edition|version|model)\b|hong\s*kong\s+model", re.I)
GLOBAL_ROM_RE = re.compile(r"global\s*rom\b|flashed\s+with\s+(?:the\s+)?global|global\s+firmware", re.I)

LABELS = {
    "chipset": ("chipset", "processor", "cpu", "soc"),
    "display": ("size", "screen", "display", "screen size", "display size"),
    "battery": ("battery", "battery capacity"),
    "charging": ("charging", "fast charging", "charge", "wired charging"),
    "os": ("os", "operating system", "system"),
    "languages": ("language", "languages"),
    "google_play": ("google play store", "google play", "gms", "google services"),
    "ram": ("ram", "memory"),
    "storage": ("internal storage", "rom", "storage", "internal"),
}


def html_to_text(h: str) -> str:
    h = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"<(br|/p|/li|/tr|/h\d|/div|/dd|/td)[^>]*>", "\n", h, flags=re.I)
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", h))
    return "\n".join(re.sub(r"\s+", " ", line).strip() for line in t.splitlines() if line.strip())


def spec_table(h: str) -> dict[str, str]:
    """Collect label -> value pairs from th/td, dt/dd and 2+ cell table rows."""
    out: dict[str, str] = {}

    def put(label: str, value: str) -> None:
        label = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", label))).strip(" :").lower()
        value = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", value))).strip()
        if label and value and len(label) < 40 and label not in out:
            out[label] = value

    for label, value in re.findall(r"<dt[^>]*>(.*?)</dt>\s*<dd[^>]*>(.*?)</dd>", h, re.S | re.I):
        put(label, value)
    for label, value in re.findall(r"<th[^>]*>(.*?)</th>\s*<td[^>]*>(.*?)</td>", h, re.S | re.I):
        put(label, value)
    # "<p><strong>Battery</strong>------6210mAh ...</p>" (gshopper descriptions)
    for label, value in re.findall(r"<p[^>]*>\s*<strong>([^<]{2,30})</strong>\s*[:：\-–]*\s*(.*?)</p>", h, re.S | re.I):
        put(label, value)
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", h, re.S | re.I):
        cells = [c for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I) if re.sub(r"<[^>]+>|&nbsp;|\s", "", c)]
        if len(cells) >= 2:
            put(cells[0], " | ".join(cells[1:]))
    return out


def _find(spec: dict[str, str], key: str) -> str | None:
    for label in LABELS[key]:
        if label in spec:
            return spec[label]
    return None


def detect_statement_edition(text: str) -> tuple[str | None, str | None]:
    """Edition stated in a description -> (cn | cn_global_rom | global | None, sentence).

    'Chinese version: flashed with Global rom' is cn_global_rom; 'Global Version
    (Hong Kong model)' is global; 'This is a Chinese Edition' is cn. Sentences
    advertising another listing ('Buy the Global version HERE') are ignored.
    """
    found: dict[str, str] = {}
    for sentence in re.split(r"[\n.;!]", text):
        if CROSS_SELL_RE.search(sentence):
            continue
        for name, rx in (("rom", GLOBAL_ROM_RE), ("global", GLOBAL_RE), ("cn", CHINA_RE)):
            if name not in found and rx.search(sentence):
                found[name] = sentence.strip()[:160]
    if "rom" in found:
        return "cn_global_rom", found["rom"]
    if "global" in found and "cn" not in found:
        return "global", found["global"]
    if "cn" in found:
        return "cn", found["cn"]
    return None, None


BANK_RE = re.compile(r"bank[\s-]*transfer|bank payment|wire transfer|\bsepa\b", re.I)
PERCENT_OFF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%(?:\s|<[^>]*>|\\/)*(?:off|discount)", re.I)


def bank_transfer_discount(page: str) -> float | None:
    """'Enjoy 4.5% discount by using local bank transfer', '4.5% Discount for Bank Transfer',
    'Bank transfer: Get 4.5% off instantly' -> 4.5. The percentage and "bank transfer" must be
    within ~150 characters of each other (markup included), in either order."""
    for m in PERCENT_OFF_RE.finditer(page):
        window = page[max(0, m.start() - 150) : m.end() + 150]
        if BANK_RE.search(window):
            return float(m.group(1))
    return None


def detect_condition(text: str) -> str | None:
    """new | new_unsealed | used | refurbished, from description statements."""
    if re.search(r"\brefurbished\b|\brenewed\b", text, re.I):
        return "refurbished"
    if re.search(r"\b(pre-?owned|second[- ]hand|used (?:device|phone|condition))\b", text, re.I):
        return "used"
    if re.search(r"\bunsealed\b|opened (?:for|to) (?:install|activat)|activated before (?:ship|deliver)", text, re.I):
        return "new_unsealed"
    if re.search(r"\b(brand new|factory sealed|sealed)\b", text, re.I):
        return "new"
    return None


def detect_shipping(text: str) -> tuple[str | None, str | None]:
    """(eu | cn | None, delivery note) from shipping widgets / statements."""
    m = re.search(r"\b(EU|Global) Priority\s*·\s*([^\n|]{3,40})", text)  # tradingshenzhen delivery widget
    if m:
        return ("eu" if m.group(1) == "EU" else "cn"), f"{m.group(1)} Priority {m.group(2).strip()}"
    m = re.search(r"((?:will be )?shipped out[^\n]{0,80})", text, re.I)  # wonda "Will be shipped out 2-3 working days..."
    return None, (m.group(1).strip()[:100] if m else None)


def _google_play(spec_value: str | None, text: str) -> bool | None:
    if spec_value:
        if re.search(r"\b(no|without|not)\b", spec_value, re.I):
            return False
        if re.search(r"\byes\b|installed|support", spec_value, re.I):
            return True
    if re.search(r"without\W+google|no google|not (?:support|have|come with)\W+google|google\W+(?:is )?not (?:supported|installed)", text, re.I):
        return False
    if re.search(r"(?:installed|with|pre-installed|supports?)\W+(?:full\W+)?google(?: play| mobile service| services)?|google play(?: store)? (?:is )?(?:installed|pre-installed|supported)", text, re.I):
        return True
    return None


def _first_number(pattern: str, text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(pattern, text, re.I)
    return float(m.group(1)) if m else None


def clean_chipset(v: str | None) -> str | None:
    if not v:
        return None
    v = re.sub(r"\bCPU:\s*", "", v, flags=re.I)
    m = re.search(
        r"(snapdragon\s+[\w\s+]*?(?:gen\s*\d+|\d{3}\+?)(?:\s+(?:leading|elite|extreme))?|dimensity\s+\d+\+?\w*|kirin\s+\d+\w*|tensor\s+g\d+|exynos\s+\d+|a\d{2}\s+(?:pro|bionic)?|xring\s+\w+|helio\s+\w+|unisoc\s+\w+)",
        v,
        re.I,
    )
    s = m.group(1) if m else v.split(",")[0].split(" - ")[0]
    s = re.sub(r"^qualcomm\s+(sm\w+\s+)?", "", s.strip(), flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+(soc|chipset|processor|platform|mobile platform)$", "", s, flags=re.I)
    return " ".join(w if w.isupper() or any(c.isdigit() for c in w) else w.capitalize() for w in s.split())[:60]


def extract(description_html: str, extra_text: str = "") -> dict:
    """Return the shared detail fields plus 'spec_ram'/'spec_storage' hints."""
    spec = spec_table(description_html)
    text = html_to_text(description_html) + "\n" + extra_text
    edition, evidence = detect_statement_edition(text)
    ships_from, delivery_note = detect_shipping(text)
    display_raw = _find(spec, "display")
    battery_raw = _find(spec, "battery") or text
    out = {
        "edition_note": edition,
        "edition_evidence": evidence,
        "condition": detect_condition(text),
        "ships_from": ships_from,
        "delivery_note": delivery_note,
        "google_play": _google_play(_find(spec, "google_play"), text),
        "chipset": clean_chipset(_find(spec, "chipset") or (re.search(r"(?:snapdragon|dimensity|kirin|tensor|exynos|xring)[^\n,;.]{0,40}", text, re.I) or [None])[0]),
        "display_in": _first_number(r"(\d{1,2}\.\d{1,2})\s*(?:-|\s)?\s*(?:inch|in\b|\"|”)", display_raw) or _first_number(r"(\d{1,2}\.\d{1,2})\s*(?:-|\s)?\s*inch", text),
        "battery_mah": _first_number(r"(\d{4,5})\s*mah", battery_raw),
        "charging_w": _first_number(r"(\d{2,3})\s*w\b(?!\s*wireless)", _find(spec, "charging") or _find(spec, "battery") or "") or _first_number(r"(\d{2,3})\s*w\b\s*(?:wired|super|fast|hyper|flash|supervooc|\w*charg)", text),
        "os": (_find(spec, "os") or None),
        "languages": (_find(spec, "languages") or None),
        "spec_ram": _find(spec, "ram"),
        "spec_storage": _find(spec, "storage"),
    }
    for k in ("battery_mah", "charging_w"):
        if out[k] is not None:
            out[k] = int(out[k])
    if out["os"]:
        out["os"] = out["os"][:60]
    if out["languages"]:
        out["languages"] = out["languages"][:200]
    return out


def device_type(title: str, display_in: float | None) -> str:
    t = title.lower()
    # Title only: screen size would misclassify tri-folds like the Mate XT (10.2").
    if re.search(r"\b(pad|tab|tablet|matepad|magicpad|matebook|laptop|notebook)\b", t) and "gaming pad" not in t:
        return "tablet"
    if re.search(r"\b(watch|band|buds|earphone|headset|case|charger|controller|gaming pad)\b", t):
        return "accessory"
    return "phone"
