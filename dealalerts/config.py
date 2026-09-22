"""Load and validate sources.yaml."""

import difflib
import logging
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple, get_type_hints

import yaml

from dealalerts.models import KNOWN_KINDS, REGIONS, Source

logger = logging.getLogger(__name__)

# Every top-level section sources.yaml may hold. Anything else is a typo.
KNOWN_SECTIONS: Tuple[str, ...] = (
    "settings", "glitch_words", "sale_words", "ignore_words", "in_store_words",
    "expired_words", "sources",
)

# The kind of a setting that holds a list of text, such as online_only_regions.
# YAML gives a list; the Settings field keeps a tuple, so it is converted on the
# way in and the loaded settings stay immutable like every other record here.
_TEXT_LIST = Tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    """Thresholds and limits. Defaults match the design spec."""

    alert_discount: float = 70
    dashboard_discount: float = 40
    heat_window_minutes: int = 180
    seen_days: int = 30
    dashboard_days: int = 7
    failure_note_after: int = 5
    same_host_gap_seconds: float = 3
    max_alerts_per_run: int = 15
    stale_alert_hours: int = 6
    max_consecutive_send_failures: int = 3
    fetch_budget_seconds: float = 300
    max_alert_age_hours: int = 24
    # How many headlines one run may send for translation. The free allowance is
    # a few thousand characters a day, shared by every run.
    max_translations_per_run: int = 40
    # How many deals already on the page may have a second try at an English
    # headline each run, for the ones stored while the service was unreachable.
    retranslate_per_run: int = 5
    # Regions where a deal that can only be taken in a shop or a restaurant is
    # no use, so it is dropped. Singapore is left out: that is where she is.
    online_only_regions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    """Everything read from sources.yaml."""

    settings: Settings
    sources: Tuple[Source, ...]
    glitch_words: Tuple[str, ...]
    sale_words: Tuple[str, ...]
    expired_words: Tuple[str, ...]
    # Posts containing one of these are never judged on their percentage: the
    # percentage belongs to a sign-up offer, a lottery or a similar teaser.
    ignore_words: Tuple[str, ...] = ()
    # Posts containing one of these name a shop floor or a dining room. In the
    # regions listed under online_only_regions they are dropped outright.
    in_store_words: Tuple[str, ...] = ()


def _flatten(words_by_language: Dict[str, Iterable[str]]) -> Tuple[str, ...]:
    """Merge per-language word lists into one casefolded tuple."""
    merged = []
    for words in words_by_language.values():
        merged.extend(word.casefold() for word in words)
    return tuple(dict.fromkeys(merged))


def _check_sections(raw: Dict[Any, Any]) -> None:
    """Raise ValueError if the file has a top-level section this tool does not know.

    A misspelt section would otherwise be dropped in silence, and the owner would
    see alerts quietly stop rather than a message saying why. YAML allows keys
    that are not text at all (the bare word ``on`` becomes the boolean True), so
    every key is turned into text before it is matched or shown.
    """
    for key in raw:
        name = str(key)
        if name in KNOWN_SECTIONS:
            continue
        close = difflib.get_close_matches(name, KNOWN_SECTIONS, n=1)
        hint = f" — did you mean {close[0]!r}?" if close else ""
        raise ValueError(f"sources.yaml: unknown section {name!r}{hint}")


def _what_it_must_be(wanted: Any) -> str:
    """Plain words for the kind of value a setting takes."""
    return {int: "a whole number", float: "a number", bool: "true or false",
            _TEXT_LIST: "a list of text"}.get(wanted, "text")


def _has_right_type(value: Any, wanted: Any) -> bool:
    """True if value fits the setting's declared type.

    ``True`` is an ``int`` in Python, so a boolean is never accepted where a
    number is wanted; a whole-number setting never accepts a fraction.
    """
    if wanted == _TEXT_LIST:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    if wanted is bool:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if wanted is float:
        return isinstance(value, (int, float))
    if wanted is int:
        return isinstance(value, int)
    return isinstance(value, wanted)


def _check_region_list(values: Iterable[str], key: str) -> None:
    """Raise ValueError if a settings list names a region this tool does not have.

    The message names the setting and the regions this tool knows, never the
    value from the file, for the same reason as every other check here.
    """
    for region in values:
        if region not in REGIONS:
            raise ValueError(f"sources.yaml: setting {key!r} may only list these regions: "
                             f"{', '.join(REGIONS)}")


