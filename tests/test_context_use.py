"""Finding and using context: the orchestrator picks projects from a high-level reference
and asks for what each subtask needs; Monster fetches it locally for the executor."""

from dataclasses import replace

import pytest
from test_supervised import controller, observation

from monster.context.store import ContextStore
from monster.providers.base import Decision, Verification


@pytest.fixture
def store(tmp_path):
    s = ContextStore(tmp_path / "context")
    yield s
    s.close()


def verdict(status, **fields):
    base = {"success_criteria": "Form filled", "status": status, "evidence": "seen", "answer": "",
            "next_subtask": "", "success_criterion": "", "projects": [], "context_query": "",
            "use_documents": []}
    return {**base, **fields}


def lean_with_context(store, verdicts, decisions, bundle_id=None):
    engine, bridge, model = controller(decisions)
    engine.config = replace(engine.config, approve_each_step=False, hands_free=True, pipeline="lean")
    engine.context_store = store
    engine.context_bundle_id = bundle_id
    seen = {"orchestrator": [], "decide": []}

    async def next_subtask(model_name, goal, criteria, history, *args, **kwargs):
        seen["orchestrator"].append(kwargs)
        return verdicts.pop(0)

    async def decide(model_name, instruction, *args, **kwargs):
        seen["decide"].append((instruction, kwargs))
        return model.decisions.pop(0)

    async def completion(*args):
        return Verification("succeeded", "ok")

    model.next_subtask, model.decide, model.verify_completion = next_subtask, decide, completion
    engine.capture_requested.connect(lambda: engine.observation_ready(observation(), engine.generation))
    return engine, bridge, seen


def test_orchestrator_picks_the_project_and_the_executor_gets_exact_values(qtbot, store):
    jane = store.create_project("Job applications – Jane", "Prefer the mobile number.")
    store.add_text(jane, "resume.txt", "Jane Doe. Phone 555-0100. Email jane@example.com.")
    other = store.create_project("Payment system design")
    store.add_text(other, "reqs.txt", "Payments must be idempotent.")
    engine, bridge, seen = lean_with_context(
        store,
        [verdict("next", projects=[jane, "no-such-id"], next_subtask="Fill the phone field",
                 success_criterion="Phone filled", context_query="Jane phone number"),
         verdict("done", answer="Phone filled with 555-0100")],
        [Decision("complete", "Phone field shows 555-0100")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("fill the application with Jane's details")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        first = seen["orchestrator"][0]["context"]
        assert {p["name"] for p in first["projects"]} == {"Job applications – Jane",
                                                          "Payment system design"}
        assert engine.context_projects == [jane]  # Unknown ids are ignored.
        later = seen["orchestrator"][1]["context"]
        assert [p["id"] for p in later["selected_projects"]] == [jane]
        instruction = seen["decide"][0][0]
        assert "555-0100" in instruction and "Prefer the mobile number." in instruction
        assert "idempotent" not in instruction  # Other projects never leak in.
        assert "never invent personal data" in instruction
    finally:
        engine.close()


def test_temporary_context_and_requested_images_reach_the_executor(qtbot, store):
    bundle = store.create_bundle(text="Requirements: a payment API with a ledger database.",
                                 images=[("architecture.png", b"\x89PNG\r\n\x1a\nfake")])
    engine, bridge, seen = lean_with_context(
        store,
        [verdict("next", next_subtask="Draw the ledger database box", success_criterion="Box drawn",
                 context_query="ledger database", use_documents=["architecture.png"]),
         verdict("done", answer="Drawn")],
        [Decision("complete", "Box drawn")], bundle_id=bundle)
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("draw the design I sent")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        first = seen["orchestrator"][0]
        assert first["context"]["temporary"][0]["text"].startswith("Requirements")
        assert [name for name, _ in first["context_images"]] == ["architecture.png"]
        instruction, kwargs = seen["decide"][0]
        assert "ledger database" in instruction
        assert [name for name, _ in kwargs["context_images"]] == ["architecture.png"]
    finally:
        engine.close()


def test_without_context_nothing_extra_is_sent(qtbot, store):
    engine, bridge, seen = lean_with_context(
        store, [verdict("next", next_subtask="Open Start", success_criterion="Start open"),
                verdict("done", answer="ok")],
        [Decision("complete", "Start open")])
    finished = []
    engine.finished.connect(finished.append)
    try:
        engine.submit("open start")
        qtbot.waitUntil(lambda: bool(finished), timeout=6000)
        assert seen["orchestrator"][0] == {}
        assert "Context for this subtask" not in seen["decide"][0][0]
    finally:
        engine.close()
