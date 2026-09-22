"""One full run: read every source, score new posts, send alerts."""

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Callable, List, Mapping, Optional, Tuple

from dealalerts.config import Config
from dealalerts.fetchers import Fetcher, fetch
from dealalerts.http import FetchError, PoliteClient
from dealalerts.models import Deal, RawPost, Source
from dealalerts.notify import NotifyError, chat_id_for, format_message, send_message
from dealalerts.parse import deal_id, parse_fields
from dealalerts.score import score
from dealalerts.store import State
from dealalerts.times import parse_utc
from dealalerts.translate import Translator

logger = logging.getLogger(__name__)

_TITLE_LIMIT = 200
_SEND_GAP_SECONDS = 1.1

# What one delivery attempt did. "skipped" means nothing was tried at all (a dry
# run, or Telegram not set up), so it is neither a success nor a failure.
_SENT = "sent"
_FAILED = "failed"
_SKIPPED = "skipped"


@dataclass(frozen=True)
class RunReport:
    """Counts from one run."""

    fetched: int
    new: int
    skipped: int
    alerts_sent: int
    failures: Tuple[str, ...]
    send_errors: int = 0
    sources_skipped: int = 0


def _deal_identifier(region: str, link: str) -> str:
    """Build the dedup id for a post: its hashed link, scoped to its region.

    The same link posted by two sources in ONE region is one deal (only the
    first alerts). The same link in two different regions is two separate
    deals, because regions are never mixed. This is the only place the id is
    built; ``to_deal`` receives it rather than recomputing it, so there is one
    definition of "same deal" for both deduplication and the stored record.
    """
    return f"{region}-{deal_id(link)}"


def to_deal(post: RawPost, source: Source, now: datetime, identifier: str) -> Deal:
    """Parse a raw post into a Deal. Region always comes from the source.

    ``identifier`` is the region-scoped id already computed by the caller (see
    ``_deal_identifier``), so the same id used for deduplication is stored.
    """
    fields = parse_fields(post.title, post.summary, source.region)
    return Deal(
        id=identifier, region=source.region, source=source.name,
        title=post.title[:_TITLE_LIMIT], link=post.link, shop=fields.shop,
        price_now=fields.price_now, usual_price=fields.usual_price, currency=fields.currency,
        discount_pct=fields.discount_pct, heat=post.heat, posted_at=post.posted_at,
        first_seen=now.isoformat(),
    )


def _translated(deal: Deal, source: Source, translator: Optional[Translator]) -> Deal:
    """Attach an English headline to a deal from a source that is not in English.

    Never raises and never holds anything up: a headline that cannot be
    translated simply stays in its own language. The translator is injected, so
    it could be anything; only an exception's class name is ever logged, because
    its text could quote the address that carries the email.
    """
    if translator is None or source.language == "en":
        return deal
    english: Optional[str] = None
    failure: Optional[str] = None
    try:
        english = translator.english_title(deal.title, source.language)
    except Exception as error:  # noqa: BLE001 - deliberate: see docstring
        failure = type(error).__name__
    if failure is not None:
        logger.warning("A title could not be translated (%s); the original is kept", failure)
        return deal
    return deal if english is None else replace(deal, title_en=english)


def _deliver(client: PoliteClient, env: Mapping[str, str], region: str, text: str,
             dry_run: bool) -> str:
    """Send one message. Returns _SENT, _FAILED or _SKIPPED.

    Logs the message text and the region only. The bot token itself is never logged.
    """
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat = chat_id_for(region, env)
    if dry_run or not token or not chat:
        logger.info("Not sending (dry run or Telegram not set up) to %s:\n%s", region, text)
        return _SKIPPED
    try:
        send_message(client, token, chat, text)
    except NotifyError as error:
        logger.warning("Telegram send to %s failed: %s", region, error)
        return _FAILED
    return _SENT


