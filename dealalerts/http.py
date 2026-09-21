"""A web client that waits between calls to the same host and never leaks addresses."""

import logging
import time
from typing import Any, Callable, Dict, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; deal-alerts/0.1; +https://github.com/eloriahu/deal-alerts)"
TIMEOUT_SECONDS = 20


class FetchError(Exception):
    """A source could not be read. The message never contains a full address."""


class PoliteClient:
    """Wraps requests. Keeps a minimum gap between calls to one host."""

    def __init__(
        self,
        gap_seconds: float,
        session: Optional[Any] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._gap = gap_seconds
        self._session = session or requests.Session()
        self._sleep = sleep
        self._clock = clock
        self._last_call: Dict[str, float] = {}

    def _wait_turn(self, host: str) -> None:
        """Sleep just long enough that this host has not been called too recently."""
        last = self._last_call.get(host)
        now = self._clock()
        if last is not None and now - last < self._gap:
            self._sleep(self._gap - (now - last))
            now = last + self._gap
        self._last_call[host] = now

    def _call(self, method: str, url: str, **kwargs: Any) -> Any:
        """Make one polite request. Raises FetchError (host only) on any failure."""
        host = urlsplit(url).netloc
        self._wait_turn(host)
        failure: Optional[str] = None
        response: Any = None
        try:
            response = getattr(self._session, method)(
                url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_SECONDS, **kwargs
            )
        except requests.RequestException as error:
            failure = f"{host}: {type(error).__name__}"
        except Exception as error:  # noqa: BLE001 - deliberate: see comment below
            # A session can be backed by any library, and an arbitrary exception's
            # str() may quote the full request address, which for Telegram carries
            # the bot key. Only the class name is ever recorded.
            failure = f"{host}: {type(error).__name__}"
        if failure is not None:
            raise FetchError(failure)
        if response.status_code >= 400:
            raise FetchError(f"{host}: HTTP {response.status_code}")
        return response

    def get(self, url: str) -> bytes:
        """Fetch a page or feed. Raises FetchError on any failure."""
        return self._call("get", url).content

    def post_json(self, url: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send JSON, return the JSON reply. Raises FetchError on any failure."""
        response = self._call("post", url, json=payload)
        host = urlsplit(url).netloc
        failure: Optional[str] = None
        decoded: Any = None
        try:
            decoded = response.json()
        except ValueError:
            failure = f"{host}: invalid JSON reply"
        except Exception as error:  # noqa: BLE001 - deliberate: same reason as _call
            failure = f"{host}: {type(error).__name__}"
        if failure is not None:
            raise FetchError(failure)
        if not isinstance(decoded, dict):
            raise FetchError(f"{host}: invalid JSON reply")
        return decoded
