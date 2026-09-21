"""Sending: which alerts go out, when the run gives up, and source-down notes.

Reading sources is covered in test_pipeline.py.
"""

import logging
from datetime import timedelta
from typing import List

from dealalerts.http import FetchError
from dealalerts.pipeline import run_once
from dealalerts.store import State

from pipeline_support import (
    CONFIG, ENV, NOW, ExplodingClient, FakeClient, ScriptedClient, make_fetch, post, run,
    seed_pending,
)


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
    run(state, client, {"SG Feed": [post(5, "price error phone S$10")], "US Feed": []},
        dry_run=True)
    assert client.sent == [] and len(state.pending_alerts()) == 1


def test_lost_source_down_note_is_retried_until_it_is_delivered() -> None:
    """A source-down note that fails to send must not be lost forever: it is
    retried on later runs while the source keeps failing, and stops once one copy
    has actually been delivered."""
    state = State()
    feeds = {"SG Feed": FetchError("sg.test: HTTP 403"), "US Feed": []}

    down = FakeClient(ok=False)
    run(state, down, feeds)  # fails=1, no note yet
    run(state, down, feeds)  # fails=2, note attempted, fails
    assert down.sent == []

    healthy_a = FakeClient(ok=True)
    run(state, healthy_a, feeds)  # note retried, delivered
    assert len(healthy_a.sent) == 1
    assert healthy_a.sent[0]["chat_id"] == "-1001"

    healthy_b = FakeClient(ok=True)
    run(state, healthy_b, feeds)  # already delivered, no repeat
    assert healthy_b.sent == []


def test_unexpected_send_error_is_contained_and_never_reaches_a_log_or_the_state(
    caplog,
) -> None:
    """Anything other than NotifyError coming out of the send path must not stop the
    run, must never have its text logged or stored, and must not be retried forever."""
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    feeds = {"SG Feed": [post(20, "PRICE ERROR kettle S$9")], "US Feed": []}

    caplog.set_level(logging.WARNING)
    report = run(state, ExplodingClient(), feeds)

    assert report.send_errors == 1
    assert report.alerts_sent == 0
    assert state.pending_alerts() == []
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "SECRET123" not in log_text
    assert "SECRET123" not in repr(state.deals) + repr(state.health) + repr(state.alerted)


def test_unformattable_alert_is_marked_and_the_next_one_is_still_sent(caplog) -> None:
    """A stored alert record that cannot be formatted (here: no title) is given up on
    once, and does not block the alerts queued behind it."""
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    state.deals.append({
        "id": "sg-broken", "region": "sg", "source": "SG Feed",
        "link": "https://shop.test/broken", "tier": "alert", "kind": "glitch",
        "reasons": [], "first_seen": (NOW - timedelta(hours=1)).isoformat(),
    })
    feeds = {"SG Feed": [post(21, "PRICE ERROR fan S$7")], "US Feed": []}

    caplog.set_level(logging.WARNING)
    client = FakeClient()
    report = run(state, client, feeds)

    assert report.send_errors == 1
    assert report.alerts_sent == 1
    assert state.is_alerted("sg-broken")
    assert len(client.sent) == 1 and "fan" in client.sent[0]["text"]
    assert state.pending_alerts() == []


def test_checkpoint_runs_after_fetching_and_after_each_successful_send() -> None:
    """Alerts already delivered must survive a later crash, so the state is saved
    after the fetch loop and again after every message that actually went out."""
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    posts = [post(30, "PRICE ERROR one S$1"), post(31, "PRICE ERROR two S$2")]
    calls: List[int] = []
    client = FakeClient()
    report = run_once(CONFIG, state, client, ENV, NOW, False,
                      fetch_fn=make_fetch({"SG Feed": posts, "US Feed": []}),
                      sleep=lambda _: None, checkpoint=lambda: calls.append(1))
    assert report.alerts_sent == 2
    assert len(calls) == 3


def test_checkpoint_is_not_called_for_a_send_that_failed() -> None:
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    calls: List[int] = []
    run_once(CONFIG, state, FakeClient(ok=False), ENV, NOW, False,
             fetch_fn=make_fetch({"SG Feed": [post(32, "PRICE ERROR three S$3")], "US Feed": []}),
             sleep=lambda _: None, checkpoint=lambda: calls.append(1))
    assert len(calls) == 1


def test_a_checkpoint_that_raises_does_not_stop_the_run(caplog) -> None:
    def explode() -> None:
        raise RuntimeError("https://api.telegram.org/botSECRET123/x")

    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    caplog.set_level(logging.WARNING)
    client = FakeClient()
    report = run_once(CONFIG, state, client, ENV, NOW, False,
                      fetch_fn=make_fetch({"SG Feed": [post(33, "PRICE ERROR four S$4")],
                                           "US Feed": []}),
                      sleep=lambda _: None, checkpoint=explode)
    assert report.alerts_sent == 1
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "SECRET123" not in log_text
    assert "RuntimeError" in log_text


def test_sending_stops_after_three_failures_in_a_row_and_the_rest_stay_pending() -> None:
    state = State()
    client = ScriptedClient([False] * 5)
    seed_pending(state, client, 5)
    assert client.attempts == 3
    assert len(state.pending_alerts()) == 5


def test_a_success_resets_the_failure_run() -> None:
    """The cap counts failures in a row, not failures in total."""
    state = State()
    client = ScriptedClient([False, False, True, False, False, True])
    seed_pending(state, client, 6)
    assert client.attempts == 6
    assert len(client.sent) == 2


def test_dry_run_is_not_treated_as_a_string_of_delivery_failures() -> None:
    """Nothing is attempted in a dry run, so the failure cap must not cut the listing."""
    state = State()
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    posts = [post(40 + index, f"PRICE ERROR dry {index} S${index}") for index in range(5)]
    run(state, FakeClient(), {"SG Feed": posts, "US Feed": []}, dry_run=True)
    assert len(state.pending_alerts()) == 5
