from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import pytest

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


def bad_record(title: str, **overrides: Any) -> Dict[str, Any]:
    """A stored-deal dict copied from a good deal, with some fields overridden."""
    record = asdict(make_deal("bad", title, "https://x.test/bad", 2))
    record.update(tier="dashboard", kind="discount", reasons=["10% off"])
    record.update(overrides)
    return record


def make_state() -> State:
    state = State()
    state.add_deal(make_deal("drop", "Ordinary big drop", "https://x.test/drop", 5),
                   Verdict("dashboard", "discount", ("50% off",)))
    state.add_deal(make_deal("glitch", "Glitch <script>alert(1)</script>", "https://x.test/g", 90),
                   Verdict("alert", "glitch", ('glitch word "glitch"',)))
    state.add_deal(make_deal("evil", "Evil link", "javascript:alert(1)", 1),
                   Verdict("dashboard", "discount", ("45% off",)))
    state.record_success("Good Feed", NOW)
    state.record_failure("Dead Feed", "b.test: HTTP 403", NOW, note_after=5)
    return state


def build(tmp_path: Path) -> str:
    return build_site(make_state(), CONFIG, NOW, tmp_path).read_text(encoding="utf-8")


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


def test_malformed_discount_pct_does_not_crash_the_page(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append(bad_record("Bad discount deal", discount_pct="83.8"))
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")
    assert "Ordinary big drop" in page
    assert "Bad discount deal" not in page


def test_malformed_price_now_does_not_crash_the_page(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append(bad_record("Bad price deal", price_now="cheap"))
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")
    assert "Ordinary big drop" in page
    assert "Bad price deal" not in page


def test_non_dict_deal_does_not_crash_the_page(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append("junk")
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")
    assert "Ordinary big drop" in page
    for label in ("Hong Kong", "Japan", "US", "Europe"):
        assert label in page


def test_skipped_deal_is_logged_without_its_title(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = make_state()
    state.deals.append(bad_record("Secret bad title", discount_pct="83.8"))
    with caplog.at_level("WARNING"):
        build_site(state, CONFIG, NOW, tmp_path)
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert any("Skipping" in message for message in warnings)
    assert all("Secret bad title" not in message for message in warnings)


def test_link_with_a_quote_is_escaped_in_the_attribute(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append(bad_record(
        "Quote link deal", link='https://x.test/a"onmouseover="alert(1)',
    ))
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")
    assert '"onmouseover="alert(1)' not in page
    assert "&quot;" in page


def test_title_with_dollar_placeholders_does_not_break_the_template(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append(bad_record("Price $tabs ${panes} drop"))
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")
    assert "Price $tabs ${panes} drop" in page


def test_a_dict_missing_region_does_not_blank_every_region(tmp_path: Path) -> None:
    sg_deal = bad_record("SG deal still shows")
    hk_deal = bad_record("HK deal still shows", region="hk")
    broken = bad_record("Broken deal never shows")
    del broken["region"]

    state = State({"deals": [sg_deal, hk_deal, broken]})
    page = build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")

    assert "SG deal still shows" in page
    assert "HK deal still shows" in page


def test_build_site_never_mutates_state_deals(tmp_path: Path) -> None:
    state = make_state()
    state.deals.append(bad_record("Bad discount deal", discount_pct="83.8"))
    before = state.deals
    snapshot = list(state.deals)

    build_site(state, CONFIG, NOW, tmp_path)

    assert state.deals is before
    assert state.deals == snapshot


# ---------------------------------------------------------------------------
# English titles on the page: the link text, with the original muted underneath.
# ---------------------------------------------------------------------------


def build_with(tmp_path: Path, **overrides: Any) -> str:
    """The page, with one extra deal carrying the given fields."""
    state = make_state()
    state.deals.append(bad_record(overrides.pop("title", "A deal"), **overrides))
    return build_site(state, CONFIG, NOW, tmp_path).read_text(encoding="utf-8")


def test_the_english_title_is_the_link_text_with_the_original_underneath(tmp_path: Path) -> None:
    page = build_with(tmp_path, title="【価格ミス】ソニー", title_en="Sony price error")
    assert ">Sony price error</a>" in page
    assert '<div class="orig">【価格ミス】ソニー</div>' in page
    assert ".orig" in page  # the muted style is defined


def test_a_deal_with_no_english_title_renders_as_before(tmp_path: Path) -> None:
    assert 'class="orig"' not in build(tmp_path)


def test_an_english_title_equal_to_the_original_is_not_repeated(tmp_path: Path) -> None:
    assert 'class="orig"' not in build_with(tmp_path, title="Same", title_en="Same")


def test_a_hostile_english_title_is_escaped(tmp_path: Path) -> None:
    page = build_with(tmp_path, title="plain <b>original</b>",
                      title_en="<script>alert(1)</script>")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;b&gt;original&lt;/b&gt;" in page
