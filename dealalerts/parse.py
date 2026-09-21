"""Pull prices, the discount and the shop name out of a deal post's text."""

import hashlib
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|ref$|ref_|spm$|aff)", re.IGNORECASE)

_AMOUNT = r"\d(?:[\d.,]*\d)?"
_PRICE = re.compile(
    r"(?P<cur>S\$|SGD|HK\$|HKD|港幣|US\$|USD|\$|£|GBP|€|EUR|¥|￥|JPY)\s?(?P<amt>" + _AMOUNT + r")"
    r"|(?P<amt2>" + _AMOUNT + r")\s?(?P<cur2>円|€|EUR|港元|HKD|SGD)"
)
_CURRENCY: Dict[str, str] = {
    "S$": "SGD", "SGD": "SGD", "HK$": "HKD", "HKD": "HKD", "港幣": "HKD", "港元": "HKD",
    "US$": "USD", "USD": "USD", "£": "GBP", "GBP": "GBP", "€": "EUR", "EUR": "EUR",
    "¥": "JPY", "￥": "JPY", "円": "JPY", "JPY": "JPY",
}
_BARE_DOLLAR: Dict[str, str] = {"sg": "SGD", "hk": "HKD", "us": "USD", "jp": "USD", "eu": "USD"}
_EUROPEAN_AMOUNT = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{1,2}|\d+,\d{2}")
_USUAL_BEFORE = re.compile(
    r"(?:\b(?:was|reg\.?|regular(?:ly)?|list(?: price)?|usual(?: price)?|u\.p\.?|rrp|msrp|"
    r"orig(?:inal(?:ly)?)?|statt|uvp|au lieu de)|原價|原价|正價|通常価格|参考価格|定価)\s*[:：]?\s*$",
    re.IGNORECASE,
)

_PERCENT = re.compile(r"(\d{1,2}(?:\.\d)?)\s?[%％]")
_UP_TO_BEFORE = re.compile(
    r"(?:up\s?to|as much as|bis zu|jusqu'?\s?[àa]|最大|最高|低至|高達|高达)\W{0,3}$", re.IGNORECASE
)
# "Up to an extra 70% off": still an "up to", with a word or two in between.
_UP_TO_EXTRA_BEFORE = re.compile(
    r"(?:up\s?to|as much as|bis zu|jusqu'?\s?[àa])\s+(?:an?\s+|ein[e]?[nr]?\s+)?"
    r"(?:extra|additional|further|weitere[rn]?)\s*$",
    re.IGNORECASE,
)
_RANGE_BEFORE = re.compile(r"\d\s?[%％]?\s?(?:-|–|to|bis)\s?$", re.IGNORECASE)
# "Save 80%" may have a space; a minus sign must touch the number, so that
# "128GB - 85% battery health" is not read as a price cut.
_SAVE_WORD_BEFORE = re.compile(r"(?:save|sparen|spare|économisez)\s?$", re.IGNORECASE)
_SAVE_DASH_BEFORE = re.compile(r"[-−–]$")
_OFF_AFTER = re.compile(
    r"^\s?(?:off|オフ|引|割引|rabatt|de réduction|de remise|discount|折扣)", re.IGNORECASE
)
# "70% off shipping", "75% off your first month": the cut is on something other
# than the thing being bought, or on a later purchase.
_CONDITIONAL_AFTER = re.compile(
    r"^\s?(?:off|rabatt|discount|de réduction|de remise)\s+(?:your\s+|the\s+|a\s+|an\s+)?"
    r"(?:second|2nd|next|first|1st|another|additional|extra|shipping|delivery|postage|versand)",
    re.IGNORECASE,
)
_NOT_A_PRICE_CUT_AFTER = re.compile(
    r"^\s?(?:ポイント|還元|points?|cash\s?back|回贈|回赠|p\.a\.|interest|apr)", re.IGNORECASE
)
_JA_TENTHS_OFF = re.compile(r"(\d)割引")
_JA_HALF_PRICE = re.compile(r"半額")
_ZH_SHARE_PAID = re.compile(r"(\d{1,2}(?:\.\d)?)折(?!扣)")

_SHOP_AFTER_AT = re.compile(
    r"(?:\bat|@)\s+([A-Z][\w&.'’\- ]{1,30}?)\s*(?=$|[-–|,(:+]|\bfor\b|\bwith\b|\bvia\b)"
)
_SHOP_LEADING = re.compile(r"^\s*([^：]{2,30})：")


