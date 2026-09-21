import pytest

from dealalerts.fetchers.feed import parse_feed
from dealalerts.http import FetchError

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Slickdeals Frontpage RSS Feed</title>
<item>
  <title>The Time Machine (1960, Blu-ray) $9 or less</title>
  <link>https://slickdeals.net/f/123-time-machine?utm_source=rss</link>
  <description>&lt;p&gt;Amazon has it for &lt;b&gt;$9&lt;/b&gt;.&lt;/p&gt; Thumb Score: +17</description>
  <pubDate>Mon, 21 Sep 2026 03:15:00 +0000</pubDate>
</item>
<item>
  <title>No votes here</title>
  <link>https://slickdeals.net/f/124</link>
  <description>Plain text</description>
</item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Jetso Club</title>
<entry>
  <title type="text">大家樂：豬扒撈公仔麵 $32</title>
  <link rel="alternate" href="https://www.jetsoclub.com/2026/09/cafe.html"/>
  <updated>2026-09-21T02:00:00+08:00</updated>
  <content type="html">詳情</content>
</entry>
</feed>""".encode("utf-8")


def test_rss_items_become_posts_with_votes_and_clean_summary() -> None:
    posts = parse_feed(RSS)
    assert len(posts) == 2
    first = posts[0]
    assert first.title == "The Time Machine (1960, Blu-ray) $9 or less"
    assert first.link == "https://slickdeals.net/f/123-time-machine?utm_source=rss"
    assert first.heat == 17
    assert "<" not in first.summary and "Amazon has it for $9" in first.summary
    assert first.posted_at == "2026-09-21T03:15:00+00:00"
    assert posts[1].heat is None and posts[1].posted_at is None


def test_atom_entries_are_read_and_times_become_utc() -> None:
    posts = parse_feed(ATOM)
    assert posts[0].title == "大家樂：豬扒撈公仔麵 $32"
    assert posts[0].link == "https://www.jetsoclub.com/2026/09/cafe.html"
    assert posts[0].posted_at == "2026-09-20T18:00:00+00:00"


def test_a_web_page_instead_of_a_feed_is_an_error() -> None:
    with pytest.raises(FetchError, match="not a feed"):
        parse_feed(b"<!DOCTYPE html><html><body>Access denied</body></html>")
