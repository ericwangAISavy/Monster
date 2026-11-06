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
    QToolButton,
    QVBoxLayout,
    QWidget,
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
            "desktop screenshot to this server. Real control follows your step approval setting. "
            "Use a disposable test desktop. Ctrl+Alt+X stops."
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
        self.mode.addItem("Real computer control (experimental)", "supervised")
        self.mode.setCurrentIndex(self.mode.findData(config.mode))
        form.addRow("Server", self.url)
        form.addRow("Installed model", self.model)
        form.addRow("Total request timeout", self.timeout)
        form.addRow(self.http)
        form.addRow(self.local)
        form.addRow("Task mode", self.mode)
        self.approve_each_step = QCheckBox("Ask human approval before every action")
        self.approve_each_step.setChecked(config.approve_each_step)
        self.approve_each_step.setEnabled(config.mode == "supervised")
        self.mode.currentIndexChanged.connect(
            lambda _: self.approve_each_step.setEnabled(self.mode.currentData() == "supervised")
        )
        form.addRow(self.approve_each_step)
        approval_hint = QLabel(
            "Unchecked: routine typing, movement, scrolling and navigation run "
            "automatically. Clicks, submit keys and uncertain/sensitive actions "
            "still ask. Final completion confirmation stays on."
        )
        approval_hint.setWordWrap(True)
        form.addRow(approval_hint)
        layout.addLayout(form)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced inference settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setStyleSheet("QToolButton {color:#eeeaff; padding:4px;}")
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        self.context_size = QSpinBox()
        self.context_size.setRange(512, 131072)
        self.context_size.setSingleStep(1024)
        self.context_size.setValue(config.context_size)
        self.max_output = QSpinBox()
        self.max_output.setRange(256, 16384)
        self.max_output.setSingleStep(256)
        self.max_output.setValue(config.max_output_tokens)
        self.keep_alive = QComboBox()
        self.keep_alive.addItems(["0", "1m", "5m", "15m", "30m"])
        self.keep_alive.setCurrentText(config.keep_alive)
        self.thinking = QCheckBox("Enable model thinking (uses output budget and time)")
        self.thinking.setChecked(config.thinking)
        advanced_form.addRow("Context size · tokens", self.context_size)
        advanced_form.addRow("Max output tokens", self.max_output)
        advanced_form.addRow("Keep model loaded", self.keep_alive)
        advanced_form.addRow(self.thinking)
        hint = QLabel(
            "Larger context/output limits can use more server memory and time. "
            "Incomplete JSON is never executed."
        )
        hint.setWordWrap(True)
        advanced_form.addRow(hint)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.toggle_advanced)
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
            self.context_size.valueChanged,
            self.max_output.valueChanged,
            self.keep_alive.currentTextChanged,
            self.thinking.toggled,
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
            approve_each_step=self.approve_each_step.isChecked(),
            **self.inference_values(),
        )
        config.validate()
        return config

    def inference_values(self):
        return {
            "context_size": self.context_size.value(),
            "max_output_tokens": self.max_output.value(),
            "keep_alive": self.keep_alive.currentText(),
            "thinking": self.thinking.isChecked(),
        }

    def toggle_advanced(self, expanded):
        self.advanced.setVisible(expanded)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.adjustSize()

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
            approve_each_step=True,  # Execution policy does not change model compatibility.
            **self.inference_values(),
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
            if config.mode in {"preview", "supervised"} and self.probed_config != replace(
                config, mode="demo", approve_each_step=True
            ):
                raise ValueError(
                    "Run and pass the vision + JSON probe for these exact settings first."
                )
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.saved.emit(config)
