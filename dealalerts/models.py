"""Records passed between the parts. All are immutable."""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

REGIONS: Tuple[str, ...] = ("sg", "hk", "jp", "us", "eu")
REGION_LABELS: Dict[str, str] = {
    "sg": "Singapore",
    "hk": "Hong Kong",
    "jp": "Japan",
    "us": "US",
    "eu": "Europe",
}
KNOWN_KINDS: Tuple[str, ...] = ("feed", "telegram")


@dataclass(frozen=True)
class Source:
    """One place deals are read from."""

    name: str
    region: str
    kind: str
    url: str
    language: str = "en"
    heat_threshold: Optional[int] = None
    require_sale_word: bool = False


@dataclass(frozen=True)
class RawPost:
    """One post as a fetcher read it, before any price parsing."""

    title: str
    link: str
    summary: str
    posted_at: Optional[str]
    heat: Optional[int]


@dataclass(frozen=True)
class Deal:
    """One post after parsing. Times are ISO 8601 strings in UTC."""

    id: str
    region: str
    source: str
    title: str
    link: str
    shop: Optional[str]
    price_now: Optional[float]
    usual_price: Optional[float]
    currency: Optional[str]
    discount_pct: Optional[float]
    heat: Optional[int]
    posted_at: Optional[str]
    first_seen: str


@dataclass(frozen=True)
class Verdict:
    """Scoring result. tier is 'alert', 'dashboard' or 'ignore'.

    kind is 'glitch', 'discount', 'heat' or '' and picks the alert headline.
    """

    tier: str
    kind: str
    reasons: Tuple[str, ...]
