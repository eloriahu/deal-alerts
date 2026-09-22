from typing import List

import pytest
import requests

from dealalerts.http import FetchError, PoliteClient


class FakeResponse:
    def __init__(self, status: int = 200, content: bytes = b"ok") -> None:
        self.status_code = status
        self.content = content

    def json(self) -> dict:
        return {"ok": True}


class FakeSession:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.calls: List[str] = []

    def get(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.status)

    def post(self, url: str, **_: object) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(self.status)


class RaisingGetSession:
    """A session whose get() always raises a given exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def get(self, url: str, **_: object) -> FakeResponse:
        raise self._error


class RaisingPostSession:
    """A session whose post() always raises a given exception."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def post(self, url: str, **_: object) -> FakeResponse:
        raise self._error


class JsonRaisingResponse:
    """A response whose json() always raises a given exception."""

    def __init__(self, error: Exception) -> None:
        self.status_code = 200
        self._error = error

    def json(self) -> dict:
        raise self._error


class JsonListResponse:
    """A response whose json() decodes to a list, not a dict."""

    def __init__(self) -> None:
        self.status_code = 200

    def json(self) -> list:
        return ["not", "a", "dict"]


class FixedResponseSession:
    """A session whose get() and post() always return a given response."""

    def __init__(self, response: object) -> None:
        self._response = response

    def get(self, url: str, **_: object) -> object:
        return self._response

    def post(self, url: str, **_: object) -> object:
        return self._response


def test_second_call_to_same_host_waits_for_the_gap() -> None:
    slept: List[float] = []
    times = iter([100.0, 101.0, 101.0])
    client = PoliteClient(3, session=FakeSession(), sleep=slept.append, clock=lambda: next(times))
    client.get("https://www.reddit.com/r/a/.rss")
    client.get("https://www.reddit.com/r/b/.rss")
    assert slept == [2.0]


def test_different_hosts_do_not_wait() -> None:
    slept: List[float] = []
    client = PoliteClient(3, session=FakeSession(), sleep=slept.append, clock=lambda: 100.0)
    client.get("https://a.test/feed")
    client.get("https://b.test/feed")
    assert slept == []


def test_error_status_raises_without_leaking_the_address() -> None:
    client = PoliteClient(0, session=FakeSession(status=403), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get("https://api.test/botSECRET123/sendMessage")
    assert "403" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert "api.test" in str(caught.value)


def test_get_connection_error_raises_without_leaking_the_address_or_chaining() -> None:
    error = requests.ConnectionError(
        "HTTPSConnectionPool: Max retries exceeded with url: /botSECRET123/sendMessage"
    )
    client = PoliteClient(0, session=RaisingGetSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get("https://api.test/botSECRET123/sendMessage")
    assert "api.test" in str(caught.value)
    assert "ConnectionError" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_post_json_timeout_raises_without_leaking_the_address_or_chaining() -> None:
    error = requests.Timeout("Read timed out: https://api.test/botSECRET123/sendMessage")
    client = PoliteClient(0, session=RaisingPostSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_post_json_invalid_json_reply_raises_without_leaking_the_address() -> None:
    error = ValueError("Expecting value: https://api.test/botSECRET123/x")
    response = JsonRaisingResponse(error)
    client = PoliteClient(0, session=FixedResponseSession(response), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})
    assert "invalid JSON reply" in str(caught.value)
    assert "api.test" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_post_json_non_dict_reply_is_an_error() -> None:
    client = PoliteClient(0, session=FixedResponseSession(JsonListResponse()), sleep=lambda _: None)
    with pytest.raises(FetchError, match="invalid JSON reply"):
        client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})


def test_post_json_returns_the_decoded_dict() -> None:
    client = PoliteClient(0, session=FakeSession(), sleep=lambda _: None)
    result = client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})
    assert result == {"ok": True}


class RaisingJsonSession:
    """A session whose post() returns a response whose json() raises."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def post(self, url: str, **_: object) -> object:
        return JsonRaisingResponse(self._error)


def test_get_unexpected_error_raises_without_leaking_the_address_or_chaining() -> None:
    """A library error that is not a RequestException must still become a FetchError
    naming only the host and the exception class, with no chaining."""
    error = RuntimeError("https://api.telegram.org/botSECRET123/sendMessage blew up")
    client = PoliteClient(0, session=RaisingGetSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get("https://api.test/botSECRET123/sendMessage")
    assert "api.test" in str(caught.value)
    assert "RuntimeError" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_post_json_unexpected_error_raises_without_leaking_the_address_or_chaining() -> None:
    error = RuntimeError("https://api.telegram.org/botSECRET123/sendMessage blew up")
    client = PoliteClient(0, session=RaisingPostSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})
    assert "api.test" in str(caught.value)
    assert "RuntimeError" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


def test_post_json_unexpected_decode_error_raises_without_leaking_the_address() -> None:
    """json() can raise something other than ValueError; that must not escape either."""
    error = RuntimeError("decoder broke on https://api.telegram.org/botSECRET123/x")
    client = PoliteClient(0, session=RaisingJsonSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.post_json("https://api.test/botSECRET123/sendMessage", {"a": 1})
    assert "api.test" in str(caught.value)
    assert "RuntimeError" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


# ---------------------------------------------------------------------------
# get_json: the same handling as post_json, for a service that answers on GET.
# The address carries the query, which can hold the optional email, so no
# failure here may ever quote more than the host.
# ---------------------------------------------------------------------------


def test_get_json_returns_the_decoded_dict() -> None:
    client = PoliteClient(0, session=FakeSession(), sleep=lambda _: None)
    assert client.get_json("https://api.test/get?q=x&de=SECRET123") == {"ok": True}


def test_get_json_invalid_json_reply_raises_without_leaking_the_address() -> None:
    error = ValueError("Expecting value: https://api.test/get?de=SECRET123")
    client = PoliteClient(0, session=FixedResponseSession(JsonRaisingResponse(error)),
                          sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get_json("https://api.test/get?de=SECRET123")
    assert "invalid JSON reply" in str(caught.value)
    assert "api.test" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_get_json_non_dict_reply_is_an_error() -> None:
    client = PoliteClient(0, session=FixedResponseSession(JsonListResponse()),
                          sleep=lambda _: None)
    with pytest.raises(FetchError, match="invalid JSON reply"):
        client.get_json("https://api.test/get?de=SECRET123")


def test_get_json_network_failure_raises_without_leaking_the_address() -> None:
    error = requests.Timeout("Read timed out: https://api.test/get?de=SECRET123")
    client = PoliteClient(0, session=RaisingGetSession(error), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get_json("https://api.test/get?de=SECRET123")
    assert "api.test" in str(caught.value)
    assert "Timeout" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_get_json_refuses_a_bad_status_without_leaking_the_address() -> None:
    client = PoliteClient(0, session=FakeSession(status=429), sleep=lambda _: None)
    with pytest.raises(FetchError) as caught:
        client.get_json("https://api.test/get?de=SECRET123")
    assert "HTTP 429" in str(caught.value)
    assert "SECRET123" not in str(caught.value)
