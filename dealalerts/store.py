"""What the tool remembers between runs. Saved as data/state.json and committed by the run."""

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

from dealalerts.models import REGIONS, Deal, Verdict
from dealalerts.times import parse_utc

logger = logging.getLogger(__name__)

# How long one delivered source-down note silences the next one for that source.
NOTE_COOLDOWN_HOURS = 24


def _clean_mapping(value: Any, value_type: Type[Any]) -> Tuple[Dict[str, Any], int]:
    """Keep only str-keyed entries of value whose value matches value_type.

    Returns the cleaned dict and how many entries were dropped. A value that is
    not a dict at all becomes an empty dict, counted as zero dropped entries
    (there is nothing meaningful to count item-by-item).
    """
    if not isinstance(value, dict):
        return {}, 0
    cleaned = {
        key: item for key, item in value.items()
        if isinstance(key, str) and isinstance(item, value_type)
    }
    return cleaned, len(value) - len(cleaned)


def _is_valid_deal(record: Any) -> bool:
    """True if a stored deal has the minimum shape deals_for and build_site rely on."""
    if not isinstance(record, dict):
        return False
    deal_id = record.get("id")
    first_seen = record.get("first_seen")
    return (
        isinstance(deal_id, str) and bool(deal_id)
        and record.get("region") in REGIONS
        and isinstance(first_seen, str) and bool(first_seen)
        and isinstance(record.get("tier"), str)
        and isinstance(record.get("kind"), str)
        # The message builder and the page read these straight out of the record.
        and isinstance(record.get("title"), str)
        and isinstance(record.get("link"), str)
        and isinstance(record.get("source"), str)
    )


