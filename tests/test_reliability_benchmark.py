"""Reliability scoring never dispatches input or treats replay as model evidence."""

from pathlib import Path

import pytest

ROOT = Path(__file__).parent / "fixtures/reliability"


def test_verification_confusion_keeps_uncertain_separate():
    from monster.benchmarks.reliability import verification_metrics

    metrics = verification_metrics(
        [
            {"truth": "succeeded", "prediction": "succeeded"},
            {"truth": "failed", "prediction": "succeeded"},
            {"truth": "succeeded", "prediction": "failed"},
            {"truth": "failed", "prediction": "failed"},
            {"truth": "uncertain", "prediction": "uncertain"},
            {"truth": "uncertain", "prediction": "succeeded"},
            {"truth": "succeeded", "prediction": "uncertain"},
        ]
    )
    assert metrics["false_success_count"] == 2
    assert metrics["false_failure_count"] == 1
    assert metrics["missed_success_count"] == 2
    assert metrics["abstention_count"] == 2
    assert metrics["confusion"]["uncertain"]["succeeded"] == 1
    assert metrics["false_positive_rate"] == pytest.approx(2 / 4)
    assert metrics["false_negative_rate"] == pytest.approx(2 / 3)


def test_replay_report_does_not_claim_model_or_windows_qualification():
    import asyncio
    import json

    from monster.benchmarks.reliability import run_benchmark

    report = asyncio.run(run_benchmark(ROOT / "cases.json", repeats=2))
    assert report["mode"] == "recorded_replay"
    assert report["model_accuracy"] is None and report["latency_s"] is None
    assert not report["execution_authorized"] and not report["native_windows_qualified"]
    assert not report["model_qualified"] and not report["real_input"]
    assert report["attempted"] == report["planned"]
    assert report["verification"]["false_success_count"] > 0
    assert report["grounding"]["wrong_target_count"] > 0
    assert "Hello from Monster" not in json.dumps(report)


def config():
    from monster.providers.config import OllamaConfig

    return OllamaConfig(model="fixture-model", local_server_confirmed=True)


class Provider:
    def __init__(self):
        self.calls = []

    async def decide(self, *args, history=()):
        from monster.providers.base import Decision

        self.calls.append((args, history))
        assert history == ()
        assert all(word not in args[1] for word in ("oracle", "recorded_response", "label_source"))
        return Decision("unable", "PRIVATE answer")

    async def verify_action(self, *args):
        from monster.providers.base import Verification

        self.calls.append((args, ()))
        return Verification("succeeded", "PRIVATE EVIDENCE")

    async def verify_completion(self, *args):
        from monster.providers.base import Verification

        self.calls.append((args, ()))
        return Verification("succeeded", "PRIVATE EVIDENCE")


def test_injected_provider_cannot_masquerade_as_live_model_measurement():
    import asyncio
    import json

    from monster.benchmarks.reliability import run_benchmark

    provider = Provider()
    report = asyncio.run(
        run_benchmark(
            ROOT / "cases.json", config=config(), provider=provider, allow_fixture_data=True
        )
    )
    assert report["mode"] == "injected_provider"
    assert report["model_accuracy"] is None and report["latency_s"] is None
    assert report["pipeline_latency_s"]["p95"] >= 0
    assert len(provider.calls) == report["attempted"] == 18
    assert report["verification"]["false_success_count"] > 0
    assert "PRIVATE" not in json.dumps(report)
    assert report["executor_error_count"] is None


@pytest.mark.parametrize("change", [{}, {"local_server_confirmed": False}, {"model": ""}])
def test_live_requires_exact_saved_configuration_and_data_consent(change):
    import asyncio
    from dataclasses import replace

    from monster.benchmarks.reliability import run_benchmark

    provider = Provider()
    with pytest.raises(ValueError):
        asyncio.run(
            run_benchmark(
                ROOT / "cases.json",
                config=replace(config(), **change),
                provider=provider,
                allow_fixture_data=bool(change),
            )
        )
    assert provider.calls == []


def test_provider_failure_is_redacted_and_never_retried_implicitly():
    import asyncio
    import json

    from monster.benchmarks.reliability import run_benchmark
    from monster.providers.base import ProviderError

    class Bad(Provider):
        async def decide(self, *args, **kwargs):
            self.calls.append(None)
            raise ProviderError("PRIVATE SERVER BODY")

    provider = Bad()
    report = asyncio.run(
        run_benchmark(
            ROOT / "cases.json",
            config=config(),
            provider=provider,
            allow_fixture_data=True,
            repeats=3,
        )
    )
    assert report["status"] == "stopped_after_three_errors"
    assert len(provider.calls) == 3
    assert report["outcomes"]["provider_error"] == 3
    assert "PRIVATE" not in json.dumps(report)
    assert report["verification"]["false_positive_rate"] is None


