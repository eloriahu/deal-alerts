"""Command-line entry point. Run with: uv run python run.py --dry-run"""

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from dealalerts.config import Config, Settings, load_config
from dealalerts.http import PoliteClient
from dealalerts.models import REGION_LABELS, REGIONS
from dealalerts.notify import NotifyError, chat_id_for, send_message
from dealalerts.pipeline import run_once
from dealalerts.site import build_site
from dealalerts.store import State
from dealalerts.translate import Translator

logger = logging.getLogger("dealalerts.run")

# The health entry that keeps the "cannot read sources.yaml" warning to one a day.
CONFIG_HEALTH_KEY = "sources.yaml"


def _send_test_messages(client: PoliteClient) -> int:
    """Send one line to every region's chat, to prove Telegram delivery. Returns failures."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("TELEGRAM_BOT_TOKEN is not set.")
        return len(REGIONS)
    failed = 0
    for region in REGIONS:
        chat = chat_id_for(region, os.environ)
        if not chat:
            logger.error("No chat id for %s.", REGION_LABELS[region])
            failed += 1
            continue
        try:
            send_message(client, token, chat, f"Deal Alerts test for {REGION_LABELS[region]}. It works.")
            logger.info("Test message sent for %s.", REGION_LABELS[region])
        except NotifyError as error:
            logger.error("Test message for %s failed: %s", REGION_LABELS[region], error)
            failed += 1
    return failed


def _read_config(path: Path) -> Tuple[Optional[Config], Optional[str]]:
    """Load sources.yaml. Returns (config, problem); exactly one of them is set.

    ``load_config``'s ValueError text is written by this project and never
    quotes the file's contents, so it is safe to log and to pass on.
    """
    problem: Optional[str] = None
    config: Optional[Config] = None
    try:
        config = load_config(path)
    except ValueError as error:
        problem = str(error)
    return config, problem


def _first_chat() -> Optional[str]:
    """The first Telegram chat that is set up, in region order."""
    for region in REGIONS:
        chat = chat_id_for(region, os.environ)
        if chat:
            return chat
    return None


def _send_config_warning(message: str) -> bool:
    """Send one line about the unreadable file. Returns True if it went out."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = _first_chat()
    if not token or not chat:
        logger.error("No Telegram chat is set up, so that could not be sent.")
        return False
    client = PoliteClient(Settings().same_host_gap_seconds)
    failure: Optional[str] = None
    try:
        send_message(client, token, chat, message)
    except NotifyError as error:
        failure = str(error)
    if failure is not None:
        logger.error("That warning could not be sent: %s", failure)
        return False
    return True


def _report_config_problem(args: argparse.Namespace, problem: str) -> int:
    """Log the problem and tell the owner once a day. Always returns exit code 1."""
    message = (
        "Deal Alerts could not read sources.yaml, so nothing is being checked. "
        f"Undo your last edit to that file. Details: {problem}"
    )
    logger.error("%s", message)
    if args.dry_run:
        return 1
    now = datetime.now(timezone.utc)
    state = State.load(Path(args.state))
    if state.record_failure(CONFIG_HEALTH_KEY, problem, now, note_after=1):
        if not _send_config_warning(message):
            # Nobody saw it, so let the next run try again.
            state.rearm_failure_note(CONFIG_HEALTH_KEY)
    state.save(Path(args.state))
    return 1


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    """Read the command line."""
    parser = argparse.ArgumentParser(description="Scan deal sources and send alerts.")
    parser.add_argument("--dry-run", action="store_true", help="send nothing, save nothing")
    parser.add_argument("--test-message", action="store_true", help="send a test line to every chat")
    parser.add_argument("--config", default="sources.yaml")
    parser.add_argument("--state", default="data/state.json")
    parser.add_argument("--out", default="public")
    return parser.parse_args(argv)


def _test_message_run(args: argparse.Namespace, client: PoliteClient) -> int:
    """Handle --test-message. Returns the process exit code."""
    if args.dry_run:
        # --dry-run must send nothing, so skip _send_test_messages entirely.
        # Region labels only: never a chat id, never the token.
        logger.info(
            "Dry run: a test message would be sent to every region: %s.",
            ", ".join(REGION_LABELS[region] for region in REGIONS),
        )
        return 0
    return 1 if _send_test_messages(client) else 0


def _scan(args: argparse.Namespace, config: Config, client: PoliteClient, state: State,
          now: datetime) -> int:
    """Run the pipeline once, save the state and rebuild the page."""
    state_path = Path(args.state)
    # Save as the run goes, so an alert that was delivered is never sent twice
    # because the run died before its final save. A dry run saves nothing.
    checkpoint = None if args.dry_run else (lambda: state.save(state_path))
    # MYMEMORY_EMAIL is optional: with it the free daily translation allowance is
    # ten times larger. It is a secret, so it is read here and never logged.
    translator = Translator(client, os.environ.get("MYMEMORY_EMAIL") or None,
                            config.settings.max_translations_per_run)
    report = run_once(config, state, client, os.environ, now, args.dry_run,
                      checkpoint=checkpoint, translator=translator)
    state.prune(now, config.settings.seen_days, config.settings.dashboard_days)
    if not args.dry_run:
        state.save(state_path)
    build_site(state, config, now, Path(args.out))
    logger.info(
        "Read %d posts, %d new, %d posts skipped, %d alerts sent, %d alerts given up on, "
        "%d sources not reached in time, failing sources: %s",
        report.fetched, report.new, report.skipped, report.alerts_sent, report.send_errors,
        report.sources_skipped, ", ".join(report.failures) or "none",
    )
    return 0


def _save_after_failure(args: argparse.Namespace, state: Optional[State]) -> None:
    """Try to keep what the failed run already learned. Never raises.

    Only the exception class name is logged, never its text: an arbitrary
    exception could quote an address carrying the Telegram bot key.
    """
    if state is None or args.dry_run:
        return
    failure: Optional[str] = None
    try:
        state.save(Path(args.state))
    except Exception as error:  # noqa: BLE001 - deliberate: see docstring
        failure = type(error).__name__
    if failure is not None:
        logger.error("The state file could not be saved (%s).", failure)


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and run the pipeline once.

    Any unexpected failure is reported by class name only and gives exit code 1.
    No traceback is ever printed, because the logs of a public repository are
    public and an exception's text could quote an address holding the bot key.
    ``KeyboardInterrupt`` and ``SystemExit`` are not caught.
    """
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    state: Optional[State] = None
    failure: Optional[str] = None
    try:
        config, problem = _read_config(Path(args.config))
        if config is None:
            return _report_config_problem(args, problem or "it could not be read")
        client = PoliteClient(config.settings.same_host_gap_seconds)
        if args.test_message:
            return _test_message_run(args, client)
        now = datetime.now(timezone.utc)
        state = State.load(Path(args.state))
        return _scan(args, config, client, state, now)
    except Exception as error:  # noqa: BLE001 - deliberate: see docstring
        failure = type(error).__name__
    logger.error("The run stopped on an unexpected problem (%s). Nothing else was done.", failure)
    _save_after_failure(args, state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
