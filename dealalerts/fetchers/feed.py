"""Read RSS, Atom and RDF feeds. Reddit's feeds are Atom, so they come through here too."""

import html
import logging
import re
from datetime import datetime, timezone
from time import struct_time
from typing import List, Optional

import feedparser

from dealalerts.fetchers import register_fetcher
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_THUMB_SCORE = re.compile(r"Thumb Score:\s*\+?(-?\d+)")


def _plain_text(markup: str) -> str:
    return _SPACE.sub(" ", html.unescape(_TAG.sub(" ", markup))).strip()


def _to_iso(parsed_time: Optional[struct_time]) -> Optional[str]:
    if parsed_time is None:
        return None
    return datetime(*parsed_time[:6], tzinfo=timezone.utc).isoformat()


def parse_feed(content: bytes) -> List[RawPost]:
    """Turn feed bytes into posts.

    Raises:
        FetchError: when the bytes are not a feed (for example a block page).
    """
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise FetchError("not a feed")
    posts: List[RawPost] = []
    for entry in parsed.entries:
        title = _plain_text(entry.get("title", ""))
        link = entry.get("link", "")
        if not title or not link:
            continue
        summary = _plain_text(entry.get("summary", ""))
        # Slickdeals puts "Thumb Score: +N" in content:encoded, not in the description,
        # so the vote count is looked for in every text field the entry carries.
        extra = " ".join(_plain_text(c.get("value", "")) for c in entry.get("content", []))
        votes = _THUMB_SCORE.search(summary) or _THUMB_SCORE.search(extra)
        posts.append(
            RawPost(
                title=title,
                link=link,
                summary=summary,
                posted_at=_to_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
                heat=int(votes.group(1)) if votes else None,
            )
        )
    return posts


@register_fetcher("feed")
def fetch_feed(source: Source, client: PoliteClient) -> List[RawPost]:
    """Download and parse one feed."""
    return parse_feed(client.get(source.url))