@pytest.mark.parametrize(
    "mutation", ["future", "extra", "duplicate", "path", "hash", "bool_version"]
)
def test_invalid_manifest_is_rejected_before_any_provider_call(tmp_path, mutation):
    import asyncio
    import json
    import shutil

    from monster.benchmarks.reliability import run_benchmark

    target = tmp_path / "data"
    shutil.copytree(ROOT, target)
    data = json.loads((target / "cases.json").read_text())
    if mutation == "future":
        data["version"] = 2
    if mutation == "bool_version":
        data["version"] = True
    if mutation == "extra":
        data["cases"][0]["allow_unrestricted"] = True
    if mutation == "duplicate":
        data["cases"][1]["id"] = data["cases"][0]["id"]
    if mutation == "path":
        data["cases"][0]["image"]["path"] = "../secret.png"
    if mutation == "hash":
        data["cases"][0]["image"]["sha256"] = "0" * 64
    (target / "cases.json").write_text(json.dumps(data))
    provider = Provider()
    with pytest.raises(ValueError):
        asyncio.run(
            run_benchmark(
                target / "cases.json", config=config(), provider=provider, allow_fixture_data=True
            )
        )
    assert provider.calls == []


def test_grounding_oracle_excludes_the_send_button():
    import asyncio

    from monster.benchmarks.reliability import grounding_score
    from monster.benchmarks.reliability_cases import read_manifest
    from monster.providers.base import Decision

    manifest, _ = asyncio.run(read_manifest(ROOT / "cases.json"))
    case = next(c for c in manifest.cases if c.id == "visible-target")
    result, _ = grounding_score(
        Decision(
            "action",
            "Focus composer",
            {"op": "click", "x": 1290, "y": 909, "button": "left", "count": 1},
        ),
        case.oracle,
    )
    assert result == "wrong_target"


def test_no_denominator_is_not_reported_as_perfect_accuracy():
    from monster.benchmarks.reliability import verification_metrics

    result = verification_metrics([{"truth": "succeeded", "prediction": None}])
    assert result["unassessed_count"] == 1 and result["assessment_coverage"] == 0
    assert result["false_positive_rate"] is None and result["false_negative_rate"] is None
    assert result["exact_status_accuracy"] is None


def one_case(tmp_path, operation="grounding"):
    import json
    import shutil

    data = json.loads((ROOT / "cases.json").read_text())
    case = next(c for c in data["cases"] if c["operation"] == operation)
    data["cases"] = [case]
    for key in ("image", "before"):
        if key in case:
            shutil.copyfile(ROOT / case[key]["path"], tmp_path / case[key]["path"])
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data))
    return path


def test_deadline_preserves_inflight_trial_and_late_provider_cannot_claim_success(tmp_path):
    import asyncio

    from monster.benchmarks.reliability import run_benchmark
    from monster.providers.base import Decision

    class Slow(Provider):
        async def decide(self, *args, **kwargs):
            self.calls.append(None)
            try:
                await asyncio.sleep(20)
            except asyncio.CancelledError:
                pass
            return Decision("unable", "Late provider answer")

    provider = Slow()
    report = asyncio.run(
        run_benchmark(
            one_case(tmp_path),
            config=config(),
            provider=provider,
            allow_fixture_data=True,
            deadline=1,
        )
    )
    assert report["status"] == "deadline" and report["attempted"] == 1
    assert report["outcomes"]["cancelled"] == 1
    assert report["matched"] == 0 and len(provider.calls) == 1