@dataclass(frozen=True)
class ParsedFields:
    """What could be read from a post. Any field may be None."""

    price_now: Optional[float]
    usual_price: Optional[float]
    currency: Optional[str]
    discount_pct: Optional[float]
    shop: Optional[str]


def clean_link(url: str) -> str:
    """Lower-case the host, drop tracking parameters, the fragment and a trailing slash."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING.match(key)
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


def deal_id(url: str) -> str:
    """Short stable id for a deal: a hash of its cleaned link."""
    return hashlib.sha1(clean_link(url).encode("utf-8")).hexdigest()[:16]


def _to_amount(raw: str) -> Optional[float]:
    if _EUROPEAN_AMOUNT.fullmatch(raw):
        raw = raw.replace(".", "").replace(",", ".")
    else:
        raw = raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _find_prices(text: str, region: str) -> List[Tuple[str, float, bool]]:
    """Return (currency, amount, is_usual_price) for every price in the text."""
    prices: List[Tuple[str, float, bool]] = []
    for match in _PRICE.finditer(text):
        symbol = match.group("cur") or match.group("cur2")
        amount = _to_amount(match.group("amt") or match.group("amt2"))
        if amount is None or amount <= 0:
            continue
        currency = _BARE_DOLLAR.get(region, "USD") if symbol == "$" else _CURRENCY[symbol]
        before = text[max(0, match.start() - 25):match.start()]
        prices.append((currency, amount, bool(_USUAL_BEFORE.search(before))))
    return prices


def find_discount(text: str) -> Optional[float]:
    """Largest firmly stated discount in the text, as a percentage, or None.

    'Up to' wording, ranges, points-back, cashback and interest rates do not count.
    In Hong Kong usage 'N折' states the share paid, so '3折' is 70% off.
    """
    found: List[float] = []
    for match in _PERCENT.finditer(text):
        before = text[max(0, match.start() - 30):match.start()]
        after = text[match.end():match.end() + 24]
        if _UP_TO_BEFORE.search(before) or _UP_TO_EXTRA_BEFORE.search(before):
            continue
        if _RANGE_BEFORE.search(before):
            continue
        if _NOT_A_PRICE_CUT_AFTER.match(after) or _CONDITIONAL_AFTER.match(after):
            continue
        if (_OFF_AFTER.match(after) or _SAVE_WORD_BEFORE.search(before)
                or _SAVE_DASH_BEFORE.search(before)):
            found.append(float(match.group(1)))
    for match in _JA_TENTHS_OFF.finditer(text):
        if not _UP_TO_BEFORE.search(text[max(0, match.start() - 12):match.start()]):
            found.append(float(match.group(1)) * 10)
    for match in _JA_HALF_PRICE.finditer(text):
        if not _UP_TO_BEFORE.search(text[max(0, match.start() - 12):match.start()]):
            found.append(50.0)
    for match in _ZH_SHARE_PAID.finditer(text):
        before = text[max(0, match.start() - 12):match.start()]
        if _UP_TO_BEFORE.search(before) or text[match.end():match.end() + 1] == "起":
            continue
        share = float(match.group(1))
        share = share / 10 if share >= 10 else share
        found.append(round((10 - share) * 10, 1))
    valid = [value for value in found if 0 < value < 100]
    return max(valid) if valid else None


def _find_shop(title: str) -> Optional[str]:
    match = _SHOP_AFTER_AT.search(title) or _SHOP_LEADING.match(title)
    return match.group(1).strip() if match else None


def parse_fields(title: str, summary: str, region: str) -> ParsedFields:
    """Read price now, usual price, currency, discount and shop from a post."""
    lead = summary[:300]
    prices = _find_prices(title, region) or _find_prices(lead, region)
    if prices and not any(is_usual for _, _, is_usual in prices):
        prices = prices + [price for price in _find_prices(lead, region) if price[2]]
    now = next((price for price in prices if not price[2]), None)
    usual = next((price for price in prices if price[2]), None)

    discount = find_discount(title)
    if discount is None:
        discount = find_discount(lead)
    if discount is None and now and usual and now[0] == usual[0] and usual[1] > now[1]:
        discount = round((1 - now[1] / usual[1]) * 100, 1)

    return ParsedFields(
        price_now=now[1] if now else None,
        usual_price=usual[1] if usual else None,
        currency=now[0] if now else (usual[0] if usual else None),
        discount_pct=discount,
        shop=_find_shop(title),
    )
