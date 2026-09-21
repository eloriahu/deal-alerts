"""Tests for probe.yml, scripts/candidates.yaml and scripts/probe_sources.py.

The probe is manual-only and never runs on the timer, so these checks guard
that separation as well as the probe script's own behaviour. They also hold the
rule that neither workflow file may contain a literal bot key or chat id.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, List

import pytest

from dealalerts.config import load_config
from dealalerts.http import FetchError
from dealalerts.models import REGIONS, RawPost

from workflow_support import (
    BOT_TOKEN_PATTERN, CANDIDATES_PATH, CHAT_ID_PATTERN, PROBE_SCRIPT_PATH,
    PROBE_WORKFLOW_PATH, SCAN_PATH, check_single_git_add, find_step_run, load_yaml,
    trigger_section,
)


def _load_probe_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("probe_sources_under_test", PROBE_SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_is_manual_only() -> None:
    workflow = load_yaml(PROBE_WORKFLOW_PATH)
    trigger = trigger_section(workflow)
    assert "workflow_dispatch" in trigger
    assert "schedule" not in trigger


def test_probe_permissions_are_contents_write_only() -> None:
    workflow = load_yaml(PROBE_WORKFLOW_PATH)
    assert workflow["permissions"] == {"contents": "write"}


def test_probe_runs_the_probe_script() -> None:
    workflow = load_yaml(PROBE_WORKFLOW_PATH)
    steps = workflow["jobs"]["probe"]["steps"]
    run_lines = [step.get("run", "") for step in steps]
    assert any("scripts/probe_sources.py" in line for line in run_lines)


def test_probe_adds_only_the_probe_results_file() -> None:
    text = PROBE_WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "git add docs/source-probe-github.md" in text
    assert "git add -A" not in text
    assert "git add ." not in text

    workflow = load_yaml(PROBE_WORKFLOW_PATH)
    script = find_step_run(workflow["jobs"]["probe"]["steps"], "Save the results")
    assert check_single_git_add(script, "git add docs/source-probe-github.md") == []


def test_probe_job_settings() -> None:
    workflow = load_yaml(PROBE_WORKFLOW_PATH)
    job = workflow["jobs"]["probe"]
    assert job["timeout-minutes"] == 15
    assert job["runs-on"] == "ubuntu-latest"


# ---------------------------------------------------------------------------
# check_single_git_add: mutation checks proving the helper itself bites.
# ---------------------------------------------------------------------------


def test_check_single_git_add_accepts_the_exact_line() -> None:
    assert check_single_git_add("git add data/state.json\n", "git add data/state.json") == []


def test_check_single_git_add_catches_a_second_path_on_the_same_line() -> None:
    problems = check_single_git_add(
        "git add data/state.json data/other.json\n", "git add data/state.json"
    )
    assert problems != []


def test_check_single_git_add_catches_a_second_git_add_line() -> None:
    problems = check_single_git_add(
        "git add data/state.json\ngit add data/other.json\n", "git add data/state.json"
    )
    assert problems != []


# ---------------------------------------------------------------------------
# Both files: no secret can leak from the workflow text itself.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", [SCAN_PATH, PROBE_WORKFLOW_PATH])
def test_no_literal_bot_token_or_chat_id(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        assert not BOT_TOKEN_PATTERN.search(line), f"looks like a bot token in {path}: {line!r}"
        assert not CHAT_ID_PATTERN.search(line), f"looks like a chat id in {path}: {line!r}"


# ---------------------------------------------------------------------------
# scripts/candidates.yaml
# ---------------------------------------------------------------------------


def test_candidates_yaml_loads_and_every_region_is_known() -> None:
    config = load_config(CANDIDATES_PATH)
    assert len(config.sources) > 0
    for source in config.sources:
        assert source.region in REGIONS


# ---------------------------------------------------------------------------
# scripts/probe_sources.py
# ---------------------------------------------------------------------------


class _FakePoliteClient:
    """Records its constructor argument. `fetch` is monkeypatched, so nothing
    ever calls a method on this and no real sleep or network call can happen.
    """

    def __init__(self, gap_seconds: float) -> None:
        self.gap_seconds = gap_seconds


def test_probe_sources_never_stops_on_a_single_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_probe_module()

    sources_file = tmp_path / "sources.yaml"
    sources_file.write_text(
        "sources:\n"
        # Works and Fails share a host: if the real PoliteClient were ever used
        # instead of the fake one, this pair alone would force a 5-second wait.
        "  - {name: Works, region: sg, kind: feed, url: 'https://shared.test/works'}\n"
        "  - {name: Fails, region: sg, kind: feed, url: 'https://shared.test/fails'}\n"
        "  - {name: Crashes, region: sg, kind: feed, url: 'https://crashes.test/feed'}\n",
        encoding="utf-8",
    )
    candidates_file = tmp_path / "candidates.yaml"
    candidates_file.write_text("sources: []\n", encoding="utf-8")
    output_file = tmp_path / "source-probe-github.md"

    def fake_fetch(source: Any, client: Any) -> List[RawPost]:
        if source.name == "Works":
            return [
                RawPost(title="a", link="https://shared.test/a", summary="", posted_at=None, heat=None),
                RawPost(title="b", link="https://shared.test/b", summary="", posted_at=None, heat=None),
            ]
        if source.name == "Fails":
            raise FetchError("x.test: HTTP 403")
        raise RuntimeError("https://api.telegram.org/botSECRET123/x")

    captured_clients: List[_FakePoliteClient] = []

    def fake_polite_client(gap_seconds: float) -> _FakePoliteClient:
        client = _FakePoliteClient(gap_seconds)
        captured_clients.append(client)
        return client

    monkeypatch.setattr(module, "fetch", fake_fetch)
    monkeypatch.setattr(module, "PoliteClient", fake_polite_client)

    result = module.main(
        sources_path=sources_file,
        candidates_path=candidates_file,
        output_path=output_file,
    )

    assert result == 0
    assert len(captured_clients) == 1
    assert captured_clients[0].gap_seconds == 5

    text = output_file.read_text(encoding="utf-8")
    assert "works, 2 posts" in text
    assert "FAILED: x.test: HTTP 403" in text
    assert "FAILED: RuntimeError" in text
    assert "SECRET123" not in text
