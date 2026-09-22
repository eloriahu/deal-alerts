from pathlib import Path

import pytest

from dealalerts.config import load_config
from dealalerts.models import REGIONS

ROOT = Path(__file__).resolve().parents[1]


def test_real_sources_file_loads_and_covers_every_region() -> None:
    config = load_config(ROOT / "sources.yaml")
    regions_with_sources = {source.region for source in config.sources}
    assert regions_with_sources == set(REGIONS)
    assert config.settings.alert_discount == 70
    assert config.settings.dashboard_discount == 40


def test_glitch_words_are_flattened_and_casefolded() -> None:
    config = load_config(ROOT / "sources.yaml")
    assert "price error" in config.glitch_words
    assert "preisfehler" in config.glitch_words
    assert "価格ミス" in config.glitch_words


def test_unknown_region_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: X, region: mars, kind: feed, url: 'https://x.test/feed'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="region"):
        load_config(bad)


def test_unknown_kind_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: X, region: sg, kind: carrier-pigeon, url: 'https://x.test/feed'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="kind"):
        load_config(bad)


def test_duplicate_source_names_are_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n"
        "  - {name: X, region: sg, kind: feed, url: 'https://x.test/a'}\n"
        "  - {name: X, region: us, kind: feed, url: 'https://x.test/b'}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_config(bad)


def test_ignore_words_are_flattened_and_casefolded() -> None:
    config = load_config(ROOT / "sources.yaml")
    assert "new customers" in config.ignore_words
    assert "neukunden" in config.ignore_words


def test_a_source_missing_a_required_key_names_the_source(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: Half Done, region: sg, kind: feed}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Half Done"):
        load_config(bad)


def test_a_yaml_typo_gives_a_plain_message_with_no_file_contents(tmp_path: Path) -> None:
    """The message travels to Telegram and into public logs, so it names the
    position of the problem and nothing else."""
    bad = tmp_path / "sources.yaml"
    bad.write_text(
        "sources:\n  - {name: SECRETNAME, region: sg, kind: feed\n", encoding="utf-8"
    )
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    text = str(caught.value)
    assert text.startswith("sources.yaml could not be read:")
    assert "line" in text
    assert "SECRETNAME" not in text
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_a_file_that_is_not_a_mapping_is_rejected_plainly(tmp_path: Path) -> None:
    bad = tmp_path / "sources.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="sources.yaml could not be read"):
        load_config(bad)


# ---------------------------------------------------------------------------
# Every other way the file can be wrong must reach the same plain ValueError,
# so the owner gets the one Telegram line instead of a silent stop.
# ---------------------------------------------------------------------------


def write(tmp_path: Path, text: str) -> Path:
    """Write a sources file and return its path."""
    bad = tmp_path / "sources.yaml"
    bad.write_text(text, encoding="utf-8")
    return bad


ONE_SOURCE = "sources:\n  - {name: X, region: sg, kind: feed, url: 'https://x.test/feed'}\n"


def test_an_unknown_setting_is_named(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  alert_discout: 80\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="unknown setting 'alert_discout'"):
        load_config(bad)


def test_a_setting_that_should_be_a_number_rejects_text(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  alert_discount: seventy\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="setting 'alert_discount' must be a number"):
        load_config(bad)


def test_a_setting_that_should_be_a_number_rejects_a_list(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  alert_discount: [70, 80]\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="setting 'alert_discount' must be a number"):
        load_config(bad)


def test_true_is_not_accepted_as_a_number(tmp_path: Path) -> None:
    """In Python True is an int; the check must not let that through."""
    bad = write(tmp_path, "settings:\n  alert_discount: true\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="setting 'alert_discount' must be a number"):
        load_config(bad)


def test_a_whole_number_setting_rejects_a_fraction(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  seen_days: 2.5\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="setting 'seen_days' must be a whole number"):
        load_config(bad)


def test_a_settings_section_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings: 70\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="settings section"):
        load_config(bad)


def test_a_sources_section_that_is_not_a_list_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "sources:\n  name: X\n")
    with pytest.raises(ValueError, match="sources section"):
        load_config(bad)


