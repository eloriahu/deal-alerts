"""Reading sources: what one run takes in, what it records and what it skips.

Sending alerts and source-down notes is covered in test_pipeline_alerts.py.
"""

import logging
from dataclasses import replace
from datetime import timedelta
from typing import Any, List

from dealalerts.models import RawPost, Source
from dealalerts.pipeline import run_once
import dealalerts.pipeline as pipeline_module
from dealalerts.store import State

from pipeline_support import (
    CONFIG, ENV, NOW, FakeClient, make_fetch, post, post_at, run,
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
