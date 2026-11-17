"""Projects page: create a project, add files and text, remove, delete."""

import pytest

from monster.context.store import ContextStore
from monster.ui.projects import ProjectsWindow


@pytest.fixture
def window(qtbot, tmp_path):
    store = ContextStore(tmp_path / "context")
    w = ProjectsWindow(store)
    qtbot.addWidget(w)
    yield w, store
    store.close()


def test_create_project_add_files_and_text(window, tmp_path):
    w, store = window
    assert not w.add_files_button.isEnabled()  # Nothing to add to yet.
    project = w.create_project("Job applications – Jane")
    assert w.selected_project() == project and w.add_files_button.isEnabled()
    resume = tmp_path / "resume.txt"
    resume.write_text("Jane Doe, backend engineer", encoding="utf-8")
    assert w.add_paths([str(resume), str(tmp_path / "missing.pdf")]) == 1
    assert "Skipped: missing.pdf" in w.status.text()
    w.add_text("Salary", "Expected salary: 150k")
    assert w.documents.count() == 2
    assert {d["name"] for d in store.documents(project_id=project)} == {"resume.txt", "Salary.txt"}
    assert "2 item(s)" in w.projects.currentItem().text()


def test_save_details_and_remove_document(window):
    w, store = window
    project = w.create_project("Payment design")
    w.add_text("Reqs", "Payments must be idempotent")
    w.name.setText("Payment system design")
    w.instructions.setPlainText("Draw in draw.io")
    w._save_details()
    assert store.project(project)["name"] == "Payment system design"
    assert store.project(project)["instructions"] == "Draw in draw.io"
    w.documents.setCurrentRow(0)
    w._remove_document()
    assert w.documents.count() == 0 and store.documents(project_id=project) == []
