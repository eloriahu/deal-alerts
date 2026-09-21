# Deal Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A free, always-on tool that reads public deal feeds for Singapore, Hong Kong, Japan, the US and Europe every 15 minutes, sends a Telegram alert for suspected price errors and discounts of 70% or more, and publishes a five-tab dashboard.

**Architecture:** A small Python package run by a GitHub Actions timer. Each run reads the feeds, turns posts into `Deal` records, scores them, saves what it has seen to `data/state.json` (committed back to the repository), sends Telegram messages for new alert-tier deals, and builds a static page that GitHub Pages publishes. Every part is a pure function or a thin wrapper around one network call, so everything except the network calls is unit-tested.

**Tech Stack:** Python 3.12, uv, feedparser, requests, PyYAML, beautifulsoup4, pytest, GitHub Actions, GitHub Pages, Telegram Bot API.

**Spec:** `docs/superpowers/specs/2026-09-21-deal-alerts-design.md`. Source test results: `docs/source-probe-2026-09-21.md`.

## Global Constraints

- Cost is zero. No paid service, no credit card, no paid tier of anything.
- Regions are exactly `sg`, `hk`, `jp`, `us`, `eu`. Region comes from the source entry in `sources.yaml`, never from the post text. Regions are never mixed in one list or one Telegram channel.
- Phone alert tier: glitch word, or discount of 70% or more, or fast votes. Dashboard tier: discount from 40% up to 70%. Everything else is ignored.
- "Up to N% off" wording never counts as an N% discount. A discount is computed from two prices only when both are in the same currency. Points-back and cashback percentages are not discounts.
- Each deal alerts once, remembered by a hash of its cleaned link for 30 days. The dashboard shows 7 days.
- The Telegram bot key and chat ids live only in GitHub encrypted secrets and environment variables: `TELEGRAM_BOT_TOKEN`, `TG_CHAT_SG`, `TG_CHAT_HK`, `TG_CHAT_JP`, `TG_CHAT_US`, `TG_CHAT_EU`, plus optional `TG_CHAT_TEST`. They must never appear in the repository, in logs, in error messages, or on the dashboard.
- The repository is public. Nothing about the owner goes into it. Commits use the repository owner's GitHub name and the noreply address `eloriahu@users.noreply.github.com` (already set in the local repository config).
- No logging in to any shop, no purchasing, no personal or payment data.
- Code style: type hints on every function, docstrings, module-level `logger = logging.getLogger(__name__)`, no `print`, frozen dataclasses for records, no bare `except`, files under 400 lines, `__all__` in each package `__init__.py`.
- Run everything locally through uv: `uv run pytest ...`, `uv run python run.py ...`. Python is not on PATH on this PC except through uv.
- Shell on this PC: run commands in Git Bash (the Bash tool). Before any `git add`, confirm `git rev-parse --show-toplevel` prints `<project folder>`.
- Commits follow Conventional Commits and end with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Spec deviation, agreed here: the spec lists the bare word "mistake" as a glitch word. This plan uses the phrases "price mistake", "pricing mistake" and "mistake fare" instead, because the bare word fires on ordinary sentences.
- Spec deviation: HardwareZone has no deals section (only garage sales), so it is not a source.

## File Structure

```
deal-alerts/
  pyproject.toml                  project + dependencies (uv)
  .gitignore
  sources.yaml                    sources, glitch words, thresholds
  run.py                          command-line entry point (thin)
  dealalerts/
    __init__.py                   public names
    models.py                     Source, RawPost, Deal, Verdict records; region names
    config.py                     load and validate sources.yaml
    parse.py                      clean links; pull prices, discount, shop from text
    score.py                      decide tier and reasons
    http.py                       polite web client (gap between calls to one host)
    fetchers/
      __init__.py                 registry: kind -> fetch function
      feed.py                     RSS / Atom / RDF feeds (covers Reddit feeds too)
      telegram.py                 public Telegram channel preview pages
    store.py                      State: seen, alerted, deals, source health
    notify.py                     Telegram message text and sending
    site.py                       build public/index.html
    pipeline.py                   one full run, wiring the parts together
  scripts/
    probe_sources.py              test every source, print a table
  tests/
    test_config.py  test_parse.py  test_score.py  test_http.py
    test_feed.py  test_telegram_fetcher.py  test_store.py
    test_notify.py  test_site.py  test_pipeline.py
  data/state.json                 written by runs, committed
  public/                         built page, not committed
  .github/workflows/
    probe.yml                     manual: run the source probe on GitHub
    scan.yml                      every 15 minutes: run, save state, publish page
```

---

### Task 1: Project scaffold, records, and configuration

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `sources.yaml`
- Create: `dealalerts/__init__.py`, `dealalerts/models.py`, `dealalerts/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `models.REGIONS`, `models.REGION_LABELS`, `models.KNOWN_KINDS`, records `Source`, `RawPost`, `Deal`, `Verdict`; `config.Settings`, `config.Config`, `config.load_config(path: Path) -> Config`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "deal-alerts"
version = "0.1.0"
description = "Free online alerts for price errors and very deep discounts"
requires-python = ">=3.12"
dependencies = [
    "feedparser>=6.0.11",
    "requests>=2.32",
    "PyYAML>=6.0",
    "beautifulsoup4>=4.12",
]

[dependency-groups]
dev = ["pytest>=8.0"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Write `.gitignore`**

```
.venv/
__pycache__/
.pytest_cache/
public/
*.pyc
.env
```

- [ ] **Step 3: Write `sources.yaml`**

Every address below returned a readable feed on 2026-09-21 from the owner's PC, except the Reddit ones marked in the probe notes as rate-limited. Task 10 re-tests all of them from GitHub and removes any that fail there.

```yaml
settings:
  alert_discount: 70
  dashboard_discount: 40
  heat_window_minutes: 180
  seen_days: 30
  dashboard_days: 7
  failure_note_after: 5
  same_host_gap_seconds: 3
  max_alerts_per_run: 15
  stale_alert_hours: 6

glitch_words:
  en: ["price error", "pricing error", "price glitch", "glitch", "misprice", "mispriced",
       "price mistake", "pricing mistake", "error fare", "mistake fare"]
  de: ["preisfehler"]
  fr: ["erreur de prix"]
  zh: ["錯價", "出錯價", "標錯價", "價錢錯誤"]
  ja: ["価格ミス", "価格エラー", "誤表記", "価格バグ", "設定ミス"]

sale_words:
  en: ["sale", "deal", "discount", "% off", "price drop"]
  ja: ["セール", "特価", "割引", "半額", "値下げ", "タイムセール"]

expired_words: ["expired", "abgelaufen", "expiré", "ausverkauft", "sold out",
                "終了しました", "已完結", "已結束"]

sources:
  # Singapore
  - {name: SingPromos, region: sg, kind: feed, language: en,
     url: "https://www.singpromos.com/feed/"}
  - {name: MoneyDigest, region: sg, kind: feed, language: en,
     url: "https://www.moneydigest.sg/feed/"}
  - {name: MileLion, region: sg, kind: feed, language: en, require_sale_word: true,
     url: "https://milelion.com/feed/"}
  - {name: r/singaporedeals, region: sg, kind: feed, language: en,
     url: "https://www.reddit.com/r/singaporedeals/new/.rss"}
  # Hong Kong
  - {name: Jetso Club, region: hk, kind: feed, language: zh,
     url: "https://www.jetsoclub.com/feeds/posts/default"}
  - {name: Jetso Today, region: hk, kind: feed, language: zh,
     url: "https://www.jetsotoday.com/feed"}
  - {name: GoTrip, region: hk, kind: feed, language: zh,
     url: "https://www.gotrip.hk/feed"}
  # Japan
  - {name: Gekiyasu, region: jp, kind: feed, language: ja,
     url: "https://gekiyasu-gekiyasu.doorblog.jp/index.rdf"}
  - {name: Traicy, region: jp, kind: feed, language: ja, require_sale_word: true,
     url: "https://www.traicy.com/feed"}
  - {name: PC Watch, region: jp, kind: feed, language: ja, require_sale_word: true,
     url: "https://pc.watch.impress.co.jp/data/rss/1.0/pcw/feed.rdf"}
  # US
  - {name: Slickdeals Frontpage, region: us, kind: feed, language: en, heat_threshold: 60,
     url: "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1"}
  - {name: Slickdeals Popular, region: us, kind: feed, language: en, heat_threshold: 60,
     url: "https://slickdeals.net/newsearch.php?mode=popdeals&searcharea=deals&searchin=first&rss=1"}
  - {name: r/deals, region: us, kind: feed, language: en,
     url: "https://www.reddit.com/r/deals/new/.rss"}
  - {name: r/buildapcsales, region: us, kind: feed, language: en,
     url: "https://www.reddit.com/r/buildapcsales/new/.rss"}
  - {name: The Flight Deal, region: us, kind: feed, language: en,
     url: "https://www.theflightdeal.com/feed/"}
  # Europe
  - {name: DealDoktor, region: eu, kind: feed, language: de,
     url: "https://www.dealdoktor.de/feed/"}
  - {name: Mein-Deal, region: eu, kind: feed, language: de,
     url: "https://www.mein-deal.com/feed/"}
  - {name: Travel-Dealz, region: eu, kind: feed, language: en,
     url: "https://travel-dealz.eu/feed/"}
  - {name: r/UKDeals, region: eu, kind: feed, language: en,
     url: "https://www.reddit.com/r/UKDeals/new/.rss"}
```

- [ ] **Step 4: Write the failing test `tests/test_config.py`**

```python
from pathlib import Path

import pytest

from dealalerts.config import load_config
from dealalerts.models import REGIONS

ROOT = Path(__file__).resolve().parents[1]


def test_real_sources_file_loads_and_covers_every_region() -> None:
    config = load_config(ROOT / "sources.yaml")
    regions_with_sources = {source.region for source in config.sources}
    assert regions_with_sources == set(REGIONS)
    assert config.settings.alert_discount == 70
    assert config.settings.dashboard_discount == 40


def test_glitch_words_are_flattened_and_casefolded() -> None:
    config = load_config(ROOT / "sources.yaml")
    assert "price error" in config.glitch_words
    assert "preisfehler" in config.glitch_words
    assert "価格ミス" in config.glitch_words


def test_unknown_region_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: X, region: mars, kind: feed, url: 'https://x.test/feed'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="region"):
        load_config(bad)


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: X, region: sg, kind: carrier-pigeon, url: 'https://x.test/feed'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="kind"):
        load_config(bad)


def test_duplicate_source_names_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n"
        "  - {name: X, region: sg, kind: feed, url: 'https://x.test/a'}\n"
        "  - {name: X, region: us, kind: feed, url: 'https://x.test/b'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(bad)
```

- [ ] **Step 5: Run it and watch it fail**

Run: `cd ~/deal-alerts && uv sync && uv run pytest tests/test_config.py -v`
Expected: `uv sync` creates `.venv` and `uv.lock`; pytest FAILS with `ModuleNotFoundError: No module named 'dealalerts'`.

- [ ] **Step 6: Write `dealalerts/models.py`**

```python
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
```

- [ ] **Step 7: Write `dealalerts/config.py`**

```python
"""Load and validate sources.yaml."""

import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple

import yaml

from dealalerts.models import KNOWN_KINDS, REGIONS, Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Settings:
    """Thresholds and limits. Defaults match the design spec."""

    alert_discount: float = 70
    dashboard_discount: float = 40
    heat_window_minutes: int = 180
    seen_days: int = 30
    dashboard_days: int = 7
    failure_note_after: int = 5
    same_host_gap_seconds: float = 3
    max_alerts_per_run: int = 15
    stale_alert_hours: int = 6


