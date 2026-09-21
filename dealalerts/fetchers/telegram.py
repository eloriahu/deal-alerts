"""Read a public Telegram channel through its preview page, https://t.me/s/<channel>."""

import logging
from typing import List

from bs4 import BeautifulSoup

from dealalerts.fetchers import register_fetcher
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source
from dealalerts.times import parse_utc

logger = logging.getLogger(__name__)

_TITLE_LIMIT = 150


def parse_channel_page(content: bytes) -> List[RawPost]:
    """Turn a channel preview page into posts.

    Raises:
        FetchError: when the page holds no messages (wrong name, or a private channel).
    """
    soup = BeautifulSoup(content, "html.parser")
    messages = soup.select("div.tgme_widget_message[data-post]")
    if not messages:
        raise FetchError("no messages on the Telegram preview page")
    posts: List[RawPost] = []
    for message in messages:
        body = message.select_one("div.tgme_widget_message_text")
        if body is None:
            continue
        lines = [line.strip() for line in body.get_text("\n").split("\n") if line.strip()]
        if not lines:
            continue
        stamp = message.select_one("time[datetime]")
        moment = parse_utc(stamp["datetime"]) if stamp else None
        posted_at = moment.isoformat() if moment else None
        posts.append(
            RawPost(
                title=lines[0][:_TITLE_LIMIT],
                link=f"https://t.me/{message['data-post']}",
                summary=" ".join(lines),
                posted_at=posted_at,
                heat=None,
            )
        )
    return posts


@register_fetcher("telegram")
def fetch_channel(source: Source, client: PoliteClient) -> List[RawPost]:
    """Download and parse one channel preview page."""
    return parse_channel_page(client.get(source.url))
