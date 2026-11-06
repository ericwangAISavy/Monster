from monster.agent.metrics import OperationTimings


def test_timings_separate_outcomes_and_ignore_late_results():
    now = [10.0]
    metrics = OperationTimings(clock=lambda: now[0])
    metrics.begin(("model", 1), "planning")
    now[0] = 12.0
    metrics.finish(("model", 1), "completed")
    metrics.begin(("model", 2), "planning")
    now[0] = 13.0
    metrics.finish(("model", 2), "failed")
    metrics.begin(("model", 3), "verification")
    now[0] = 13.5
    metrics.cancel_pending()
    now[0] = 99.0
    metrics.finish(("model", 3), "completed")
    report = metrics.report()
    assert "Planning: completed 1, failed 1, cancelled 0; total 3.000s; max 2.000s" in report
    assert "Visual verification: completed 0, failed 0, cancelled 1" in report
    assert "0.500s" in report


def test_metrics_reject_content_fields_and_reset_between_tasks():
    import pytest

    metrics = OperationTimings(clock=lambda: 1.0)
    with pytest.raises(ValueError):
        metrics.begin(("model", 1), "PRIVATE_TASK_TEXT")
    metrics.begin(("input", 1), "dispatch")
    with pytest.raises(ValueError):
        metrics.finish(("input", 1), "PRIVATE_MODEL_REASONING")
    metrics.finish(("input", 1), "completed")
    assert "Input dispatch: completed 1" in metrics.report()
    metrics.reset()
    assert "Input dispatch: completed 1" not in metrics.report()
    assert not metrics.pending


def test_existing_action_details_include_content_free_timings():
    from types import SimpleNamespace

    from monster.app import CompanionApp
    from monster.providers.base import Decision

    metrics = OperationTimings(clock=lambda: 1.0)
    metrics.begin(("model", 1), "planning")
    metrics.finish(("model", 1), "completed")
    shown = []
    view = SimpleNamespace(
        last_preview=Decision("complete", "Draft visible"),
        controller=SimpleNamespace(timings=metrics),
        _show_details=lambda title, text, description: shown.append(text),
    )
    CompanionApp.show_preview_details(view)
    assert len(shown) == 1
    assert "Planning: completed 1" in shown[0]
    assert "not proof of action success" in shown[0]


def test_action_details_show_exact_condition_sent_to_verifier():
    from types import SimpleNamespace

    from monster.app import CompanionApp
    from monster.providers.base import Decision, Verification

    shown = []
    view = SimpleNamespace(
        last_preview=Decision(
            "action", "Open Start", {"op": "press", "key": "WIN"},
            expected_result="The Start menu is visible, not yet Sticky Notes",
        ),
        controller=SimpleNamespace(last_verification=Verification("failed", "Start menu is visible")),
        _show_details=lambda title, text, description: shown.append(text),
    )
    CompanionApp.show_preview_details(view)
    assert "Expected visible result: The Start menu is visible, not yet Sticky Notes" in shown[0]
    assert "Evidence: Start menu is visible" in shown[0]


def test_action_details_pair_verification_with_its_own_action_not_next_proposal():
    from types import SimpleNamespace

    from monster.app import CompanionApp
    from monster.providers.base import Decision, Verification

    shown = []
    verified = Decision(
        "action", "Open Start", {"op": "press", "key": "WIN"},
        expected_result="Start is visible",
    )
    view = SimpleNamespace(
        last_preview=Decision(
            "action", "Search apps", {"op": "text", "text": "Sticky Notes"},
            expected_result="Search results are visible",
        ),
        controller=SimpleNamespace(
            last_verification=Verification("failed", "Cannot recognize Start"),
            last_verified_proposal=verified,
        ),
        _show_details=lambda title, text, description: shown.append(text),
    )
    CompanionApp.show_preview_details(view)
    assert "Expected visible result: Search results are visible" in shown[0]
    assert 'Last verified action: {"op": "press", "key": "WIN"}' in shown[0]
    assert "Expected visible result for that action: Start is visible" in shown[0]
    assert "Evidence: Cannot recognize Start" in shown[0]