@dataclass(frozen=True)
class Config:
    """Everything read from sources.yaml."""

    settings: Settings
    sources: Tuple[Source, ...]
    glitch_words: Tuple[str, ...]
    sale_words: Tuple[str, ...]
    expired_words: Tuple[str, ...]


def _flatten(words_by_language: Dict[str, Iterable[str]]) -> Tuple[str, ...]:
    """Merge per-language word lists into one casefolded tuple."""
    merged = []
    for words in words_by_language.values():
        merged.extend(word.casefold() for word in words)
    return tuple(dict.fromkeys(merged))


def _build_source(raw: Dict[str, Any]) -> Source:
    allowed = {field.name for field in fields(Source)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"source {raw.get('name')!r} has unknown keys: {sorted(unknown)}")
    source = Source(**raw)
    if source.region not in REGIONS:
        raise ValueError(f"source {source.name!r} has unknown region {source.region!r}")
    if source.kind not in KNOWN_KINDS:
        raise ValueError(f"source {source.name!r} has unknown kind {source.kind!r}")
    return source


def load_config(path: Path) -> Config:
    """Read sources.yaml.

    Raises:
        ValueError: on an unknown region, unknown kind, unknown key, or duplicate name.
    """
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    sources = tuple(_build_source(entry) for entry in raw.get("sources", []))
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ValueError("duplicate source names in sources.yaml")
    return Config(
        settings=Settings(**raw.get("settings", {})),
        sources=sources,
        glitch_words=_flatten(raw.get("glitch_words", {})),
        sale_words=_flatten(raw.get("sale_words", {})),
        expired_words=tuple(word.casefold() for word in raw.get("expired_words", [])),
    )
```

- [ ] **Step 8: Write `dealalerts/__init__.py`**

```python
"""Deal Alerts: spot price errors and very deep discounts from public deal feeds."""

from dealalerts.config import Config, Settings, load_config
from dealalerts.models import REGION_LABELS, REGIONS, Deal, RawPost, Source, Verdict

__all__ = [
    "Config",
    "Deal",
    "RawPost",
    "REGION_LABELS",
    "REGIONS",
    "Settings",
    "Source",
    "Verdict",
    "load_config",
]
```

- [ ] **Step 9: Run the tests and watch them pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 5 passed.

- [ ] **Step 10: Commit**

```bash
git rev-parse --show-toplevel   # must print <project folder>
git add pyproject.toml uv.lock .gitignore sources.yaml dealalerts tests
git commit -m "feat: add project scaffold, records and config loader"
```

---

### Task 2: Parsing prices, discounts, shop and links

**Files:**
- Create: `dealalerts/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Produces: `clean_link(url: str) -> str`, `deal_id(url: str) -> str` (16 hex characters), `find_discount(text: str) -> Optional[float]`, `ParsedFields(price_now, usual_price, currency, discount_pct, shop)`, `parse_fields(title: str, summary: str, region: str) -> ParsedFields`.

The test titles are real ones taken from the feeds on 2026-09-21, plus hand-written glitch and false-alarm cases.

- [ ] **Step 1: Write the failing test `tests/test_parse.py`**

```python
import pytest

from dealalerts.parse import clean_link, deal_id, find_discount, parse_fields


def test_clean_link_drops_tracking_and_fragment() -> None:
    dirty = "HTTPS://Shop.Example.com/item/42/?utm_source=x&color=red&fbclid=abc#reviews"
    assert clean_link(dirty) == "https://shop.example.com/item/42?color=red"


def test_deal_id_is_stable_across_tracking_noise() -> None:
    first = deal_id("https://shop.example.com/item/42?utm_source=a")
    second = deal_id("https://shop.example.com/item/42/?utm_campaign=b")
    assert first == second
    assert len(first) == 16


def test_single_us_price_no_discount() -> None:
    fields = parse_fields(
        "2-Pk 1.5m Beats USB-A to USB-C or USB-C Charging Cables (Black) $14", "", "us"
    )
    assert (fields.price_now, fields.currency) == (14.0, "USD")
    assert fields.usual_price is None
    assert fields.discount_pct is None


def test_singapore_usual_price_gives_discount_and_shop() -> None:
    fields = parse_fields("Sony WH-1000XM6 S$89 (U.P. S$549) at Amazon.sg", "", "sg")
    assert (fields.price_now, fields.usual_price, fields.currency) == (89.0, 549.0, "SGD")
    assert fields.discount_pct == pytest.approx(83.8, abs=0.1)
    assert fields.shop == "Amazon.sg"


def test_bare_dollar_follows_region() -> None:
    assert parse_fields("McDonald's S'pore $1 Coffee Deal", "", "sg").currency == "SGD"
    assert parse_fields("爭鮮迴轉店：堂食$34產品", "", "hk").currency == "HKD"


def test_was_price_in_us_title() -> None:
    fields = parse_fields("Dyson V8 Cordless Vacuum $199.99 (was $449.99)", "", "us")
    assert fields.discount_pct == pytest.approx(55.6, abs=0.1)


def test_japanese_yen_price_with_thousands_comma() -> None:
    fields = parse_fields("【1,073円】ポッカサッポロ じっくりコトコト 3食入り×5個がセール特価", "", "jp")
    assert (fields.price_now, fields.currency) == (1073.0, "JPY")


def test_japanese_points_back_is_not_a_discount() -> None:
    fields = parse_fields("【実質436円】マンチキン ミラクルカップが70％ポイント還元", "", "jp")
    assert fields.price_now == 436.0
    assert fields.discount_pct is None


def test_german_prices_with_decimal_comma_and_statt() -> None:
    fields = parse_fields("Preisfehler? Bosch Akkuschrauber 29,99 € statt 129,99 €", "", "eu")
    assert (fields.price_now, fields.usual_price, fields.currency) == (29.99, 129.99, "EUR")
    assert fields.discount_pct == pytest.approx(76.9, abs=0.1)


def test_mixed_currencies_never_give_a_discount() -> None:
    fields = parse_fields("Gadget US$50 (was S$200)", "", "sg")
    assert fields.discount_pct is None


def test_leading_shop_with_fullwidth_colon() -> None:
    assert parse_fields("宜家 IKEA：美食站 本月精選", "", "hk").shop == "宜家 IKEA"


def test_shop_after_at() -> None:
    assert parse_fields("Up to 70% off sitewide at Nike", "", "us").shop == "Nike"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Anker charger 70% off", 70.0),
        ("Save 80% on winter coats", 80.0),
        ("Lego set -72%", 72.0),
        ("全場3折", 70.0),
        ("指定貨品85折", 15.0),
        ("お弁当が半額", 50.0),
        ("全品7割引", 70.0),
        ("Extra 10% off, already 75% off", 75.0),
    ],
)
def test_stated_discounts(text: str, expected: float) -> None:
    assert find_discount(text) == pytest.approx(expected)


@pytest.mark.parametrize(
    "text",
    [
        "Up to 70% off sitewide at Nike",
        "Bis zu 80% Rabatt im Sale",
        "10-70% off everything",
        "低至3折",
        "3折起",
        "最大半額",
        "70％ポイント還元",
        "90% cashback for new users",
        "Bank of China Offers Up to 1.8% Time Deposit Promo Rates",
        "CIMB fixed deposit now pays 1.85% p.a.",
        "Battery lasts 80% longer",
    ],
)
def test_false_alarm_wording_gives_no_discount(text: str) -> None:
    assert find_discount(text) is None


def test_summary_is_used_when_title_has_no_price() -> None:
    fields = parse_fields("Crazy headphone deal", "Now $20 (reg. $100) while stock lasts", "us")
    assert (fields.price_now, fields.usual_price) == (20.0, 100.0)
    assert fields.discount_pct == pytest.approx(80.0)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.parse'`.

- [ ] **Step 3: Write `dealalerts/parse.py`**

```python
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
_RANGE_BEFORE = re.compile(r"\d\s?[%％]?\s?(?:-|–|to|bis)\s?$", re.IGNORECASE)
_SAVE_BEFORE = re.compile(r"(?:-|−|–|save|sparen|spare|économisez)\s?$", re.IGNORECASE)
_OFF_AFTER = re.compile(
    r"^\s?(?:off|オフ|引|割引|rabatt|de réduction|de remise|discount|折扣)", re.IGNORECASE
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
        before = text[max(0, match.start() - 12):match.start()]
        after = text[match.end():match.end() + 14]
        if _UP_TO_BEFORE.search(before) or _RANGE_BEFORE.search(before):
            continue
        if _NOT_A_PRICE_CUT_AFTER.match(after):
            continue
        if _OFF_AFTER.match(after) or _SAVE_BEFORE.search(before):
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
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_parse.py -v`
Expected: all passed. If a regular-expression case fails, fix the pattern, never the test: every test case states a rule from the spec.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/parse.py tests/test_parse.py
git commit -m "feat: parse prices, discounts, shop and links from post text"
```

---

### Task 3: Scoring

**Files:**
- Create: `dealalerts/score.py`
- Test: `tests/test_score.py`

**Interfaces:**
- Consumes: `Deal`, `Source`, `Verdict` from `models`; `Config` from `config`.
- Produces: `score(deal: Deal, text: str, source: Source, config: Config, now: datetime) -> Verdict`. `text` is the post title plus summary, used for the glitch-word and sale-word checks. `now` is timezone-aware UTC.

- [ ] **Step 1: Write the failing test `tests/test_score.py`**

```python
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from dealalerts.config import Config, Settings
from dealalerts.models import Deal, Source
from dealalerts.score import score

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
CONFIG = Config(
    settings=Settings(),
    sources=(),
    glitch_words=("price error", "glitch", "preisfehler", "錯價", "価格ミス"),
    sale_words=("sale", "セール"),
    expired_words=("expired", "sold out"),
)
SOURCE = Source(name="Test", region="us", kind="feed", url="https://x.test/feed")
HOT_SOURCE = replace(SOURCE, heat_threshold=60)
NEWS_SOURCE = replace(SOURCE, require_sale_word=True)
DEAL = Deal(
    id="abc", region="us", source="Test", title="Plain toaster $20", link="https://x.test/1",
    shop=None, price_now=20.0, usual_price=None, currency="USD", discount_pct=None,
    heat=None, posted_at=(NOW - timedelta(minutes=30)).isoformat(), first_seen=NOW.isoformat(),
)


def test_plain_post_is_ignored() -> None:
    assert score(DEAL, DEAL.title, SOURCE, CONFIG, NOW).tier == "ignore"


def test_glitch_word_alerts() -> None:
    verdict = score(DEAL, "Possible PRICE ERROR on toaster", SOURCE, CONFIG, NOW)
    assert (verdict.tier, verdict.kind) == ("alert", "glitch")
    assert 'glitch word "price error"' in verdict.reasons


def test_chinese_and_japanese_glitch_words_alert() -> None:
    assert score(DEAL, "百佳標錯價 出錯價", SOURCE, CONFIG, NOW).tier == "alert"
    assert score(DEAL, "Amazonで価格ミスか", SOURCE, CONFIG, NOW).tier == "alert"


def test_seventy_percent_alerts_and_forty_goes_to_dashboard() -> None:
    assert score(replace(DEAL, discount_pct=70.0), "x", SOURCE, CONFIG, NOW).tier == "alert"
    assert score(replace(DEAL, discount_pct=69.9), "x", SOURCE, CONFIG, NOW).tier == "dashboard"
    assert score(replace(DEAL, discount_pct=40.0), "x", SOURCE, CONFIG, NOW).tier == "dashboard"
    assert score(replace(DEAL, discount_pct=39.9), "x", SOURCE, CONFIG, NOW).tier == "ignore"


