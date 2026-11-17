"""Projects page: permanent context people add once and Monster finds by itself later."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from monster.context.store import ContextError

MAX_FILE_BYTES = 50 * 1024 * 1024


def _size(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB"


class ProjectsWindow(QDialog):
    changed = Signal()

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("Monster · Projects")
        self.setMinimumWidth(400)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QDialog {background:#222626;} QLabel {color:#f2f4f3;}"
            "QLineEdit,QPlainTextEdit,QListWidget {background:#303535;color:#f2f4f3;"
            "border:1px solid #505956;border-radius:5px;padding:4px;}"
            "QPushButton {padding:6px 10px;}"
        )
        layout = QVBoxLayout(self)
        hint = QLabel("Permanent context for tasks. Add files or text once; Monster finds the "
                      "right project from your commands. Everything stays until you delete it. "
                      "Temporary context comes with a command from Claude Code or ChatGPT.")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.projects = QListWidget()
        self.projects.setMaximumHeight(150)
        self.projects.currentItemChanged.connect(lambda *_: self._show_selected())
        layout.addWidget(self.projects)
        row = QHBoxLayout()
        self.new_button = QPushButton("＋ New project")
        self.delete_button = QPushButton("Delete project")
        self.new_button.clicked.connect(self._ask_new_project)
        self.delete_button.clicked.connect(self._ask_delete_project)
        row.addWidget(self.new_button)
        row.addWidget(self.delete_button)
        layout.addLayout(row)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Project name")
        self.instructions = QPlainTextEdit()
        self.instructions.setPlaceholderText(
            "Optional instructions for tasks using this project, e.g. which details to prefer.")
        self.instructions.setMaximumHeight(110)
        self.save_button = QPushButton("Save name and instructions")
        self.save_button.clicked.connect(self._save_details)
        layout.addWidget(QLabel("Name"))
        layout.addWidget(self.name)
        layout.addWidget(QLabel("Instructions"))
        layout.addWidget(self.instructions)
        layout.addWidget(self.save_button)

        layout.addWidget(QLabel("Files and text  ·  drop files here"))
        self.documents = QListWidget()
        layout.addWidget(self.documents, 1)
        row = QHBoxLayout()
        self.add_files_button = QPushButton("Add files…")
        self.add_text_button = QPushButton("Paste text…")
        self.remove_button = QPushButton("Remove")
        self.add_files_button.clicked.connect(self._ask_files)
        self.add_text_button.clicked.connect(self._ask_text)
        self.remove_button.clicked.connect(self._remove_document)
        for button in (self.add_files_button, self.add_text_button, self.remove_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh()

    # ---- state -----------------------------------------------------------------
    def selected_project(self):
        item = self.projects.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, select=None):
        select = select or self.selected_project()
        self.projects.blockSignals(True)
        self.projects.clear()
        for project in self.store.list_projects():
            item = QListWidgetItem(f"{project['name']}  ·  {project['documents']} item(s)")
            item.setData(Qt.ItemDataRole.UserRole, project["id"])
            self.projects.addItem(item)
            if project["id"] == select:
                self.projects.setCurrentItem(item)
        if self.projects.currentItem() is None and self.projects.count():
            self.projects.setCurrentRow(0)
        self.projects.blockSignals(False)
        self._show_selected()

    def _show_selected(self):
        project_id = self.selected_project()
        has = project_id is not None
        for widget in (self.name, self.instructions, self.save_button, self.documents,
                       self.add_files_button, self.add_text_button, self.remove_button,
                       self.delete_button):
            widget.setEnabled(has)
        self.documents.clear()
        if not has:
            self.name.clear()
            self.instructions.clear()
            return
        project = self.store.project(project_id)
        self.name.setText(project["name"])
        self.instructions.setPlainText(project["instructions"])
        for document in self.store.documents(project_id=project_id):
            item = QListWidgetItem(f"{document['name']}  ·  {document['kind']}  ·  {_size(document['size'])}")
            item.setToolTip(document["summary"])
            item.setData(Qt.ItemDataRole.UserRole, document["id"])
            self.documents.addItem(item)

    # ---- actions (also used directly by tests and drops) -----------------------
    def create_project(self, name):
        try:
            project_id = self.store.create_project(name)
        except ContextError as exc:
            self.status.setText(str(exc))
            return None
        self.refresh(select=project_id)
        self.status.setText("Project created. Add files or text to it.")
        self.changed.emit()
        return project_id

    def add_paths(self, paths):
        project_id = self.selected_project()
        if project_id is None:
            self.status.setText("Create or select a project first.")
            return 0
        added, skipped = 0, []
        for path in paths:
            path = Path(path)
            try:
                if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                    skipped.append(path.name)
                    continue
                self.store.add_file(project_id, path.name, path.read_bytes())
                added += 1
            except (OSError, ContextError):
                skipped.append(path.name)
        self.refresh(select=project_id)
        self.status.setText(f"Added {added} file(s)." + (
            f" Skipped: {', '.join(skipped)} (not a file or over 50 MB)." if skipped else ""))
        self.changed.emit()
        return added

    def add_text(self, name, text):
        project_id = self.selected_project()
        if project_id is None or not (text or "").strip():
            return None
        document_id = self.store.add_text(project_id, (name or "Notes").strip() + (
            "" if (name or "").lower().endswith(".txt") else ".txt"), text)
        self.refresh(select=project_id)
        self.status.setText("Text added.")
        self.changed.emit()
        return document_id

    def _ask_new_project(self):
        name, ok = QInputDialog.getText(self, "New project", "Project name:")
        if ok:
            self.create_project(name)

    def _ask_delete_project(self):
        project_id = self.selected_project()
        if project_id is None:
            return
        answer = QMessageBox.question(
            self, "Delete project",
            f"Delete “{self.store.project(project_id)['name']}” and all its files?")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete_project(project_id)
            self.refresh()
            self.status.setText("Project deleted.")
            self.changed.emit()

    def _save_details(self):
        project_id = self.selected_project()
        if project_id is None:
            return
        try:
            self.store.update_project(project_id, name=self.name.text(),
                                      instructions=self.instructions.toPlainText())
        except ContextError as exc:
            self.status.setText(str(exc))
            return
        self.refresh(select=project_id)
        self.status.setText("Saved.")
        self.changed.emit()

    def _ask_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add files to the project")
        if paths:
            self.add_paths(paths)

    def _ask_text(self):
        name, ok = QInputDialog.getText(self, "Paste text", "Name for this text:", text="Notes")
        if not ok:
            return
        text, ok = QInputDialog.getMultiLineText(self, "Paste text", "Text:")
        if ok:
            self.add_text(name, text)

    def _remove_document(self):
        item = self.documents.currentItem()
        if item is None:
            return
        self.store.delete_document(item.data(Qt.ItemDataRole.UserRole))
        self.refresh()
        self.status.setText("Removed.")
        self.changed.emit()

    # ---- drag & drop -------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()
