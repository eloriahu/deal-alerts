"""Reading sources: what one run takes in, what it records and what it skips.

Sending alerts and source-down notes is covered in test_pipeline_alerts.py.
"""

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any, List, Optional

import pytest

from dealalerts.models import Deal, RawPost, Source, Verdict
from dealalerts.pipeline import run_once
import dealalerts.pipeline as pipeline_module
from dealalerts.store import State

from dealalerts.http import FetchError
from dealalerts.translate import Translator

from pipeline_support import (
    CONFIG, ENV, NOW, FakeClient, FakeTranslateClient, FakeTranslator, english,
    make_fetch, post, post_at, run, translating_run,
)


def test_first_contact_stores_backlog_without_buzzing() -> None:
    state, client = State(), FakeClient()
    report = run(state, client, {"SG Feed": [post(1, "Price error on TV S$9")], "US Feed": []})
    assert client.sent == [] and report.alerts_sent == 0
    assert [d["id"] for d in state.deals_for("sg")] and state.pending_alerts() == []


def test_unexpected_exception_from_one_source_does_not_stop_the_run_or_leak_the_key() -> None:
    """A non-FetchError from a fetcher (e.g. a library raising with a raw URL that could
    contain the Telegram bot key) must not stop other sources, must not raise out of
    run_once, and must never have its str() representation recorded anywhere in state."""
    state, client = State(), FakeClient()
    feeds = {
        "SG Feed": RuntimeError("https://api.telegram.org/botSECRET123/x exploded"),
        "US Feed": [],
    }
    report = run(state, client, feeds)
    assert report.failures == ("SG Feed",)
    assert state.has_succeeded("US Feed")
    assert state.health["SG Feed"]["last_error"] == "RuntimeError"
    assert "SECRET123" not in repr(state.health)


def test_bad_post_is_skipped_and_does_not_crash_the_run() -> None:
    """A post that cannot even yield an id (e.g. link=None) must not crash the run,
    must not stop other sources, and is simply skipped (it can never be deduplicated,
    so it will be skipped again on later runs too)."""
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    bad = RawPost(title="x", link=None, summary="", posted_at=None, heat=None)
    feeds = {
        "SG Feed": [bad, post(6, "PRICE ERROR blender S$40")],
        "US Feed": [post(7, "Boring kettle")],
    }
    report = run(state, client, feeds)
    assert report.skipped == 1
    assert state.has_succeeded("US Feed")
    assert len(client.sent) == 1
    assert client.sent[0]["chat_id"] == "-1001" and "blender" in client.sent[0]["text"]


def test_bad_post_cannot_re_crash_scoring_and_never_logs_the_error_text(
    monkeypatch, caplog
) -> None:
    """A post that fails during parsing/scoring (after its id was computed) must be
    marked seen right away, so it is skipped without re-attempting on the next run,
    and the raw exception text (which could carry a secret) is never logged."""
    bad_title = "PRICE ERROR gadget S$20"
    original_score = pipeline_module.score

    def fake_score(deal, text, source, config, now):
        if deal.title == bad_title:
            raise ValueError("https://api.telegram.org/botSECRET123/x")
        return original_score(deal, text, source, config, now)

    monkeypatch.setattr(pipeline_module, "score", fake_score)

    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    feeds = {"SG Feed": [post(8, bad_title)], "US Feed": []}

    caplog.set_level(logging.WARNING)
    first = run(state, client, feeds)
    second = run(state, client, feeds)

    assert first.skipped == 1
    assert second.skipped == 0
    assert not any("SECRET123" in record.getMessage() for record in caplog.records)


def test_same_link_in_two_regions_is_two_separate_deals() -> None:
    """Regions are never mixed: the same link posted by sources in two different
    regions is two separate deals, each alerting to its own region's chat."""
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    shared = post(9, "PRICE ERROR shared gadget S$5")
    feeds = {"SG Feed": [shared], "US Feed": [shared]}
    report = run(state, client, feeds)
    assert report.new == 2
    assert len(state.deals_for("sg")) == 1
    assert len(state.deals_for("us")) == 1
    assert len(client.sent) == 2
    assert {message["chat_id"] for message in client.sent} == {"-1001", "-1002"}


