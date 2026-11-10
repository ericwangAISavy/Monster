"""Read-only per-attempt native evidence inventory. Never captures or dispatches input."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import fields
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from monster.agent.contracts import Hash, Reference
from monster.agent.diagnostics import RUNTIME
from monster.agent.metrics import OperationTimings
from monster.benchmarks.grounding import percentiles, require
from monster.mission.scheduling import canonical_digest
from monster.providers.base import ProviderError
from monster.providers.config import OllamaConfig
from monster.providers.ollama import strict_json
from monster.providers.ui_venus_preview import _read
from monster.qualification import assess_native

TASK = "Type 'Hello from Monster' into the currently open Slack message composer. Do not send."
CaseName = Literal[
    "draft_focused_approval",
    "draft_focused_automatic",
    "draft_unfocused_approval",
    "draft_unfocused_automatic",
]
CASES = tuple(CaseName.__args__)


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class ReportRef(Spec):
    path: str = Field(min_length=1, max_length=256)
    sha256: Hash


class Observed(Spec):
    started_empty: bool | None = None
    initially_focused: bool | None = None
    intended_field: bool | None = None
    exact_text: bool | None = None
    unsent: bool | None = None
    insertions: int | None = Field(default=None, ge=0, le=1000)
    wrong_target_actions: int | None = Field(default=None, ge=0, le=1000)
    duplicate_typing: bool | None = None
    interventions: int | None = Field(default=None, ge=0, le=1000)
    recovery: Literal["not_needed", "recovered", "failed", "unknown"] = "unknown"
    failure_stage: Literal[
        "none",
        "unknown",
        "capture",
        "grounding",
        "proposal_validation",
        "focus",
        "input_dispatch",
        "verification",
        "completion",
        "cleanup",
    ] = "unknown"
    failure_domain: Literal["none", "unknown", "model", "provider", "tool", "executor", "host"] = (
        "unknown"
    )
    stop_requested: bool | None = None
    stop_confirmed: bool | None = None
    stop_latency_ms: float | None = Field(default=None, ge=0, le=600000)
    held_input_clear: bool | None = None

    @model_validator(mode="after")
    def stop_scope(self):
        if self.stop_requested is not True and (
            self.stop_confirmed is not None or self.stop_latency_ms is not None
        ):
            raise ValueError("Stop observations require a requested stop")
        return self


class Trial(Spec):
    id: Reference
    case: CaseName
    report: ReportRef | None = None
    observed: Observed = Field(default_factory=Observed)


class Manifest(Spec):
    schema_version: Literal[1]
    protocol: Literal["slack-unsent-draft-v1"]
    evidence_kind: Literal["native_operator_observed"]
    repeats: int = Field(ge=1, le=20)
    trials: list[Trial] = Field(max_length=80)

    @field_validator("schema_version", mode="before")
    @classmethod
    def version_type(cls, value):
        if type(value) is not int:
            raise ValueError("Invalid schema version")
        return value

    @model_validator(mode="after")
    def unique_ids(self):
        if len({t.id for t in self.trials}) != len(self.trials):
            raise ValueError("Duplicate trial ID")
        if any(count > self.repeats for count in Counter(t.case for t in self.trials).values()):
            raise ValueError("Too many trials for a case")
        return self


def template(repeats=3):
    require(type(repeats) is int and 1 <= repeats <= 20)
    return Manifest(
        schema_version=1,
        protocol="slack-unsent-draft-v1",
        evidence_kind="native_operator_observed",
        repeats=repeats,
        trials=[
            Trial(id=f"{case}-{n:02d}", case=case) for case in CASES for n in range(1, repeats + 1)
        ],
    ).model_dump(mode="json")


def read_json(path):
    return strict_json(_read(Path(path), 2 * 1024 * 1024))


def read_report(directory, ref):
    relative = Path(ref.path)
    resolved = (directory / relative).resolve()
    require(
        not relative.is_absolute()
        and ".." not in relative.parts
        and resolved.is_relative_to(directory.resolve()),
        "Report outside dataset",
    )
    raw = _read(resolved, 2 * 1024 * 1024)
    require(hashlib.sha256(raw).hexdigest() == ref.sha256, "Report changed")
    data = strict_json(raw)
    require(type(data) is dict and data.get("format") == "monster.run.v1", "Invalid run report")
    return data


def wait_totals(summary):
    """Project known wait totals only; never forward arbitrary report keys/content."""
    timings = summary.get("timings")
    require(type(timings) is dict, "Invalid timing metadata")
    projected = {}
    for stage in OperationTimings.LABELS:
        if stage not in timings:
            continue
        item = timings[stage]
        require(
            type(item) is dict and set(item) == {"completed", "failed", "cancelled", "total", "max"}
        )
        for name in ("completed", "failed", "cancelled"):
            require(type(item[name]) is int and 0 <= item[name] <= 1000000)
        total, maximum = item["total"], item["max"]
        require(
            type(total) in (int, float)
            and type(maximum) in (int, float)
            and 0 <= maximum <= total <= 7200
        )
        require(sum(item[n] for n in ("completed", "failed", "cancelled")) > 0)
        projected[stage] = {
            name: item[name] for name in ("completed", "failed", "cancelled", "total", "max")
        }
    return projected


def valid_configuration(configuration):
    # Never fill missing exported values from dataclass defaults.
    if set(configuration) != {f.name for f in fields(OllamaConfig)}:
        return False
    if any(
        type(configuration[k]) is not bool for k in ("allow_lan_http", "local_server_confirmed")
    ):
        return False
    try:
        OllamaConfig(**configuration).validate()
    except (ValueError, TypeError):
        return False
    return True


def observed_total(observed, key, transform=lambda v: v):
    if not observed or any(o[key] is None or o[key] == "unknown" for o in observed):
        return None
    return sum(transform(o[key]) for o in observed)


def assess_trials(manifest_path, native_record):
    path = Path(manifest_path)
    raw = _read(path, 1024 * 1024)
    manifest = Manifest.model_validate_json(json.dumps(strict_json(raw), allow_nan=False))
    native = assess_native(native_record)
    blockers = ["native:" + b for b in native["blockers"]]
    rows, run_ids, digests, configurations = [], set(), set(), set()
    for trial in manifest.trials:
        obs = trial.observed
        issues = []
        row = {
            "id": trial.id,
            "case": trial.case,
            "attempted": trial.report is not None,
            "successful_draft": False,
            "run_id": None,
            "report_sha256": None,
            "accepted_transactions": None,
            "operation_waits_s": {},
            "failure_stage": obs.failure_stage,
            "operator_failure_domain": obs.failure_domain,
            "observed": obs.model_dump(mode="json"),
        }
        if trial.report is None:
            issues.append("run_report_missing")
        else:
            report = read_report(path.parent, trial.report)
            run_id = report.get("run_id")
            require(
                type(run_id) is str
                and len(run_id) == 32
                and all(c in "0123456789abcdef" for c in run_id)
            )
            require(
                run_id not in run_ids and trial.report.sha256 not in digests, "Reused run evidence"
            )
            run_ids.add(run_id)
            digests.add(trial.report.sha256)
            row.update(run_id=run_id, report_sha256=trial.report.sha256)
            runtime = report.get("runtime")
            configuration = report.get("configuration")
            summary, outcome = report.get("summary"), report.get("outcome")
            require(all(type(v) is dict for v in (runtime, configuration, summary, outcome)))
            if (
                runtime.get("os") != "Windows"
                or runtime.get("source_fingerprint_complete") is not True
                or runtime.get("source_sha256") != RUNTIME["source_sha256"]
                or runtime != native_record["runtime"]
            ):
                issues.append("runtime_binding_mismatch")
            consistency = report.get("runtime_consistency")
            if (
                type(consistency) is not dict
                or consistency.get("source_mismatch") is not False
                or consistency.get("worker_source_hashes") != [RUNTIME["source_sha256"]]
            ):
                issues.append("worker_source_unconfirmed")
            if report.get("task") != TASK:
                issues.append("protocol_task_mismatch")
            if (
                not valid_configuration(configuration)
                or configuration.get("mode") != "supervised"
                or configuration.get("hands_free") is not False
                or configuration.get("local_server_confirmed") is not True
                or configuration.get("approve_each_step") is not trial.case.endswith("_approval")
                or type(configuration.get("model")) is not str
                or not configuration["model"].strip()
            ):
                issues.append("configuration_policy_mismatch")
            digest = canonical_digest(
                {k: v for k, v in configuration.items() if k != "approve_each_step"}
            )
            configurations.add(digest)
            if (
                outcome.get("status") != "complete"
                or outcome.get("cleanup_confirmed") is not True
                or outcome.get("cleanup_failed") is not False
                or summary.get("phase_at_snapshot") != "idle"
                or summary.get("active") is not False
                or summary.get("cleanup_blocked") is not False
                or any(
                    k not in summary or summary[k] is not None
                    for k in ("pending_model_job", "pending_input_job", "pending_review_id")
                )
            ):
                issues.append("terminal_or_cleanup_unconfirmed")
            count = summary.get("accepted_transactions")
            require(type(count) is int and 0 <= count <= 1000000)
            row["accepted_transactions"] = count
            row["operation_waits_s"] = wait_totals(summary)
            if count == 0:
                issues.append("no_confirmed_input_transaction")
        focus = "_unfocused_" not in trial.case
        for name, expected in (
            ("started_empty", True),
            ("initially_focused", focus),
            ("intended_field", True),
            ("exact_text", True),
            ("unsent", True),
            ("insertions", 1),
            ("wrong_target_actions", 0),
            ("duplicate_typing", False),
            ("interventions", 0),
            ("recovery", "not_needed"),
            ("failure_stage", "none"),
            ("failure_domain", "none"),
            ("held_input_clear", True),
        ):
            if getattr(obs, name) != expected:
                issues.append("observation:" + name)
        if obs.stop_requested is None:
            issues.append("stop_observation_missing")
        if obs.stop_requested is True:
            if obs.stop_confirmed is not True or obs.stop_latency_ms is None:
                issues.append("requested_stop_unconfirmed")
            threshold = native_record.get("stop_threshold_ms")
            if threshold is None or (
                obs.stop_latency_ms is not None and obs.stop_latency_ms > threshold
            ):
                issues.append("stop_threshold_unconfirmed_or_exceeded")
        row["successful_draft"] = not issues
        row["blockers"] = issues
        blockers.extend(trial.id + ":" + issue for issue in issues)
        rows.append(row)
    if len(configurations) > 1:
        blockers.append("model_or_configuration_changed_between_trials")
    cases = {}
    for case in CASES:
        group = [r for r in rows if r["case"] == case]
        measured = [r for r in group if r["attempted"]]
        observed = [r["observed"] for r in measured]
        counts = {
            "attempts": len(measured),
            "supported": sum(r["successful_draft"] for r in measured),
            "wrong_target": observed_total(observed, "wrong_target_actions"),
            "duplicate": observed_total(observed, "duplicate_typing"),
            "interventions": observed_total(observed, "interventions"),
            "recovery_attempts": observed_total(
                observed, "recovery", lambda v: v in ("recovered", "failed")
            ),
            "recovered": observed_total(observed, "recovery", lambda v: v == "recovered"),
        }
        cases[case] = {
            **counts,
            "successful_drafts": counts["supported"],
            "required": manifest.repeats,
        }
        if len(group) != manifest.repeats or counts["supported"] != manifest.repeats:
            blockers.append(case + ":matrix_incomplete_or_failed")
        aggregate = native_record["checks"].get(case, {})
        if any(aggregate.get(k) != v for k, v in counts.items()):
            blockers.append(case + ":aggregate_counts_disagree")
    requested = [r for r in rows if r["attempted"] and r["observed"]["stop_requested"] is True]

    def latencies(stage):
        return percentiles(
            [
                r["operation_waits_s"][stage]["total"]
                for r in rows
                if stage in r["operation_waits_s"]
            ]
        )

    return {
        "schema_version": 1,
        "protocol": manifest.protocol,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        "evidence_trust": "operator_reported_not_attested",
        "execution_authorized": False,
        "native_windows_qualified": False,
        "real_input": False,
        "screenshots_captured": False,
        "review_eligible": not blockers,
        "blockers": blockers,
        "native_checks_complete": native["complete"],
        "planned": len(CASES) * manifest.repeats,
        "attempted": len(run_ids),
        "successful_drafts": sum(r["successful_draft"] for r in rows),
        "cases": cases,
        "trials": rows,
        "configuration_digests": sorted(configurations),
        "operator_failure_domains": dict(
            Counter(r["operator_failure_domain"] for r in rows if r["attempted"])
        ),
        "model_wait_per_trial_s": latencies("planning"),
        "verification_wait_per_trial_s": latencies("verification"),
        "stop": {
            "requested": len(requested),
            "confirmed": sum(r["observed"]["stop_confirmed"] is True for r in requested),
            "latency_ms": percentiles(
                [
                    r["observed"]["stop_latency_ms"]
                    for r in requested
                    if r["observed"]["stop_latency_ms"] is not None
                ]
            ),
        },
        "note": "Advisory inventory, not attestation, execution permission or qualification. "
        "Failure domains and visible outcomes are operator reported. Waits are per-trial totals, not model-call latency. "
        "real_input describes this assessor, not the referenced runs.",
    }


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    sub = cli.add_subparsers(dest="command", required=True)
    create = sub.add_parser("template")
    create.add_argument("--repeats", type=int, default=3)
    assess = sub.add_parser("assess")
    assess.add_argument("--manifest", type=Path, required=True)
    assess.add_argument("--native", type=Path, required=True)
    args = cli.parse_args(argv)
    try:
        result = (
            template(args.repeats)
            if args.command == "template"
            else assess_trials(args.manifest, read_json(args.native))
        )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if args.command == "template" or result["review_eligible"] else 1
    except (ValueError, TypeError, OSError, RecursionError, ProviderError):
        print(
            json.dumps(
                {
                    "status": "invalid_evidence",
                    "execution_authorized": False,
                    "native_windows_qualified": False,
                    "real_input": False,
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
