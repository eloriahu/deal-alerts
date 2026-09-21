"""Decide whether a deal buzzes the phone, sits on the dashboard, or is dropped."""

import logging
from datetime import datetime
from typing import List

from dealalerts.config import Config
from dealalerts.models import Deal, Source, Verdict
from dealalerts.times import parse_utc

logger = logging.getLogger(__name__)

_IGNORE = Verdict(tier="ignore", kind="", reasons=())


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
        text: Title plus summary, checked for glitch words and sale words.
        source: Where the deal came from (vote threshold, sale-word gate).
        config: Thresholds and word lists.
        now: Current time, timezone-aware UTC.
    """
    settings = config.settings
    if any(word in deal.title.casefold() for word in config.expired_words):
        return _IGNORE

    folded = text.casefold()
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