def _send_one(client: PoliteClient, env: Mapping[str, str], item: Mapping[str, Any],
              now: datetime, dry_run: bool) -> Tuple[str, Optional[str]]:
    """Format and deliver one queued alert.

    Returns (outcome, failure). ``failure`` is the class name of an unexpected
    exception, or None. Only the class name is ever returned: a stored record is
    untrusted text and an arbitrary exception's str() could quote an address that
    carries the Telegram bot key. ``NotifyError`` is already handled inside
    ``_deliver`` and never reaches this handler.
    """
    failure: Optional[str] = None
    outcome = _SKIPPED
    try:
        outcome = _deliver(client, env, item["region"], format_message(item, now), dry_run)
    except Exception as error:  # noqa: BLE001 - deliberate: see docstring
        failure = type(error).__name__
    return outcome, failure


def _is_first_contact(state: State, source_name: str, now: datetime,
                      max_age_hours: int) -> bool:
    """True when a source's whole front page counts as backlog rather than news.

    That is the case the first time a source is read, and again after an outage
    long enough that nothing still in the feed would be fresh enough to alert on.
    """
    last_ok = state.last_success(source_name)
    if last_ok is None:
        return True
    return (now - last_ok).total_seconds() / 3600 > max_age_hours


def _save_progress(checkpoint: Optional[Callable[[], None]]) -> None:
    """Write down what the run has done so far. Never raises.

    Alerts that were delivered but not saved would be sent again on the next
    run, so progress is written after every message that actually went out. A
    checkpoint that fails must not stop the run, and only its exception class
    name is logged (an arbitrary exception's text could quote the bot key).
    """
    if checkpoint is None:
        return
    failure: Optional[str] = None
    try:
        checkpoint()
    except Exception as error:  # noqa: BLE001 - deliberate: see docstring
        failure = type(error).__name__
    if failure is not None:
        logger.warning("Progress could not be saved (%s); the run continues", failure)