def test_a_source_entry_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "sources:\n  - just a string\n")
    with pytest.raises(ValueError, match="source entry"):
        load_config(bad)


def test_a_word_section_that_is_not_a_mapping_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "glitch_words: ['price error']\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="glitch_words section"):
        load_config(bad)


def test_a_language_that_does_not_hold_a_list_of_words_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "sale_words:\n  en: sale\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="'en' list under sale_words"):
        load_config(bad)


def test_a_language_list_holding_something_other_than_text_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "ignore_words:\n  en: ['lotto', 7]\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="'en' list under ignore_words"):
        load_config(bad)


def test_expired_words_must_be_a_list_of_text(tmp_path: Path) -> None:
    bad = write(tmp_path, "expired_words:\n  en: ['expired']\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="expired_words section"):
        load_config(bad)


def test_an_empty_sources_list_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "sources: []\n")
    with pytest.raises(ValueError, match="no sources are listed"):
        load_config(bad)


def test_a_file_with_no_sources_at_all_is_rejected(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  alert_discount: 70\n")
    with pytest.raises(ValueError, match="no sources are listed"):
        load_config(bad)


def test_no_message_from_these_checks_quotes_a_value(tmp_path: Path) -> None:
    """Messages travel to Telegram and into public logs: keys and sections only."""
    bad = write(tmp_path, "settings:\n  alert_discount: SECRETVALUE\n" + ONE_SOURCE)
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    assert "SECRETVALUE" not in str(caught.value)


def test_a_misspelt_section_is_named_with_a_hint(tmp_path: Path) -> None:
    bad = write(tmp_path, "glitchwords:\n  en: ['price error']\n" + ONE_SOURCE)
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    text = str(caught.value)
    assert "unknown section 'glitchwords'" in text
    assert "did you mean 'glitch_words'" in text


def test_a_section_nothing_like_a_known_one_gets_no_invented_hint(tmp_path: Path) -> None:
    bad = write(tmp_path, "colour: red\n" + ONE_SOURCE)
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    text = str(caught.value)
    assert "unknown section 'colour'" in text
    assert "did you mean" not in text


def test_a_section_name_that_is_not_text_is_rejected_plainly(tmp_path: Path) -> None:
    """YAML turns the bare word `on` into the boolean True and `7` into a number.
    Neither may reach the matcher and raise something other than ValueError."""
    for line in ("on: yes\n", "7: seven\n"):
        bad = write(tmp_path, line + ONE_SOURCE)
        with pytest.raises(ValueError, match="unknown section"):
            load_config(bad)


def test_the_shipped_files_still_load() -> None:
    assert load_config(ROOT / "sources.yaml").sources
    load_config(ROOT / "scripts" / "candidates.yaml")


def test_online_only_regions_rejects_an_unknown_region(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  online_only_regions: [mars]\n" + ONE_SOURCE)
    with pytest.raises(ValueError, match="setting 'online_only_regions' may only list"):
        load_config(bad)


def test_the_unknown_region_message_does_not_quote_the_value(tmp_path: Path) -> None:
    bad = write(tmp_path, "settings:\n  online_only_regions: [SECRETPLACE]\n" + ONE_SOURCE)
    with pytest.raises(ValueError) as caught:
        load_config(bad)
    assert "SECRETPLACE" not in str(caught.value)


def test_online_only_regions_must_be_a_list_of_text(tmp_path: Path) -> None:
    for line in ("  online_only_regions: hk\n", "  online_only_regions: [hk, 7]\n"):
        bad = write(tmp_path, "settings:\n" + line + ONE_SOURCE)
        with pytest.raises(ValueError,
                           match="setting 'online_only_regions' must be a list of text"):
            load_config(bad)


def test_the_shipped_file_lists_the_online_only_regions_and_in_store_words() -> None:
    config = load_config(ROOT / "sources.yaml")
    assert config.settings.online_only_regions == ("hk", "jp", "us", "eu")
    assert "dine-in" in config.in_store_words
    assert "堂食" in config.in_store_words
    assert "filiale" in config.in_store_words