class State:
    """Seen ids, alerted ids, deals for the dashboard, and per-source health."""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        """Build state from loaded JSON, keeping only well-formed data.

        A record or entry that does not match the expected shape is dropped
        rather than kept or allowed to raise, since data/state.json is untyped
        and a legacy or hand-edited record could otherwise take down every
        downstream reader (dashboard build, pending-alerts scan, pruning).
        """
        data = data or {}
        seen, seen_dropped = _clean_mapping(data.get("seen", {}), str)
        alerted, alerted_dropped = _clean_mapping(data.get("alerted", {}), str)
        health, health_dropped = _clean_mapping(data.get("health", {}), dict)
        raw_deals = data.get("deals", [])
        if isinstance(raw_deals, list):
            deals = [record for record in raw_deals if _is_valid_deal(record)]
            deals_dropped = len(raw_deals) - len(deals)
        else:
            deals = []
            deals_dropped = 0

        self.seen: Dict[str, str] = seen
        self.alerted: Dict[str, str] = alerted
        self.deals: List[Dict[str, Any]] = deals
        self.health: Dict[str, Dict[str, Any]] = health

        dropped = seen_dropped + alerted_dropped + health_dropped + deals_dropped
        if dropped:
            logger.warning("Dropped %d malformed entries from the saved state", dropped)

    @classmethod
    def load(cls, path: Path) -> "State":
        """Read the state file. A missing file gives an empty state."""
        path = Path(path)
        if not path.exists():
            return cls()

        corrupt = False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                corrupt = True
        except ValueError:
            corrupt = True

        if corrupt:
            logger.warning("Resetting corrupt state file: %s", path)
            corrupt_path = path.with_stem(f"{path.stem}.corrupt")
            try:
                os.replace(path, corrupt_path)
            except OSError:
                logger.warning("Could not move corrupt file %s to %s", path, corrupt_path)
            return cls()

        return cls(data)

    def save(self, path: Path) -> None:
        """Write the state file through a temporary file, so a crash cannot leave half a file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "seen": self.seen, "alerted": self.alerted, "deals": self.deals, "health": self.health,
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, path)

    def is_seen(self, deal_id: str) -> bool:
        """Return True if the deal ID has been seen before."""
        return deal_id in self.seen

    def mark_seen(self, deal_id: str, now: datetime) -> None:
        """Record when a deal ID was first seen."""
        self.seen[deal_id] = now.isoformat()

    def is_alerted(self, deal_id: str) -> bool:
        """Return True if the deal ID has been alerted on."""
        return deal_id in self.alerted

    def mark_alerted(self, deal_id: str, now: datetime) -> None:
        """Record when a deal ID alert was sent."""
        self.alerted[deal_id] = now.isoformat()

    def has_succeeded(self, source_name: str) -> bool:
        """True once a source has been read successfully at least once."""
        return bool(self.health.get(source_name, {}).get("last_ok"))

    def last_success(self, source_name: str) -> Optional[datetime]:
        """When a source was last read successfully, or None if never or unreadable."""
        stamp = self.health.get(source_name, {}).get("last_ok")
        return parse_utc(stamp) if isinstance(stamp, str) else None

    def add_deal(self, deal: Deal, verdict: Verdict) -> None:
        record = asdict(deal)
        record.update(tier=verdict.tier, kind=verdict.kind, reasons=list(verdict.reasons))
        self.deals.append(record)

    def pending_alerts(self) -> List[Dict[str, Any]]:
        """Alert-tier deals that have not been sent yet, oldest first."""
        waiting = [d for d in self.deals if d["tier"] == "alert" and d["id"] not in self.alerted]
        return sorted(waiting, key=lambda d: d["first_seen"])

    def deals_for(self, region: str) -> List[Dict[str, Any]]:
        """One region's deals: suspected glitches first, then newest first."""
        mine = [d for d in self.deals if d["region"] == region]
        mine.sort(key=lambda d: d["first_seen"], reverse=True)
        mine.sort(key=lambda d: d["kind"] != "glitch")
        return mine

    def _health_entry(self, source_name: str) -> Dict[str, Any]:
        """Get or create a health record for a source."""
        return self.health.setdefault(
            source_name,
            {"last_ok": None, "fails": 0, "noted": False, "noted_at": None, "last_error": ""},
        )

    def record_success(self, source_name: str, now: datetime) -> None:
        """Mark a source as successfully read and reset its failure count.

        ``noted_at`` is deliberately left alone: a source that recovers and
        fails again an hour later must not send a second note the same day.
        """
        entry = self._health_entry(source_name)
        entry.update(last_ok=now.isoformat(), fails=0, noted=False, last_error="")

    def _noted_within_a_day(self, entry: Dict[str, Any], now: datetime) -> bool:
        """True if this source's down note was already delivered in the last 24 hours."""
        stamp = entry.get("noted_at")
        noted_at = parse_utc(stamp) if isinstance(stamp, str) else None
        if noted_at is None:
            return False
        return (now - noted_at).total_seconds() < NOTE_COOLDOWN_HOURS * 3600

    def record_failure(self, source_name: str, error: str, now: datetime, note_after: int) -> bool:
        """Count a failure. Returns True at most once per source per 24 hours."""
        entry = self._health_entry(source_name)
        entry["fails"] += 1
        entry["last_error"] = error
        if entry["fails"] < note_after or entry["noted"]:
            return False
        if self._noted_within_a_day(entry, now):
            return False
        entry["noted"] = True
        entry["noted_at"] = now.isoformat()
        return True

    def rearm_failure_note(self, source_name: str) -> None:
        """Allow the source-down note to be sent again (used when delivery failed).

        The note never reached anyone, so the once-a-day limit is cleared too.
        """
        entry = self.health.get(source_name)
        if entry is not None:
            entry["noted"] = False
            entry["noted_at"] = None

    def prune(self, now: datetime, seen_days: int, dashboard_days: int) -> None:
        """Forget ids older than seen_days and dashboard deals older than dashboard_days."""
        seen_cutoff = (now - timedelta(days=seen_days)).isoformat()
        deal_cutoff = (now - timedelta(days=dashboard_days)).isoformat()
        self.seen = {key: stamp for key, stamp in self.seen.items() if stamp >= seen_cutoff}
        self.alerted = {key: stamp for key, stamp in self.alerted.items() if stamp >= seen_cutoff}
        self.deals = [deal for deal in self.deals if deal["first_seen"] >= deal_cutoff]
