"""Synthetic evidence-validation tests; never native Windows qualification."""

import hashlib
import json
from copy import deepcopy

import pytest

TASK = "Type 'Hello from Monster' into the currently open Slack message composer. Do not send."
CASES = (
    "draft_focused_approval",
    "draft_focused_automatic",
    "draft_unfocused_approval",
    "draft_unfocused_automatic",
)


def write(path, value):
    raw = json.dumps(value).encode()
    path.write_bytes(raw)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def dataset(tmp_path, repeats=3):
    from monster.agent.diagnostics import RUNTIME
    from monster.benchmarks.native_trials import template
    from monster.qualification import native_template

    native = native_template()
    native["runtime"]["os"] = "Windows"
    native["environment"] = {
        "unlocked_standard_desktop": True,
        "dpi_percent_tested": [100, 125, 150, 200],
        "display_layouts_tested": ["single", "negative_origin", "mixed_dpi"],
    }
    native.update(stop_threshold_ms=1000, stop_samples_ms=[20, 30, 40])
    for check in native["checks"].values():
        check.update(status="pass", attempts=repeats, supported=repeats)
    manifest = template(repeats)
    for i, trial in enumerate(manifest["trials"]):
        report = {
            "format": "monster.run.v1",
            "run_id": f"{i + 1:032x}",
            "runtime": deepcopy(native["runtime"]),
            "runtime_consistency": {
                "source_mismatch": False,
                "worker_source_hashes": [RUNTIME["source_sha256"]],
            },
            "task": TASK,
            "configuration": {
                "model": "test-model",
                "url": "http://private.invalid:11434",
                "mode": "supervised",
                "hands_free": False,
                "local_server_confirmed": True,
                "timeout": 60,
                "context_size": 16384,
                "max_output_tokens": 4096,
                "thinking": False,
                "keep_alive": "5m",
                "allow_lan_http": True,
                "approve_each_step": trial["case"].endswith("_approval"),
            },
            "outcome": {"status": "complete", "cleanup_confirmed": True, "cleanup_failed": False},
            "summary": {
                "accepted_transactions": 1,
                "planning_calls": 2,
                "visual_checks": 1,
                "active": False,
                "cleanup_blocked": False,
                "phase_at_snapshot": "idle",
                "pending_model_job": None,
                "pending_input_job": None,
                "pending_review_id": None,
                "timings": {
                    "planning": {
                        "completed": 2,
                        "failed": 0,
                        "cancelled": 0,
                        "total": 1.5,
                        "max": 1.0,
                    },
                    "verification": {
                        "completed": 1,
                        "failed": 0,
                        "cancelled": 0,
                        "total": 0.5,
                        "max": 0.5,
                    },
                },
            },
            "events": [{"content": {"text": "PRIVATE_CANARY"}}],
        }
        trial["report"] = write(tmp_path / f"run-{i}.json", report)
        trial["observed"] = {
            "started_empty": True,
            "initially_focused": "_unfocused_" not in trial["case"],
            "intended_field": True,
            "exact_text": True,
            "unsent": True,
            "insertions": 1,
            "wrong_target_actions": 0,
            "duplicate_typing": False,
            "interventions": 0,
            "recovery": "not_needed",
            "failure_stage": "none",
            "failure_domain": "none",
            "stop_requested": False,
            "stop_confirmed": None,
            "stop_latency_ms": None,
            "held_input_clear": True,
        }
    path = tmp_path / "trials.json"
    write(path, manifest)
    return path, manifest, native


def assess(path, native):
    from monster.benchmarks.native_trials import assess_trials

    return assess_trials(path, native)


def update_report(tmp_path, manifest, mutate):
    ref = manifest["trials"][0]["report"]
    path = tmp_path / ref["path"]
    data = json.loads(path.read_text())
    mutate(data)
    manifest["trials"][0]["report"] = write(path, data)
    write(tmp_path / "trials.json", manifest)


def test_template_is_unmeasured_and_never_qualifies(tmp_path):
    from monster.benchmarks.native_trials import template
    from monster.qualification import native_template

    path = tmp_path / "trials.json"
    record = template()
    write(path, record)
    result = assess(path, native_template())
    assert len(record["trials"]) == 12
    assert result["attempted"] == 0 and result["successful_drafts"] == 0
    assert result["review_eligible"] is False and result["blockers"]
    assert result["model_wait_per_trial_s"] is None
    assert result["execution_authorized"] is False and result["native_windows_qualified"] is False


