from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import pytest

from dealalerts.http import FetchError
from dealalerts.notify import NotifyError, chat_id_for, format_message, money, send_message

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
FULL = {
    "id": "a", "region": "sg", "source": "SingPromos", "title": "Sony WH-1000XM6 & case",
    "link": "https://x.test/1", "shop": "Amazon.sg", "price_now": 89.0, "usual_price": 549.0,
    "currency": "SGD", "discount_pct": 83.8, "heat": None,
    "posted_at": (NOW - timedelta(minutes=6)).isoformat(), "first_seen": NOW.isoformat(),
    "tier": "alert", "kind": "glitch", "reasons": ['glitch word "price error"', "84% off"],
}


class FakeClient:
    def __init__(self, reply: Dict[str, Any] = None, error: Exception = None) -> None:
        self.reply = reply if reply is not None else {"ok": True}
        self.error = error
        self.calls: List[Dict[str, Any]] = []

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.calls.append({"url": url, "payload": payload})
        if self.error:
            raise self.error
        return self.reply


def test_money_formats_each_currency() -> None:
    assert money("SGD", 89.0) == "S$89"
    assert money("USD", 199.99) == "US$199.99"
    assert money("JPY", 1073.0) == "¥1,073"
    assert money("EUR", 1070.0) == "€1,070"
    assert money(None, 5.5) == "5.5"


def test_full_message_matches_the_spec_layout() -> None:
    assert format_message(FULL, NOW) == (
        "<b>PRICE ERROR?</b>  Sony WH-1000XM6 &amp; case\n"
        "S$89 (usual S$549)  -84%\n"
        "Shop: Amazon.sg\n"
        'Why: glitch word "price error"; 84% off\n'
        "Source: SingPromos, posted 6 min ago\n"
        "https://x.test/1"
    )


def test_missing_fields_drop_their_lines_and_headline_follows_kind() -> None:
    bare = dict(FULL, shop=None, price_now=None, usual_price=None, discount_pct=None,
                kind="heat", reasons=["75 votes in 30 min"],
                posted_at=(NOW - timedelta(hours=3)).isoformat())
    assert format_message(bare, NOW) == (
        "<b>HEATING UP</b>  Sony WH-1000XM6 &amp; case\n"
        "Why: 75 votes in 30 min\n"
        "Source: SingPromos, posted 3 h ago\n"
        "https://x.test/1"
    )
    assert format_message(dict(FULL, kind="discount"), NOW).startswith("<b>BIG DROP</b>")


def test_test_chat_overrides_every_region() -> None:
    env = {"TG_CHAT_SG": "-1001", "TG_CHAT_US": "-1002"}
    assert chat_id_for("sg", env) == "-1001"
    assert chat_id_for("hk", env) is None
    assert chat_id_for("sg", dict(env, TG_CHAT_TEST="-1009")) == "-1009"
    assert chat_id_for("hk", dict(env, TG_CHAT_TEST="-1009")) == "-1009"


def test_send_posts_html_to_the_chat() -> None:
    client = FakeClient()
    send_message(client, "TOKEN", "-1001", "hello")
    call = client.calls[0]
    assert call["url"] == "https://api.telegram.org/botTOKEN/sendMessage"
    assert call["payload"] == {"chat_id": "-1001", "text": "hello", "parse_mode": "HTML"}


def test_refusal_and_network_failure_raise_without_the_key() -> None:
    with pytest.raises(NotifyError, match="chat not found"):
        send_message(FakeClient(reply={"ok": False, "description": "chat not found"}),
                     "TOKEN", "-1", "x")
    with pytest.raises(NotifyError) as caught:
        send_message(FakeClient(error=FetchError("api.telegram.org: HTTP 400")), "TOKEN", "-1", "x")
    assert "TOKEN" not in str(caught.value)


@pytest.mark.parametrize(
    ("status", "hint"),
    [
        (400, "chat id may be wrong, or the bot is not in that channel"),
        (401, "the bot key is wrong"),
        (403, "the bot was removed from the channel or is not an administrator"),
        (404, "the bot key is wrong"),
    ],
)
def test_a_bare_status_number_becomes_a_plain_hint(status: int, hint: str) -> None:
    """The owner does not read HTTP status numbers, so say what to check."""
    client = FakeClient(error=FetchError(f"api.telegram.org: HTTP {status}"))
    with pytest.raises(NotifyError) as caught:
        send_message(client, "TOKEN", "-1", "x")
    text = str(caught.value)
    assert hint in text
    assert "TOKEN" not in text
    assert "https://" not in text and "/bot" not in text
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_a_status_with_no_known_hint_gets_none_invented() -> None:
    client = FakeClient(error=FetchError("api.telegram.org: HTTP 500"))
    with pytest.raises(NotifyError) as caught:
        send_message(client, "TOKEN", "-1", "x")
    assert str(caught.value) == "api.telegram.org: HTTP 500"


def test_garbage_posted_at_falls_back_to_first_seen() -> None:
    """Test that garbage posted_at does not raise and falls back to first_seen."""
    deal = dict(FULL, posted_at="garbage")
    msg = format_message(deal, NOW)
    # Should not raise and Source line should end with "posted 0 min ago"
    # since first_seen equals NOW in FULL fixture
    assert msg.endswith("Source: SingPromos, posted 0 min ago\nhttps://x.test/1")


def test_posted_at_with_no_timezone_six_minutes_ago() -> None:
    """Test that posted_at without timezone is handled correctly."""
    # Build a time 6 minutes before NOW with no timezone
    posted_naive = (NOW - timedelta(minutes=6)).replace(tzinfo=None).isoformat()
    deal = dict(FULL, posted_at=posted_naive)
    msg = format_message(deal, NOW)
    # Should show "posted 6 min ago"
    assert "posted 6 min ago" in msg


# ---------------------------------------------------------------------------
# English headlines. Display only: the original is always kept underneath.
# ---------------------------------------------------------------------------


def test_the_english_headline_leads_with_the_original_underneath() -> None:
    deal = dict(FULL, title="【価格ミス】ソニー ヘッドホン", title_en="Sony headphones price error")
    assert format_message(deal, NOW).startswith(
        "<b>PRICE ERROR?</b>  Sony headphones price error\n"
        "<i>【価格ミス】ソニー ヘッドホン</i>\n"
    )


def test_a_deal_with_no_english_headline_reads_exactly_as_before() -> None:
    assert format_message(dict(FULL, title_en=None), NOW) == format_message(FULL, NOW)


def test_a_record_saved_before_english_headlines_still_formats() -> None:
    """Records already in data/state.json have no such key at all."""
    assert "title_en" not in FULL
    assert format_message(FULL, NOW).startswith(
        "<b>PRICE ERROR?</b>  Sony WH-1000XM6 &amp; case\nS$89"
    )


def test_an_english_headline_equal_to_the_original_is_not_repeated() -> None:
    assert "<i>" not in format_message(dict(FULL, title_en=FULL["title"]), NOW)


def test_a_hostile_english_headline_is_escaped() -> None:
    deal = dict(FULL, title="<b>original</b>", title_en="<script>alert(1)</script>")
    message = format_message(deal, NOW)
    assert "<script>" not in message
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in message
    assert "<i>&lt;b&gt;original&lt;/b&gt;</i>" in message
