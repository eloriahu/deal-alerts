"""Read every source once and write a results table. Run on GitHub to see what GitHub can reach."""

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dealalerts.config import load_config  # noqa: E402
from dealalerts.fetchers import fetch  # noqa: E402
from dealalerts.http import FetchError, PoliteClient  # noqa: E402

logger = logging.getLogger("dealalerts.probe")

DEFAULT_SOURCES_PATH = ROOT / "sources.yaml"
DEFAULT_CANDIDATES_PATH = ROOT / "scripts" / "candidates.yaml"
DEFAULT_OUTPUT_PATH = ROOT / "docs" / "source-probe-github.md"


def main(
    sources_path: Optional[Path] = None,
    candidates_path: Optional[Path] = None,
    output_path: Optional[Path] = None,
) -> int:
    """Probe every source in the live and candidate lists and write a results table.

    One source failing never stops the probe: a `FetchError` is recorded with its
    host-only message, and any other exception is recorded by its type name only,
    since an arbitrary exception's text could contain a full web address.

    Args:
        sources_path: Path to the live sources file. Defaults to `sources.yaml` at
            the repository root.
        candidates_path: Path to the candidate sources file. Defaults to
            `scripts/candidates.yaml`.
        output_path: Where to write the results table. Defaults to
            `docs/source-probe-github.md`.

    Returns:
        0. A single source failing is recorded in the table, not raised.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sources_path = sources_path or DEFAULT_SOURCES_PATH
    candidates_path = candidates_path or DEFAULT_CANDIDATES_PATH
    output_path = output_path or DEFAULT_OUTPUT_PATH
    client = PoliteClient(gap_seconds=5)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: List[str] = [
        f"# Source probe from GitHub, {stamp}",
        "",
        "| List | Source | Region | Result |",
        "|---|---|---|---|",
    ]
    for label, path in (("live", sources_path), ("candidate", candidates_path)):
        # The candidate list is allowed to run empty once every candidate has
        # been promoted or dropped; the live list is not.
        config = load_config(path, require_sources=(label == "live"))
        for source in config.sources:
            try:
                result = f"works, {len(fetch(source, client))} posts"
            except FetchError as error:
                result = f"FAILED: {error}"
            except Exception as error:  # noqa: BLE001 - a source must never stop the probe
                result = f"FAILED: {type(error).__name__}"
            lines.append(f"| {label} | {source.name} | {source.region} | {result} |")
            logger.info("%s %s: %s", label, source.name, result)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
