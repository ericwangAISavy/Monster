"""Secondary LAN model settings; never adds controls to the everyday pill."""

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from monster.providers.config import OllamaConfig
from monster.providers.jobs import ProviderJobs
from monster.providers.ollama import OllamaProvider


class ProviderSettings(QDialog):
    saved = Signal(object)

    def __init__(self, config, provider_factory=OllamaProvider):
        super().__init__()
        self.setWindowTitle("Monster · LAN Ollama")
        self.setMinimumWidth(440)
        self.provider_factory = provider_factory
        self.config = config
        self.jobs = ProviderJobs(self)
        self.jobs.completed.connect(self._result)
        self.pending = None
        self.operation = ""
        self.probed_config = None
        self.setStyleSheet(
            "QDialog {background:#12172c;} QLabel,QCheckBox {color:#eeeaff;}"
            "QLineEdit,QComboBox,QSpinBox {background:#242b46;color:#eeeaff;"
            "padding:6px;border:1px solid #615c91;border-radius:5px;}"
            "QPushButton {padding:6px 12px;}"
        )
        layout = QVBoxLayout(self)
        note = QLabel(
            "Connect to your own local/LAN Ollama server. Preview mode sends one "
            "desktop screenshot to this server and proposes an action; it never executes it."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.url = QLineEdit(config.url)
        self.url.setAccessibleName("Ollama server URL")
        self.model = QComboBox()
        self.model.setEditable(False)
        if config.model:
            self.model.addItem(config.model)
        self.timeout = QSpinBox()
        self.timeout.setRange(5, 300)
        self.timeout.setSuffix(" seconds")
        self.timeout.setValue(config.timeout)
        self.http = QCheckBox("Allow unencrypted HTTP on my trusted LAN")
        self.http.setChecked(config.allow_lan_http)
        self.local = QCheckBox(
            "I confirmed this server uses local weights with cloud routing disabled"
        )
        self.local.setChecked(config.local_server_confirmed)
        self.mode = QComboBox()
        self.mode.addItem("GUI demo · no model requests", "demo")
        self.mode.addItem("Ollama screenshot/action preview · no input", "preview")
        self.mode.setCurrentIndex(1 if config.mode == "preview" else 0)
        form.addRow("Server", self.url)
        form.addRow("Installed model", self.model)
        form.addRow("Total request timeout", self.timeout)
        form.addRow(self.http)
        form.addRow(self.local)
        form.addRow("Task mode", self.mode)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.connect_button = QPushButton("Test connection / discover")
        self.probe_button = QPushButton("Test vision + JSON")
        self.cancel_button = QPushButton("Cancel request")
        self.cancel_button.setEnabled(False)
        self.connect_button.clicked.connect(self.discover)
        self.probe_button.clicked.connect(self.probe)
        self.cancel_button.clicked.connect(self.cancel_clicked)
        actions.addWidget(self.connect_button)
        actions.addWidget(self.probe_button)
        actions.addWidget(self.cancel_button)
        layout.addLayout(actions)
        self.status = QLabel("Not connected · connection checks send no desktop images.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for signal in (
            self.url.textChanged,
            self.model.currentTextChanged,
            self.timeout.valueChanged,
            self.http.toggled,
            self.local.toggled,
        ):
            signal.connect(self.invalidate)
        self.finished.connect(lambda _: self.jobs.close())

    def value(self):
        config = OllamaConfig(
            self.url.text().strip(),
            self.model.currentText(),
            self.timeout.value(),
            self.http.isChecked(),
            self.local.isChecked(),
            self.mode.currentData(),
        )
        config.validate()
        return config

    def invalidate(self, *_):
        self.probed_config = None
        self.cancel_request()
        self.status.setText("Configuration changed · test the connection and selected model again.")

    def cancel_request(self):
        if self.pending:
            self.jobs.cancel(self.pending)
        self.pending = None
        self.cancel_button.setEnabled(False)
        self.connect_button.setEnabled(True)
        self.probe_button.setEnabled(True)

    def cancel_clicked(self):
        self.cancel_request()
        self.status.setText("Request cancelled · no desktop screenshot was sent.")

    def _start(self, operation, coroutine):
        self.cancel_request()
        self.operation = operation
        self.pending = self.jobs.start(coroutine)
        self.cancel_button.setEnabled(True)
        self.connect_button.setEnabled(False)
        self.probe_button.setEnabled(False)
        self.status.setText(
            "Checking server…"
            if operation == "discover"
            else "Testing synthetic vision + JSON… no desktop screenshot is sent."
        )

    def discover(self):
        try:
            config = replace(self.value_for_checks(), mode="demo")
            provider = self.provider_factory(config)
        except ValueError as exc:
            self.status.setText(str(exc))
            return

        async def discover_models():
            return await provider.health(), await provider.models()

        self._start("discover", discover_models)

    def value_for_checks(self):
        # Discovery must work before a model/preview confirmation has been selected.
        config = OllamaConfig(
            self.url.text().strip(),
            self.model.currentText(),
            self.timeout.value(),
            self.http.isChecked(),
            self.local.isChecked(),
            "demo",
        )
        config.validate()
        return config

    def probe(self):
        try:
            config = self.value_for_checks()
            if not config.model:
                raise ValueError("Discover and select an installed vision model first.")
            if not config.local_server_confirmed:
                raise ValueError("Confirm local-only server operation before the vision probe.")
            provider = self.provider_factory(config)
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self._probe_value = config
        self._start("probe", lambda: provider.probe(config.model))

    def _result(self, job, result, error):
        if job != self.pending:
            return
        self.jobs.futures.pop(job, None)
        operation = self.operation
        self.cancel_request()
        if error:
            self.status.setText(error)
        elif operation == "discover":
            version, models = result
            selected = self.model.currentText()
            self.model.blockSignals(True)
            self.model.clear()
            self.model.addItems([model.id for model in models])
            index = self.model.findText(selected)
            if index >= 0:
                self.model.setCurrentIndex(index)
            self.model.blockSignals(False)
            self.probed_config = None
            self.status.setText(
                f"Connected · Ollama {version} · {len(models)} installed models. "
                "Select a model and test vision + JSON."
            )
        else:
            self.probed_config = self._probe_value
            self.status.setText(
                f"Vision + JSON probe passed · {result.model}. "
                "This checks compatibility, not GUI grounding accuracy."
            )

    def apply(self):
        try:
            config = self.value()
            if self.pending:
                raise ValueError("Wait for the current check or cancel it before saving.")
            if config.mode == "preview" and self.probed_config != replace(config, mode="demo"):
                raise ValueError(
                    "Run and pass the vision + JSON probe for these exact settings first."
                )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.saved.emit(config)
