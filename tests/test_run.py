"""Tests for the run.py command-line entry point."""

import logging
from pathlib import Path
from typing import Any, Dict, List

import pytest

import dealalerts.notify as notify_module
import run as run_module
from dealalerts.pipeline import RunReport
from dealalerts.translate import Translator

SOURCES_PATH = Path(__file__).resolve().parent.parent / "sources.yaml"

FAKE_TOKEN = "FAKETESTTOKEN"
FAKE_CHATS: Dict[str, str] = {
    "TG_CHAT_SG": "-9001", "TG_CHAT_HK": "-9002", "TG_CHAT_JP": "-9003",
    "TG_CHAT_US": "-9004", "TG_CHAT_EU": "-9005",
}


def test_dry_run_with_test_message_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """--dry-run together with --test-message must send nothing at all, and must
    not log any chat id or the bot token."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    for key, value in FAKE_CHATS.items():
        monkeypatch.setenv(key, value)

    sent: List[Dict[str, Any]] = []

    def fake_send_message(client: Any, token: str, chat_id: str, text: str) -> None:
        sent.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr(run_module, "send_message", fake_send_message)
    monkeypatch.setattr(notify_module, "send_message", fake_send_message)

    caplog.set_level(logging.INFO)
    exit_code = run_module.main(
        ["--dry-run", "--test-message", "--config", str(SOURCES_PATH)]
    )

    assert exit_code == 0
    assert sent == []
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert FAKE_TOKEN not in log_text
    for chat_id in FAKE_CHATS.values():
        assert chat_id not in log_text


def test_unexpected_error_exits_one_without_a_traceback_or_the_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture, tmp_path: Path,
) -> None:
    """An unexpected failure anywhere in the run must give exit code 1, log only the
    exception class, print no traceback, and still try to save the state."""
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("https://api.telegram.org/botSECRET123/sendMessage exploded")

    monkeypatch.setattr(run_module, "run_once", boom)
    state_path = tmp_path / "data" / "state.json"

    caplog.set_level(logging.INFO)
    exit_code = run_module.main([
        "--config", str(SOURCES_PATH),
        "--state", str(state_path),
        "--out", str(tmp_path / "public"),
    ])

    assert exit_code == 1
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in log_text
    assert "SECRET123" not in log_text
    assert "Traceback" not in log_text
    captured = capsys.readouterr()
    assert "SECRET123" not in captured.err and "Traceback" not in captured.err
    assert "SECRET123" not in captured.out and "Traceback" not in captured.out
    assert state_path.exists()


def test_run_passes_a_state_saving_checkpoint_and_none_in_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Alerts already delivered must survive a crash, so run.py hands the pipeline a
    checkpoint that writes the state file. A dry run saves nothing, so it gets none."""
    captured: List[Any] = []

    def fake_run_once(*_args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs.get("checkpoint"))
        return RunReport(
            fetched=0, new=0, skipped=0, alerts_sent=0, failures=(),
        )

    monkeypatch.setattr(run_module, "run_once", fake_run_once)
    state_path = tmp_path / "data" / "state.json"
    common = ["--config", str(SOURCES_PATH), "--out", str(tmp_path / "public")]

    assert run_module.main(common + ["--state", str(state_path)]) == 0
    checkpoint = captured[0]
    assert callable(checkpoint)
    state_path.unlink()
    checkpoint()
    assert state_path.exists()

    dry_state = tmp_path / "dry" / "state.json"
    assert run_module.main(common + ["--state", str(dry_state), "--dry-run"]) == 0
    assert captured[1] is None
    assert not dry_state.exists()


BAD_CONFIG = "sources:\n  - {name: Half Done, region: sg, kind: feed}\n"

CONFIG_WARNING_START = (
    "Deal Alerts could not read sources.yaml, so nothing is being checked. "
    "Undo your last edit to that file. Details: "
)