def test_same_link_from_two_sources_in_one_region_still_dedupes() -> None:
    """The spec's duplicate rule: the same link from two sources in ONE region is
    one deal; only the first alerts."""
    sg2 = Source(name="SG Feed 2", region="sg", kind="feed", url="https://sg2.test/feed")
    config = replace(CONFIG, sources=(CONFIG.sources[0], sg2, CONFIG.sources[1]))
    state, client = State(), FakeClient()
    run_once(config, state, client, ENV, NOW, False,
             fetch_fn=make_fetch({"SG Feed": [], "SG Feed 2": [], "US Feed": []}),
             sleep=lambda _: None)
    shared = post(10, "PRICE ERROR duplicate gadget S$8")
    feeds = {"SG Feed": [shared], "SG Feed 2": [shared], "US Feed": []}
    run_once(config, state, client, ENV, NOW, False,
             fetch_fn=make_fetch(feeds), sleep=lambda _: None)
    assert len(state.deals_for("sg")) == 1
    assert len(client.sent) == 1


def test_fetch_budget_skips_the_remaining_sources(caplog) -> None:
    """Once the budget is spent the rest of the sources are left alone: not read,
    not marked healthy and not counted as failures."""
    state, client = State(), FakeClient()
    ticks = iter([0.0, 0.0, 400.0])
    asked: List[str] = []

    def counting_fetch(source: Source, _client: Any) -> List[RawPost]:
        asked.append(source.name)
        return []

    caplog.set_level(logging.WARNING)
    report = run_once(CONFIG, state, client, ENV, NOW, False, fetch_fn=counting_fetch,
                      sleep=lambda _: None, clock=lambda: next(ticks, 400.0))

    assert asked == ["SG Feed"]
    assert report.sources_skipped == 1
    assert report.failures == ()
    assert state.has_succeeded("SG Feed")
    assert "US Feed" not in state.health
    assert any("1" in record.getMessage() for record in caplog.records)


def test_a_post_that_stays_in_the_feed_alerts_only_once_over_months() -> None:
    """Some feeds keep a post for months. Seeing it again refreshes its stamp, so
    pruning never forgets it while it is still on show and re-alerts on it."""
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    feeds = {"SG Feed": [post(50, "PRICE ERROR evergreen S$5")], "US Feed": []}
    for day in range(65):
        moment = NOW + timedelta(days=day)
        run(state, client, feeds, now=moment)
        state.prune(moment, seen_days=30, dashboard_days=7)
    assert len(client.sent) == 1


def test_a_source_silent_for_days_is_treated_as_first_contact_again() -> None:
    """After a long outage a feed's whole front page is backlog, not news, so it is
    stored for the page without buzzing the phone."""
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    later = NOW + timedelta(days=3)
    feeds = {"SG Feed": [post_at(60, "PRICE ERROR after outage S$3", later)], "US Feed": []}

    report = run(state, client, feeds, now=later)

    assert client.sent == [] and report.alerts_sent == 0
    assert len(state.deals_for("sg")) == 1
    assert state.pending_alerts() == []


def test_a_source_read_an_hour_ago_is_not_first_contact() -> None:
    state, client = State(), FakeClient()
    run(state, client, {"SG Feed": [], "US Feed": []})
    soon = NOW + timedelta(hours=1)
    feeds = {"SG Feed": [post_at(61, "PRICE ERROR still fresh S$4", soon)], "US Feed": []}

    report = run(state, client, feeds, now=soon)

    assert report.alerts_sent == 1
    assert len(client.sent) == 1 and "fresh" in client.sent[0]["text"]


# ---------------------------------------------------------------------------
# English titles. They are display only: nothing here may change what is
# scored, what is stored beyond the extra field, or when an alert goes out.
# ---------------------------------------------------------------------------


def warm_up(state: State, client: FakeClient, translator: Any) -> None:
    """Read both feeds once, so the next run is not treated as backlog."""
    translating_run(state, client, {"SG Feed": [], "JP Feed": []}, translator)


