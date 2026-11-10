"""Read-only labelled Worker trials. No desktop capture, input, ledger or qualification."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from monster.agent.worker import BoundedWorker, ProposalRequest, VerificationRequest, VisualContext
from monster.agent.worker_checks import VisualCheckRequest
from monster.benchmarks.grounding import percentiles, score
from monster.benchmarks.reliability_cases import read_image, read_manifest
from monster.computer.contracts import Bounds, validate_action
from monster.providers.base import ProviderError, Verification
from monster.providers.decisions import parse_decision
from monster.providers.ollama import OllamaProvider, strict_json

STATUSES = ("succeeded", "failed", "uncertain")
DEFAULT_MANIFEST = Path(__file__).resolve().parents[3] / "tests/fixtures/reliability/cases.json"


def verification_metrics(rows):
    confusion = {truth: {prediction: 0 for prediction in STATUSES} for truth in STATUSES}
    for row in rows:
        if row["prediction"] in STATUSES:
            confusion[row["truth"]][row["prediction"]] += 1
    valid = sum(sum(values.values()) for values in confusion.values())
    negative = sum(confusion[s][p] for s in ("failed", "uncertain") for p in STATUSES)
    positive = sum(confusion["succeeded"].values())
    fp = confusion["failed"]["succeeded"] + confusion["uncertain"]["succeeded"]
    fn = confusion["succeeded"]["failed"] + confusion["succeeded"]["uncertain"]
    return {
        "attempted": len(rows),
        "assessed": valid,
        "unassessed_count": len(rows) - valid,
        "assessment_coverage": valid / len(rows) if rows else None,
        "confusion": confusion,
        "false_success_count": fp,
        "false_failure_count": confusion["succeeded"]["failed"],
        "missed_success_count": fn,
        "abstention_count": sum(confusion[s]["uncertain"] for s in STATUSES),
        "false_positive_rate": fp / negative if negative else None,
        "false_negative_rate": fn / positive if positive else None,
        "exact_status_accuracy": sum(confusion[s][s] for s in STATUSES) / valid if valid else None,
    }


@dataclass(frozen=True)
class Candidate:
    kind: str
    image_action: dict | None = None


def grounding_score(decision, oracle):
    kind = {
        "complete": "completion_claim",
        "done": "completion_claim",
        "clarify": "needs_user",
        "unable": "needs_user",
    }.get(decision.kind, decision.kind)
    if decision.action == {"op": "screenshot"}:
        kind = "observe"
    outcome = score(Candidate(kind, decision.action), oracle)
    duplicate = False
    if decision.action and decision.action.get("op") == "text" and oracle.get("op") == "text":
        text = decision.action["text"]
        for count in range(2, len(text) + 1):
            if len(text) % count:
                continue
            part = text[: len(text) // count]
            if (
                part * count == text
                and hashlib.sha256(part.encode()).hexdigest() == oracle["text_sha256"]
            ):
                duplicate = True
                break
    return outcome, duplicate


class ReplayProvider:
    """Uses the same Worker boundary, with explicit recorded data rather than inference."""

    def __init__(self, response):
        self.response = response

    def decoded(self):
        try:
            return strict_json(self.response)
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("Model returned an invalid recorded response.") from None

    async def decide(self, model, instruction, image, width, height, history=()):
        return parse_decision(self.decoded(), width, height)

    async def verify_action(self, *args):
        try:
            return Verification(**self.decoded())
        except (ValueError, TypeError):
            raise ProviderError("Model returned an invalid recorded assessment.") from None

    async def verify_completion(self, *args):
        return await self.verify_action(*args)


async def run_benchmark(
    path, *, config=None, provider=None, allow_fixture_data=False, repeats=1, deadline=600
):
    if type(repeats) is not int or not 1 <= repeats <= 20:
        raise ValueError("Repeats must be 1-20")
    if type(deadline) is not int or not 1 <= deadline <= 3600:
        raise ValueError("Deadline must be 1-3600s")
    live = config is not None
    measured = live and provider is None
    if provider is not None and not live:
        raise ValueError("A provider requires explicit live routing")
    if live:
        config.validate()
        if isinstance(provider, OllamaProvider) and provider.config != config:
            raise ValueError("Injected provider does not match the consented configuration")
        if not allow_fixture_data or not config.model or not config.local_server_confirmed:
            raise ValueError("Explicit saved-model fixture-data consent required")
    path = Path(path)
    from monster.agent.diagnostics import RUNTIME
    from monster.mission.scheduling import provider_binding

    report = {
        "schema_version": 1,
        "mode": "live_read_only"
        if measured
        else "injected_provider"
        if provider is not None
        else "recorded_replay",
        "dataset_sha256": None,
        "dataset_split": None,
        "label_source": None,
        "runtime": dict(RUNTIME),
        "model": config.model if live else None,
        "configuration_digest": provider_binding(config) if live else None,
        "real_input": False,
        "execution_authorized": False,
        "model_qualified": False,
        "native_windows_qualified": False,
        "planned": 0,
        "status": "completed",
        "trials": [],
        "executor_error_count": None,
        "duplicate_dispatch_count": None,
        "recovery_outcomes": None,
        "successful_drafts": None,
        "draft_attempts": None,
        "native_stop_latency_s": None,
    }
    worker = BoundedWorker()
    live_provider = (provider if provider is not None else OllamaProvider(config)) if live else None
    consecutive_errors = 0
    started = time.monotonic()
    try:
        async with asyncio.timeout(deadline):
            manifest, digest = await read_manifest(path)
            planned = len(manifest.cases) * repeats
            if planned > 1000:
                raise ValueError("At most 1000 predictions")
            if not live and any(c.recorded_response is None for c in manifest.cases):
                raise ValueError("Replay response missing")
            report.update(
                dataset_sha256=digest,
                dataset_split=manifest.split,
                label_source=manifest.label_source,
                planned=planned,
            )
            for repeat in range(repeats):
                for case in manifest.cases:
                    await asyncio.sleep(0)
                    row = {
                        "case_id": case.id,
                        "scenario": case.scenario,
                        "operation": case.operation,
                        "repeat": repeat + 1,
                        "dpi_percent": case.image.dpi_percent,
                        "outcome": "tool_error",
                        "prediction": None,
                        "truth": case.truth,
                        "duplicate_text_proposal": False,
                        "latency_s": None,
                        "inference_and_validation_s": None,
                        "failure_stage": None,
                    }
                    report["trials"].append(row)
                    request_start = time.monotonic()
                    stage = "fixture"
                    inference_start = None
                    try:
                        obs = read_image(path.parent, case.image, case.id)
                        before = (
                            read_image(path.parent, case.before, case.id + "-before")
                            if case.before
                            else None
                        )
                        ctx = VisualContext(
                            config.model if live else "recorded-replay", case.instruction, obs
                        )
                        current = live_provider if live else ReplayProvider(case.recorded_response)
                        stage = "worker_request"
                        if case.operation == "grounding":
                            request = ProposalRequest.create(
                                ctx, generation=repeat + 1, task_ref="benchmark"
                            )
                        elif case.operation == "action_verification":
                            request = VerificationRequest.from_action(
                                ctx,
                                case.action,
                                case.expected,
                                generation=repeat + 1,
                                task_ref="benchmark",
                                before_image=before.png if before else None,
                            )
                        else:
                            request = VisualCheckRequest.create(
                                "completion", ctx, generation=repeat + 1, task_ref="benchmark"
                            )
                        stage = "inference"
                        inference_start = time.monotonic()
                        if case.operation == "grounding":
                            result = await worker.propose(current, request)
                        elif case.operation == "action_verification":
                            result = await worker.verify(current, request)
                        else:
                            result = await worker.check(current, request)
                        if live:
                            row["inference_and_validation_s"] = round(
                                time.monotonic() - inference_start, 6
                            )
                        if time.monotonic() - started >= deadline:
                            row.update(outcome="cancelled", failure_stage="inference")
                            report["status"] = "deadline"
                            break
                        stage = "scoring"
                        if result.binding != request.binding:
                            raise ValueError("Worker binding mismatch")
                        if case.operation == "grounding":
                            prediction = result.to_decision()
                            stage = "proposal_validation"
                            if prediction.kind == "action" and prediction.action != {
                                "op": "screenshot"
                            }:
                                validate_action(
                                    prediction.action,
                                    [Bounds(0, 0, obs.bounds.width, obs.bounds.height)],
                                )
                            stage = "scoring"
                            row["outcome"], row["duplicate_text_proposal"] = grounding_score(
                                prediction, case.oracle
                            )
                        else:
                            row["prediction"] = result.status
                            row["outcome"] = (
                                "matched"
                                if result.status == case.truth
                                else "verification_mismatch"
                            )
                        consecutive_errors = 0
                    except asyncio.CancelledError:
                        row.update(outcome="cancelled", failure_stage=stage)
                        raise
                    except ProviderError as exc:
                        # Only known fixed parser categories influence attribution; never store text.
                        invalid = str(exc).startswith(
                            (
                                "Model returned an invalid",
                                "Model did not return",
                                "Model decision",
                                "Model action",
                                "Unsupported model",
                            )
                        )
                        local = stage in ("fixture", "worker_request", "scoring")
                        row.update(
                            outcome="tool_error"
                            if local
                            else "model_response_error"
                            if invalid
                            else "provider_error",
                            failure_stage=stage if local else "model" if invalid else "provider",
                        )
                        consecutive_errors += 1
                    except Exception:  # noqa: BLE001 -- local/provider boundary must redact private content.
                        row.update(
                            outcome="model_response_error"
                            if stage == "proposal_validation"
                            else "tool_error"
                            if stage in ("fixture", "worker_request", "scoring")
                            else "pipeline_error",
                            failure_stage=stage,
                        )
                        consecutive_errors += 1
                    finally:
                        if live:
                            row["latency_s"] = round(time.monotonic() - request_start, 6)
                            if (
                                inference_start is not None
                                and row["inference_and_validation_s"] is None
                            ):
                                row["inference_and_validation_s"] = round(
                                    time.monotonic() - inference_start, 6
                                )
                    if row["outcome"] == "tool_error":
                        report["status"] = "fixture_or_worker_error"
                        break
                    if consecutive_errors >= 3:
                        report["status"] = "stopped_after_three_errors"
                        break
                if report["status"] != "completed":
                    break
    except TimeoutError:
        report["status"] = "deadline"
    report["elapsed_s"] = round(time.monotonic() - started, 6)
    rows = report["trials"]
    ground = [r for r in rows if r["operation"] == "grounding"]
    verified = [r for r in rows if r["operation"] != "grounding"]
    report.update(attempted=len(rows), matched=sum(r["outcome"] == "matched" for r in rows))
    report["grounding"] = {
        "attempted": len(ground),
        "matched": sum(r["outcome"] == "matched" for r in ground),
        "wrong_target_count": sum(r["outcome"] == "wrong_target" for r in ground),
        "duplicate_text_proposal_count": sum(r["duplicate_text_proposal"] for r in ground),
        "unverified_completion_count": sum(r["outcome"] == "unverified_completion" for r in ground),
    }
    report["verification"] = verification_metrics(verified)
    report["outcomes"] = {
        name: sum(r["outcome"] == name for r in rows)
        for name in sorted({r["outcome"] for r in rows})
    }
    report["pipeline_latency_s"] = percentiles([r["latency_s"] for r in rows]) if live else None
    report["latency_s"] = report["pipeline_latency_s"] if measured else None
    report["latency_by_operation_s"] = (
        {
            op: percentiles([r["latency_s"] for r in rows if r["operation"] == op])
            for op in ("grounding", "action_verification", "completion")
        }
        if measured
        else None
    )
    report["verification_by_scenario"] = {
        scenario: verification_metrics([r for r in verified if r["scenario"] == scenario])
        for scenario in sorted({r["scenario"] for r in verified})
    }
    report["inference_and_validation_by_operation_s"] = (
        {
            op: percentiles(
                [
                    r["inference_and_validation_s"]
                    for r in rows
                    if r["operation"] == op and r["inference_and_validation_s"] is not None
                ]
            )
            for op in ("grounding", "action_verification", "completion")
        }
        if measured
        else None
    )
    report["all_predictions_matched"] = (
        report["matched"] == report["planned"] and report["attempted"] == report["planned"]
    )
    report["model_digest"] = None  # Alias alone does not establish checkpoint identity.
    report["measurement_scope"] = (
        "model_predictions_on_declared_dataset"
        if measured
        else "fixture_or_injected_predictions_only"
    )
    report["model_accuracy"] = (
        {
            "grounding": report["grounding"]["matched"] / len(ground) if ground else None,
            "verification_exact": report["verification"]["exact_status_accuracy"],
        }
        if measured
        else None
    )
    report["per_dpi"] = {
        str(dpi): {
            "attempted": sum(r["dpi_percent"] == dpi for r in rows),
            "matched": sum(r["dpi_percent"] == dpi and r["outcome"] == "matched" for r in rows),
        }
        for dpi in sorted({r["dpi_percent"] for r in rows})
    }
    return report


def main(argv=None):
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    cli.add_argument("--repeats", type=int, default=1)
    cli.add_argument("--deadline", type=int, default=600)
    cli.add_argument("--config", type=Path, help="Existing saved Ollama config; never modified")
    cli.add_argument(
        "--allow-fixture-data",
        action="store_true",
        help="Consent to send manifest task/image data to that configured server",
    )
    args = cli.parse_args(argv)
    try:
        from monster.providers.config import load

        config = load(args.config) if args.config else None
        result = asyncio.run(
            run_benchmark(
                args.manifest,
                config=config,
                allow_fixture_data=args.allow_fixture_data,
                repeats=args.repeats,
                deadline=args.deadline,
            )
        )
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result["status"] == "completed" else 1
    except (ValueError, TypeError, OSError, RecursionError, ProviderError):
        print(
            json.dumps(
                {
                    "status": "invalid_dataset_or_unavailable_route",
                    "real_input": False,
                    "execution_authorized": False,
                }
            )
        )
        return 1
    except KeyboardInterrupt:
        print(
            json.dumps({"status": "cancelled", "real_input": False, "execution_authorized": False})
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