def _catch_sends(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Replace the real sender so no test can reach the network."""
    sent: List[Dict[str, Any]] = []

    def fake_send_message(client: Any, token: str, chat_id: str, text: str) -> None:
        sent.append({"token": token, "chat_id": chat_id, "text": text})

    monkeypatch.setattr(run_module, "send_message", fake_send_message)
    return sent


def test_an_unreadable_config_warns_once_a_day_and_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    for key, value in FAKE_CHATS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TG_CHAT_TEST", raising=False)
    sent = _catch_sends(monkeypatch)

    config_path = tmp_path / "sources.yaml"
    config_path.write_text(BAD_CONFIG, encoding="utf-8")
    state_path = tmp_path / "data" / "state.json"
    argv = ["--config", str(config_path), "--state", str(state_path),
            "--out", str(tmp_path / "public")]

    caplog.set_level(logging.ERROR)
    assert run_module.main(argv) == 1

    assert len(sent) == 1
    assert sent[0]["chat_id"] == FAKE_CHATS["TG_CHAT_SG"]
    assert sent[0]["text"].startswith(CONFIG_WARNING_START)
    assert "Half Done" in sent[0]["text"]
    assert state_path.exists()
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert CONFIG_WARNING_START.strip() in log_text

    # A second broken run the same day must stay quiet.
    assert run_module.main(argv) == 1
    assert len(sent) == 1


def test_the_config_warning_goes_to_the_test_chat_when_one_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    for key, value in FAKE_CHATS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("TG_CHAT_TEST", "-9999")
    sent = _catch_sends(monkeypatch)

    config_path = tmp_path / "sources.yaml"
    config_path.write_text(BAD_CONFIG, encoding="utf-8")
    assert run_module.main([
        "--config", str(config_path), "--state", str(tmp_path / "state.json"),
        "--out", str(tmp_path / "public"),
    ]) == 1
    assert [message["chat_id"] for message in sent] == ["-9999"]


def test_a_dry_run_with_an_unreadable_config_only_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    for key, value in FAKE_CHATS.items():
        monkeypatch.setenv(key, value)
    sent = _catch_sends(monkeypatch)

    config_path = tmp_path / "sources.yaml"
    config_path.write_text(BAD_CONFIG, encoding="utf-8")
    state_path = tmp_path / "state.json"

    caplog.set_level(logging.ERROR)
    assert run_module.main([
        "--dry-run", "--config", str(config_path), "--state", str(state_path),
        "--out", str(tmp_path / "public"),
    ]) == 1
    assert sent == []
    assert not state_path.exists()
    assert any(CONFIG_WARNING_START.strip() in record.getMessage() for record in caplog.records)


def test_a_misspelt_setting_takes_the_same_plain_path_as_any_other_mistake(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A typo in a settings name must not stop the tool silently: it gets the same
    log sentence and the same single Telegram line as any other sources.yaml fault."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", FAKE_TOKEN)
    for key, value in FAKE_CHATS.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("TG_CHAT_TEST", raising=False)
    sent = _catch_sends(monkeypatch)

    config_path = tmp_path / "sources.yaml"
    config_path.write_text(
        "settings:\n  alert_discout: 80\n"
        "sources:\n  - {name: X, region: sg, kind: feed, url: 'https://x.test/feed'}\n",
        encoding="utf-8",
    )
    state_path = tmp_path / "data" / "state.json"

    caplog.set_level(logging.ERROR)
    exit_code = run_module.main([
        "--config", str(config_path), "--state", str(state_path),
        "--out", str(tmp_path / "public"),
    ])

    assert exit_code == 1
    assert len(sent) == 1
    assert sent[0]["chat_id"] == FAKE_CHATS["TG_CHAT_SG"]
    assert sent[0]["text"].startswith(CONFIG_WARNING_START)
    assert "alert_discout" in sent[0]["text"]
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert CONFIG_WARNING_START.strip() in log_text
    assert "TypeError" not in log_text
    assert state_path.exists()


def test_the_closing_line_says_how_many_sources_were_not_reached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Sources skipped for running out of time are neither successes nor failures,
    so the closing line has to say so or they vanish silently."""
    def fake_run_once(*_args: Any, **_kwargs: Any) -> RunReport:
        return RunReport(fetched=12, new=3, skipped=0, alerts_sent=1, failures=(),
                         send_errors=0, sources_skipped=4)

    monkeypatch.setattr(run_module, "run_once", fake_run_once)

    caplog.set_level(logging.INFO)
    exit_code = run_module.main([
        "--config", str(SOURCES_PATH),
        "--state", str(tmp_path / "data" / "state.json"),
        "--out", str(tmp_path / "public"),
    ])

    assert exit_code == 0
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "4 sources not reached in time" in log_text


def test_run_hands_the_pipeline_a_translator_built_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """MYMEMORY_EMAIL is optional and raises the free daily limit. It must reach the
    translator and must never appear in a log line. A dry run translates too."""
    captured: List[Any] = []

    def fake_run_once(*_args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs.get("translator"))
        return RunReport(fetched=0, new=0, skipped=0, alerts_sent=0, failures=())

    monkeypatch.setattr(run_module, "run_once", fake_run_once)
    monkeypatch.setenv("MYMEMORY_EMAIL", "owner@example.test")
    argv = ["--config", str(SOURCES_PATH), "--state", str(tmp_path / "state.json"),
            "--out", str(tmp_path / "public"), "--dry-run"]

    caplog.set_level(logging.INFO)
    assert run_module.main(argv) == 0

    translator = captured[0]
    assert isinstance(translator, Translator)
    # Reaching into the one private field that proves the wiring: nothing public
    # exposes the email, deliberately.
    assert translator._email == "owner@example.test"
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "owner@example.test" not in log_text


def test_a_missing_translation_email_is_simply_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without the optional secret the anonymous daily limit applies; nothing breaks."""
    captured: List[Any] = []

    def fake_run_once(*_args: Any, **kwargs: Any) -> Any:
        captured.append(kwargs.get("translator"))
        return RunReport(fetched=0, new=0, skipped=0, alerts_sent=0, failures=())

    monkeypatch.setattr(run_module, "run_once", fake_run_once)
    monkeypatch.delenv("MYMEMORY_EMAIL", raising=False)
    assert run_module.main([
        "--config", str(SOURCES_PATH), "--state", str(tmp_path / "state.json"),
        "--out", str(tmp_path / "public"), "--dry-run",
    ]) == 0
    assert captured[0]._email is None
