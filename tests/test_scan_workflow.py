"""Structural tests for .github/workflows/scan.yml.

These parse the YAML rather than trusting the raw text, so a typo in the timer,
the permissions, the step guards or the secret wiring cannot silently break the
schedule. The helper checks used here live in workflow_support.py and each is
proven against a deliberately mutated copy of the real workflow below.
"""

import copy

from workflow_support import (
    EXPECTED_PUSH_CHECK, EXPECTED_PUSH_LOOP, EXPECTED_SCAN_ENV_VARS, RUN_PY_STEP_NAMES,
    SCAN_PATH, check_push_is_retried_and_verified, check_scan_conditions, check_single_git_add,
    find_step, find_step_run, load_yaml, trigger_section,
)


def test_scan_schedule_is_every_five_minutes() -> None:
    workflow = load_yaml(SCAN_PATH)
    trigger = trigger_section(workflow)
    assert trigger["schedule"] == [{"cron": "*/5 * * * *"}]


def test_scan_has_workflow_dispatch_with_test_message_boolean() -> None:
    workflow = load_yaml(SCAN_PATH)
    trigger = trigger_section(workflow)
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert inputs["test_message"]["type"] == "boolean"
    assert inputs["test_message"]["default"] is False


def test_scan_permissions() -> None:
    workflow = load_yaml(SCAN_PATH)
    assert workflow["permissions"] == {
        "contents": "write",
        "pages": "write",
        "id-token": "write",
    }


def test_scan_concurrency() -> None:
    workflow = load_yaml(SCAN_PATH)
    assert workflow["concurrency"] == {"group": "scan", "cancel-in-progress": False}


def test_scan_job_hands_the_secrets_to_no_step_by_default() -> None:
    """Job-level env would hand the bot key to the checkout and publish steps too."""
    workflow = load_yaml(SCAN_PATH)
    assert "env" not in workflow["jobs"]["scan"]


def test_only_the_two_steps_that_run_run_py_carry_the_secrets() -> None:
    workflow = load_yaml(SCAN_PATH)
    steps = workflow["jobs"]["scan"]["steps"]
    for name in RUN_PY_STEP_NAMES:
        env = find_step(steps, name)["env"]
        assert set(env.keys()) == set(EXPECTED_SCAN_ENV_VARS), name
        for variable in EXPECTED_SCAN_ENV_VARS:
            assert env[variable] == f"${{{{ secrets.{variable} }}}}"
    elsewhere = [
        step.get("name") or step.get("uses")
        for step in steps
        if step.get("name") not in RUN_PY_STEP_NAMES and "env" in step
    ]
    assert elsewhere == []


def test_scan_runs_run_py_and_a_test_message_variant() -> None:
    workflow = load_yaml(SCAN_PATH)
    steps = workflow["jobs"]["scan"]["steps"]
    run_lines = [step.get("run", "") for step in steps]
    assert any(line.strip().endswith("python run.py") for line in run_lines)
    assert any(line.strip().endswith("python run.py --test-message") for line in run_lines)


def test_scan_step_order_and_if_guards_are_correct() -> None:
    """The safety-critical mechanism: which steps run on a real scan vs. a test-message run."""
    workflow = load_yaml(SCAN_PATH)
    assert check_scan_conditions(workflow) == []


def test_check_scan_conditions_catches_a_flipped_save_state_condition() -> None:
    """Mutation check: flipping 'Save state' to run only on test-message must be caught."""
    workflow = load_yaml(SCAN_PATH)
    mutated = copy.deepcopy(workflow)
    for step in mutated["jobs"]["scan"]["steps"]:
        if step.get("name") == "Save state":
            step["if"] = "${{ inputs.test_message }}"
    problems = check_scan_conditions(mutated)
    assert problems != []
    assert any("Save state" in problem for problem in problems)


def test_check_scan_conditions_catches_a_dropped_always_on_save_state() -> None:
    """Mutation check: losing always() would lose alerts a failed scan already sent."""
    workflow = load_yaml(SCAN_PATH)
    mutated = copy.deepcopy(workflow)
    for step in mutated["jobs"]["scan"]["steps"]:
        if step.get("name") == "Save state":
            step["if"] = "${{ !inputs.test_message }}"
    problems = check_scan_conditions(mutated)
    assert problems != []
    assert any("Save state" in problem for problem in problems)


