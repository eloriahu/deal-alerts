"""Parse and normalize timestamps from untrusted sources."""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def parse_utc(stamp: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp to timezone-aware UTC.

    Args:
        stamp: An ISO format timestamp string, None, or empty string.

    Returns:
        A timezone-aware datetime in UTC, or None if the input is missing,
        empty, or cannot be parsed. Naive timestamps are treated as UTC.
    """
    if not stamp:
        return None

    try:
        parsed = datetime.fromisoformat(stamp)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        # Naive result: treat as UTC
        return parsed.replace(tzinfo=timezone.utc)
    else:
        # Aware result: convert to UTC
        return parsed.astimezone(timezone.utc)
