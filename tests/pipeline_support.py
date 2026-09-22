"""Shared fixtures for the pipeline tests.

Not a test module itself: `test_pipeline.py` covers reading sources and
recording deals, `test_pipeline_alerts.py` covers sending alerts and
source-down notes, and both build their state from the helpers here.
"""

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from dealalerts.config import Config, Settings
from dealalerts.http import FetchError
from dealalerts.models import RawPost, Source
from dealalerts.pipeline import RunReport, run_once
from dealalerts.store import State

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SG = Source(name="SG Feed", region="sg", kind="feed", url="https://sg.test/feed")
US = Source(name="US Feed", region="us", kind="feed", url="https://us.test/feed")
CONFIG = Config(
    settings=Settings(failure_note_after=2), sources=(SG, US),
    glitch_words=("price error",), sale_words=(), expired_words=("expired",),
)
ENV = {"TELEGRAM_BOT_TOKEN": "TOKEN", "TG_CHAT_SG": "-1001", "TG_CHAT_US": "-1002"}


# A second config for the translation tests: one English source and one
# Japanese one, so "translate everything that is not English" can be shown to
# hold both ways round in one run.
JP = Source(name="JP Feed", region="jp", kind="feed", url="https://jp.test/feed", language="ja")
TRANSLATING_CONFIG = replace(
    CONFIG, sources=(SG, JP), glitch_words=("price error", "価格ミス"),
)
TRANSLATING_ENV = dict(ENV, TG_CHAT_JP="-1003")


class FakeTranslator:
    """Stands in for Translator. Answers every ask with a marked English headline."""

    def __init__(self, answers: Optional[Dict[str, Optional[str]]] = None) -> None:
        self.asked: List[Tuple[str, str]] = []
        self._answers = answers or {}

    def english_title(self, text: str, language: str) -> Optional[str]:
        self.asked.append((text, language))
        return self._answers.get(text, f"EN: {text}")


class FakeTranslateClient:
    """Stands in for PoliteClient for translation only. Answers each ask in turn."""

    def __init__(self, replies: List[Any]) -> None:
        self.replies = list(replies)
        self.urls: List[str] = []

    def get_json(self, url: str) -> Dict[str, Any]:
        self.urls.append(url)
        reply = self.replies.pop(0) if self.replies else {}
        if isinstance(reply, Exception):
            raise reply
        return reply


def english(text: str) -> Dict[str, Any]:
    """One reply in MyMemory's shape."""
    return {"responseStatus": 200, "responseData": {"translatedText": text}}


def post(number: int, title: str) -> RawPost:
    """A post said to have been put up at NOW."""
    return RawPost(title=title, link=f"https://shop.test/{number}", summary="",
                   posted_at=NOW.isoformat(), heat=None)


def post_at(number: int, title: str, moment: datetime) -> RawPost:
    """A post with an explicit posting time."""
    return RawPost(title=title, link=f"https://shop.test/{number}", summary="",
                   posted_at=moment.isoformat(), heat=None)


class FakeClient:
    """Stands in for PoliteClient. Records what would have gone to Telegram."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: List[Dict[str, Any]] = []

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.ok:
            raise FetchError("api.telegram.org: HTTP 500")
        self.sent.append(payload)
        return {"ok": True}


class ExplodingClient(FakeClient):
    """A client whose post_json raises a non-NotifyError carrying a secret-looking address."""

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage exploded")


class ScriptedClient(FakeClient):
    """A client that follows a script of successes and failures."""

    def __init__(self, outcomes: List[bool]) -> None:
        super().__init__()
        self.outcomes = list(outcomes)
        self.attempts = 0

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.attempts += 1
        succeed = self.outcomes.pop(0) if self.outcomes else True
        if not succeed:
            raise FetchError("api.telegram.org: HTTP 500")
        self.sent.append(payload)
        return {"ok": True}


def make_fetch(feeds: Dict[str, Any]) -> Callable[[Source, Any], List[RawPost]]:
    """Build a fetch function that serves `feeds`, raising anything stored in it."""
    def fake_fetch(source: Source, client: Any) -> List[RawPost]:
        result = feeds[source.name]
        if isinstance(result, Exception):
            raise result
        return result
    return fake_fetch


def run(state: State, client: FakeClient, feeds: Dict[str, Any], now: datetime = NOW,
        dry_run: bool = False) -> RunReport:
    """One pipeline run over `feeds`, with no real sleeping and no network."""
    return run_once(CONFIG, state, client, ENV, now, dry_run,
                    fetch_fn=make_fetch(feeds), sleep=lambda _: None)


def translating_run(state: State, client: FakeClient, feeds: Dict[str, Any],
                    translator: Any, now: datetime = NOW) -> RunReport:
    """One run over TRANSLATING_CONFIG with a translator attached."""
    return run_once(TRANSLATING_CONFIG, state, client, TRANSLATING_ENV, now, False,
                    fetch_fn=make_fetch(feeds), sleep=lambda _: None, translator=translator)


def seed_pending(state: State, client: FakeClient, count: int) -> None:
    """Give the state `count` alert-tier deals waiting to be sent."""
    run(state, FakeClient(), {"SG Feed": [], "US Feed": []})
    posts = [post(100 + index, f"PRICE ERROR item {index} S${index}") for index in range(count)]
    run(state, client, {"SG Feed": posts, "US Feed": []})
