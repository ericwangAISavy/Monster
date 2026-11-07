"""Opt-in secondary workspace surface. All network/guest work stays off Qt."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout


class WorkspaceSettings(QDialog):
    mode_requested = Signal(str)
    register_requested = Signal()
    forget_requested = Signal()
    pair_requested = Signal()
    start_requested = Signal()
    stop_requested = Signal()
    resume_requested = Signal()
    preview_requested = Signal()
    revoke_requested = Signal()
    reconcile_requested = Signal()

    def __init__(self, descriptor=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Monster · Workspaces")
        self.resize(480, 500)
        layout = QVBoxLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Personal Desktop", "personal_desktop")
        self.mode.addItem("Independent Workspace · simulation", "guest_simulation")
        layout.addWidget(self.mode)
        label = QLabel(
            "Native VM connection and input: PENDING design review / Windows qualification.\n"
            "Simulation opens no network listener and creates no real document."
        )
        label.setWordWrap(True)
        label.setMinimumHeight(64)
        layout.addWidget(label)
        self.registration = QLabel(descriptor.display_name if descriptor else "No registered guest")
        layout.addWidget(self.registration)
        self.status = QLabel("Personal Desktop selected")
        self.status.setWordWrap(True)
        self.status.setMinimumHeight(32)
        layout.addWidget(self.status)
        for text, signal in (
            ("Register one manual guest (simulation)", self.register_requested),
            ("Verify simulated pairing…", self.pair_requested),
            ("Start six-stage guest simulation…", self.start_requested),
            ("Resume / request fresh consent", self.resume_requested),
            ("Preview one synthetic guest frame (consent)", self.preview_requested),
            ("Stop guest now", self.stop_requested),
            ("Inspect reconciliation status (observe only)", self.reconcile_requested),
            ("Revoke pairing", self.revoke_requested),
            ("Forget workspace", self.forget_requested),
        ):
            button = QPushButton(text)
            button.clicked.connect(signal.emit)
            layout.addWidget(button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.mode.currentIndexChanged.connect(
            lambda _: self.mode_requested.emit(self.mode.currentData())
        )