def run_once(
    config: Config,
    state: State,
    client: PoliteClient,
    env: Mapping[str, str],
    now: datetime,
    dry_run: bool,
    fetch_fn: Fetcher = fetch,
    sleep: Callable[[float], None] = time.sleep,
    checkpoint: Optional[Callable[[], None]] = None,
    clock: Callable[[], float] = time.monotonic,
    translator: Optional[Translator] = None,
) -> RunReport:
    """Read all sources, record new deals, send pending alerts and source-down notes.

    Args:
        checkpoint: Called after the fetch loop and after every alert that was
            actually delivered, so work already done survives a later crash.
        clock: Elapsed-time source for the fetch budget, injected for tests.
        translator: Optional English-headline lookup. None means every title is
            kept in its own language.
    """
    settings = config.settings
    fetched = new = skipped = sources_skipped = 0
    failures: List[str] = []
    notes: List[Tuple[str, str, str]] = []  # (source name, region, text)
    started = clock()

    for position, source in enumerate(config.sources):
        if clock() - started > settings.fetch_budget_seconds:
            # A slow or hanging set of sources must not eat the whole job timeout
            # and leave no time to send what has already been found. The sources
            # left over are simply not checked: neither a success nor a failure,
            # so their health record is untouched.
            sources_skipped = len(config.sources) - position
            logger.warning(
                "Reading sources took longer than %.0f s: %d sources were not checked this run",
                settings.fetch_budget_seconds, sources_skipped,
            )
            break
        try:
            posts = fetch_fn(source, client)
        except FetchError as error:
            # FetchError text is host-only (see dealalerts.http) so it is safe to log
            # and to store.
            failures.append(source.name)
            logger.warning("Source %s failed: %s", source.name, error)
            if state.record_failure(source.name, str(error), now, settings.failure_note_after):
                notes.append((source.name, source.region,
                              f"Source down: {source.name} has failed "
                              f"{settings.failure_note_after} checks in a row ({error})."))
            continue
        except Exception as error:  # noqa: BLE001 - deliberate: see comment below
            # A fetcher can be backed by an arbitrary library (feed parsing, HTTP
            # clients, etc.) that may raise something other than FetchError. Its
            # str() could contain a full request URL, and that URL could carry the
            # Telegram bot token (it is passed around in the same environment and
            # some libraries echo request context into exception messages). Only the
            # exception type name is ever recorded or logged for this branch, never
            # str(error) and never a traceback, so a leaked secret cannot end up in
            # the state file, the dashboard or the log.
            failures.append(source.name)
            logger.warning("Source %s failed: %s", source.name, type(error).__name__)
            if state.record_failure(
                source.name, type(error).__name__, now, settings.failure_note_after
            ):
                notes.append((source.name, source.region,
                              f"Source down: {source.name} has failed "
                              f"{settings.failure_note_after} checks in a row "
                              f"({type(error).__name__})."))
            continue
        backlog = _is_first_contact(state, source.name, now, settings.max_alert_age_hours)
        state.record_success(source.name, now)
        fetched += len(posts)
        for post in posts:
            try:
                identifier = _deal_identifier(source.region, post.link)
            except Exception as error:  # noqa: BLE001 - deliberate: see comment below
                # A post's link can be missing or otherwise unusable (e.g. a feed
                # entry with link=None). Without a link there is no stable id, so
                # the post can never be deduplicated; it is skipped instead of
                # crashing the whole run, and will be skipped again on later runs
                # for the same reason. Only the exception type name is logged,
                # never str(error) or a traceback, for the same secret-leak reason
                # as the fetch-error handler above.
                skipped += 1
                logger.warning(
                    "Source %s: skipping a post that has no usable id (%s)",
                    source.name, type(error).__name__,
                )
                continue
            if state.is_seen(identifier):
                # Refresh the stamp: a post that sits in a feed for months would
                # otherwise be pruned while still on show, and alert all over again.
                state.mark_seen(identifier, now)
                continue
            state.mark_seen(identifier, now)
            new += 1
            try:
                deal = to_deal(post, source, now, identifier)
                verdict = score(deal, f"{post.title} {post.summary}", source, config, now)
                if verdict.tier != "ignore":
                    # Only what is going to be shown is worth translating, and
                    # the lookup has its own guard, so it can neither raise out
                    # of here nor cost the deal.
                    deal = _translated(deal, source, translator)
            except Exception as error:  # noqa: BLE001 - deliberate: see comment above
                # The id is already marked seen (above), so this post will not be
                # retried and cannot crash the run again on a later pass.
                skipped += 1
                logger.warning(
                    "Source %s: skipping a post that could not be parsed or scored (%s)",
                    source.name, type(error).__name__,
                )
                continue
            if verdict.tier == "ignore":
                continue
            state.add_deal(deal, verdict)
            if backlog and verdict.tier == "alert":
                state.mark_alerted(deal.id, now)

    _save_progress(checkpoint)

    sent = 0
    send_errors = 0
    failures_in_a_row = 0
    for item in state.pending_alerts():
        if sent >= settings.max_alerts_per_run:
            break
        if failures_in_a_row >= settings.max_consecutive_send_failures:
            # Telegram is plainly not answering. Stop rather than burn the rest of
            # the job on it; the alerts left over stay pending for the next run.
            logger.warning("Stopping after %d failed sends in a row; %d alerts stay pending",
                           failures_in_a_row, len(state.pending_alerts()))
            break
        # The state file can be hand-edited, so parse its timestamp defensively
        # instead of calling datetime.fromisoformat directly.
        seen_at = parse_utc(item.get("first_seen")) or now
        age_hours = (now - seen_at).total_seconds() / 3600
        if age_hours > settings.stale_alert_hours:
            state.mark_alerted(item["id"], now)
            continue
        outcome, failure = _send_one(client, env, item, now, dry_run)
        if failure is not None:
            # A record that cannot be formatted now will never become formattable,
            # so it is marked as done instead of blocking the queue every run.
            send_errors += 1
            logger.warning("Alert %s could not be sent (%s); giving up on it",
                           item["id"], failure)
            state.mark_alerted(item["id"], now)
            continue
        if outcome == _FAILED:
            failures_in_a_row += 1
            continue
        if outcome == _SENT:
            failures_in_a_row = 0
            state.mark_alerted(item["id"], now)
            sent += 1
            _save_progress(checkpoint)
            sleep(_SEND_GAP_SECONDS)

    for name, region, text in notes:
        if _deliver(client, env, region, text, dry_run) != _SENT and not dry_run:
            # Delivery failed (or Telegram isn't configured); let the next run try
            # this note again instead of losing it forever.
            state.rearm_failure_note(name)

    return RunReport(
        fetched=fetched, new=new, skipped=skipped, alerts_sent=sent,
        failures=tuple(failures), send_errors=send_errors, sources_skipped=sources_skipped,
    )