def test_check_scan_conditions_catches_a_missing_condition_on_scan_step() -> None:
    """Mutation check: dropping 'Scan''s guard (so it always runs) must be caught."""
    workflow = load_yaml(SCAN_PATH)
    mutated = copy.deepcopy(workflow)
    for step in mutated["jobs"]["scan"]["steps"]:
        if step.get("name") == "Scan":
            del step["if"]
    problems = check_scan_conditions(mutated)
    assert problems != []
    assert any("Scan" in problem for problem in problems)


def test_scan_state_step_adds_only_state_json() -> None:
    text = SCAN_PATH.read_text(encoding="utf-8")
    assert "git add data/state.json" in text
    assert "git add -A" not in text
    assert "git add ." not in text

    workflow = load_yaml(SCAN_PATH)
    script = find_step_run(workflow["jobs"]["scan"]["steps"], "Save state")
    assert check_single_git_add(script, "git add data/state.json") == []


def test_scan_state_step_retries_the_push_and_proves_it_landed() -> None:
    """A lost state push means alerts get sent twice, so the push is retried and
    the step fails loudly if the branch is still ahead of the remote afterwards."""
    workflow = load_yaml(SCAN_PATH)
    script = find_step_run(workflow["jobs"]["scan"]["steps"], "Save state")
    assert check_push_is_retried_and_verified(script) == []


def test_scan_step_has_its_own_timeout() -> None:
    """The scan must give up in time for the save and publish steps to still run."""
    workflow = load_yaml(SCAN_PATH)
    assert find_step(workflow["jobs"]["scan"]["steps"], "Scan")["timeout-minutes"] == 7


def test_scan_pages_upload_path_is_public() -> None:
    workflow = load_yaml(SCAN_PATH)
    steps = workflow["jobs"]["scan"]["steps"]
    upload_steps = [step for step in steps if "upload-pages-artifact" in step.get("uses", "")]
    assert len(upload_steps) == 1
    assert upload_steps[0]["with"]["path"] == "public"


def test_scan_job_settings() -> None:
    workflow = load_yaml(SCAN_PATH)
    job = workflow["jobs"]["scan"]
    assert job["timeout-minutes"] == 10
    assert job["runs-on"] == "ubuntu-latest"
    assert job["environment"] == {
        "name": "github-pages",
        "url": "${{ steps.deploy.outputs.page_url }}",
    }


def test_scan_deploy_step_settings() -> None:
    workflow = load_yaml(SCAN_PATH)
    deploy_step = find_step(workflow["jobs"]["scan"]["steps"], "Publish the page")
    assert deploy_step["id"] == "deploy"
    assert deploy_step["uses"] == "actions/deploy-pages@v4"


def test_scan_upload_step_settings() -> None:
    workflow = load_yaml(SCAN_PATH)
    upload_step = find_step(workflow["jobs"]["scan"]["steps"], "Package the page")
    assert upload_step["uses"] == "actions/upload-pages-artifact@v3"
    assert upload_step["with"]["path"] == "public"


# ---------------------------------------------------------------------------
# check_push_is_retried_and_verified: mutation checks proving the helper bites.
# ---------------------------------------------------------------------------


def test_check_push_accepts_the_real_save_state_script() -> None:
    assert check_push_is_retried_and_verified(
        f"git add data/state.json\n{EXPECTED_PUSH_LOOP}\n{EXPECTED_PUSH_CHECK}\n"
    ) == []


def test_check_push_catches_a_single_try_push() -> None:
    problems = check_push_is_retried_and_verified(
        f"git push || (git pull --rebase && git push)\n{EXPECTED_PUSH_CHECK}\n"
    )
    assert problems != []


def test_check_push_catches_a_missing_final_check() -> None:
    problems = check_push_is_retried_and_verified(f"{EXPECTED_PUSH_LOOP}\n")
    assert problems != []
    assert any("final check" in problem for problem in problems)


def test_check_push_catches_a_final_check_that_is_not_last() -> None:
    problems = check_push_is_retried_and_verified(
        f"{EXPECTED_PUSH_LOOP}\n{EXPECTED_PUSH_CHECK}\necho done\n"
    )
    assert problems != []