def test_a_japanese_deal_is_stored_and_sent_with_an_english_headline() -> None:
    state, client, translator = State(), FakeClient(), FakeTranslator()
    warm_up(state, client, translator)
    translating_run(state, client,
                    {"SG Feed": [], "JP Feed": [post(1, "Amazonで価格ミス")]}, translator)

    stored = state.deals_for("jp")[0]
    assert stored["title"] == "Amazonで価格ミス"
    assert stored["title_en"] == "EN: Amazonで価格ミス"
    assert translator.asked == [("Amazonで価格ミス", "ja")]
    assert "EN: Amazonで価格ミス" in client.sent[0]["text"]


def test_an_english_source_is_never_sent_for_translation() -> None:
    state, client, translator = State(), FakeClient(), FakeTranslator()
    warm_up(state, client, translator)
    translating_run(state, client,
                    {"SG Feed": [post(2, "PRICE ERROR toaster S$9")], "JP Feed": []}, translator)

    assert translator.asked == []
    assert state.deals_for("sg")[0]["title_en"] is None


def test_a_dropped_post_is_never_translated() -> None:
    """Ignored posts are the bulk of every run; paying for them would burn the budget."""
    state, client, translator = State(), FakeClient(), FakeTranslator()
    warm_up(state, client, translator)
    translating_run(state, client,
                    {"SG Feed": [], "JP Feed": [post(3, "ふつうのニュース")]}, translator)

    assert translator.asked == []
    assert state.deals_for("jp") == []


def test_a_run_with_no_translator_stores_the_original_only() -> None:
    state, client = State(), FakeClient()
    warm_up(state, client, None)
    translating_run(state, client,
                    {"SG Feed": [], "JP Feed": [post(4, "Amazonで価格ミス")]}, None)

    assert state.deals_for("jp")[0]["title_en"] is None


def test_the_run_honours_the_translation_budget() -> None:
    state, client = State(), FakeClient()
    asked = FakeTranslateClient([english("First"), english("Second")])
    translator = Translator(asked, None, max_attempts=1)
    warm_up(state, client, translator)
    posts = [post(10, "Amazonで価格ミス 1"), post(11, "Amazonで価格ミス 2")]
    translating_run(state, client, {"SG Feed": [], "JP Feed": posts}, translator)

    assert sorted(deal["title_en"] or "" for deal in state.deals_for("jp")) == ["", "First"]
    assert len(asked.urls) == 1


def test_one_translation_failure_does_not_stop_the_run() -> None:
    """The service returns the odd gateway error while perfectly well, and giving
    up the whole run for one of those costs every later headline for nothing."""
    state, client = State(), FakeClient()
    asked = FakeTranslateClient([
        FetchError("api.test: HTTP 504"), english("Second title"), english("First title"),
    ])
    translator = Translator(asked, None, max_attempts=10)
    warm_up(state, client, translator)
    posts = [post(20, "Amazonで価格ミス 1"), post(21, "Amazonで価格ミス 2")]
    translating_run(state, client, {"SG Feed": [], "JP Feed": posts}, translator)

    # The second headline is translated as usual, and the fill-in pass picks the
    # first one up again before anything is sent, so the hiccup costs nothing.
    assert sorted(deal["title_en"] or "" for deal in state.deals_for("jp")) == \
        ["First title", "Second title"]
    assert len(asked.urls) == 3
    assert len(client.sent) == 2


def test_two_translation_failures_stop_the_rest_of_the_run_but_not_the_alerts() -> None:
    state, client = State(), FakeClient()
    asked = FakeTranslateClient([
        FetchError("api.test: HTTP 500"), FetchError("api.test: HTTP 500"),
        english("Never asked for"),
    ])
    translator = Translator(asked, None, max_attempts=10)
    warm_up(state, client, translator)
    posts = [post(20, "Amazonで価格ミス 1"), post(21, "Amazonで価格ミス 2"),
             post(22, "Amazonで価格ミス 3")]
    translating_run(state, client, {"SG Feed": [], "JP Feed": posts}, translator)

    assert [deal["title_en"] for deal in state.deals_for("jp")] == [None, None, None]
    assert len(asked.urls) == 2
    assert len(client.sent) == 3