def test_fast_votes_alert_only_inside_the_window() -> None:
    fresh = replace(DEAL, heat=75)
    verdict = score(fresh, "x", HOT_SOURCE, CONFIG, NOW)
    assert (verdict.tier, verdict.kind) == ("alert", "heat")
    old = replace(fresh, posted_at=(NOW - timedelta(hours=5)).isoformat())
    assert score(old, "x", HOT_SOURCE, CONFIG, NOW).tier == "ignore"
    assert score(fresh, "x", SOURCE, CONFIG, NOW).tier == "ignore"  # no threshold set


def test_expired_title_is_ignored_even_with_glitch_word() -> None:
    expired = replace(DEAL, title="[Expired] price error toaster")
    assert score(expired, expired.title, SOURCE, CONFIG, NOW).tier == "ignore"


def test_news_source_needs_a_sale_word_unless_glitch() -> None:
    big = replace(DEAL, discount_pct=80.0)
    assert score(big, "New laptop review", NEWS_SOURCE, CONFIG, NOW).tier == "ignore"
    assert score(big, "Laptop sale this week", NEWS_SOURCE, CONFIG, NOW).tier == "alert"
    assert score(DEAL, "Retailer glitch reported", NEWS_SOURCE, CONFIG, NOW).tier == "alert"


def test_glitch_takes_the_headline_when_several_rules_fire() -> None:
    both = replace(DEAL, discount_pct=90.0)
    verdict = score(both, "price error", SOURCE, CONFIG, NOW)
    assert verdict.kind == "glitch"
    assert "90% off" in verdict.reasons
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_score.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.score'`.

- [ ] **Step 3: Write `dealalerts/score.py`**

```python
"""Decide whether a deal buzzes the phone, sits on the dashboard, or is dropped."""

import logging
from datetime import datetime
from typing import List

from dealalerts.config import Config
from dealalerts.models import Deal, Source, Verdict

logger = logging.getLogger(__name__)

_IGNORE = Verdict(tier="ignore", kind="", reasons=())


def _age_minutes(deal: Deal, now: datetime) -> float:
    posted = datetime.fromisoformat(deal.posted_at or deal.first_seen)
    return max(0.0, (now - posted).total_seconds() / 60)


def score(deal: Deal, text: str, source: Source, config: Config, now: datetime) -> Verdict:
    """Apply the spec's scoring rules.

    Args:
        deal: The parsed deal.
        text: Title plus summary, checked for glitch words and sale words.
        source: Where the deal came from (vote threshold, sale-word gate).
        config: Thresholds and word lists.
        now: Current time, timezone-aware UTC.
    """
    settings = config.settings
    if any(word in deal.title.casefold() for word in config.expired_words):
        return _IGNORE

    folded = text.casefold()
    glitch = next((word for word in config.glitch_words if word in folded), None)
    if glitch is None and source.require_sale_word:
        if not any(word in folded for word in config.sale_words):
            return _IGNORE

    reasons: List[str] = []
    kind = ""
    if glitch is not None:
        reasons.append(f'glitch word "{glitch}"')
        kind = "glitch"

    discount = deal.discount_pct
    if discount is not None and discount >= settings.alert_discount:
        reasons.append(f"{discount:.0f}% off")
        kind = kind or "discount"

    age = _age_minutes(deal, now)
    if (
        source.heat_threshold is not None
        and deal.heat is not None
        and deal.heat >= source.heat_threshold
        and age <= settings.heat_window_minutes
    ):
        reasons.append(f"{deal.heat} votes in {age:.0f} min")
        kind = kind or "heat"

    if reasons:
        return Verdict(tier="alert", kind=kind, reasons=tuple(reasons))
    if discount is not None and discount >= settings.dashboard_discount:
        return Verdict(tier="dashboard", kind="discount", reasons=(f"{discount:.0f}% off",))
    return _IGNORE
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_score.py -v`
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/score.py tests/test_score.py
git commit -m "feat: score deals into alert, dashboard and ignore tiers"
```

---

### Task 4: Polite web client and the feed fetcher

**Files:**
- Create: `dealalerts/http.py`, `dealalerts/fetchers/__init__.py`, `dealalerts/fetchers/feed.py`
- Test: `tests/test_http.py`, `tests/test_feed.py`

**Interfaces:**
- Produces: `http.FetchError`, `http.PoliteClient(gap_seconds: float, session=None, sleep=time.sleep, clock=time.monotonic)` with `get(url: str) -> bytes` and `post_json(url: str, payload: dict) -> dict`; `fetchers.register_fetcher(kind)`, `fetchers.fetch(source: Source, client: PoliteClient) -> List[RawPost]`; `fetchers.feed.parse_feed(content: bytes) -> List[RawPost]`.
- Error messages from `PoliteClient` name the host only, never the full address, because the Telegram address contains the bot key.

- [ ] **Step 1: Write the failing test `tests/test_http.py`**

```python
from typing import List

import pytest

from dealalerts.http import FetchError, PoliteClient


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"ok") -> None:
        self.status_code = status
        self.content = content

    def json(self) -> dict:
        return {"ok": True}


class FakeSession:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: List[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.status)

    def post(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.status)


def test_second_call_to_same_host_waits_for_the_gap() -> None:
    slept: List[float] = []
    times = iter([100.0, 101.0, 101.0])
    client = PoliteClient(3, session=FakeSession(), sleep=slept.append, clock=lambda: next(times))
    client.get("https://www.reddit.com/r/a/.rss")
    client.get("https://www.reddit.com/r/b/.rss")
    assert slept == [2.0]


def test_different_hosts_do_not_wait() -> None:
    slept: List[float] = []
    client = PoliteClient(3, session=FakeSession(), sleep=slept.append, clock=lambda: 100.0)
    client.get("https://a.test/feed")
    client.get("https://b.test/feed")
    assert slept == []


