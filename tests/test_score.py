from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from dealalerts.config import Config, Settings, load_config
from dealalerts.models import Deal, Source
from dealalerts.parse import parse_fields
from dealalerts.score import score

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
CONFIG = Config(
    settings=Settings(),
    sources=(),
    glitch_words=("price error", "price glitch", "preisfehler", "錯價", "価格ミス"),
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


def test_glitch_word_in_the_headline_alerts() -> None:
    # Price-error words count in the headline only (see test_price_error_words_are
    # _read_in_the_headline_only), so the fixture carries the word in its title.
    glitchy = replace(DEAL, title="Possible PRICE ERROR on toaster")
    verdict = score(glitchy, glitchy.title, SOURCE, CONFIG, NOW)
    assert (verdict.tier, verdict.kind) == ("alert", "glitch")
    assert 'glitch word "price error"' in verdict.reasons


def test_price_error_words_are_read_in_the_headline_only() -> None:
    """A summary mentioning a price error is not itself a price-error deal."""
    plain = replace(DEAL, title="Weekly toaster round-up")
    assert score(plain, f"{plain.title} a reader reports a price error", SOURCE,
                 CONFIG, NOW).tier == "ignore"


def test_chinese_and_japanese_glitch_words_alert() -> None:
    for headline in ("百佳標錯價 出錯價", "Amazonで価格ミスか"):
        deal = replace(DEAL, title=headline)
        assert score(deal, headline, SOURCE, CONFIG, NOW).tier == "alert"


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
    headline = replace(DEAL, title="Retailer price glitch reported")
    assert score(headline, headline.title, NEWS_SOURCE, CONFIG, NOW).tier == "alert"


def test_glitch_takes_the_headline_when_several_rules_fire() -> None:
    both = replace(DEAL, discount_pct=90.0, title="price error toaster")
    verdict = score(both, both.title, SOURCE, CONFIG, NOW)
    assert verdict.kind == "glitch"
    assert "90% off" in verdict.reasons


def test_naive_posted_at_timestamp_does_not_crash() -> None:
    fresh = replace(DEAL, heat=75, posted_at=(NOW - timedelta(minutes=30)).replace(tzinfo=None).isoformat())
    verdict = score(fresh, "x", HOT_SOURCE, CONFIG, NOW)
    assert (verdict.tier, verdict.kind) == ("alert", "heat")


def test_missing_posted_at_falls_back_to_first_seen() -> None:
    fresh = replace(DEAL, heat=75, posted_at=None)
    verdict = score(fresh, "x", HOT_SOURCE, CONFIG, NOW)
    assert verdict.tier == "alert"


def test_garbage_posted_at_falls_back_to_first_seen_and_does_not_crash() -> None:
    fresh = replace(DEAL, heat=75, posted_at="garbage")
    verdict = score(fresh, "x", HOT_SOURCE, CONFIG, NOW)
    assert verdict.tier == "alert"


def test_an_alert_older_than_a_day_drops_to_the_dashboard() -> None:
    """A price error from last week is long gone; it belongs on the page, not on
    the phone. The reasons say why it was held back."""
    old = replace(DEAL, title="Possible PRICE ERROR on toaster",
                  posted_at=(NOW - timedelta(days=5)).isoformat())
    verdict = score(old, old.title, SOURCE, CONFIG, NOW)
    assert verdict.tier == "dashboard"
    assert verdict.kind == "glitch"
    assert 'glitch word "price error"' in verdict.reasons
    assert "posted over 24 h ago" in verdict.reasons


def test_a_two_hour_old_alert_still_buzzes() -> None:
    recent = replace(DEAL, title="Possible PRICE ERROR on toaster",
                     posted_at=(NOW - timedelta(hours=2)).isoformat())
    assert score(recent, recent.title, SOURCE, CONFIG, NOW).tier == "alert"


def test_a_post_with_no_usable_posted_at_is_not_held_back() -> None:
    for stamp in (None, "", "garbage"):
        unknown = replace(DEAL, title="Possible PRICE ERROR on toaster", posted_at=stamp)
        verdict = score(unknown, unknown.title, SOURCE, CONFIG, NOW)
        assert verdict.tier == "alert", stamp


REAL_CONFIG = load_config(Path(__file__).resolve().parents[1] / "sources.yaml")


def titled(title: str, **overrides: Any) -> Deal:
    """The standard deal with a different title (and anything else overridden)."""
    return replace(DEAL, title=title, **overrides)


@pytest.mark.parametrize(
    ("title", "summary"),
    [
        ("Glitch Productions merch restock", ""),
        ("Nintendo Switch game 'Glitchpunk' $10", ""),
        ("Weekly gadget round-up", "the app has a glitch that eats your save file"),
        ("Weekly gadget round-up", "a reader reports a price error at some shop"),
    ],
)
def test_ordinary_posts_do_not_count_as_price_errors(title: str, summary: str) -> None:
    """The word has to be about a price, and it has to be in the headline."""
    deal = titled(title, discount_pct=None)
    verdict = score(deal, f"{title} {summary}", SOURCE, REAL_CONFIG, NOW)
    assert verdict.tier == "ignore"


@pytest.mark.parametrize(
    "title",
    ["クラウドの設定ミスで顧客情報が流出", "製品仕様の誤表記に関するお詫び"],
)
def test_japanese_news_headlines_are_not_price_errors(title: str) -> None:
    """PC Watch runs stories about configuration mistakes and misprinted specs."""
    deal = titled(title, discount_pct=None)
    assert score(deal, title, NEWS_SOURCE, REAL_CONFIG, NOW).tier == "ignore"


@pytest.mark.parametrize(
    "title",
    [
        "PRICE ERROR Dyson V12 S$99",
        "Amazonで価格ミスか",
        "百佳標錯價",
        "Preisfehler? Bosch Akkuschrauber",
    ],
)
def test_real_price_error_headlines_still_alert(title: str) -> None:
    deal = titled(title, discount_pct=None)
    assert score(deal, title, SOURCE, REAL_CONFIG, NOW).tier == "alert"


def test_a_news_source_can_still_be_let_through_by_a_price_error_headline() -> None:
    deal = titled("Amazon pricing glitch sends laptops out at S$1", discount_pct=None)
    assert score(deal, deal.title, NEWS_SOURCE, REAL_CONFIG, NOW).tier == "alert"


def test_an_ignore_word_switches_off_the_discount_rules() -> None:
    """A lottery ticket for new customers is not a 98% discount on anything."""
    headline = "Lotto Neukunden: 0,10 € statt 4,70 €"
    fields = parse_fields(headline, "", "eu")
    assert fields.discount_pct is not None and fields.discount_pct >= 70
    deal = titled(headline, discount_pct=fields.discount_pct)
    assert score(deal, headline, SOURCE, REAL_CONFIG, NOW).tier == "ignore"


def test_an_ignore_word_leaves_the_price_error_and_vote_rules_alone() -> None:
    glitchy = titled("PRICE ERROR: Lotto machine for new customers S$1", discount_pct=95.0)
    verdict = score(glitchy, glitchy.title, SOURCE, REAL_CONFIG, NOW)
    assert (verdict.tier, verdict.kind) == ("alert", "glitch")
    assert not any("95% off" in reason for reason in verdict.reasons)

    hot = titled("Lotto machine for new customers", discount_pct=95.0, heat=75)
    assert score(hot, hot.title, HOT_SOURCE, REAL_CONFIG, NOW).tier == "alert"


# ---------------------------------------------------------------------------
# Online-only regions. Outside Singapore a bargain she would have to walk into
# a shop or sit down in a restaurant for is no use, so it is dropped outright.
# ---------------------------------------------------------------------------


def in_region(region: str) -> Source:
    """The standard source, moved to another region."""
    return replace(SOURCE, region=region)


def test_an_in_store_deal_outside_singapore_is_dropped() -> None:
    deal = titled("Sony WH-1000XM6 S$89 in-store only", discount_pct=84.0)
    assert score(deal, deal.title, in_region("us"), REAL_CONFIG, NOW).tier == "ignore"


def test_the_same_in_store_deal_in_singapore_is_kept() -> None:
    """Singapore is the one region she can walk into, so it is never filtered."""
    deal = titled("Sony WH-1000XM6 S$89 in-store only", discount_pct=84.0)
    assert score(deal, deal.title, in_region("sg"), REAL_CONFIG, NOW).tier == "alert"


def test_a_hong_kong_dine_in_deal_is_dropped() -> None:
    deal = titled("大家樂：豬扒撈公仔麵 $32 堂食", discount_pct=84.0)
    assert score(deal, deal.title, in_region("hk"), REAL_CONFIG, NOW).tier == "ignore"


def test_an_ordinary_japanese_deal_is_left_alone_by_the_rule() -> None:
    deal = titled("【715円】ケーブルクリップ セール", discount_pct=84.0)
    assert score(deal, deal.title, in_region("jp"), REAL_CONFIG, NOW).tier == "alert"


def test_a_branch_only_european_deal_is_dropped_however_big_the_cut() -> None:
    deal = titled("Filiale: Bosch Akkuschrauber 29,99 € statt 129,99 €", discount_pct=76.7)
    assert score(deal, deal.title, in_region("eu"), REAL_CONFIG, NOW).tier == "ignore"


def test_the_rule_beats_a_price_error_headline() -> None:
    """A price error she could only take at a till is still a price error she cannot take."""
    deal = titled("PRICE ERROR Dyson V12 $99 in-store", discount_pct=None)
    assert score(deal, deal.title, in_region("us"), REAL_CONFIG, NOW).tier == "ignore"


def test_the_rule_reads_the_summary_too_and_beats_the_vote_rule() -> None:
    hot = titled("Dyson V12 at $99", heat=75, discount_pct=None)
    assert score(hot, hot.title, HOT_SOURCE, REAL_CONFIG, NOW).tier == "alert"
    assert score(hot, f"{hot.title} dine-in customers only", HOT_SOURCE,
                 REAL_CONFIG, NOW).tier == "ignore"


def test_the_rule_reads_both_lists_out_of_the_file() -> None:
    """Neither the words nor the regions are hard-coded in the scoring code."""
    config = replace(REAL_CONFIG, in_store_words=("kiosk",),
                     settings=replace(REAL_CONFIG.settings, online_only_regions=("jp",)))
    deal = titled("Camera at the kiosk", discount_pct=84.0)
    assert score(deal, deal.title, in_region("jp"), config, NOW).tier == "ignore"
    assert score(deal, deal.title, in_region("us"), config, NOW).tier == "alert"
