import json
import time
import zipfile

import pytest

from monster.agent.diagnostics import RunDiagnostics
from monster.computer.contracts import Bounds
from monster.computer.screenshot import Observation
from monster.providers.config import OllamaConfig


def frame(png=b"image", foreground=77):
    return Observation("source", time.monotonic(), Bounds(-100, 0, 100, 100),
                       (Bounds(-100, 0, 100, 100),), png, "hash", foreground)


def test_run_report_preserves_task_settings_order_and_immutable_events():
    report = RunDiagnostics("Open Sticky Notes", OllamaConfig(model="gemma4:e4b"))
    action = {"op": "text", "text": "PRIVATE_CANARY"}
    report.add("proposal", "deciding", call=1, content={"action": action})
    action["text"] = "changed"
    report.add("rejected", "deciding", category="tool/action validation",
               content={"reason": "Shortcut not allowed"})
    report.end("error", "deciding", "Three proposals rejected", cleanup_confirmed=True)
    snapshot = report.snapshot()
    data = snapshot.data
    assert data["task"] == "Open Sticky Notes"
    assert data["configuration"]["model"] == "gemma4:e4b"
    assert data["runtime"]["source_sha256"] and data["runtime"]["python"]
    assert data["events"][0]["content"]["action"]["text"] == "PRIVATE_CANARY"
    assert [event["sequence"] for event in data["events"]] == [1, 2]
    assert data["outcome"]["stage"] == "deciding"
    assert "PRIVATE_CANARY" not in report.snapshot(include_content=False).text()
    assert "Open Sticky Notes" not in report.snapshot(include_content=False).text()


def test_screenshot_retention_is_bounded_and_export_is_explicit(tmp_path):
    report = RunDiagnostics("Test", OllamaConfig(), max_images=2, max_image_bytes=8)
    first = report.observe(frame(b"aaaa"), "observing")
    second = report.observe(frame(b"bbbb", 88), "rechecking")
    third = report.observe(frame(b"cccc"), "verifying_capture")
    assert not list(tmp_path.iterdir())
    snapshot = report.snapshot()
    assert set(snapshot.images) == {second, third}
    assert snapshot.data["observations"][0]["id"] == first
    assert snapshot.data["observations"][0]["retained"] is False
    assert snapshot.data["retention"]["images_dropped"] == 1
    assert snapshot.data["observations"][0]["bounds"]["x"] == -100
    assert snapshot.data["observations"][1]["foreground"] != snapshot.data["observations"][0]["foreground"]
    plain = tmp_path / "report.json"
    snapshot.export(plain)
    assert json.loads(plain.read_text())["screenshots_included"] is False
    archive = tmp_path / "bundle.zip"
    snapshot.export(archive, include_screenshots=True)
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.read(f"screenshots/{third}.png") == b"cccc"
        assert json.loads(bundle.read("report.json"))["screenshots_included"] is True
    with pytest.raises(FileExistsError):
        snapshot.export(plain)


def test_run_snapshots_and_truncation_do_not_mix_later_work():
    report = RunDiagnostics("First task", OllamaConfig(), max_events=2)
    for index in range(4):
        report.add("example", "observing", index=index)
    frozen = report.snapshot()
    report.add("later", "deciding")
    assert frozen.data["retention"]["events_dropped"] == 2
    assert [event["sequence"] for event in frozen.data["events"]] == [3, 4]
    assert report.snapshot().data["retention"]["events_dropped"] == 3
    assert "later" not in frozen.text()


def test_config_does_not_collect_url_credentials_or_unknown_fields():
    from dataclasses import replace

    config = replace(OllamaConfig(), url="https://user:SECRET@private.test/path?key=TOKEN")
    report = RunDiagnostics("Task", config).snapshot()
    assert report.data["configuration"]["url"] == "https://private.test"
    assert "SECRET" not in report.text() and "TOKEN" not in report.text()


def test_provider_callbacks_can_record_concurrently_without_sequence_collisions():
    import concurrent.futures

    report = RunDiagnostics("Task", OllamaConfig(), max_events=512)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda index: report.add("provider", "deciding", index=index), range(100)))
    events = report.snapshot().data["events"]
    assert [event["sequence"] for event in events] == list(range(1, 101))


def test_exception_evidence_never_contains_message_or_locals():
    from monster.agent.diagnostics import exception_evidence

    try:
        raise ValueError("PRIVATE_EXCEPTION_MESSAGE")
    except ValueError as exc:
        data = exception_evidence(exc)
    assert data["exception_class"] == "ValueError"
    assert "PRIVATE_EXCEPTION_MESSAGE" not in json.dumps(data)
    assert data["project_stack"] == []  # Test code is outside the application source root.


def test_runtime_report_flags_parent_worker_source_mismatch():
    report = RunDiagnostics("Task", OllamaConfig())
    assert report.snapshot().data["runtime_consistency"]["source_mismatch"] is None
    report.add("worker_event", "opening", worker={"runtime": {"source_sha256": "0" * 64}})
    assert report.snapshot().data["runtime_consistency"]["source_mismatch"] is True


def test_incomplete_worker_runtime_metadata_cannot_break_report_export():
    report = RunDiagnostics("Task", OllamaConfig())
    report.add("worker_event", "opening", worker=None)
    report.add("worker_event", "opening", worker={"runtime": {"source_sha256": []}})
    assert report.snapshot().data["runtime_consistency"]["source_mismatch"] is None