def test_consistent_synthetic_records_are_only_advisory_and_content_free(tmp_path):
    path, _, native = dataset(tmp_path)
    result = assess(path, native)
    assert result["review_eligible"] is True
    assert result["attempted"] == result["successful_drafts"] == 12
    assert all(v["successful_drafts"] == 3 for v in result["cases"].values())
    assert result["evidence_trust"] == "operator_reported_not_attested"
    assert not result["execution_authorized"] and not result["native_windows_qualified"]
    text = json.dumps(result)
    assert all(s not in text for s in ("PRIVATE_CANARY", TASK, "private.invalid", "test-model"))
    assert result["model_wait_per_trial_s"]["p50"] == 1.5
    assert result["verification_wait_per_trial_s"]["p50"] == 0.5


@pytest.mark.parametrize(
    "field,value",
    [
        ("started_empty", False),
        ("initially_focused", False),
        ("intended_field", False),
        ("exact_text", False),
        ("unsent", False),
        ("insertions", 2),
        ("wrong_target_actions", 1),
        ("duplicate_typing", True),
        ("interventions", 1),
        ("recovery", "recovered"),
        ("held_input_clear", False),
        ("exact_text", None),
    ],
)
def test_wrong_partial_duplicate_sent_intervened_or_unknown_trial_never_passes(
    tmp_path, field, value
):
    path, manifest, native = dataset(tmp_path)
    manifest["trials"][0]["observed"][field] = value
    write(path, manifest)
    result = assess(path, native)
    assert not result["review_eligible"] and result["successful_drafts"] == 11
    assert result["attempted"] == 12


@pytest.mark.parametrize(
    "mutation",
    [
        "failed",
        "cleanup",
        "active",
        "pending",
        "source",
        "worker",
        "task",
        "approval",
        "model",
        "preview",
        "hands_free",
    ],
)
def test_report_contradictions_block_operator_claimed_success(tmp_path, mutation):
    path, manifest, native = dataset(tmp_path)

    def change(r):
        if mutation == "failed":
            r["outcome"]["status"] = "error"
        elif mutation == "cleanup":
            r["outcome"]["cleanup_confirmed"] = None
        elif mutation == "active":
            r["summary"]["active"] = True
        elif mutation == "pending":
            r["summary"]["pending_input_job"] = "job-2"
        elif mutation == "source":
            r["runtime"]["source_sha256"] = "a" * 64
        elif mutation == "worker":
            r["runtime_consistency"]["worker_source_hashes"] = []
        elif mutation == "task":
            r["task"] = "PRIVATE_OTHER_TASK"
        elif mutation == "approval":
            r["configuration"]["approve_each_step"] = False
        elif mutation == "model":
            r["configuration"]["model"] = "changed-model"
        elif mutation == "preview":
            r["configuration"]["mode"] = "preview"
        elif mutation == "hands_free":
            r["configuration"]["hands_free"] = True

    update_report(tmp_path, manifest, change)
    result = assess(path, native)
    assert not result["review_eligible"] and result["blockers"]
    assert "PRIVATE_OTHER_TASK" not in json.dumps(result)


def test_missing_attempts_are_not_removed_from_matrix(tmp_path):
    path, manifest, native = dataset(tmp_path)
    manifest["trials"] = manifest["trials"][:3]
    write(path, manifest)
    result = assess(path, native)
    assert not result["review_eligible"] and result["planned"] == 12
    assert result["attempted"] == 3 and result["successful_drafts"] == 3


@pytest.mark.parametrize(
    "mutation",
    [
        "version",
        "extra",
        "duplicate_id",
        "duplicate_run",
        "hash",
        "path",
        "bool_count",
        "nan",
        "duplicate_key",
    ],
)
def test_invalid_or_reused_evidence_is_refused(tmp_path, mutation):
    path, manifest, native = dataset(tmp_path)
    if mutation == "version":
        manifest["schema_version"] = True
    elif mutation == "extra":
        manifest["screenshot"] = "PRIVATE"
    elif mutation == "duplicate_id":
        manifest["trials"][1]["id"] = manifest["trials"][0]["id"]
    elif mutation == "duplicate_run":
        manifest["trials"][1]["report"] = manifest["trials"][0]["report"]
    elif mutation == "hash":
        manifest["trials"][0]["report"]["sha256"] = "a" * 64
    elif mutation == "path":
        manifest["trials"][0]["report"]["path"] = "../outside.json"
    elif mutation == "bool_count":
        manifest["trials"][0]["observed"]["insertions"] = True
    elif mutation == "nan":
        manifest["trials"][0]["observed"]["stop_latency_ms"] = float("nan")
    write(path, manifest)
    if mutation == "duplicate_key":
        path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError):
        assess(path, native)


def test_operator_failure_domain_and_stop_unknown_remain_explicit(tmp_path):
    path, manifest, native = dataset(tmp_path)
    manifest["trials"][0]["observed"].update(
        failure_stage="grounding",
        failure_domain="model",
        exact_text=False,
        stop_requested=True,
        stop_confirmed=None,
        stop_latency_ms=None,
    )
    write(path, manifest)
    result = assess(path, native)
    assert result["operator_failure_domains"]["model"] == 1
    assert result["stop"]["requested"] == 1 and result["stop"]["confirmed"] == 0
    assert result["stop"]["latency_ms"] is None
    assert not result["review_eligible"]


