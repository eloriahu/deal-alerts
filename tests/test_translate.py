"""Tests for the English-title lookup.

Every test uses a fake client, so nothing here reaches MyMemory. The two rules
that matter most: a translation is display only and never changes scoring, and
nothing in this module may ever raise or log the email, the address or the text.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import pytest

from dealalerts.http import FetchError
from dealalerts.translate import BYTE_LIMIT, ENDPOINT, Translator, translate_title

EMAIL = "owner@example.test"
QUOTA = "MYMEMORY WARNING: YOU USED ALL AVAILABLE FREE TRANSLATIONS FOR TODAY"


def ok(text: str, status: Any = 200) -> Dict[str, Any]:
    """A reply in MyMemory's shape."""
    return {"responseStatus": status, "responseData": {"translatedText": text}}


class FakeClient:
    """Stands in for PoliteClient. Records every address it was asked for."""

    def __init__(self, replies: Optional[List[Any]] = None,
                 error: Optional[Exception] = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.urls: List[str] = []

    def get_json(self, url: str) -> Dict[str, Any]:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.replies.pop(0) if self.replies else ok("English")


def query_of(url: str) -> Dict[str, List[str]]:
    """The query part of an address, as a mapping."""
    return parse_qs(urlsplit(url).query)


def test_a_japanese_headline_is_asked_for_in_english() -> None:
    client = FakeClient([ok("Cable clips on sale")])
    assert translate_title(client, "ケーブルクリップ セール", "ja") == "Cable clips on sale"
    assert client.urls[0].startswith(f"{ENDPOINT}?")
    query = query_of(client.urls[0])
    assert query["q"] == ["ケーブルクリップ セール"]
    assert query["langpair"] == ["ja|en"]
    assert "de" not in query


def test_each_language_uses_its_own_code() -> None:
    for language, code in (("zh", "zh-TW"), ("ja", "ja"), ("de", "de"), ("fr", "fr")):
        client = FakeClient([ok("English")])
        assert translate_title(client, "x", language) == "English", language
        assert query_of(client.urls[0])["langpair"] == [f"{code}|en"], language


def test_the_optional_email_is_sent_only_when_it_is_given() -> None:
    """It raises the free daily limit from 5,000 to 50,000 characters."""
    with_email = FakeClient([ok("English")])
    translate_title(with_email, "Angebot", "de", email=EMAIL)
    assert query_of(with_email.urls[0])["de"] == [EMAIL]

    without = FakeClient([ok("English")])
    translate_title(without, "Angebot", "de", email=None)
    assert "de" not in query_of(without.urls[0])


def test_english_and_unknown_languages_are_never_sent_anywhere() -> None:
    for language in ("en", "es", "", "EN"):
        client = FakeClient()
        assert translate_title(client, "Sony headphones", language) is None, language
        assert client.urls == [], language


def test_an_empty_headline_is_never_sent_anywhere() -> None:
    client = FakeClient()
    assert translate_title(client, "   ", "ja") is None
    assert client.urls == []


def test_a_long_headline_is_cut_to_a_hundred_and_fifty_characters() -> None:
    """MyMemory limits one ask to 500 bytes, and one Japanese character is three."""
    client = FakeClient([ok("English")])
    translate_title(client, "あ" * 400, "ja")
    assert query_of(client.urls[0])["q"] == ["あ" * 150]


def test_the_quota_warning_is_never_shown_as_a_headline() -> None:
    for reply in (ok(QUOTA), ok(QUOTA, status=403)):
        assert translate_title(FakeClient([reply]), "セール", "ja") is None


def test_a_reply_that_only_echoes_the_original_is_not_a_translation() -> None:
    assert translate_title(FakeClient([ok("  SONY   WH-1000XM6 ")]),
                           "Sony WH-1000XM6", "de") is None


def test_a_refused_or_unusable_reply_gives_nothing() -> None:
    replies: List[Any] = [
        {"responseStatus": 403, "responseData": {"translatedText": "no"}},
        {"responseStatus": 200, "responseData": {"translatedText": ""}},
        {"responseStatus": 200, "responseData": {"translatedText": 7}},
        {"responseStatus": 200, "responseData": "not a mapping"},
        {"responseStatus": 200},
        {},
    ]
    for reply in replies:
        assert translate_title(FakeClient([reply]), "セール", "ja") is None, reply


def test_a_failed_request_gives_nothing_and_never_raises() -> None:
    client = FakeClient(error=FetchError("api.mymemory.translated.net: HTTP 500"))
    assert translate_title(client, "セール", "ja") is None


def test_an_unexpected_client_failure_is_swallowed_too() -> None:
    """The client is injected, so it can raise anything. A headline is never worth a crash."""
    client = FakeClient(error=RuntimeError("https://api.test/get?de=SECRET123 blew up"))
    assert translate_title(client, "セール", "ja") is None


# ---------------------------------------------------------------------------
# Translator: one run's budget, one failure, one log line.
# ---------------------------------------------------------------------------


def test_the_translator_stops_asking_once_its_budget_is_used() -> None:
    client = FakeClient([ok("One"), ok("Two"), ok("Three")])
    translator = Translator(client, None, max_attempts=2)
    assert translator.english_title("セール", "ja") == "One"
    assert translator.english_title("特価", "ja") == "Two"
    assert translator.english_title("値下げ", "ja") is None
    assert len(client.urls) == 2


def test_a_spent_allowance_gives_up_for_the_whole_run_at_once() -> None:
    """There is no point asking 39 more times once the day's allowance is gone."""
    client = FakeClient([ok(QUOTA, status=403), ok("Never asked for")])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("セール", "ja") is None
    assert translator.english_title("特価", "ja") is None
    assert len(client.urls) == 1


def test_an_echoed_reply_is_not_treated_as_a_failure() -> None:
    """An identical answer means the headline was already English enough, not that
    the service is down, so the rest of the run still gets translated."""
    client = FakeClient([ok("Sony WH-1000XM6"), ok("Headphones")])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("Sony WH-1000XM6", "de") is None
    assert translator.english_title("ヘッドホン", "ja") == "Headphones"


def test_the_translator_says_so_once_and_names_nothing_it_should_not(
    caplog: pytest.LogCaptureFixture
) -> None:
    client = FakeClient(error=FetchError("api.mymemory.translated.net: HTTP 500"))
    translator = Translator(client, EMAIL, max_attempts=10)
    with caplog.at_level(logging.INFO):
        translator.english_title("セール品 ヘッドホン", "ja")
        translator.english_title("特価 テレビ", "ja")
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "translation unavailable" in messages[0]
    assert EMAIL not in messages[0]
    assert "セール品" not in messages[0]
    assert "mymemory" not in messages[0].lower()
    assert "https://" not in messages[0]


def test_an_english_source_costs_the_translator_nothing() -> None:
    client = FakeClient([ok("One")])
    translator = Translator(client, None, max_attempts=1)
    assert translator.english_title("Sony headphones", "en") is None
    assert client.urls == []
    assert translator.english_title("セール", "ja") == "One"


def test_the_translator_never_raises_whatever_the_client_does() -> None:
    client = FakeClient(error=RuntimeError("https://api.test/get?de=SECRET123 blew up"))
    translator = Translator(client, EMAIL, max_attempts=3)
    assert translator.english_title("セール", "ja") is None


# ---------------------------------------------------------------------------
# One hiccup is not an outage. MyMemory returns the odd 504, and giving up on
# the whole run for one of those costs every later title for nothing.
# ---------------------------------------------------------------------------


class ScriptedClient(FakeClient):
    """A client that works through a script of replies and exceptions."""

    def get_json(self, url: str) -> Dict[str, Any]:
        self.urls.append(url)
        reply = self.replies.pop(0) if self.replies else ok("English")
        if isinstance(reply, Exception):
            raise reply
        return reply


GATEWAY = FetchError("api.mymemory.translated.net: HTTP 504")


def test_one_failure_then_a_success_keeps_the_run_translating() -> None:
    client = ScriptedClient([GATEWAY, ok("Second title")])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("セール", "ja") is None
    assert translator.english_title("特価", "ja") == "Second title"
    assert len(client.urls) == 2


def test_two_failures_in_a_row_stop_the_run() -> None:
    client = ScriptedClient([GATEWAY, GATEWAY, ok("Never asked for")])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("セール", "ja") is None
    assert translator.english_title("特価", "ja") is None
    assert translator.english_title("値下げ", "ja") is None
    assert len(client.urls) == 2


def test_a_success_clears_the_failure_count() -> None:
    """Two failures with a good one between them is a wobbly service, not a dead one."""
    client = ScriptedClient([GATEWAY, ok("Good"), GATEWAY, ok("Still going")])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("a", "ja") is None
    assert translator.english_title("b", "ja") == "Good"
    assert translator.english_title("c", "ja") is None
    assert translator.english_title("d", "ja") == "Still going"
    assert len(client.urls) == 4


def test_an_unusable_answer_never_stops_the_run() -> None:
    """A refused or empty answer is about that one title, not about the service."""
    client = ScriptedClient([
        {"responseStatus": 413, "responseData": {"translatedText": "too long"}},
        {"responseStatus": 200, "responseData": {"translatedText": ""}},
        ok("Third title"),
    ])
    translator = Translator(client, None, max_attempts=10)
    assert translator.english_title("a", "ja") is None
    assert translator.english_title("b", "ja") is None
    assert translator.english_title("c", "ja") == "Third title"
    assert len(client.urls) == 3


def test_the_log_line_still_appears_once_and_names_the_first_reason(
    caplog: pytest.LogCaptureFixture
) -> None:
    client = ScriptedClient([GATEWAY, GATEWAY, GATEWAY])
    translator = Translator(client, EMAIL, max_attempts=10)
    with caplog.at_level(logging.INFO):
        for text in ("a", "b", "c"):
            translator.english_title(text, "ja")
    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1
    assert "translation unavailable" in messages[0]
    assert "FetchError" in messages[0]
    assert EMAIL not in messages[0] and "https://" not in messages[0]


# ---------------------------------------------------------------------------
# The cut is measured in bytes, because MyMemory's limit is.
# ---------------------------------------------------------------------------


def test_the_text_sent_is_never_more_than_450_bytes() -> None:
    """One emoji is four bytes, so 140 of them overflow a limit counted in characters."""
    client = FakeClient([ok("English")])
    assert translate_title(client, "🎧" * 140, "ja") == "English"
    sent = query_of(client.urls[0])["q"][0]
    assert len(sent.encode("utf-8")) <= 450
    assert "\ufffd" not in sent  # no half a character left at the end


def test_a_cut_never_splits_a_character() -> None:
    for text in ("あ" * 400, "🎧" * 140, "a" * 900, "Ä" * 300):
        client = FakeClient([ok("English")])
        translate_title(client, text, "ja")
        sent = query_of(client.urls[0])["q"][0]
        assert len(sent.encode("utf-8")) <= 450, text[:4]
        assert sent == sent.encode("utf-8").decode("utf-8"), text[:4]