def _check_settings(raw: Any) -> Dict[str, Any]:
    """Return the settings block, or raise ValueError naming the offending key.

    Every message holds a key name and nothing else. These messages travel into
    a public log and a Telegram line, so no value from the file may appear.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("sources.yaml: the settings section must be a list of name: value lines")
    expected = get_type_hints(Settings)
    for key, value in raw.items():
        if key not in expected:
            raise ValueError(f"sources.yaml: unknown setting {key!r}")
        if not _has_right_type(value, expected[key]):
            raise ValueError(
                f"sources.yaml: setting {key!r} must be {_what_it_must_be(expected[key])}"
            )
    _check_region_list(raw.get("online_only_regions", ()), "online_only_regions")
    return {
        key: tuple(value) if expected[key] == _TEXT_LIST else value
        for key, value in raw.items()
    }


def _check_word_map(raw: Any, section: str) -> Dict[str, Iterable[str]]:
    """Return a per-language word section, or raise ValueError naming it."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"sources.yaml: the {section} section must be a list of languages, "
                         "each holding its own list of words")
    for language, words in raw.items():
        if not isinstance(words, list) or not all(isinstance(word, str) for word in words):
            raise ValueError(f"sources.yaml: the {language!r} list under {section} must be a "
                             "list of words, each in quotes")
    return raw


def _check_word_list(raw: Any, section: str) -> Iterable[str]:
    """Return a flat word section, or raise ValueError naming it."""
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(word, str) for word in raw):
        raise ValueError(f"sources.yaml: the {section} section must be a list of words, "
                         "each in quotes")
    return raw


def _check_source_list(raw: Any) -> Iterable[Any]:
    """Return the sources section, or raise ValueError naming it."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("sources.yaml: the sources section must be a list of sources, "
                         "each line starting with a dash")
    return raw


def _build_source(raw: Dict[str, Any]) -> Source:
    if not isinstance(raw, dict):
        raise ValueError("sources.yaml: a source entry must be a list of name: value settings")
    allowed = {field.name for field in fields(Source)}
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"source {raw.get('name')!r} has unknown keys: {sorted(unknown)}")
    missing: Optional[str] = None
    source: Optional[Source] = None
    try:
        source = Source(**raw)
    except TypeError:
        # Only the key names are named, never the value, so nothing from the
        # file's contents can travel into a log or a Telegram message.
        missing = (f"source {raw.get('name')!r} is missing something it needs: "
                   "name, region, kind and url are all required")
    if missing is not None:
        raise ValueError(missing)
    if source.region not in REGIONS:
        raise ValueError(f"source {source.name!r} has unknown region {source.region!r}")
    if source.kind not in KNOWN_KINDS:
        raise ValueError(f"source {source.name!r} has unknown kind {source.kind!r}")
    return source


def _read_yaml(path: Path) -> Dict[str, Any]:
    """Parse the file. Raises ValueError naming the position of a syntax problem.

    A YAML error's own text quotes the offending line, which would put part of
    the file into a public log or a Telegram message, so only the line and
    column are kept.
    """
    problem: Optional[str] = None
    raw: Any = None
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        problem = (f"line {mark.line + 1}, column {mark.column + 1}"
                   if mark is not None else "it is not valid YAML")
    if problem is not None:
        raise ValueError(f"sources.yaml could not be read: {problem}")
    if not isinstance(raw, dict):
        raise ValueError("sources.yaml could not be read: it must be a list of settings")
    return raw


def load_config(path: Path, require_sources: bool = True) -> Config:
    """Read sources.yaml.

    Every way the file can be wrong raises ValueError, so one code path in
    ``run.py`` can tell the owner in plain words instead of the run stopping
    silently on a TypeError or an AttributeError.

    Args:
        path: The file to read.
        require_sources: Reject a file that lists no sources. The candidate list
            used by the source probe is allowed to be empty, and passes False.

    Raises:
        ValueError: on a syntax problem, an unknown or mistyped setting, a
            section of the wrong shape, a missing or unknown source key, an
            unknown region or kind, a duplicate source name, or no sources at
            all. The message never contains any of the file's contents beyond a
            key, a language, a section name or a source's name.
    """
    raw = _read_yaml(Path(path))
    _check_sections(raw)
    sources = tuple(_build_source(entry) for entry in _check_source_list(raw.get("sources")))
    names = [source.name for source in sources]
    if len(names) != len(set(names)):
        raise ValueError("duplicate source names in sources.yaml")
    if require_sources and not sources:
        raise ValueError("sources.yaml: no sources are listed")
    return Config(
        settings=Settings(**_check_settings(raw.get("settings"))),
        sources=sources,
        glitch_words=_flatten(_check_word_map(raw.get("glitch_words"), "glitch_words")),
        sale_words=_flatten(_check_word_map(raw.get("sale_words"), "sale_words")),
        expired_words=tuple(
            word.casefold() for word in _check_word_list(raw.get("expired_words"), "expired_words")
        ),
        ignore_words=_flatten(_check_word_map(raw.get("ignore_words"), "ignore_words")),
        in_store_words=_flatten(
            _check_word_map(raw.get("in_store_words"), "in_store_words")
        ),
    )