def test_error_status_raises_without_leaking_the_address() -> None:
    client = PoliteClient(0, session=FakeSession(status=403), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get("https://api.test/botSECRET123/sendMessage")
    assert "403" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert "api.test" in str(caught.value)
```

- [ ] **Step 2: Write the failing test `tests/test_feed.py`**

```python
import pytest

from dealalerts.fetchers.feed import parse_feed
from dealalerts.http import FetchError

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Slickdeals Frontpage RSS Feed</title>
<item>
  <title>The Time Machine (1960, Blu-ray) $9 or less</title>
  <link>https://slickdeals.net/f/123-time-machine?utm_source=rss</link>
  <description>&lt;p&gt;Amazon has it for &lt;b&gt;$9&lt;/b&gt;.&lt;/p&gt; Thumb Score: +17</description>
  <pubDate>Mon, 21 Sep 2026 03:15:00 +0000</pubDate>
</item>
<item>
  <title>No votes here</title>
  <link>https://slickdeals.net/f/124</link>
  <description>Plain text</description>
</item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Jetso Club</title>
<entry>
  <title type="text">大家樂：豬扒撈公仔麵 $32</title>
  <link rel="alternate" href="https://www.jetsoclub.com/2026/09/cafe.html"/>
  <updated>2026-09-21T02:00:00+08:00</updated>
  <content type="html">詳情</content>
</entry>
</feed>""".encode("utf-8")


def test_rss_items_become_posts_with_votes_and_clean_summary() -> None:
    posts = parse_feed(RSS)
    assert len(posts) == 2
    first = posts[0]
    assert first.title == "The Time Machine (1960, Blu-ray) $9 or less"
    assert first.link == "https://slickdeals.net/f/123-time-machine?utm_source=rss"
    assert first.heat == 17
    assert "<" not in first.summary and "Amazon has it for $9" in first.summary
    assert first.posted_at == "2026-09-21T03:15:00+00:00"
    assert posts[1].heat is None and posts[1].posted_at is None


def test_atom_entries_are_read_and_times_become_utc() -> None:
    posts = parse_feed(ATOM)
    assert posts[0].title == "大家樂：豬扒撈公仔麵 $32"
    assert posts[0].link == "https://www.jetsoclub.com/2026/09/cafe.html"
    assert posts[0].posted_at == "2026-09-20T18:00:00+00:00"


def test_a_web_page_instead_of_a_feed_is_an_error() -> None:
    with pytest.raises(FetchError, match="not a feed"):
        parse_feed(b"<!DOCTYPE html><html><body>Access denied</body></html>")
```

- [ ] **Step 3: Run both and watch them fail**

Run: `uv run pytest tests/test_http.py tests/test_feed.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.http'`.

- [ ] **Step 4: Write `dealalerts/http.py`**

```python
"""A web client that waits between calls to the same host and never leaks addresses."""

import logging
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; deal-alerts/0.1; +https://github.com/eloriahu/deal-alerts)"
TIMEOUT_SECONDS = 20


class FetchError(Exception):
    """A source could not be read. The message never contains a full address."""


class PoliteClient:
    """Wraps requests. Keeps a minimum gap between calls to one host."""

    def __init__(
        self,
        gap_seconds: float,
        session: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gap = gap_seconds
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_call: Dict[str, float] = {}

    def _wait_turn(self, host: str) -> None:
        last = self._last_call.get(host)
        now = self._clock()
        if last is not None and now - last < self._gap:
            self._sleep(self._gap - (now - last))
            now = last + self._gap
        self._last_call[host] = now

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        host = urlsplit(url).netloc
        self._wait_turn(host)
        try:
            response = getattr(self._session, method)(
                url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as error:
            raise FetchError(f"{host}: {type(error).__name__}") from None
        if response.status_code >= 400:
            raise FetchError(f"{host}: HTTP {response.status_code}")
        return response

    def get(self, url: str) -> bytes:
        """Fetch a page or feed. Raises FetchError on any failure."""
        return self._call("get", url).content

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send JSON, return the JSON reply. Raises FetchError on any failure."""
        return self._call("post", url, json=payload).json()
```

- [ ] **Step 5: Write `dealalerts/fetchers/__init__.py`**

```python
"""Registry of fetchers: one function per source kind."""

from typing import Callable, Dict, List

from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source

Fetcher = Callable[[Source, PoliteClient], List[RawPost]]
FETCHERS: Dict[str, Fetcher] = {}


def register_fetcher(kind: str) -> Callable[[Fetcher], Fetcher]:
    """Decorator that registers a fetch function for a source kind."""

    def decorator(function: Fetcher) -> Fetcher:
        FETCHERS[kind] = function
        return function

    return decorator


def fetch(source: Source, client: PoliteClient) -> List[RawPost]:
    """Read one source. Raises FetchError if the kind is unknown or the read fails."""
    from dealalerts.fetchers import feed, telegram  # noqa: F401  (registers the fetchers)

    try:
        fetcher = FETCHERS[source.kind]
    except KeyError:
        raise FetchError(f"no fetcher for kind {source.kind!r}") from None
    return fetcher(source, client)


__all__ = ["FETCHERS", "Fetcher", "fetch", "register_fetcher"]
```

Note: this file imports `telegram`, which Task 5 creates. Until then, create `dealalerts/fetchers/telegram.py` containing only the line `"""Telegram preview fetcher (added in Task 5)."""` so the import succeeds.

- [ ] **Step 6: Write `dealalerts/fetchers/feed.py`**

```python
"""Read RSS, Atom and RDF feeds. Reddit's feeds are Atom, so they come through here too."""

import html
import logging
import re
from datetime import datetime, timezone
from time import struct_time
from typing import List, Optional

import feedparser

from dealalerts.fetchers import register_fetcher
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_THUMB_SCORE = re.compile(r"Thumb Score:\s*\+?(-?\d+)")


def _plain_text(markup: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", markup))).strip()


def _to_iso(parsed_time: Optional[struct_time]) -> Optional[str]:
    if parsed_time is None:
        return None
    return datetime(*parsed_time[:6], tzinfo=timezone.utc).isoformat()


def parse_feed(content: bytes) -> List[RawPost]:
    """Turn feed bytes into posts.

    Raises:
        FetchError: when the bytes are not a feed (for example a block page).
    """
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FetchError("not a feed")
    posts: List[RawPost] = []
    for entry in parsed.entries:
        title = _plain_text(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        summary = _plain_text(entry.get("summary", ""))
        votes = _THUMB_SCORE.search(summary)
        posts.append(
            RawPost(
                title=title,
                link=link,
                summary=summary,
                posted_at=_to_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
                heat=int(votes.group(1)) if votes else None,
            )
        )
    return posts


@register_fetcher("feed")
def fetch_feed(source: Source, client: PoliteClient) -> List[RawPost]:
    """Download and parse one feed."""
    return parse_feed(client.get(source.url))
```

- [ ] **Step 7: Run the tests and watch them pass**

Run: `uv run pytest tests/test_http.py tests/test_feed.py -v`
Expected: 6 passed.

- [ ] **Step 8: Commit**

```bash
git add dealalerts/http.py dealalerts/fetchers tests/test_http.py tests/test_feed.py
git commit -m "feat: add polite web client and feed fetcher"
```

---

### Task 5: Telegram public-channel fetcher

Public Telegram channels have a preview page at `https://t.me/s/<channel>` that needs no login. `sources.yaml` ships with no Telegram entries: the local network blocks `t.me`, so channels can only be tested from GitHub (Task 10), and only channels the owner names get added.

**Files:**
- Modify: `dealalerts/fetchers/telegram.py` (replace the one-line stub from Task 4)
- Test: `tests/test_telegram_fetcher.py`

**Interfaces:**
- Produces: `fetchers.telegram.parse_channel_page(content: bytes) -> List[RawPost]`, registered under kind `telegram`.

- [ ] **Step 1: Write the failing test `tests/test_telegram_fetcher.py`**

```python
import pytest

from dealalerts.fetchers.telegram import parse_channel_page
from dealalerts.http import FetchError

PAGE = """<html><body>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="sgdeals/101">
  <div class="tgme_widget_message_text js-message_text" dir="auto">PRICE ERROR Dyson V12 S$99<br/>Usual S$999 at Shopee<br/><a href="https://shopee.sg/x">link</a></div>
  <a class="tgme_widget_message_date" href="https://t.me/sgdeals/101"><time datetime="2026-09-21T04:00:00+00:00" class="time">12:00</time></a>
</div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="sgdeals/102">
  <div class="tgme_widget_message_photo"></div>
</div></div>
</body></html>""".encode("utf-8")


def test_messages_become_posts_and_photo_only_messages_are_skipped() -> None:
    posts = parse_channel_page(PAGE)
    assert len(posts) == 1
    post = posts[0]
    assert post.title == "PRICE ERROR Dyson V12 S$99"
    assert post.link == "https://t.me/sgdeals/101"
    assert "Usual S$999 at Shopee" in post.summary
    assert post.posted_at == "2026-09-21T04:00:00+00:00"
    assert post.heat is None


def test_page_without_messages_is_an_error() -> None:
    with pytest.raises(FetchError, match="no messages"):
        parse_channel_page(b"<html><body>Channel not found</body></html>")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_telegram_fetcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_channel_page'`.

- [ ] **Step 3: Write `dealalerts/fetchers/telegram.py`**

```python
"""Read a public Telegram channel through its preview page, https://t.me/s/<channel>."""

import logging
from typing import List

from bs4 import BeautifulSoup

from dealalerts.fetchers import register_fetcher
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source

logger = logging.getLogger(__name__)

_TITLE_LIMIT = 150


def parse_channel_page(content: bytes) -> List[RawPost]:
    """Turn a channel preview page into posts.

    Raises:
        FetchError: when the page holds no messages (wrong name, or a private channel).
    """
    soup = BeautifulSoup(content, "html.parser")
    messages = soup.select("div.tgme_widget_message[data-post]")
    if not messages:
        raise FetchError("no messages on the Telegram preview page")
    posts: List[RawPost] = []
    for message in messages:
        body = message.select_one("div.tgme_widget_message_text")
        if body is None:
            continue
        lines = [line.strip() for line in body.get_text("\n").split("\n") if line.strip()]
        if not lines:
            continue
        stamp = message.select_one("time[datetime]")
        posts.append(
            RawPost(
                title=lines[0][:_TITLE_LIMIT],
                link=f"https://t.me/{message['data-post']}",
                summary=" ".join(lines),
                posted_at=stamp["datetime"] if stamp else None,
                heat=None,
            )
        )
    return posts


@register_fetcher("telegram")
def fetch_channel(source: Source, client: PoliteClient) -> List[RawPost]:
    """Download and parse one channel preview page."""
    return parse_channel_page(client.get(source.url))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_telegram_fetcher.py tests/test_feed.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/fetchers/telegram.py tests/test_telegram_fetcher.py
git commit -m "feat: add Telegram public-channel fetcher"
```

---

### Task 6: State (what has been seen, alerted, and source health)

**Files:**
- Create: `dealalerts/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `Deal`, `Verdict`.
- Produces: class `State` with `State.load(path: Path) -> State`, `save(path: Path) -> None`, `is_seen(deal_id: str) -> bool`, `mark_seen(deal_id: str, now: datetime) -> None`, `is_alerted(deal_id: str) -> bool`, `mark_alerted(deal_id: str, now: datetime) -> None`, `has_succeeded(source_name: str) -> bool`, `add_deal(deal: Deal, verdict: Verdict) -> None`, `pending_alerts() -> List[Dict[str, Any]]`, `deals_for(region: str) -> List[Dict[str, Any]]`, `record_success(source_name: str, now: datetime) -> None`, `record_failure(source_name: str, error: str, now: datetime, note_after: int) -> bool`, `prune(now: datetime, seen_days: int, dashboard_days: int) -> None`, and the attribute `health: Dict[str, Dict[str, Any]]`.
- A stored deal is `dataclasses.asdict(deal)` plus the keys `tier`, `kind` and `reasons` (a list of strings).

- [ ] **Step 1: Write the failing test `tests/test_store.py`**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealalerts.models import Deal, Verdict
from dealalerts.store import State

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def make_deal(deal_id: str, region: str = "sg", first_seen: datetime = NOW) -> Deal:
    return Deal(
        id=deal_id, region=region, source="Test", title=f"Deal {deal_id}",
        link=f"https://x.test/{deal_id}", shop=None, price_now=None, usual_price=None,
        currency=None, discount_pct=None, heat=None, posted_at=None,
        first_seen=first_seen.isoformat(),
    )


GLITCH = Verdict(tier="alert", kind="glitch", reasons=('glitch word "price error"',))
DROP = Verdict(tier="dashboard", kind="discount", reasons=("50% off",))


def test_missing_file_loads_as_empty_state(tmp_path: Path) -> None:
    state = State.load(tmp_path / "nope.json")
    assert state.deals == [] and not state.is_seen("a")


def test_save_and_load_round_trip_keeps_chinese_text(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state.json"
    state = State()
    state.mark_seen("a", NOW)
    state.add_deal(make_deal("a"), GLITCH)
    state.deals[0]["title"] = "標錯價"
    state.save(path)
    assert "標錯價" in path.read_text(encoding="utf-8")
    loaded = State.load(path)
    assert loaded.is_seen("a") and loaded.deals[0]["kind"] == "glitch"


def test_pending_alerts_are_alert_tier_and_not_yet_alerted() -> None:
    state = State()
    state.add_deal(make_deal("a"), GLITCH)
    state.add_deal(make_deal("b"), DROP)
    assert [item["id"] for item in state.pending_alerts()] == ["a"]
    state.mark_alerted("a", NOW)
    assert state.pending_alerts() == []


def test_deals_for_region_puts_glitches_first_then_newest() -> None:
    state = State()
    state.add_deal(make_deal("old-drop", first_seen=NOW - timedelta(hours=2)), DROP)
    state.add_deal(make_deal("new-drop", first_seen=NOW), DROP)
    state.add_deal(make_deal("glitch", first_seen=NOW - timedelta(hours=5)), GLITCH)
    state.add_deal(make_deal("other-region", region="us"), GLITCH)
    assert [item["id"] for item in state.deals_for("sg")] == ["glitch", "new-drop", "old-drop"]


def test_failure_note_fires_once_and_resets_after_success() -> None:
    state = State()
    results = [state.record_failure("S", "HTTP 403", NOW, note_after=3) for _ in range(5)]
    assert results == [False, False, True, False, False]
    assert not state.has_succeeded("S")
    state.record_success("S", NOW)
    assert state.has_succeeded("S") and state.health["S"]["fails"] == 0
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is True


def test_prune_drops_old_deals_and_old_seen_ids() -> None:
    state = State()
    state.mark_seen("ancient", NOW - timedelta(days=31))
    state.mark_seen("recent", NOW - timedelta(days=2))
    state.mark_alerted("ancient", NOW - timedelta(days=31))
    state.add_deal(make_deal("stale", first_seen=NOW - timedelta(days=8)), DROP)
    state.add_deal(make_deal("fresh", first_seen=NOW - timedelta(days=1)), DROP)
    state.prune(NOW, seen_days=30, dashboard_days=7)
    assert not state.is_seen("ancient") and state.is_seen("recent")
    assert not state.is_alerted("ancient")
    assert [item["id"] for item in state.deals] == ["fresh"]
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.store'`.

- [ ] **Step 3: Write `dealalerts/store.py`**

```python
"""What the tool remembers between runs. Saved as data/state.json and committed by the run."""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from dealalerts.models import Deal, Verdict

logger = logging.getLogger(__name__)


class State:
    """Seen ids, alerted ids, deals for the dashboard, and per-source health."""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        data = data or {}
        self.seen: Dict[str, str] = dict(data.get("seen", {}))
        self.alerted: Dict[str, str] = dict(data.get("alerted", {}))
        self.deals: List[Dict[str, Any]] = list(data.get("deals", []))
        self.health: Dict[str, Dict[str, Any]] = dict(data.get("health", {}))

    @classmethod
    def load(cls, path: Path) -> "State":
        """Read the state file. A missing file gives an empty state."""
        path = Path(path)
        if not path.exists():
            return cls()
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def save(self, path: Path) -> None:
        """Write the state file through a temporary file, so a crash cannot leave half a file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen": self.seen, "alerted": self.alerted, "deals": self.deals, "health": self.health,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    def is_seen(self, deal_id: str) -> bool:
        return deal_id in self.seen

    def mark_seen(self, deal_id: str, now: datetime) -> None:
        self.seen[deal_id] = now.isoformat()

    def is_alerted(self, deal_id: str) -> bool:
        return deal_id in self.alerted

    def mark_alerted(self, deal_id: str, now: datetime) -> None:
        self.alerted[deal_id] = now.isoformat()

    def has_succeeded(self, source_name: str) -> bool:
        """True once a source has been read successfully at least once."""
        return bool(self.health.get(source_name, {}).get("last_ok"))

    def add_deal(self, deal: Deal, verdict: Verdict) -> None:
        record = asdict(deal)
        record.update(tier=verdict.tier, kind=verdict.kind, reasons=list(verdict.reasons))
        self.deals.append(record)

    def pending_alerts(self) -> List[Dict[str, Any]]:
        """Alert-tier deals that have not been sent yet, oldest first."""
        waiting = [d for d in self.deals if d["tier"] == "alert" and d["id"] not in self.alerted]
        return sorted(waiting, key=lambda d: d["first_seen"])

    def deals_for(self, region: str) -> List[Dict[str, Any]]:
        """One region's deals: suspected glitches first, then newest first."""
        mine = [d for d in self.deals if d["region"] == region]
        mine.sort(key=lambda d: d["first_seen"], reverse=True)
        mine.sort(key=lambda d: d["kind"] != "glitch")
        return mine

    def _health_entry(self, source_name: str) -> Dict[str, Any]:
        return self.health.setdefault(
            source_name, {"last_ok": None, "fails": 0, "noted": False, "last_error": ""}
        )

    def record_success(self, source_name: str, now: datetime) -> None:
        entry = self._health_entry(source_name)
        entry.update(last_ok=now.isoformat(), fails=0, noted=False, last_error="")

    def record_failure(self, source_name: str, error: str, now: datetime, note_after: int) -> bool:
        """Count a failure. Returns True exactly once, when the owner should be told."""
        entry = self._health_entry(source_name)
        entry["fails"] += 1
        entry["last_error"] = error
        if entry["fails"] >= note_after and not entry["noted"]:
            entry["noted"] = True
            return True
        return False

    def prune(self, now: datetime, seen_days: int, dashboard_days: int) -> None:
        """Forget ids older than seen_days and dashboard deals older than dashboard_days."""
        seen_cutoff = (now - timedelta(days=seen_days)).isoformat()
        deal_cutoff = (now - timedelta(days=dashboard_days)).isoformat()
        self.seen = {key: stamp for key, stamp in self.seen.items() if stamp >= seen_cutoff}
        self.alerted = {key: stamp for key, stamp in self.alerted.items() if stamp >= seen_cutoff}
        self.deals = [deal for deal in self.deals if deal["first_seen"] >= deal_cutoff]
```

Comparing ISO strings works because every time in this project is written by `datetime.isoformat()` on a UTC-aware value, so all stamps share one format and offset.

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/store.py tests/test_store.py
git commit -m "feat: add state store for seen deals, alerts and source health"
```

---

### Task 7: Telegram messages

**Files:**
- Create: `dealalerts/notify.py`
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: `PoliteClient.post_json`, `FetchError`; stored deal dictionaries from `State`.
- Produces: `NotifyError`, `money(currency: Optional[str], amount: float) -> str`, `format_message(deal: Mapping[str, Any], now: datetime) -> str`, `chat_id_for(region: str, env: Mapping[str, str]) -> Optional[str]`, `send_message(client: PoliteClient, token: str, chat_id: str, text: str) -> None`.

- [ ] **Step 1: Write the failing test `tests/test_notify.py`**

```python
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from dealalerts.http import FetchError
from dealalerts.notify import NotifyError, chat_id_for, format_message, money, send_message

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
FULL = {
    "id": "a", "region": "sg", "source": "SingPromos", "title": "Sony WH-1000XM6 & case",
    "link": "https://x.test/1", "shop": "Amazon.sg", "price_now": 89.0, "usual_price": 549.0,
    "currency": "SGD", "discount_pct": 83.8, "heat": None,
    "posted_at": (NOW - timedelta(minutes=6)).isoformat(), "first_seen": NOW.isoformat(),
    "tier": "alert", "kind": "glitch", "reasons": ['glitch word "price error"', "84% off"],
}


class FakeClient:
    def __init__(self, reply: Dict[str, Any] = None, error: Exception = None) -> None:
        self.reply = reply if reply is not None else {"ok": True}
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"url": url, "payload": payload})
        if self.error:
            raise self.error
        return self.reply


def test_money_formats_each_currency() -> None:
    assert money("SGD", 89.0) == "S$89"
    assert money("USD", 199.99) == "US$199.99"
    assert money("JPY", 1073.0) == "¥1,073"
    assert money("EUR", 1070.0) == "€1,070"
    assert money(None, 5.5) == "5.5"


def test_full_message_matches_the_spec_layout() -> None:
    assert format_message(FULL, NOW) == (
        "<b>PRICE ERROR?</b>  Sony WH-1000XM6 &amp; case\n"
        "S$89 (usual S$549)  -84%\n"
        "Shop: Amazon.sg\n"
        'Why: glitch word "price error"; 84% off\n'
        "Source: SingPromos, posted 6 min ago\n"
        "https://x.test/1"
    )


def test_missing_fields_drop_their_lines_and_headline_follows_kind() -> None:
    bare = dict(FULL, shop=None, price_now=None, usual_price=None, discount_pct=None,
                kind="heat", reasons=["75 votes in 30 min"],
                posted_at=(NOW - timedelta(hours=3)).isoformat())
    assert format_message(bare, NOW) == (
        "<b>HEATING UP</b>  Sony WH-1000XM6 &amp; case\n"
        "Why: 75 votes in 30 min\n"
        "Source: SingPromos, posted 3 h ago\n"
        "https://x.test/1"
    )
    assert format_message(dict(FULL, kind="discount"), NOW).startswith("<b>BIG DROP</b>")


def test_test_chat_overrides_every_region() -> None:
    env = {"TG_CHAT_SG": "-1001", "TG_CHAT_US": "-1002"}
    assert chat_id_for("sg", env) == "-1001"
    assert chat_id_for("hk", env) is None
    assert chat_id_for("sg", dict(env, TG_CHAT_TEST="-1009")) == "-1009"
    assert chat_id_for("hk", dict(env, TG_CHAT_TEST="-1009")) == "-1009"


def test_send_posts_html_to_the_chat() -> None:
    client = FakeClient()
    send_message(client, "TOKEN", "-1001", "hello")
    call = client.calls[0]
    assert call["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert call["payload"] == {"chat_id": "-1001", "text": "hello", "parse_mode": "HTML"}


def test_refusal_and_network_failure_raise_without_the_key() -> None:
    with pytest.raises(NotifyError, match="chat not found"):
        send_message(FakeClient(reply={"ok": False, "description": "chat not found"}),
                     "TOKEN", "-1", "x")
    with pytest.raises(NotifyError) as caught:
        send_message(FakeClient(error=FetchError("api.telegram.org: HTTP 400")), "TOKEN", "-1", "x")
    assert "TOKEN" not in str(caught.value)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.notify'`.

- [ ] **Step 3: Write `dealalerts/notify.py`**

```python
"""Build and send Telegram messages. The bot key never reaches a log or an error message."""

import html
import logging
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from dealalerts.http import FetchError, PoliteClient

logger = logging.getLogger(__name__)

_SYMBOLS: Dict[str, str] = {
    "SGD": "S$", "HKD": "HK$", "USD": "US$", "GBP": "£", "EUR": "€", "JPY": "¥",
}
_HEADLINES: Dict[str, str] = {
    "glitch": "PRICE ERROR?", "discount": "BIG DROP", "heat": "HEATING UP",
}


class NotifyError(Exception):
    """A Telegram message could not be delivered."""


def money(currency: Optional[str], amount: float) -> str:
    """Format an amount with its currency symbol, without pointless decimals."""
    number = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"{_SYMBOLS.get(currency or '', '')}{number}"


def _age(deal: Mapping[str, Any], now: datetime) -> str:
    posted = datetime.fromisoformat(deal.get("posted_at") or deal["first_seen"])
    minutes = max(0, round((now - posted).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 1440:
        return f"{round(minutes / 60)} h ago"
    return f"{round(minutes / 1440)} d ago"


def _price_line(deal: Mapping[str, Any]) -> Optional[str]:
    parts: List[str] = []
    if deal.get("price_now") is not None:
        price = money(deal.get("currency"), deal["price_now"])
        if deal.get("usual_price") is not None:
            price += f" (usual {money(deal.get('currency'), deal['usual_price'])})"
        parts.append(price)
    if deal.get("discount_pct") is not None:
        parts.append(f"-{deal['discount_pct']:.0f}%")
    return "  ".join(parts) if parts else None


def format_message(deal: Mapping[str, Any], now: datetime) -> str:
    """Build the alert text in Telegram's HTML style. Lines with no data are left out."""
    escape = lambda text: html.escape(str(text), quote=False)  # noqa: E731
    lines = [f"<b>{_HEADLINES.get(deal.get('kind', ''), 'DEAL')}</b>  {escape(deal['title'])}"]
    price_line = _price_line(deal)
    if price_line:
        lines.append(escape(price_line))
    if deal.get("shop"):
        lines.append(f"Shop: {escape(deal['shop'])}")
    if deal.get("reasons"):
        lines.append(f"Why: {escape('; '.join(deal['reasons']))}")
    lines.append(f"Source: {escape(deal['source'])}, posted {_age(deal, now)}")
    lines.append(escape(deal["link"]))
    return "\n".join(lines)


def chat_id_for(region: str, env: Mapping[str, str]) -> Optional[str]:
    """Chat id for a region. TG_CHAT_TEST, when set, takes every region's messages."""
    return env.get("TG_CHAT_TEST") or env.get(f"TG_CHAT_{region.upper()}") or None


def send_message(client: PoliteClient, token: str, chat_id: str, text: str) -> None:
    """Send one message.

    Raises:
        NotifyError: if Telegram cannot be reached or refuses the message.
    """
    try:
        reply = client.post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
    except FetchError as error:
        raise NotifyError(str(error)) from None
    if not reply.get("ok"):
        raise NotifyError(f"Telegram refused: {reply.get('description', 'no reason given')}")
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_notify.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/notify.py tests/test_notify.py
git commit -m "feat: format and send Telegram alerts"
```

---

### Task 8: Dashboard page

**Files:**
- Create: `dealalerts/site.py`
- Test: `tests/test_site.py`

**Interfaces:**
- Consumes: `State.deals_for`, `State.health`, `Config.sources`, `REGIONS`, `REGION_LABELS`, `notify.money`.
- Produces: `build_site(state: State, config: Config, now: datetime, out_dir: Path) -> Path` (returns the path of `index.html`).

The page works without JavaScript (all five regions show, one under another). JavaScript only adds the tab switching, remembers the tab, and turns times into "6 min ago". Every piece of feed text is escaped, and only `http` and `https` links are rendered, because feed content is untrusted.

- [ ] **Step 1: Write the failing test `tests/test_site.py`**

```python
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dealalerts.config import Config, Settings
from dealalerts.models import Deal, Source, Verdict
from dealalerts.site import build_site
from dealalerts.store import State

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
CONFIG = Config(
    settings=Settings(),
    sources=(
        Source(name="Good Feed", region="sg", kind="feed", url="https://a.test/feed"),
        Source(name="Dead Feed", region="sg", kind="feed", url="https://b.test/feed"),
    ),
    glitch_words=(), sale_words=(), expired_words=(),
)


def make_deal(deal_id: str, title: str, link: str, minutes_old: int) -> Deal:
    return Deal(
        id=deal_id, region="sg", source="Good Feed", title=title, link=link, shop="Shopee",
        price_now=89.0, usual_price=549.0, currency="SGD", discount_pct=83.8, heat=None,
        posted_at=None, first_seen=(NOW - timedelta(minutes=minutes_old)).isoformat(),
    )


def build(tmp_path: Path) -> str:
    state = State()
    state.add_deal(make_deal("drop", "Ordinary big drop", "https://x.test/drop", 5),
                   Verdict("dashboard", "discount", ("50% off",)))
    state.add_deal(make_deal("glitch", "Glitch <script>alert(1)</script>", "https://x.test/g", 90),
                   Verdict("alert", "glitch", ('glitch word "glitch"',)))
    state.add_deal(make_deal("evil", "Evil link", "javascript:alert(1)", 1),
                   Verdict("dashboard", "discount", ("45% off",)))
    state.record_success("Good Feed", NOW)
    state.record_failure("Dead Feed", "b.test: HTTP 403", NOW, note_after=5)
    return build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")


def test_page_has_five_tabs_and_the_last_check_time(tmp_path: Path) -> None:
    page = build(tmp_path)
    for label in ("Singapore", "Hong Kong", "Japan", "US", "Europe"):
        assert f">{label}</button>" in page
    assert 'datetime="2026-09-21T12:00:00+00:00"' in page


def test_glitch_is_listed_before_a_newer_ordinary_deal(tmp_path: Path) -> None:
    page = build(tmp_path)
    assert page.index("Glitch &lt;script&gt;") < page.index("Ordinary big drop")


def test_feed_text_is_escaped_and_unsafe_links_are_dropped(tmp_path: Path) -> None:
    page = build(tmp_path)
    assert "<script>alert(1)</script>" not in page
    assert "javascript:alert" not in page
    assert "Evil link" in page


def test_source_health_shows_the_failure(tmp_path: Path) -> None:
    page = build(tmp_path)
    assert "Dead Feed" in page and "b.test: HTTP 403" in page
    assert 'class="source bad"' in page and 'class="source ok"' in page


def test_empty_region_says_so(tmp_path: Path) -> None:
    assert "Nothing in the last 7 days." in build(tmp_path)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_site.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.site'`.

- [ ] **Step 3: Write `dealalerts/site.py`**

```python
"""Build the static dashboard page."""

import html
import logging
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any, Dict, List, Mapping
from urllib.parse import urlsplit

from dealalerts.config import Config
from dealalerts.models import REGION_LABELS, REGIONS
from dealalerts.notify import money
from dealalerts.store import State

logger = logging.getLogger(__name__)

_PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deal Alerts</title>
<style>
:root { --bg:#f7f6f3; --card:#ffffff; --ink:#1d1c1a; --soft:#6b6862; --line:#e2dfd8;
        --hot:#b3261e; --hot-bg:#fdecea; --ok:#1e7d46; --accent:#1f4fd8; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151412; --card:#1f1e1b; --ink:#f1efe9; --soft:#a29e95; --line:#34322d;
          --hot:#ff8a80; --hot-bg:#3a1d1a; --ok:#6fd39a; --accent:#8fb0ff; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font:16px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:860px; margin:0 auto; padding:20px 16px 48px; }
h1 { font-size:1.4rem; margin:0 0 4px; }
.checked { color:var(--soft); font-size:.9rem; margin:0 0 16px; }
nav { display:flex; gap:6px; overflow-x:auto; padding-bottom:4px; margin-bottom:16px; }
nav button { flex:none; padding:8px 14px; border:1px solid var(--line); border-radius:999px;
             background:var(--card); color:var(--ink); font:inherit; cursor:pointer; }
nav button[aria-selected="true"] { background:var(--ink); color:var(--bg); border-color:var(--ink); }
section h2 { font-size:1.1rem; margin:24px 0 8px; }
.deal { background:var(--card); border:1px solid var(--line); border-radius:10px;
        padding:12px 14px; margin-bottom:8px; }
.deal.glitch { border-color:var(--hot); background:var(--hot-bg); }
.deal a { color:var(--accent); text-decoration:none; font-weight:600; overflow-wrap:anywhere; }
.badge { color:var(--hot); font-size:.75rem; font-weight:700; letter-spacing:.04em; }
.price { font-variant-numeric:tabular-nums; margin-top:2px; }
.meta { color:var(--soft); font-size:.85rem; margin-top:2px; }
.empty { color:var(--soft); }
.health { margin-top:12px; font-size:.85rem; color:var(--soft); }
.source { display:block; }
.source.ok::before { content:"● "; color:var(--ok); }
.source.bad::before { content:"● "; color:var(--hot); }
</style>
</head>
<body>
<main>
<h1>Deal Alerts</h1>
<p class="checked">Last check: <time datetime="$checked">$checked_text</time></p>
<nav role="tablist">$tabs</nav>
$panes
</main>
<script>
(function () {
  var tabs = document.querySelectorAll('[data-tab]');
  var panes = document.querySelectorAll('[data-region]');
  function show(region) {
    tabs.forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.tab === region)); });
    panes.forEach(function (p) { p.hidden = p.dataset.region !== region; });
    try { localStorage.setItem('region', region); } catch (e) {}
  }
  tabs.forEach(function (t) { t.addEventListener('click', function () { show(t.dataset.tab); }); });
  var saved = null;
  try { saved = localStorage.getItem('region'); } catch (e) {}
  show(saved && document.querySelector('[data-region="' + saved + '"]') ? saved : 'sg');
  document.querySelectorAll('time[datetime]').forEach(function (el) {
    var mins = Math.round((Date.now() - new Date(el.getAttribute('datetime'))) / 60000);
    if (isNaN(mins) || mins < 0) { return; }
    el.textContent = mins < 60 ? mins + ' min ago'
      : mins < 1440 ? Math.round(mins / 60) + ' h ago' : Math.round(mins / 1440) + ' d ago';
  });
})();
</script>
</body>
</html>
""")


def _safe_link(url: str) -> str:
    return url if urlsplit(url).scheme in ("http", "https") else ""


def _stamp(iso: str) -> str:
    return f'<time datetime="{html.escape(iso)}">{html.escape(iso[:16].replace("T", " "))} UTC</time>'


def _deal_html(deal: Mapping[str, Any]) -> str:
    title = html.escape(deal["title"])
    link = _safe_link(deal["link"])
    heading = f'<a href="{html.escape(link)}" rel="noopener noreferrer">{title}</a>' if link else title
    glitch = deal.get("kind") == "glitch"
    badge = '<div class="badge">SUSPECTED PRICE ERROR</div>' if glitch else ""
    price_bits: List[str] = []
    if deal.get("price_now") is not None:
        price_bits.append(money(deal.get("currency"), deal["price_now"]))
    if deal.get("usual_price") is not None:
        price_bits.append(f"usual {money(deal.get('currency'), deal['usual_price'])}")
    if deal.get("discount_pct") is not None:
        price_bits.append(f"-{deal['discount_pct']:.0f}%")
    price = f'<div class="price">{html.escape(" · ".join(price_bits))}</div>' if price_bits else ""
    meta_bits = [deal["shop"]] if deal.get("shop") else []
    meta_bits.append(deal["source"])
    meta = html.escape(" · ".join(meta_bits))
    return (
        f'<div class="deal{" glitch" if glitch else ""}">{badge}{heading}{price}'
        f'<div class="meta">{meta} · {_stamp(deal["first_seen"])}</div></div>'
    )


def _health_html(region: str, config: Config, health: Dict[str, Dict[str, Any]]) -> str:
    lines: List[str] = []
    for source in config.sources:
        if source.region != region:
            continue
        entry = health.get(source.name, {})
        name = html.escape(source.name)
        if entry.get("fails"):
            error = html.escape(entry.get("last_error", ""))
            lines.append(f'<span class="source bad">{name}: failing ({error})</span>')
        elif entry.get("last_ok"):
            lines.append(f'<span class="source ok">{name}: read {_stamp(entry["last_ok"])}</span>')
        else:
            lines.append(f'<span class="source bad">{name}: not read yet</span>')
    return f'<div class="health">{"".join(lines)}</div>'


def build_site(state: State, config: Config, now: datetime, out_dir: Path) -> Path:
    """Write index.html into out_dir and return its path."""
    days = config.settings.dashboard_days
    tabs = "".join(
        f'<button role="tab" data-tab="{region}">{REGION_LABELS[region]}</button>'
        for region in REGIONS
    )
    panes: List[str] = []
    for region in REGIONS:
        deals = state.deals_for(region)
        body = "".join(_deal_html(deal) for deal in deals) or (
            f'<p class="empty">Nothing in the last {days} days.</p>'
        )
        panes.append(
            f'<section data-region="{region}"><h2>{REGION_LABELS[region]}</h2>'
            f"{body}{_health_html(region, config, state.health)}</section>"
        )
    page = _PAGE.substitute(
        checked=now.isoformat(),
        checked_text=now.strftime("%Y-%m-%d %H:%M UTC"),
        tabs=tabs,
        panes="\n".join(panes),
    )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "index.html"
    target.write_text(page, encoding="utf-8")
    return target
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_site.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add dealalerts/site.py tests/test_site.py
git commit -m "feat: build the five-region dashboard page"
```

---

### Task 9: One full run, the command line, and the README

**Files:**
- Create: `dealalerts/pipeline.py`, `run.py`, `README.md`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 1 to 8.
- Produces: `pipeline.to_deal(post: RawPost, source: Source, now: datetime) -> Deal`, `pipeline.RunReport(fetched: int, new: int, alerts_sent: int, failures: Tuple[str, ...])`, `pipeline.run_once(config, state, client, env, now, dry_run, fetch_fn=fetch, sleep=time.sleep) -> RunReport`; command line `run.py [--dry-run] [--test-message] [--config PATH] [--state PATH] [--out DIR]`.

Two behaviours that are easy to miss:
- **No flood on first contact.** The first time a source is read successfully, everything already in its feed is backlog. Those posts are stored and shown, but marked as already alerted, so the phone does not buzz 30 times on day one or whenever a source is added.
- **Retry, then give up.** An alert that failed to send stays pending and is retried on later runs. After `stale_alert_hours` it is marked done without sending, because a six-hour-old glitch is gone.

- [ ] **Step 1: Write the failing test `tests/test_pipeline.py`**

```python
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from dealalerts.config import Config, Settings
from dealalerts.http import FetchError
from dealalerts.models import RawPost, Source
from dealalerts.pipeline import run_once
from dealalerts.store import State

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SG = Source(name="SG Feed", region="sg", kind="feed", url="https://sg.test/feed")
US = Source(name="US Feed", region="us", kind="feed", url="https://us.test/feed")
CONFIG = Config(
    settings=Settings(failure_note_after=2), sources=(SG, US),
    glitch_words=("price error",), sale_words=(), expired_words=("expired",),
)
ENV = {"TELEGRAM_BOT_TOKEN": "TOKEN", "TG_CHAT_SG": "-1001", "TG_CHAT_US": "-1002"}


def post(number: int, title: str) -> RawPost:
    return RawPost(title=title, link=f"https://shop.test/{number}", summary="",
                   posted_at=NOW.isoformat(), heat=None)


class FakeClient:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: List[Dict[str, Any]] = []

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.ok:
            raise FetchError("api.telegram.org: HTTP 500")
        self.sent.append(payload)
        return {"ok": True}


def make_fetch(feeds: Dict[str, Any]):
    def fake_fetch(source: Source, client: Any) -> List[RawPost]:
        result = feeds[source.name]
        if isinstance(result, Exception):
            raise result
        return result
    return fake_fetch


def run(state: State, client: FakeClient, feeds: Dict[str, Any], now: datetime = NOW,
        dry_run: bool = False):
    return run_once(CONFIG, state, client, ENV, now, dry_run,
                    fetch_fn=make_fetch(feeds), sleep=lambda _: None)


def test_first_contact_stores_backlog_without_buzzing() -> None:
    state, client = State(), FakeClient()
    report = run(state, client, {"SG Feed": [post(1, "Price error on TV S$9")], "US Feed": []})
    assert client.sent == [] and report.alerts_sent == 0
    assert [d["id"] for d in state.deals_for("sg")] and state.pending_alerts() == []


def test_new_glitch_goes_once_to_its_own_region() -> None:
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    feeds = {"SG Feed": [post(2, "PRICE ERROR Dyson S$99"), post(3, "Boring kettle S$30")],
             "US Feed": []}
    report = run(state, client, feeds)
    assert report.alerts_sent == 1 and report.new == 2
    assert client.sent[0]["chat_id"] == "-1001" and "Dyson" in client.sent[0]["text"]
    run(state, client, feeds)
    assert len(client.sent) == 1


def test_one_dead_source_does_not_stop_the_other_and_is_noted_once() -> None:
    state, client = State(), FakeClient()
    feeds = {"SG Feed": FetchError("sg.test: HTTP 403"), "US Feed": []}
    first = run(state, client, feeds)
    assert first.failures == ("SG Feed",) and state.has_succeeded("US Feed")
    assert client.sent == []
    run(state, client, feeds)
    assert len(client.sent) == 1
    assert client.sent[0]["chat_id"] == "-1001" and "SG Feed" in client.sent[0]["text"]
    run(state, client, feeds)
    assert len(client.sent) == 1


def test_failed_send_is_retried_then_dropped_when_stale() -> None:
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    feeds = {"SG Feed": [post(4, "price error laptop S$50")], "US Feed": []}
    run(state, FakeClient(ok=False), feeds)
    assert len(state.pending_alerts()) == 1
    healthy = FakeClient()
    run(state, healthy, feeds, now=NOW + timedelta(minutes=15))
    assert len(healthy.sent) == 1 and state.pending_alerts() == []

    state2 = State()
    run(state2, FakeClient(), {"SG Feed": [], "US Feed": []})
    run(state2, FakeClient(ok=False), feeds)
    late = FakeClient()
    run(state2, late, feeds, now=NOW + timedelta(hours=7))
    assert late.sent == [] and state2.pending_alerts() == []


def test_dry_run_sends_nothing_and_marks_nothing() -> None:
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    run(state, client, {"SG Feed": [post(5, "price error phone S$10")], "US Feed": []}, dry_run=True)
    assert client.sent == [] and len(state.pending_alerts()) == 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dealalerts.pipeline'`.

- [ ] **Step 3: Write `dealalerts/pipeline.py`**

```python
"""One full run: read every source, score new posts, send alerts."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Mapping, Tuple

from dealalerts.config import Config
from dealalerts.fetchers import Fetcher, fetch
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import Deal, RawPost, Source
from dealalerts.notify import NotifyError, chat_id_for, format_message, send_message
from dealalerts.parse import deal_id, parse_fields
from dealalerts.score import score
from dealalerts.store import State

logger = logging.getLogger(__name__)

_TITLE_LIMIT = 200
_SEND_GAP_SECONDS = 1.1


@dataclass(frozen=True)
class RunReport:
    """Counts from one run."""

    fetched: int
    new: int
    alerts_sent: int
    failures: Tuple[str, ...]


def to_deal(post: RawPost, source: Source, now: datetime) -> Deal:
    """Parse a raw post into a Deal. Region always comes from the source."""
    fields = parse_fields(post.title, post.summary, source.region)
    return Deal(
        id=deal_id(post.link), region=source.region, source=source.name,
        title=post.title[:_TITLE_LIMIT], link=post.link, shop=fields.shop,
        price_now=fields.price_now, usual_price=fields.usual_price, currency=fields.currency,
        discount_pct=fields.discount_pct, heat=post.heat, posted_at=post.posted_at,
        first_seen=now.isoformat(),
    )


def _deliver(client: PoliteClient, env: Mapping[str, str], region: str, text: str,
             dry_run: bool) -> bool:
    """Send one message. Returns True if it was delivered."""
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id_for(region, env)
    if dry_run or not token or not chat:
        logger.info("Not sending (dry run or Telegram not set up) to %s:\n%s", region, text)
        return False
    try:
        send_message(client, token, chat, text)
    except NotifyError as error:
        logger.warning("Telegram send to %s failed: %s", region, error)
        return False
    return True


def run_once(
    config: Config,
    state: State,
    client: PoliteClient,
    env: Mapping[str, str],
    now: datetime,
    dry_run: bool,
    fetch_fn: Fetcher = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> RunReport:
    """Read all sources, record new deals, send pending alerts and source-down notes."""
    settings = config.settings
    fetched = new = 0
    failures: List[str] = []
    notes: List[Tuple[str, str]] = []

    for source in config.sources:
        try:
            posts = fetch_fn(source, client)
        except FetchError as error:
            failures.append(source.name)
            logger.warning("Source %s failed: %s", source.name, error)
            if state.record_failure(source.name, str(error), now, settings.failure_note_after):
                notes.append((source.region, f"Source down: {source.name} has failed "
                                             f"{settings.failure_note_after} checks in a row ({error})."))
            continue
        backlog = not state.has_succeeded(source.name)
        state.record_success(source.name, now)
        fetched += len(posts)
        for post in posts:
            identifier = deal_id(post.link)
            if state.is_seen(identifier):
                continue
            state.mark_seen(identifier, now)
            new += 1
            deal = to_deal(post, source, now)
            verdict = score(deal, f"{post.title} {post.summary}", source, config, now)
            if verdict.tier == "ignore":
                continue
            state.add_deal(deal, verdict)
            if backlog and verdict.tier == "alert":
                state.mark_alerted(deal.id, now)

    sent = 0
    for item in state.pending_alerts():
        if sent >= settings.max_alerts_per_run:
            break
        age_hours = (now - datetime.fromisoformat(item["first_seen"])).total_seconds() / 3600
        if age_hours > settings.stale_alert_hours:
            state.mark_alerted(item["id"], now)
            continue
        if _deliver(client, env, item["region"], format_message(item, now), dry_run):
            state.mark_alerted(item["id"], now)
            sent += 1
            sleep(_SEND_GAP_SECONDS)

    for region, text in notes:
        _deliver(client, env, region, text, dry_run)

    return RunReport(fetched=fetched, new=new, alerts_sent=sent, failures=tuple(failures))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 5 passed.

- [ ] **Step 5: Write `run.py`**

```python
"""Command-line entry point. Run with: uv run python run.py --dry-run"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from dealalerts.config import load_config
from dealalerts.http import PoliteClient
from dealalerts.models import REGION_LABELS, REGIONS
from dealalerts.notify import NotifyError, chat_id_for, send_message
from dealalerts.pipeline import run_once
from dealalerts.site import build_site
from dealalerts.store import State

logger = logging.getLogger("dealalerts.run")


def _send_test_messages(client: PoliteClient) -> int:
    """Send one line to every region's chat, to prove Telegram delivery. Returns failures."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return len(REGIONS)
    failed = 0
    for region in REGIONS:
        chat = chat_id_for(region, os.environ)
        if not chat:
            logger.error("No chat id for %s.", REGION_LABELS[region])
            failed += 1
            continue
        try:
            send_message(client, token, chat, f"Deal Alerts test for {REGION_LABELS[region]}. It works.")
            logger.info("Test message sent for %s.", REGION_LABELS[region])
        except NotifyError as error:
            logger.error("Test message for %s failed: %s", REGION_LABELS[region], error)
            failed += 1
    return failed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scan deal sources and send alerts.")
    parser.add_argument("--dry-run", action="store_true", help="send nothing, save nothing")
    parser.add_argument("--test-message", action="store_true", help="send a test line to every chat")
    parser.add_argument("--config", default="sources.yaml")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--out", default="public")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(Path(args.config))
    client = PoliteClient(config.settings.same_host_gap_seconds)
    if args.test_message:
        return 1 if _send_test_messages(client) else 0

    now = datetime.now(timezone.utc)
    state = State.load(Path(args.state))
    report = run_once(config, state, client, os.environ, now, args.dry_run)
    state.prune(now, config.settings.seen_days, config.settings.dashboard_days)
    if not args.dry_run:
        state.save(Path(args.state))
    build_site(state, config, now, Path(args.out))
    logger.info(
        "Read %d posts, %d new, %d alerts sent, failing sources: %s",
        report.fetched, report.new, report.alerts_sent, ", ".join(report.failures) or "none",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the whole suite, then a real dry run against the live feeds**

Run: `uv run pytest -v`
Expected: every test passes (about 50).

Run: `uv run python run.py --dry-run`
Expected: a log line per failing source if any (Reddit may answer 429 from this PC), a final line like `Read 300 posts, 300 new, 0 alerts sent, failing sources: ...`, no file at `data/state.json`, and a page at `public/index.html`. Open `public/index.html` in a browser and check: five tabs, deals under the right regions, source health lines at the bottom of each tab. Zero alerts is correct here, because every source is on first contact.

- [ ] **Step 7: Write `README.md`**

````markdown
# Deal Alerts

Watches public deal feeds for Singapore, Hong Kong, Japan, the US and Europe every
15 minutes. Sends a Telegram message when it sees a suspected price error or a
discount of 70% or more. Shows everything from 40% off upward on a web page:
https://eloriahu.github.io/deal-alerts/

It costs nothing to run. GitHub runs it, so no computer needs to stay on.

## What buzzes the phone

- A post that uses a price-error phrase ("price error", "glitch", 錯價, 価格ミス, and so on).
- A stated or computed discount of 70% or more. "Up to 70% off" does not count.
- A Slickdeals post collecting votes unusually fast.

Each deal buzzes once. Each region has its own Telegram channel, so any region
can be muted in Telegram without touching this project.

## Changing things

Everything is in `sources.yaml`. Edit it on GitHub in the browser and save.

- **Add a source:** copy a line under `sources:`, change the name, region and address.
  The first read of a new source never buzzes the phone.
- **Add a price-error phrase:** add it under `glitch_words:` in its language.
- **Fewer or more alerts:** change `alert_discount` (70) or a source's `heat_threshold` (60).

## Pausing and stopping

GitHub, this repository, Actions tab, "scan", the "..." button, "Disable workflow".
Enable it again the same way.

## What it cannot do

It sees a price error only after someone posts it publicly. Shops often cancel
price-error orders. It never buys anything and never logs in to any shop.

## Running it on a PC

```
uv sync
uv run pytest
uv run python run.py --dry-run
```
````

- [ ] **Step 8: Commit**

```bash
git add dealalerts/pipeline.py run.py README.md tests/test_pipeline.py
git commit -m "feat: wire the full run, add command line and README"
```

- [ ] **Step 9: Review**

Dispatch the `code-reviewer` agent on the whole `dealalerts/` package, `run.py` and `tests/`. Fix anything it rates as a correctness or security problem, re-run `uv run pytest`, and commit the fixes as `fix: address code review findings`.

---

### Task 10: Put it on GitHub and test the sources from there

This task needs the owner. The `gh` tool is not installed and the GitHub connector is failing, so repository creation and button clicks happen in the browser.

**Files:**
- Create: `scripts/probe_sources.py`, `scripts/candidates.yaml`, `.github/workflows/probe.yml`
- Modify: `sources.yaml` (after the results are in)

- [ ] **Step 1: Write `scripts/candidates.yaml`** (sources that could not be judged from the owner's PC)

```yaml
sources:
  - {name: FlyAgain, region: hk, kind: feed, language: zh, url: "https://flyagain.la/feed/"}
  - {name: r/HongKong deals, region: hk, kind: feed, language: en,
     url: "https://www.reddit.com/r/HongKong/search.rss?q=deal&restrict_sr=1&sort=new"}
  - {name: Kaimono Yosan, region: jp, kind: feed, language: ja, url: "https://kaimono-yosan.com/feed/"}
  - {name: r/japandeals, region: jp, kind: feed, language: en,
     url: "https://www.reddit.com/r/japandeals/new/.rss"}
  - {name: HotUKDeals, region: eu, kind: feed, language: en, url: "https://www.hotukdeals.com/rss/hot"}
  - {name: mydealz, region: eu, kind: feed, language: de, url: "https://www.mydealz.de/rss/hot"}
  - {name: Dealabs, region: eu, kind: feed, language: fr, url: "https://www.dealabs.com/rss/hot"}
```

- [ ] **Step 2: Write `scripts/probe_sources.py`**

```python
"""Read every source once and write a results table. Run on GitHub to see what GitHub can reach."""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dealalerts.config import load_config  # noqa: E402
from dealalerts.fetchers import fetch  # noqa: E402
from dealalerts.http import FetchError, PoliteClient  # noqa: E402

logger = logging.getLogger("dealalerts.probe")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    client = PoliteClient(gap_seconds=5)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [f"# Source probe from GitHub, {stamp}", "",
                        "| List | Source | Region | Result |", "|---|---|---|---|"]
    for label, path in (("live", ROOT / "sources.yaml"), ("candidate", ROOT / "scripts" / "candidates.yaml")):
        for source in load_config(path).sources:
            try:
                result = f"works, {len(fetch(source, client))} posts"
            except FetchError as error:
                result = f"FAILED: {error}"
            lines.append(f"| {label} | {source.name} | {source.region} | {result} |")
            logger.info("%s %s: %s", label, source.name, result)
    (ROOT / "docs" / "source-probe-github.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Write `.github/workflows/probe.yml`**

```yaml
name: probe
on:
  workflow_dispatch:
permissions:
  contents: write
jobs:
  probe:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --no-dev
      - run: uv run --no-dev python scripts/probe_sources.py
      - name: Save the results
        run: |
          git config user.name "deal-alerts-bot"
          git config user.email "deal-alerts-bot@users.noreply.github.com"
          git add docs/source-probe-github.md
          git commit -m "docs: record source probe from GitHub"
          git push
```

- [ ] **Step 4: Commit**

```bash
git add scripts .github/workflows/probe.yml
git commit -m "chore: add source probe script and workflow"
```

- [ ] **Step 5 (owner): Create the empty project on GitHub**

Ask the owner to do this in a browser, signed in to GitHub:
1. Open https://github.com/new
2. Repository name: `deal-alerts`. Choose **Public**. Leave "Add a README", ".gitignore" and "license" all unticked.
3. Press "Create repository".

Public is required: GitHub's free web page hosting and unlimited free run time both need a public project.

- [ ] **Step 6: Push** (confirm with the owner first; this makes the code public)

```bash
cd ~/deal-alerts
git remote add origin https://github.com/eloriahu/deal-alerts.git
git push -u origin main
```

A browser sign-in window opens the first time. Expected: `branch 'main' set up to track 'origin/main'`.

- [ ] **Step 7 (owner): Run the probe**

On https://github.com/eloriahu/deal-alerts open the **Actions** tab, press "I understand my workflows, go ahead and enable them" if shown, choose **probe** on the left, press **Run workflow**, then the green **Run workflow** button. It takes about 3 minutes and ends with a green tick.

- [ ] **Step 8: Apply the results**

```bash
git pull
```

Read `docs/source-probe-github.md` and edit `sources.yaml` by these rules:
- A live source that FAILED is deleted from `sources.yaml`.
- A candidate that works is moved into `sources.yaml` under its region.
- Every region must keep at least one working source. If a region would be left with none, stop and tell the owner which region and why, before going further.
- Reddit sources that fail with HTTP 429 or 403 are deleted; do not add credentials or workarounds.

Then run `uv run pytest tests/test_config.py -v` (expected: 5 passed) and:

```bash
git add sources.yaml
git commit -m "chore: keep only sources reachable from GitHub"
git push
```

- [ ] **Step 9 (owner, optional): Singapore Telegram channels**

Ask the owner which public Singapore deal Telegram channels they follow. For each channel name given, add an entry such as
`- {name: "Telegram <channel>", region: sg, kind: telegram, language: en, url: "https://t.me/s/<channel>"}`
to `scripts/candidates.yaml`, push, run the probe again, and keep the ones that work. If the owner names none, skip this step; nothing else depends on it.

---

### Task 11: Telegram, secrets, the timer, and going live

**Files:**
- Create: `.github/workflows/scan.yml`

- [ ] **Step 1: Write `.github/workflows/scan.yml`**

```yaml
name: scan
on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:
    inputs:
      test_message:
        description: "Only send a test message to every Telegram chat"
        type: boolean
        default: false
permissions:
  contents: write
  pages: write
  id-token: write
concurrency:
  group: scan
  cancel-in-progress: false
jobs:
  scan:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    environment:
      name: github-pages
      url: ${{ steps.deploy.outputs.page_url }}
    env:
      TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      TG_CHAT_SG: ${{ secrets.TG_CHAT_SG }}
      TG_CHAT_HK: ${{ secrets.TG_CHAT_HK }}
      TG_CHAT_JP: ${{ secrets.TG_CHAT_JP }}
      TG_CHAT_US: ${{ secrets.TG_CHAT_US }}
      TG_CHAT_EU: ${{ secrets.TG_CHAT_EU }}
      TG_CHAT_TEST: ${{ secrets.TG_CHAT_TEST }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --no-dev
      - name: Send test messages only
        if: ${{ inputs.test_message }}
        run: uv run --no-dev python run.py --test-message
      - name: Scan
        if: ${{ !inputs.test_message }}
        run: uv run --no-dev python run.py
      - name: Save state
        if: ${{ !inputs.test_message }}
        run: |
          git config user.name "deal-alerts-bot"
          git config user.email "deal-alerts-bot@users.noreply.github.com"
          git add data/state.json
          git diff --cached --quiet || git commit -m "chore: update state"
          git push || (git pull --rebase && git push)
      - name: Package the page
        if: ${{ !inputs.test_message }}
        uses: actions/upload-pages-artifact@v3
        with:
          path: public
      - name: Publish the page
        if: ${{ !inputs.test_message }}
        id: deploy
        uses: actions/deploy-pages@v4
```

- [ ] **Step 2: Commit and push**

```bash
git add .github/workflows/scan.yml
git commit -m "ci: run the scan every 15 minutes and publish the page"
git push
```

The timer starts running at once. Until the secrets exist it sends nothing (the code logs "Telegram not set up" and carries on), and the page step fails until Step 5 is done. Both are expected.

- [ ] **Step 3 (owner): Create the Telegram bot**

In Telegram on the phone:
1. Search for **BotFather** (blue tick) and open it. Send `/newbot`.
2. Name: `Deal Alerts`. Username: anything free that ends in `bot`, for example `my_deal_alerts_bot`.
3. BotFather replies with a long key that looks like `1234567890:AAH...`. This key is a password. Do not paste it into this chat or anywhere except the GitHub secrets page in Step 6.

- [ ] **Step 4 (owner): Create six channels and add the bot**

For each of these names, in Telegram: New Channel, type the name, choose **Private**, skip adding people. Names: `SG Deals`, `HK Deals`, `Japan Deals`, `US Deals`, `Europe Deals`, `Deal Alerts Test`.

Then in each channel: tap the channel name, **Administrators**, **Add Admin**, search for the bot's username, and leave "Post Messages" switched on.

Then find each channel's id: open https://web.telegram.org/a/ in a browser, click the channel, and look at the end of the address. It ends with `#-100` followed by digits, for example `#-1002345678901`. The id is everything after the `#`, **including the minus sign**: `-1002345678901`. Note the six ids. If the local network blocks web.telegram.org, do this step on a home or mobile connection.

- [ ] **Step 5 (owner): Switch on the web page**

On https://github.com/eloriahu/deal-alerts go to **Settings**, **Pages**. Under "Build and deployment", set **Source** to **GitHub Actions**. Nothing else to press.

- [ ] **Step 6 (owner): Store the key and the ids as secrets**

On the repository go to **Settings**, **Secrets and variables**, **Actions**, **New repository secret**. Add seven secrets, one at a time. The name must match exactly:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | the key from BotFather |
| `TG_CHAT_SG` | id of SG Deals |
| `TG_CHAT_HK` | id of HK Deals |
| `TG_CHAT_JP` | id of Japan Deals |
| `TG_CHAT_US` | id of US Deals |
| `TG_CHAT_EU` | id of Europe Deals |
| `TG_CHAT_TEST` | id of Deal Alerts Test |

While `TG_CHAT_TEST` exists, every message goes to the test channel only.

- [ ] **Step 7 (owner): Prove delivery to the test channel**

Actions tab, **scan**, **Run workflow**, tick "Only send a test message...", press the green button. Expected: a green tick, and five lines in the **Deal Alerts Test** channel ("Deal Alerts test for Singapore. It works." and so on). Ask the owner to confirm what arrived on the phone; a green tick alone is not proof.

If the run is red, open it and read the "Send test messages only" step. "chat not found" means an id is wrong or missing its minus sign. "bot is not a member" means the bot was not made an administrator of that channel.

- [ ] **Step 8 (owner): Go live**

1. Delete the `TG_CHAT_TEST` secret (Settings, Secrets and variables, Actions, the bin icon).
2. Run **scan** with the test box ticked once more. Expected: one line arrives in each of the five real channels. Ask the owner to confirm all five.
3. Run **scan** once with the box unticked. Expected: green tick, a new commit "chore: update state" in the repository, and the page live at https://eloriahu.github.io/deal-alerts/ with five tabs.

- [ ] **Step 9: Check the next day**

After about 24 hours, look at three things and report them to the owner:
- Actions tab: how late the 15-minute runs actually arrive (GitHub's timer is loose).
- Each Telegram channel: how many alerts arrived, and whether any were false alarms. Adjust `alert_discount`, a source's `heat_threshold`, or the glitch word list in `sources.yaml` accordingly.
- The page: any source showing a red mark. Remove or replace it in `sources.yaml`.

---

## Self-review notes (spec coverage)

| Spec requirement | Task |
|---|---|
| Five regions, never mixed | 1 (region fixed per source), 7 (chat per region), 8 (tab per region) |
| Sources tested from GitHub, failing ones replaced | 10 |
| `Deal` record and link-hash id | 1, 2 |
| Glitch words per language in `sources.yaml` | 1, 3 |
| 70% alert, 40 to 70% dashboard | 3 |
| Fast-votes rule | 3, 4 (Slickdeals vote score read from the feed) |
| "Up to" guard, same-currency guard, expired posts skipped | 2, 3 |
| Chinese and Japanese price and discount wording | 2 |
| Alert once, remembered 30 days, cross-source duplicate by link | 2 (id), 6 (prune), 9 |
| Telegram message layout, secrets never exposed | 4 (address scrubbed), 7 |
| Dashboard: tabs, glitches pinned, last check, source health, phone-friendly, 7 days | 8 |
| One failing source never stops the rest; note after 5 failures; send retry | 9 |
| Timer every 15 minutes, state committed, page published from `public/` | 11 |
| `--dry-run`; first live run to a test chat | 9, 11 |
| Unit tests with real titles and false-alarm cases | 2, 3 |

Known gap, stated plainly: only Slickdeals exposes a vote count in its feed, so the fast-votes rule is live for the US region only. Other regions rely on glitch words and stated discounts.