def test_native_aggregate_cannot_contradict_per_attempt_counts(tmp_path):
    path, _, native = dataset(tmp_path)
    native["checks"][CASES[0]]["attempts"] = 4
    native["checks"][CASES[0]]["supported"] = 4
    assert not assess(path, native)["review_eligible"]


def test_cli_template_and_invalid_input_do_not_write_or_leak(tmp_path, capsys):
    from monster.benchmarks.native_trials import main

    assert main(["template", "--repeats", "1"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert len(output["trials"]) == 4 and not list(tmp_path.iterdir())
    bad = tmp_path / "PRIVATE_CANARY.json"
    bad.write_text("PRIVATE_CONTENT")
    assert main(["assess", "--manifest", str(bad), "--native", str(bad)]) == 1
    text = capsys.readouterr().out
    assert "PRIVATE" not in text and "invalid_evidence" in text


def test_unknown_observations_do_not_become_zero_incidents(tmp_path):
    path, manifest, native = dataset(tmp_path)
    manifest["trials"][0]["observed"].update(
        wrong_target_actions=None, duplicate_typing=None, interventions=None
    )
    write(path, manifest)
    result = assess(path, native)
    counts = result["cases"][CASES[0]]
    assert counts["wrong_target"] is None
    assert counts["duplicate"] is None
    assert counts["interventions"] is None
    assert not result["review_eligible"]


def test_cli_oversized_file_is_fixed_error_without_traceback(tmp_path, capsys):
    from monster.benchmarks.native_trials import main

    path = tmp_path / "PRIVATE_FILE.json"
    path.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    assert main(["assess", "--native", str(path), "--manifest", str(path)]) == 1
    result = capsys.readouterr().out
    assert "invalid_evidence" in result and "PRIVATE" not in result


def test_missing_waits_remain_unmeasured(tmp_path):
    path, manifest, native = dataset(tmp_path)
    for trial in manifest["trials"]:
        ref = trial["report"]
        file = tmp_path / ref["path"]
        report = json.loads(file.read_text())
        report["summary"]["timings"] = {}
        trial["report"] = write(file, report)
    write(path, manifest)
    result = assess(path, native)
    assert result["model_wait_per_trial_s"] is None
    assert result["verification_wait_per_trial_s"] is None


def test_same_run_id_in_different_files_cannot_count_twice(tmp_path):
    path, manifest, native = dataset(tmp_path)
    first = json.loads((tmp_path / manifest["trials"][0]["report"]["path"]).read_text())
    second = tmp_path / manifest["trials"][1]["report"]["path"]
    data = json.loads(second.read_text())
    data["run_id"] = first["run_id"]
    manifest["trials"][1]["report"] = write(second, data)
    write(path, manifest)
    with pytest.raises(ValueError):
        assess(path, native)


def test_symlink_report_escape_is_refused(tmp_path):
    path, manifest, native = dataset(tmp_path)
    external = tmp_path.parent / "external-report.json"
    external.write_text("{}")
    (tmp_path / "link.json").symlink_to(external)
    manifest["trials"][0]["report"]["path"] = "link.json"
    write(path, manifest)
    with pytest.raises(ValueError):
        assess(path, native)


@pytest.mark.parametrize(
    "field",
    [
        "url",
        "model",
        "timeout",
        "context_size",
        "max_output_tokens",
        "thinking",
        "keep_alive",
        "allow_lan_http",
        "local_server_confirmed",
        "mode",
        "hands_free",
        "approve_each_step",
    ],
)
def test_missing_configuration_never_inherits_defaults(tmp_path, field):
    path, manifest, native = dataset(tmp_path)
    for trial in manifest["trials"]:
        file = tmp_path / trial["report"]["path"]
        data = json.loads(file.read_text())
        del data["configuration"][field]
        trial["report"] = write(file, data)
    write(path, manifest)
    assert not assess(path, native)["review_eligible"]


@pytest.mark.parametrize("mutation", ["cleanup_failed", "executing", "closing", "missing_phase"])
def test_contradictory_cleanup_or_phase_blocks_success(tmp_path, mutation):
    path, manifest, native = dataset(tmp_path)

    def change(report):
        if mutation == "cleanup_failed":
            report["outcome"]["cleanup_failed"] = True
        elif mutation == "missing_phase":
            del report["summary"]["phase_at_snapshot"]
        else:
            report["summary"]["phase_at_snapshot"] = mutation

    update_report(tmp_path, manifest, change)
    assert not assess(path, native)["review_eligible"]
