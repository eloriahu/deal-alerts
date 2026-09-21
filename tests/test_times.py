from datetime import datetime, timezone

from dealalerts.times import parse_utc


def test_parse_utc_with_none_and_empty_string() -> None:
    assert parse_utc(None) is None
    assert parse_utc("") is None


def test_parse_utc_with_invalid_format() -> None:
    assert parse_utc("not a date") is None


def test_parse_utc_with_naive_datetime_treats_as_utc() -> None:
    assert parse_utc("2026-09-21T11:30:00") == datetime(2026, 9, 21, 11, 30, tzinfo=timezone.utc)


def test_parse_utc_converts_plus_eight_to_utc() -> None:
    assert parse_utc("2026-09-21T19:30:00+08:00") == datetime(2026, 9, 21, 11, 30, tzinfo=timezone.utc)


def test_parse_utc_converts_utc_plus_zero() -> None:
    assert parse_utc("2026-09-21T11:30:00+00:00") == datetime(2026, 9, 21, 11, 30, tzinfo=timezone.utc)