def test_external_cancellation_propagates_without_fallback():
    import asyncio

    from monster.benchmarks.reliability import run_benchmark

    async def scenario():
        provider = Provider()
        task = asyncio.create_task(
            run_benchmark(
                ROOT / "cases.json", config=config(), provider=provider, allow_fixture_data=True
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not provider.calls

    asyncio.run(scenario())


def test_tampered_image_after_validation_stops_without_another_prediction(tmp_path):
    import asyncio
    import json
    import shutil

    from monster.benchmarks.reliability import run_benchmark
    from monster.providers.base import Decision

    shutil.copytree(ROOT, tmp_path / "data")
    manifest = tmp_path / "data/cases.json"
    data = json.loads(manifest.read_text())

    class Tamper(Provider):
        async def decide(self, *args, **kwargs):
            self.calls.append(None)
            (manifest.parent / data["cases"][1]["image"]["path"]).write_bytes(b"changed")
            return Decision("unable", "No target")

    provider = Tamper()
    report = asyncio.run(
        run_benchmark(manifest, config=config(), provider=provider, allow_fixture_data=True)
    )
    assert report["status"] == "fixture_or_worker_error"
    assert report["outcomes"]["tool_error"] == 1
    assert len(provider.calls) == 1


def test_malformed_proposal_is_a_model_output_error_not_executor_error():
    import asyncio

    from monster.benchmarks.reliability import run_benchmark
    from monster.providers.base import Decision

    class Invalid(Provider):
        async def decide(self, *args, **kwargs):
            self.calls.append(None)
            return Decision("action", "Invalid coordinates", {"op": "click", "x": 9999999, "y": 0})

    provider = Invalid()
    report = asyncio.run(
        run_benchmark(
            ROOT / "cases.json", config=config(), provider=provider, allow_fixture_data=True
        )
    )
    assert report["outcomes"]["model_response_error"] == 3
    assert report["status"] == "stopped_after_three_errors"
    assert report["executor_error_count"] is None


def test_repeated_text_detection_is_not_limited_to_two_copies():
    import hashlib

    from monster.benchmarks.reliability import grounding_score
    from monster.providers.base import Decision

    text = "Hello from Monster"
    oracle = {
        "kind": "action",
        "op": "text",
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    outcome, duplicate = grounding_score(
        Decision("action", "Type once", {"op": "text", "text": text * 5}), oracle
    )
    assert outcome == "action_mismatch" and duplicate


def test_cli_replay_outputs_only_metadata_and_never_authorizes(tmp_path, capsys):
    import json

    from monster.benchmarks.reliability import main

    assert main(["--manifest", str(one_case(tmp_path)), "--repeats", "2"]) == 0
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["mode"] == "recorded_replay" and result["attempted"] == 2
    assert result["execution_authorized"] is False and result["model_accuracy"] is None
    assert "Hello from Monster" not in output


def test_cli_config_does_not_imply_fixture_transmission_consent(tmp_path, capsys):
    from monster.benchmarks.reliability import main
    from monster.providers.config import save

    path = tmp_path / "ollama.json"
    save(path, config())
    original = path.read_bytes()
    assert main(["--manifest", str(ROOT / "cases.json"), "--config", str(path)]) == 1
    assert path.read_bytes() == original
    assert "invalid_dataset_or_unavailable_route" in capsys.readouterr().out


def test_falsey_injected_provider_never_falls_back_to_network(tmp_path, monkeypatch):
    import asyncio

    import monster.benchmarks.reliability as benchmark
    from monster.providers.ollama import OllamaProvider

    class NoNetwork(OllamaProvider):
        def __init__(self, *args, **kwargs):
            raise AssertionError("Unexpected network provider")

    class Falsey(Provider):
        def __bool__(self):
            return False

    monkeypatch.setattr(benchmark, "OllamaProvider", NoNetwork)
    provider = Falsey()
    report = asyncio.run(
        benchmark.run_benchmark(
            one_case(tmp_path), config=config(), provider=provider, allow_fixture_data=True
        )
    )
    assert report["mode"] == "injected_provider" and len(provider.calls) == 1


def test_atomic_grounding_tasks_have_visible_unambiguous_targets():
    import asyncio

    from monster.benchmarks.reliability import grounding_score
    from monster.benchmarks.reliability_cases import read_manifest
    from monster.providers.base import Decision

    manifest, _ = asyncio.run(read_manifest(ROOT / "cases.json"))
    cases = {c.id: c for c in manifest.cases}
    assert (
        grounding_score(
            Decision(
                "action",
                "Click Message",
                {"op": "click", "x": 400, "y": 800, "button": "left", "count": 1},
            ),
            cases["visible-target"].oracle,
        )[0]
        == "matched"
    )
    assert (
        grounding_score(
            Decision(
                "action",
                "Drag A to B",
                {"op": "drag", "x": 375, "y": 375, "end_x": 850, "end_y": 500},
            ),
            cases["drag-target"].oracle,
        )[0]
        == "matched"
    )
    assert (
        grounding_score(
            Decision("action", "Press Enter", {"op": "press", "key": "ENTER"}),
            cases["search-not-done"].oracle,
        )[0]
        == "matched"
    )
    # The accepted task is deliberately atomic: no arbitrary line or alternate Open click.
    assert (
        "marker A" in cases["drag-target"].instruction
        and "marker B" in cases["drag-target"].instruction
    )
    assert "Message placeholder" in cases["visible-target"].instruction
    assert "Press Enter once" in cases["search-not-done"].instruction
