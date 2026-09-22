"""Build and send Telegram messages. The bot key never reaches a log or an error message."""

import html
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from dealalerts.http import FetchError, PoliteClient
from dealalerts.times import parse_utc

logger = logging.getLogger(__name__)

_SYMBOLS: Dict[str, str] = {
    "SGD": "S$", "HKD": "HK$", "USD": "US$", "GBP": "£", "EUR": "€", "JPY": "¥",
}
_HEADLINES: Dict[str, str] = {
    "glitch": "PRICE ERROR?", "discount": "BIG DROP", "heat": "HEATING UP",
}


_STATUS = re.compile(r"HTTP (\d{3})\b")
_STATUS_HINTS: Dict[int, str] = {
    400: "chat id may be wrong, or the bot is not in that channel",
    401: "the bot key is wrong",
    403: "the bot was removed from the channel or is not an administrator",
    404: "the bot key is wrong",
}


class NotifyError(Exception):
    """A Telegram message could not be delivered."""


def _hint_for(failure: str) -> str:
    """Turn Telegram's bare status number into plain words, or return ''.

    The owner is not a programmer, so "HTTP 401" on its own is no use. The
    address is never included: it carries the bot key.
    """
    match = _STATUS.search(failure)
    if match is None:
        return ""
    hint = _STATUS_HINTS.get(int(match.group(1)))
    return f" ({hint})" if hint else ""


def money(currency: Optional[str], amount: float) -> str:
    """Format an amount with its currency symbol, without pointless decimals."""
    number = f"{amount:,.2f}".rstrip("0").rstrip(".")
    return f"{_SYMBOLS.get(currency or '', '')}{number}"


def _age(deal: Mapping[str, Any], now: datetime) -> str:
    posted = parse_utc(deal.get("posted_at")) or parse_utc(deal.get("first_seen")) or now
    minutes = max(0, round((now - posted).total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} min ago"
    if minutes < 1440:
        return f"{round(minutes / 60)} h ago"
    return f"{round(minutes / 1440)} d ago"


def _price_line(deal: Mapping[str, Any]) -> Optional[str]:
    parts: List[str] = []
    if deal.get("price_now") is not None:
        price = money(deal.get("currency"), deal["price_now"])
        if deal.get("usual_price") is not None:
            price += f" (usual {money(deal.get('currency'), deal['usual_price'])})"
        parts.append(price)
    if deal.get("discount_pct") is not None:
        parts.append(f"-{deal['discount_pct']:.0f}%")
    return "  ".join(parts) if parts else None


def format_message(deal: Mapping[str, Any], now: datetime) -> str:
    """Build the alert text in Telegram's HTML style. Lines with no data are left out.

    The headline is in English when the run managed to translate it, with the
    original kept on the next line so nothing is lost in the translation. A
    record saved before English headlines existed has no such key at all.
    """
    escape = lambda text: html.escape(str(text), quote=False)  # noqa: E731
    english = deal.get("title_en")
    headline = english or deal["title"]
    lines = [f"<b>{_HEADLINES.get(deal.get('kind', ''), 'DEAL')}</b>  {escape(headline)}"]
    if english and english != deal["title"]:
        lines.append(f"<i>{escape(deal['title'])}</i>")
    price_line = _price_line(deal)
    if price_line:
        lines.append(escape(price_line))
    if deal.get("shop"):
        lines.append(f"Shop: {escape(deal['shop'])}")
    if deal.get("reasons"):
        lines.append(f"Why: {escape('; '.join(deal['reasons']))}")
    lines.append(f"Source: {escape(deal['source'])}, posted {_age(deal, now)}")
    lines.append(escape(deal["link"]))
    return "\n".join(lines)


def chat_id_for(region: str, env: Mapping[str, str]) -> Optional[str]:
    """Chat id for a region. TG_CHAT_TEST, when set, takes every region's messages."""
    return env.get("TG_CHAT_TEST") or env.get(f"TG_CHAT_{region.upper()}") or None


def send_message(client: PoliteClient, token: str, chat_id: str, text: str) -> None:
    """Send one message.

    Raises:
        NotifyError: if Telegram cannot be reached or refuses the message.
    """
    failure: Optional[str] = None
    reply: Dict[str, Any] = {}
    try:
        reply = client.post_json(
            f"https://api.telegram.org/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
    except FetchError as error:
        failure = f"{error}{_hint_for(str(error))}"
    if failure is not None:
        raise NotifyError(failure)
    if not reply.get("ok"):
        raise NotifyError(f"Telegram refused: {reply.get('description', 'no reason given')}")
