"""Decide whether a deal buzzes the phone, sits on the dashboard, or is dropped."""

import logging
import re
from datetime import datetime
from functools import lru_cache
from typing import List, Optional, Pattern, Tuple

from dealalerts.config import Config
from dealalerts.models import Deal, Source, Verdict
from dealalerts.times import parse_utc

logger = logging.getLogger(__name__)

_IGNORE = Verdict(tier="ignore", kind="", reasons=())

# Chinese and Japanese characters, including the kana and the full-width forms.
# A word holding any of these comes from a language written without spaces.
_UNSPACED_SCRIPT = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")


@lru_cache(maxsize=8)
def _in_store_matchers(words: Tuple[str, ...]) -> Tuple[Optional[Pattern[str]], Tuple[str, ...]]:
    """Split the in-store words into a whole-word pattern and plain-search words.

    English, German and French put spaces between words, so "in store" must fire
    on "in store only" and not on "Skin Store", and "eat-in" must not fire on
    "heat-insulated". Those words are matched whole. Chinese and Japanese are
    written without spaces, so a whole-word rule would never fire on them at all
    ("店内" sits inside "期間限定の店内セール" with no break either side); those
    stay a plain search for the characters anywhere in the text.

    The result is cached because it is the same for every post in a run and
    rebuilding the pattern per post would be wasted work.
    """
    spaced = [word for word in words if _UNSPACED_SCRIPT.search(word) is None]
    unspaced = tuple(word for word in words if _UNSPACED_SCRIPT.search(word) is not None)
    pattern = None
    if spaced:
        # Longest first, so "in-store" is preferred over a shorter word that
        # starts the same way; re.escape keeps a hyphen or a dot literal.
        joined = "|".join(re.escape(word) for word in sorted(spaced, key=len, reverse=True))
        pattern = re.compile(rf"\b(?:{joined})\b")
    return pattern, unspaced


def _mentions_in_store(folded: str, words: Tuple[str, ...]) -> bool:
    """True if the casefolded text names a shop floor or a dining room."""
    pattern, unspaced = _in_store_matchers(words)
    if pattern is not None and pattern.search(folded):
        return True
    return any(word in folded for word in unspaced)


def _age_minutes(deal: Deal, now: datetime) -> float:
    """Calculate minutes since the deal was posted, tolerating missing or bad timestamps."""
    posted = parse_utc(deal.posted_at) or parse_utc(deal.first_seen) or now
    return max(0.0, (now - posted).total_seconds() / 60)


def _is_too_old_to_buzz(deal: Deal, max_age_hours: int, now: datetime) -> bool:
    """True if the post states a time and that time is older than the limit.

    A post with no usable time is never held back: the tool cannot tell whether
    it is stale, and a missed price error costs more than a late one.
    """
    posted = parse_utc(deal.posted_at)
    if posted is None:
        return False
    return (now - posted).total_seconds() / 3600 > max_age_hours


def score(deal: Deal, text: str, source: Source, config: Config, now: datetime) -> Verdict:
    """Apply the spec's scoring rules.

    Args:
        deal: The parsed deal.
        text: Title plus summary, checked for sale words, ignore words and
            in-store words.
        source: Where the deal came from (vote threshold, sale-word gate).
        config: Thresholds and word lists.
        now: Current time, timezone-aware UTC.
    """
    settings = config.settings
    if any(word in deal.title.casefold() for word in config.expired_words):
        return _IGNORE

    folded = text.casefold()
    # Outside Singapore only a deal she can take from where she sits is any use,
    # so a post naming a shop floor or a dining room is dropped. This comes
    # before every other rule on purpose: a price error at a till, and a post the
    # crowd is voting up, are both still trips she is not going to make.
    if (source.region in settings.online_only_regions
            and _mentions_in_store(folded, config.in_store_words)):
        return _IGNORE

    # Price-error words are read in the headline only. In a summary they are
    # almost always about something else ("the app has a glitch"), and a news
    # site's story about a configuration mistake is not a deal.
    folded_title = deal.title.casefold()
    glitch = next((word for word in config.glitch_words if word in folded_title), None)
    if glitch is None and source.require_sale_word:
        if not any(word in folded for word in config.sale_words):
            return _IGNORE

    reasons: List[str] = []
    kind = ""
    if glitch is not None:
        reasons.append(f'glitch word "{glitch}"')
        kind = "glitch"

    # An ignore word means the percentage on the post is not a cut on the thing
    # being bought. The price-error and vote rules are unaffected.
    discount = None if any(word in folded for word in config.ignore_words) else deal.discount_pct
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
        if _is_too_old_to_buzz(deal, settings.max_alert_age_hours, now):
            reasons.append(f"posted over {settings.max_alert_age_hours} h ago")
            return Verdict(tier="dashboard", kind=kind, reasons=tuple(reasons))
        return Verdict(tier="alert", kind=kind, reasons=tuple(reasons))
    if discount is not None and discount >= settings.dashboard_discount:
        return Verdict(tier="dashboard", kind="discount", reasons=(f"{discount:.0f}% off",))
    return _IGNORE
