from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

from dealalerts.models import REGIONS, Deal, Verdict
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


def valid_record(deal_id: str = "good", region: str = "sg", **overrides: Any) -> Dict[str, Any]:
    """A well-formed stored-deal dict, as State.__init__ would accept it."""
    record = asdict(make_deal(deal_id, region=region))
    record.update(tier="dashboard", kind="discount", reasons=["10% off"])
    record.update(overrides)
    return record


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
    # A success clears the failure count, but the note stays capped at one a day,
    # so a source that recovers and fails again straight away stays quiet.
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is False
    assert state.record_failure("S", "HTTP 403", NOW + timedelta(hours=25), note_after=1) is True


def test_rearm_failure_note_lets_the_note_fire_again() -> None:
    state = State()
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is True
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is False
    state.rearm_failure_note("S")
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is True
    state.rearm_failure_note("no-such-source")  # does nothing, does not raise


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


def test_truncated_json_returns_empty_state_and_moves_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"seen": {"a": "2026-', encoding="utf-8")
    original_content = path.read_text(encoding="utf-8")

    state = State.load(path)

    assert state.deals == [] and not state.is_seen("a")
    assert not path.exists()
    corrupt_path = tmp_path / "state.corrupt.json"
    assert corrupt_path.exists()
    assert corrupt_path.read_text(encoding="utf-8") == original_content


def test_valid_json_with_non_dict_top_level_returns_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    original_content = path.read_text(encoding="utf-8")

    state = State.load(path)

    assert state.deals == [] and not state.is_seen("a")
    assert not path.exists()
    corrupt_path = tmp_path / "state.corrupt.json"
    assert corrupt_path.exists()
    assert corrupt_path.read_text(encoding="utf-8") == original_content


def test_corrupt_load_logs_warning_with_path_and_reset(tmp_path: Path, caplog) -> None:
    import logging
    caplog.set_level(logging.WARNING)

    path = tmp_path / "state.json"
    path.write_text('{"broken', encoding="utf-8")

    state = State.load(path)

    assert state.deals == []
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelname == "WARNING"
    assert "state.json" in record.message
    assert "reset" in record.message.lower()


def test_corrupt_load_and_save_round_trip_works(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text('{"broken', encoding="utf-8")

    state = State.load(path)
    assert state.deals == []

    state.mark_seen("a", NOW)
    state.add_deal(make_deal("a"), GLITCH)
    state.save(path)

    loaded = State.load(path)
    assert loaded.is_seen("a") and loaded.deals[0]["kind"] == "glitch"


def test_wrong_type_top_level_fields_reset_to_empty_without_raising() -> None:
    state = State({"seen": [1, 2], "alerted": "x", "health": 5, "deals": "oops"})
    assert state.seen == {}
    assert state.alerted == {}
    assert state.health == {}
    assert state.deals == []


def test_malformed_deal_records_are_dropped_and_valid_ones_survive() -> None:
    good = valid_record("good")
    missing_region = valid_record("missing-region")
    del missing_region["region"]
    wrong_region = valid_record("wrong-region", region="mars")
    missing_first_seen = valid_record("missing-first-seen")
    del missing_first_seen["first_seen"]
    null_id = valid_record("null-id")
    null_id["id"] = None

    state = State({
        "deals": [good, "junk", missing_region, wrong_region, missing_first_seen, null_id],
    })

    assert [record["id"] for record in state.deals] == ["good"]
    for region in REGIONS:
        state.deals_for(region)  # must not raise for any region


def test_dropped_deal_count_is_logged_once_without_titles(caplog) -> None:
    import logging
    caplog.set_level(logging.WARNING)

    good = valid_record("good")
    bad = valid_record("bad", title="SECRET TITLE")
    del bad["region"]

    State({"deals": [good, bad, "junk"]})

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "2" in message
    assert "SECRET TITLE" not in message


def test_clean_state_logs_no_warning(caplog) -> None:
    import logging
    caplog.set_level(logging.WARNING)

    State({"deals": [valid_record("good")]})

    assert caplog.records == []


def test_round_trip_drops_malformed_records_only_once(tmp_path: Path) -> None:
    good = valid_record("good")
    missing_region = valid_record("missing-region")
    del missing_region["region"]

    state = State({"deals": [good, missing_region, "junk"]})
    path = tmp_path / "state.json"
    state.save(path)

    loaded = State.load(path)
    assert [record["id"] for record in loaded.deals] == ["good"]


def test_records_with_non_string_title_link_or_source_are_dropped() -> None:
    """format_message reads title, link and source straight out of the record, so a
    record whose text fields are not strings must never survive loading."""
    good = valid_record("good")
    no_title = valid_record("no-title")
    del no_title["title"]
    null_link = valid_record("null-link", link=None)
    numeric_source = valid_record("numeric-source", source=7)

    state = State({"deals": [good, no_title, null_link, numeric_source]})

    assert [record["id"] for record in state.deals] == ["good"]


def test_a_source_down_note_comes_at_most_once_a_day() -> None:
    """A source that flaps in and out must not send a note on every wobble."""
    state = State()
    first = [state.record_failure("S", "HTTP 403", NOW, note_after=5) for _ in range(5)]
    assert first == [False, False, False, False, True]

    state.record_success("S", NOW + timedelta(minutes=30))
    two_hours = NOW + timedelta(hours=2)
    second = [state.record_failure("S", "HTTP 403", two_hours, note_after=5) for _ in range(5)]
    assert second == [False] * 5

    state.record_success("S", NOW + timedelta(hours=24))
    next_day = NOW + timedelta(hours=25)
    third = [state.record_failure("S", "HTTP 403", next_day, note_after=5) for _ in range(5)]
    assert third == [False, False, False, False, True]


def test_rearming_clears_the_once_a_day_limit_as_well() -> None:
    """Rearming means the note was never delivered, so the day has not started."""
    state = State()
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is True
    state.rearm_failure_note("S")
    assert state.record_failure("S", "HTTP 403", NOW, note_after=1) is True
    assert state.health["S"].get("noted_at") is not None


def test_the_round_trip_keeps_the_english_title(tmp_path: Path) -> None:
    path = tmp_path / "data" / "state.json"
    state = State()
    state.add_deal(replace(make_deal("a"), title_en="English headline"), GLITCH)
    state.save(path)
    assert State.load(path).deals[0]["title_en"] == "English headline"


def test_a_record_saved_before_english_titles_is_still_kept() -> None:
    """data/state.json already holds records with no such key; they must survive."""
    record = valid_record("old")
    del record["title_en"]
    state = State({"deals": [record]})
    assert [deal["id"] for deal in state.deals] == ["old"]
    assert state.deals[0].get("title_en") is None
