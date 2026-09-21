"""Shared paths, expectations and lint helpers for the workflow tests.

Not a test module itself. `test_scan_workflow.py` covers scan.yml, and
`test_probe_workflow.py` covers probe.yml and the source probe script.

The three `check_*` helpers are small "lint" functions. Each is proven
against a deliberately mutated copy of the real workflow, so the checks are
known to bite rather than to pass by construction.
"""

import re
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SCAN_PATH = ROOT / ".github" / "workflows" / "scan.yml"
PROBE_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "probe.yml"
CANDIDATES_PATH = ROOT / "scripts" / "candidates.yaml"
PROBE_SCRIPT_PATH = ROOT / "scripts" / "probe_sources.py"

BOT_TOKEN_PATTERN = re.compile(r"\d{8,10}:[A-Za-z0-9_-]{30,}")
CHAT_ID_PATTERN = re.compile(r"-100\d{8,}")

EXPECTED_PUSH_LOOP = "for attempt in 1 2 3; do git push && break; git pull --rebase; sleep 5; done"
EXPECTED_PUSH_CHECK = 'test -z "$(git log @{u}.. --oneline)"'

RUN_PY_STEP_NAMES = ("Send test messages only", "Scan")

EXPECTED_SCAN_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "TG_CHAT_SG",
    "TG_CHAT_HK",
    "TG_CHAT_JP",
    "TG_CHAT_US",
    "TG_CHAT_EU",
    "TG_CHAT_TEST",
)

# The scan job's steps, in the exact required order. Each entry lists only the
# fields that matter for that step; `if` is always checked, `None` meaning "no
# 'if' key at all".
EXPECTED_SCAN_STEPS: List[Dict[str, Any]] = [
    {"uses": "actions/checkout@v4", "if": None},
    {"uses": "astral-sh/setup-uv@v6", "if": None},
    {"run": "uv sync --frozen --no-dev", "if": None},
    {
        "name": "Send test messages only",
        "run": "uv run --no-dev python run.py --test-message",
        "if": "${{ inputs.test_message }}",
    },
    {
        "name": "Scan",
        "run": "uv run --no-dev python run.py",
        "if": "${{ !inputs.test_message }}",
    },
    # The state must be written back even when the scan step failed part way:
    # alerts already delivered would otherwise be sent again on the next run.
    {"name": "Save state", "if": "${{ always() && !inputs.test_message }}"},
    {
        "name": "Package the page",
        "uses": "actions/upload-pages-artifact@v3",
        "if": "${{ !inputs.test_message }}",
    },
    {
        "name": "Publish the page",
        "uses": "actions/deploy-pages@v4",
        "if": "${{ !inputs.test_message }}",
    },
]


def load_yaml(path: Path) -> Dict[str, Any]:
    """Parse a workflow file."""
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def trigger_section(workflow: Dict[str, Any]) -> Dict[str, Any]:
    """PyYAML reads the bare key `on` as the boolean True (YAML 1.1 quirk)."""
    return workflow.get("on", workflow.get(True))


def find_step(steps: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """The one step with this name, or an assertion failure."""
    for step in steps:
        if step.get("name") == name:
            return step
    raise AssertionError(f"no step named {name!r} in {steps!r}")


def find_step_run(steps: List[Dict[str, Any]], name: str) -> str:
    """The `run:` script of the named step."""
    return find_step(steps, name).get("run", "")


def check_scan_conditions(workflow: Dict[str, Any]) -> List[str]:
    """Check the scan job's step order, identity and `if` guards.

    Returns a list of problem descriptions; an empty list means everything
    matches `EXPECTED_SCAN_STEPS` exactly.
    """
    problems: List[str] = []
    steps = workflow.get("jobs", {}).get("scan", {}).get("steps", [])
    if len(steps) != len(EXPECTED_SCAN_STEPS):
        problems.append(f"expected {len(EXPECTED_SCAN_STEPS)} steps in job 'scan', found {len(steps)}")
        return problems

    for index, (step, expected) in enumerate(zip(steps, EXPECTED_SCAN_STEPS)):
        label = expected.get("name") or expected.get("uses") or expected.get("run") or f"step {index}"

        if "name" in expected:
            if step.get("name") != expected["name"]:
                problems.append(f"step {index} ({label}): expected name {expected['name']!r}, got {step.get('name')!r}")
        elif step.get("name") is not None:
            problems.append(f"step {index} ({label}): expected no name, got {step.get('name')!r}")

        if "uses" in expected and step.get("uses") != expected["uses"]:
            problems.append(f"step {index} ({label}): expected uses {expected['uses']!r}, got {step.get('uses')!r}")

        if "run" in expected:
            actual_run = (step.get("run") or "").strip()
            if actual_run != expected["run"]:
                problems.append(f"step {index} ({label}): expected run {expected['run']!r}, got {actual_run!r}")

        expected_if = expected.get("if")
        actual_if = step.get("if")
        actual_if_stripped = actual_if.strip() if isinstance(actual_if, str) else actual_if
        if expected_if is None:
            if actual_if is not None:
                problems.append(f"step {index} ({label}): expected no 'if', got {actual_if!r}")
        elif actual_if_stripped != expected_if:
            problems.append(f"step {index} ({label}): expected if {expected_if!r}, got {actual_if!r}")

    return problems


def check_push_is_retried_and_verified(script: str) -> List[str]:
    """Check that a save step retries its push and then proves the push landed.

    Returns a list of problem descriptions; an empty list means the script
    pushes only through the retry loop and ends with the ahead-of-remote check.
    """
    problems: List[str] = []
    lines = [line.strip() for line in script.splitlines() if line.strip()]
    push_lines = [line for line in lines if "git push" in line]
    if push_lines != [EXPECTED_PUSH_LOOP]:
        problems.append(f"expected exactly one push line {EXPECTED_PUSH_LOOP!r}, got {push_lines!r}")
    if EXPECTED_PUSH_CHECK not in lines:
        problems.append(f"missing the final check {EXPECTED_PUSH_CHECK!r}")
    elif lines[-1] != EXPECTED_PUSH_CHECK:
        problems.append(f"the final check must be the last line, got {lines[-1]!r}")
    return problems


def check_single_git_add(script: str, expected_line: str) -> List[str]:
    """Check that a step's `run:` script adds exactly one path, exactly `expected_line`.

    Returns a list of problem descriptions; an empty list means the script
    contains exactly one `git add` line and it matches `expected_line` exactly.
    """
    problems: List[str] = []
    add_lines = [line.strip() for line in script.splitlines() if line.strip().startswith("git add")]
    if len(add_lines) != 1:
        problems.append(f"expected exactly one 'git add' line, found {len(add_lines)}: {add_lines!r}")
        return problems
    if add_lines[0] != expected_line:
        problems.append(f"expected {expected_line!r}, got {add_lines[0]!r}")
    return problems
