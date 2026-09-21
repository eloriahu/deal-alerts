"""Registry of fetchers: one function per source kind."""

from typing import Callable, Dict, List

from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import RawPost, Source

Fetcher = Callable[[Source, PoliteClient], List[RawPost]]
FETCHERS: Dict[str, Fetcher] = {}


def register_fetcher(kind: str) -> Callable[[Fetcher], Fetcher]:
    """Decorator that registers a fetch function for a source kind."""

    def decorator(function: Fetcher) -> Fetcher:
        FETCHERS[kind] = function
        return function

    return decorator


def fetch(source: Source, client: PoliteClient) -> List[RawPost]:
    """Read one source. Raises FetchError if the kind is unknown or the read fails."""
    from dealalerts.fetchers import feed, telegram  # noqa: F401  (registers the fetchers)

    fetcher = FETCHERS.get(source.kind)
    if fetcher is None:
        raise FetchError(f"no fetcher for kind {source.kind!r}")
    return fetcher(source, client)


__all__ = ["FETCHERS", "Fetcher", "fetch", "register_fetcher"]
