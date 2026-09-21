import pytest

from dealalerts.fetchers.telegram import parse_channel_page
from dealalerts.http import FetchError

PAGE = """<html><body>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="sgdeals/101">
  <div class="tgme_widget_message_text js-message_text" dir="auto">PRICE ERROR Dyson V12 S$99<br/>Usual S$999 at Shopee<br/><a href="https://shopee.sg/x">link</a></div>
  <a class="tgme_widget_message_date" href="https://t.me/sgdeals/101"><time datetime="2026-09-21T04:00:00+00:00" class="time">12:00</time></a>
</div></div>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="sgdeals/102">
  <div class="tgme_widget_message_photo"></div>
</div></div>
</body></html>""".encode("utf-8")


def test_messages_become_posts_and_photo_only_messages_are_skipped() -> None:
    posts = parse_channel_page(PAGE)
    assert len(posts) == 1
    post = posts[0]
    assert post.title == "PRICE ERROR Dyson V12 S$99"
    assert post.link == "https://t.me/sgdeals/101"
    assert "Usual S$999 at Shopee" in post.summary
    assert post.posted_at == "2026-09-21T04:00:00+00:00"
    assert post.heat is None


def test_page_without_messages_is_an_error() -> None:
    with pytest.raises(FetchError, match="no messages"):
        parse_channel_page(b"<html><body>Channel not found</body></html>")


def test_message_with_nonsense_datetime_yields_none_posted_at() -> None:
    page_with_bad_time = """<html><body>
<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="sgdeals/103">
  <div class="tgme_widget_message_text js-message_text" dir="auto">Some deal here</div>
  <a class="tgme_widget_message_date" href="https://t.me/sgdeals/103"><time datetime="nonsense">12:00</time></a>
</div></div>
</body></html>""".encode("utf-8")
    posts = parse_channel_page(page_with_bad_time)
    assert len(posts) == 1
    assert posts[0].posted_at is None
