"""Turn a foreign-language headline into English, for display only.

MyMemory is a free translation service that needs no sign-up. The English
headline it gives back is shown on the phone message and on the page; it never
reaches scoring, so a translation that is wrong, late or missing can only change
what a deal looks like, never whether it is alerted on.

Nothing here ever raises: a headline is not worth losing a deal over. Nothing
here ever logs the address, the email or the post's text either. The address
carries both the email and the headline in its query, and these logs are public.
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from dealalerts.http import FetchError, PoliteClient

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.mymemory.translated.net/get"

# MyMemory's code for each language sources.yaml uses. A source in English, or
# in a language not listed here, is never sent anywhere at all.
LANGUAGE_CODES: Dict[str, str] = {"zh": "zh-TW", "ja": "ja", "de": "de", "fr": "fr"}

# MyMemory limits one ask to 500 bytes, so the cut is measured in bytes too. A
# limit counted in characters would still overflow: one emoji is four bytes.
BYTE_LIMIT = 450

# The service answers with this in place of a translation once the free daily
# allowance is used up.
QUOTA_MARKER = "MYMEMORY WARNING"

# How many headlines one run may send before it stops on its own.
DEFAULT_MAX_ATTEMPTS = 40

# How many failures in a row mean the service is really down rather than busy.
# MyMemory returns the occasional 504, and giving up the whole run for one of
# those would cost every later headline for nothing.
STOP_AFTER_FAILURES = 2

# What one ask ended up doing.
_DONE = "done"            # an English headline came back
_NOTHING = "nothing"      # no request was made, or the answer was not worth keeping
_QUOTA = "quota"          # the day's allowance is spent: stop at once
_TRANSPORT = "transport"  # the service could not be reached: two in a row stop it
_UNUSABLE = "unusable"    # an answer this tool cannot use: just try the next headline

_UNUSABLE_REASON = "an answer it could not use"


@dataclass(frozen=True)
class _Answer:
    """One ask's result.

    ``reason`` is only ever a fixed phrase or an exception's class name, never
    anything from the reply or the address, because it reaches a public log.
    """

    outcome: str
    english: Optional[str] = None
    reason: str = ""


def _cut(text: str) -> str:
    """The most of the text that fits the service's limit, never splitting a character.

    Cutting the encoded bytes can leave half a character at the end; "ignore"
    drops that remainder rather than raising or leaving a replacement mark.
    """
    return text.encode("utf-8")[:BYTE_LIMIT].decode("utf-8", "ignore").strip()


def _squashed(text: str) -> str:
    """The text with every run of spacing removed and capitals ignored."""
    return "".join(text.split()).casefold()


def _read_reply(reply: Dict[str, Any], asked: str) -> _Answer:
    """Pull the English headline out of one reply."""
    data = reply.get("responseData")
    english = data.get("translatedText") if isinstance(data, dict) else None
    if isinstance(english, str) and QUOTA_MARKER in english.upper():
        return _Answer(_QUOTA, reason=_QUOTA)
    if reply.get("responseStatus") not in (200, "200"):
        return _Answer(_UNUSABLE, reason=_UNUSABLE_REASON)
    if not isinstance(english, str) or not english.strip():
        return _Answer(_UNUSABLE, reason=_UNUSABLE_REASON)
    if _squashed(english) == _squashed(asked):
        # The service handed the headline straight back, so it was already as
        # English as it is going to get. Not a failure of any kind.
        return _Answer(_NOTHING)
    return _Answer(_DONE, english=english.strip())


def _ask(client: PoliteClient, text: str, language: str, email: Optional[str]) -> _Answer:
    """Ask MyMemory for one headline. Never raises.

    Nothing is sent for a source that is already in English, for a language this
    tool has no code for, or for an empty headline.
    """
    code = LANGUAGE_CODES.get(language)
    if code is None or not text.strip():
        return _Answer(_NOTHING)
    asked = _cut(text)
    if not asked:
        return _Answer(_NOTHING)
    query = {"q": asked, "langpair": f"{code}|en"}
    if email:
        query["de"] = email
    reply: Dict[str, Any] = {}
    failure: Optional[str] = None
    try:
        reply = client.get_json(f"{ENDPOINT}?{urlencode(query)}")
    except FetchError as error:
        failure = type(error).__name__
    except Exception as error:  # noqa: BLE001 - deliberate: see module docstring
        # The client is injected and can be backed by any library. An arbitrary
        # exception's str() may quote the full address, and this address holds
        # the email in its query, so only the class name is ever kept.
        failure = type(error).__name__
    if failure is not None:
        return _Answer(_TRANSPORT, reason=failure)
    return _read_reply(reply, asked)


def translate_title(client: PoliteClient, text: str, language: str,
                    email: Optional[str] = None) -> Optional[str]:
    """The English headline for one post, or None if there is not one to show.

    Args:
        client: The polite web client.
        text: The post's headline, cut to BYTE_LIMIT bytes before sending.
        language: The source's language field (en, zh, ja, de, fr).
        email: Optional. Any email raises the free daily allowance from 5,000 to
            50,000 characters. It is a secret and is never logged.

    Returns:
        The English headline, or None when there is nothing to send, the answer
        was unusable, the allowance is spent, or the service could not be
        reached. This function never raises.
    """
    return _ask(client, text, language, email).english


class Translator:
    """One run's worth of translation: a budget, a giving-up point, one log line.

    The state lives here rather than in a module-level variable so that a test
    gets a fresh one by building a fresh object.
    """

    def __init__(self, client: PoliteClient, email: Optional[str] = None,
                 max_attempts: int = DEFAULT_MAX_ATTEMPTS) -> None:
        """Build a translator for one run.

        Args:
            client: The polite web client.
            email: Optional address that raises the free daily allowance.
            max_attempts: How many headlines this run may ask about.
        """
        self._client = client
        self._email = email
        self._left = max_attempts
        self._stopped = False
        self._failures_in_a_row = 0
        self._first_reason: Optional[str] = None

    def _give_up(self) -> None:
        """Stop asking for the rest of this run and say so once."""
        if self._stopped:
            return
        self._stopped = True
        logger.info(
            "translation unavailable (%s); this run keeps the original titles",
            self._first_reason or _UNUSABLE_REASON,
        )

    def english_title(self, text: str, language: str) -> Optional[str]:
        """The English headline for one post, or None. Never raises.

        A spent allowance stops the run at once: there is no point asking again
        today. Anything else has to fail twice in a row before the run gives up,
        because the service returns the odd gateway error while perfectly well.
        An answer that simply cannot be used costs only that one headline.
        """
        if language not in LANGUAGE_CODES or self._stopped or self._left <= 0:
            return None
        self._left -= 1
        answer = _ask(self._client, text, language, self._email)
        if self._first_reason is None and answer.reason:
            self._first_reason = answer.reason
        if answer.outcome == _TRANSPORT:
            self._failures_in_a_row += 1
        elif answer.outcome != _UNUSABLE:
            self._failures_in_a_row = 0
        if answer.outcome == _QUOTA or self._failures_in_a_row >= STOP_AFTER_FAILURES:
            self._give_up()
        return answer.english
