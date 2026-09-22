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
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode

from dealalerts.http import FetchError, PoliteClient

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.mymemory.translated.net/get"

# MyMemory's code for each language sources.yaml uses. A source in English, or
# in a language not listed here, is never sent anywhere at all.
LANGUAGE_CODES: Dict[str, str] = {"zh": "zh-TW", "ja": "ja", "de": "de", "fr": "fr"}

# MyMemory limits one ask to 500 bytes. A Japanese or Chinese character is three
# bytes in UTF-8, so 150 characters stays inside the limit whatever the script.
TEXT_LIMIT = 150

# The service answers with this in place of a translation once the free daily
# allowance is used up.
QUOTA_MARKER = "MYMEMORY WARNING"

# How many titles one run may ask for before it stops on its own.
DEFAULT_MAX_ATTEMPTS = 40

_QUOTA = "quota"
_UNUSABLE = "an answer it could not use"


def _squashed(text: str) -> str:
    """The text with every run of spacing removed and capitals ignored."""
    return "".join(text.split()).casefold()


def _read_reply(reply: Dict[str, Any], asked: str) -> Tuple[Optional[str], Optional[str]]:
    """Pull the English headline out of one reply.

    Returns (headline, failure). A failure is a short, fixed phrase; nothing from
    the reply itself is ever passed back, because it goes into a public log.
    Both values are None when there was simply nothing worth keeping.
    """
    data = reply.get("responseData")
    english = data.get("translatedText") if isinstance(data, dict) else None
    if isinstance(english, str) and QUOTA_MARKER in english.upper():
        return None, _QUOTA
    if reply.get("responseStatus") not in (200, "200"):
        return None, _UNUSABLE
    if not isinstance(english, str) or not english.strip():
        return None, _UNUSABLE
    if _squashed(english) == _squashed(asked):
        # The service handed the headline straight back, so it was already as
        # English as it is going to get. Not a failure: the run carries on.
        return None, None
    return english.strip(), None


def _ask(client: PoliteClient, text: str, language: str,
         email: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Ask MyMemory for one headline. Returns (headline, failure). Never raises.

    Nothing is sent for a source that is already in English, for a language this
    tool has no code for, or for an empty headline.
    """
    code = LANGUAGE_CODES.get(language)
    if code is None or not text.strip():
        return None, None
    asked = text[:TEXT_LIMIT]
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
        return None, failure
    return _read_reply(reply, asked)


def translate_title(client: PoliteClient, text: str, language: str,
                    email: Optional[str] = None) -> Optional[str]:
    """The English headline for one post, or None if there is not one to show.

    Args:
        client: The polite web client.
        text: The post's headline, cut to TEXT_LIMIT characters before sending.
        language: The source's language field (en, zh, ja, de, fr).
        email: Optional. Any email raises the free daily allowance from 5,000 to
            50,000 characters. It is a secret and is never logged.

    Returns:
        The English headline, or None when there is nothing to send, the answer
        was unusable, the allowance is spent, or the service could not be
        reached. This function never raises.
    """
    return _ask(client, text, language, email)[0]


class Translator:
    """One run's worth of translation: a budget, one giving-up point, one log line.

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

    def english_title(self, text: str, language: str) -> Optional[str]:
        """The English headline for one post, or None. Never raises.

        The first failure stops every later ask in this run: the usual cause is
        the daily allowance running out, and asking 40 more times would only
        slow the run down for nothing.
        """
        if language not in LANGUAGE_CODES or self._stopped or self._left <= 0:
            return None
        self._left -= 1
        english, failure = _ask(self._client, text, language, self._email)
        if failure is not None:
            self._stopped = True
            logger.info(
                "translation unavailable (%s); this run keeps the original titles", failure
            )
        return english