def test_a_translator_that_explodes_costs_only_the_english_headline(
    caplog: pytest.LogCaptureFixture
) -> None:
    """The deal, the alert and the rest of the run all survive, and the exception's
    text (which could quote an address carrying the email) is never recorded."""
    class Exploding:
        def english_title(self, text: str, language: str) -> Optional[str]:
            raise RuntimeError("https://api.test/get?de=SECRET123 blew up")

    state, client = State(), FakeClient()
    warm_up(state, client, Exploding())
    with caplog.at_level(logging.WARNING):
        translating_run(state, client,
                        {"SG Feed": [], "JP Feed": [post(30, "Amazonで価格ミス")]}, Exploding())

    assert state.deals_for("jp")[0]["title_en"] is None
    assert len(client.sent) == 1
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in log_text
    assert "SECRET123" not in log_text
    assert "SECRET123" not in repr(state.deals)


# ---------------------------------------------------------------------------
# Filling in titles that an earlier run could not translate. Without this, a
# deal stored while the service was down keeps its original title for the whole
# week it is on the page.
# ---------------------------------------------------------------------------


def stored_jp(state: State, count: int, translated: int = 0) -> None:
    """Put `count` Japanese deals in the state, the first `translated` of them done."""
    for index in range(count):
        deal = Deal(
            id=f"jp-{index}", region="jp", source="JP Feed", title=f"価格ミス {index}",
            link=f"https://shop.test/{index}", shop=None, price_now=None, usual_price=None,
            currency=None, discount_pct=None, heat=None, posted_at=None,
            first_seen=(NOW - timedelta(minutes=index)).isoformat(),
            title_en=f"Done {index}" if index < translated else None,
        )
        state.add_deal(deal, Verdict("dashboard", "discount", ("50% off",)))


def test_a_later_run_fills_in_up_to_five_missing_english_titles() -> None:
    state, client, translator = State(), FakeClient(), FakeTranslator()
    stored_jp(state, 8)
    translating_run(state, client, {"SG Feed": [], "JP Feed": []}, translator)

    filled = [deal for deal in state.deals if deal["title_en"]]
    assert len(filled) == 5
    assert len(translator.asked) == 5
    # Newest first: the first five by first_seen, which is index 0 to 4.
    assert sorted(deal["id"] for deal in filled) == [f"jp-{index}" for index in range(5)]


def test_the_later_pass_skips_english_records_and_ones_already_done() -> None:
    state, client, translator = State(), FakeClient(), FakeTranslator()
    stored_jp(state, 3, translated=2)
    state.add_deal(
        Deal(id="sg-1", region="sg", source="SG Feed", title="Plain English deal",
             link="https://shop.test/sg", shop=None, price_now=None, usual_price=None,
             currency=None, discount_pct=None, heat=None, posted_at=None,
             first_seen=NOW.isoformat()),
        Verdict("dashboard", "discount", ("50% off",)),
    )
    translating_run(state, client, {"SG Feed": [], "JP Feed": []}, translator)

    assert translator.asked == [("価格ミス 2", "ja")]
    assert [deal["title_en"] for deal in state.deals if deal["id"] == "sg-1"] == [None]


def test_the_later_pass_shares_the_one_per_run_budget() -> None:
    state, client = State(), FakeClient()
    asked = FakeTranslateClient([english("One"), english("Two")])
    translator = Translator(asked, None, max_attempts=1)
    stored_jp(state, 4)
    translating_run(state, client, {"SG Feed": [], "JP Feed": []}, translator)

    assert len(asked.urls) == 1
    assert len([deal for deal in state.deals if deal["title_en"]]) == 1


def test_a_translator_that_explodes_in_the_later_pass_does_not_stop_the_run(
    caplog: pytest.LogCaptureFixture
) -> None:
    class Exploding:
        def english_title(self, text: str, language: str) -> Optional[str]:
            raise RuntimeError("https://api.test/get?de=SECRET123 blew up")

    state, client = State(), FakeClient()
    stored_jp(state, 2)
    with caplog.at_level(logging.WARNING):
        report = translating_run(state, client, {"SG Feed": [], "JP Feed": []}, Exploding())

    assert report.failures == ()
    assert all(deal["title_en"] is None for deal in state.deals)
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in log_text and "SECRET123" not in log_text


def test_no_translator_means_no_later_pass() -> None:
    state, client = State(), FakeClient()
    stored_jp(state, 3)
    translating_run(state, client, {"SG Feed": [], "JP Feed": []}, None)
    assert all(deal["title_en"] is None for deal in state.deals)
