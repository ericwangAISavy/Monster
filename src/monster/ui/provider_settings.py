"""Secondary LAN model settings; never adds controls to the everyday pill."""

import json
from dataclasses import replace

from PySide6.QtCore import QElapsedTimer, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monster.agent.diagnostics import RUNTIME
from monster.providers.config import OllamaConfig
from monster.providers.jobs import ProviderJobs
from monster.providers.ollama import PROBE_STAGES, OllamaProvider


class ProviderSettings(QDialog):
    saved = Signal(object)
    activity_changed = Signal(str)
    provider_event = Signal(int, str, object)

    def __init__(self, config, provider_factory=OllamaProvider):
        super().__init__()
        self.setWindowTitle("Monster · LAN Ollama")
        self.setMinimumWidth(440)
        self.provider_factory = provider_factory
        self.config = config
        self.jobs = ProviderJobs(self)
        self.jobs.completed.connect(self._result)
        self.jobs.diagnostic.connect(self._job_diagnostic)
        self.pending = None
        self.operation = ""
        self.request_serial = 0
        self.check_stage = ""
        self.stage_started_ms = 0
        self.provider_event.connect(self._provider_event)
        self.request_clock = QElapsedTimer()
        self.request_deadline = QTimer(self)
        self.request_deadline.setSingleShot(True)
        self.request_deadline.setTimerType(Qt.TimerType.PreciseTimer)
        self.request_deadline.timeout.connect(self._request_timed_out)
        self.request_progress = QTimer(self)
        self.request_progress.setInterval(250)
        self.request_progress.timeout.connect(self._update_request_status)
        # Existing active configurations are checked again by the task controller.
        # Saving only local execution policy should not require another UI probe.
        self.probed_config = (
            replace(config, mode="demo", approve_each_step=True)
            if config.mode in {"preview", "supervised"}
            else None
        )
        self.setStyleSheet(
            "QDialog {background:#222626;} QLabel,QCheckBox {color:#f2f4f3;}"
            "QLineEdit,QComboBox,QSpinBox {background:#303535;color:#f2f4f3;"
            "padding:6px;border:1px solid #505956;border-radius:5px;}"
            "QPushButton {padding:6px 12px;}"
        )
        outer = QVBoxLayout(self)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(0, 0, 6, 0)
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.form_scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget {background:#222626;}")
        self.form_scroll.setWidget(content)
        outer.addWidget(self.form_scroll, 1)
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
        self.http = QCheckBox("Allow unencrypted HTTP on trusted LAN")
        self.http.setChecked(config.allow_lan_http)
        self.local = QCheckBox(
            "I confirm local weights; cloud routing disabled"
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
        self.approve_each_step = QCheckBox("Ask approval before every action")
        self.approve_each_step.setChecked(config.approve_each_step)
        self.approve_each_step.setEnabled(config.mode == "supervised")
        self.mode.currentIndexChanged.connect(
            lambda _: self.approve_each_step.setEnabled(
                self.mode.currentData() == "supervised" and not self.hands_free.isChecked()
            )
        )
        form.addRow(self.approve_each_step)
        self.hands_free = QCheckBox("Hands-free: no approval prompts")
        self.hands_free.setChecked(config.hands_free)
        self.hands_free.setToolTip("Runs routine actions and verifies completion automatically. Stops if review or clarification is required.")
        form.addRow(self.hands_free)
        self.hands_free.toggled.connect(
            lambda enabled: self.approve_each_step.setEnabled(
                self.mode.currentData() == "supervised" and not enabled
            )
        )
        self.approve_each_step.setEnabled(config.mode == "supervised" and not config.hands_free)
        approval_hint = QLabel(
            "With hands-free disabled, unchecking per-step approval lets routine actions, "
            "including clearly benign clicks and Enter, run "
            "automatically after a screen recheck. Sensitive, uncertain or unclassified actions "
            "still ask and explain why. Final completion confirmation stays on."
        )
        approval_hint.setWordWrap(True)
        form.addRow(approval_hint)
        layout.addLayout(form)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText("Advanced inference settings")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setStyleSheet("QToolButton {color:#f2f4f3; padding:4px;}")
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
        self.thinking = QCheckBox("Enable model thinking")
        self.thinking.setChecked(config.thinking)
        self.thinking.setToolTip("Uses output budget and time.")
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
        self.check_details = QPlainTextEdit()
        self.check_details.setReadOnly(True)
        self.check_details.setMaximumBlockCount(200)
        self.check_details.setMinimumHeight(130)
        self.check_details.setMaximumHeight(180)
        self.check_details.setAccessibleName("Last compatibility check report")
        self.check_details.setPlaceholderText("Run a check to record stage timings. Select and copy to share.")
        advanced_form.addRow("Last check report", self.check_details)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self.toggle_advanced)
        actions = QVBoxLayout()
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
        outer.addLayout(actions)
        self.status = QLabel(
            "Saved settings · model is rechecked before each task."
            if self.probed_config is not None
            else "Not connected · connection checks send no desktop images."
        )
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(self.status)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        area = self.screen().availableGeometry()
        self.resize(600, min(720, area.height() - 40))
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
        self.finished.connect(self._closed)

    def value(self):
        config = OllamaConfig(
            self.url.text().strip(),
            self.model.currentText(),
            self.timeout.value(),
            self.http.isChecked(),
            self.local.isChecked(),
            self.mode.currentData(),
            approve_each_step=self.approve_each_step.isChecked() and not self.hands_free.isChecked(),
            hands_free=self.hands_free.isChecked(),
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

    def invalidate(self, *_):
        self.probed_config = None
        self.cancel_request()
        self.status.setText("Configuration changed · test the connection and selected model again.")

    def _closed(self, _):
        self.cancel_request()
        self.jobs.close()

    def cancel_request(self):
        self.request_deadline.stop()
        self.request_progress.stop()
        if self.pending:
            self.jobs.cancel(self.pending)
            self.activity_changed.emit("")
        self.pending = None
        self.cancel_button.setEnabled(False)
        self.connect_button.setEnabled(True)
        self.probe_button.setEnabled(True)

    def cancel_clicked(self):
        self._check_record("cancelled", "Local wait cancelled; no automatic retry.")
        self.cancel_request()
        self.status.setText("Request cancelled · no desktop screenshot was sent.")

    def _start(self, operation, coroutine, provider=None):
        self.cancel_request()
        self.request_serial += 1
        serial = self.request_serial
        self.check_stage = ""
        self.stage_started_ms = 0
        self.operation = operation
        self.request_timeout_s = self.timeout.value()
        self.probed_config = None
        self.request_clock.start()
        self.check_details.setPlainText(
            "Monster compatibility check · No desktop screenshot or model reasoning included.\n"
            f"revision={RUNTIME.get('git_revision_at_startup')}; model={self.model.currentText()}; "
            f"server={self.url.text().strip()}; "
            f"timeout={self.request_timeout_s}s; context={self.context_size.value()}; "
            f"max_output={self.max_output.value()}; thinking={self.thinking.isChecked()}; "
            f"keep_alive={self.keep_alive.currentText()}\n"
        )
        self._check_record("runtime", json.dumps(RUNTIME, ensure_ascii=True))
        if isinstance(provider, OllamaProvider):
            # Content is deliberately ignored: only provider-generated metadata
            # and synthetic color evidence enter this check report.
            provider.diagnostic_callback = lambda event, details, content=None: self.provider_event.emit(
                serial, event, details
            )
        self.pending = self.jobs.start(coroutine)
        self.cancel_button.setEnabled(True)
        self.connect_button.setEnabled(False)
        self.probe_button.setEnabled(False)
        self.request_deadline.start(self.request_timeout_s * 1000)
        self.request_progress.start()
        self._update_request_status()
        self.activity_changed.emit("Connecting" if operation == "discover" else "Vision test")

    def _check_record(self, event, details):
        elapsed = self.request_clock.elapsed() / 1000 if self.request_clock.isValid() else 0
        self.check_details.appendPlainText(f"{elapsed:.3f}s {event}: {details}")

    def _provider_event(self, serial, event, details):
        if serial != self.request_serial or self.pending is None:
            return
        if event not in {
            "probe_stage", "probe_server", "probe_model", "probe_image", "probe_answer",
            "probe_passed", "probe_failed",
            "provider_request_started", "provider_http_result", "provider_model_response", "provider_json_rejected",
            "provider_transport_error",
        }:
            return
        if event == "probe_stage" and details.get("stage") in PROBE_STAGES:
            self.check_stage = details["stage"]
            self.stage_started_ms = self.request_clock.elapsed()
            self._update_request_status()
        self._check_record(event, json.dumps(details, ensure_ascii=True))

    def _job_diagnostic(self, details):
        if self.pending is not None and details.get("job") == self.pending:
            self._check_record("internal_exception", json.dumps(details.get("evidence"), ensure_ascii=True))

    def _update_request_status(self):
        if self.pending is None:
            return
        elapsed = self.request_clock.elapsed() / 1000
        remaining = max(0, self.request_timeout_s - elapsed)
        operation = "Checking server" if self.operation == "discover" else "Testing synthetic vision + JSON"
        stage = f" Stage: {PROBE_STAGES[self.check_stage]}." if self.check_stage else ""
        self.status.setText(
            f"{operation}… {elapsed:.0f}s elapsed, {remaining:.0f}s remaining "
            f"(whole check limit: {self.request_timeout_s}s). "
            f"{stage} Waiting for Ollama; a cold model may need time to load. "
            "No desktop screenshot is sent."
        )

    def _request_timed_out(self):
        if self.pending is None:
            return
        name = "Connection/discovery" if self.operation == "discover" else "Vision + JSON test"
        stage = PROBE_STAGES.get(self.check_stage, "provider operation")
        stage_wait = (self.request_clock.elapsed() - self.stage_started_ms) / 1000
        self._check_record("whole_check_timeout", {"stage": self.check_stage, "stage_wait_s": round(stage_wait, 3)})
        self.cancel_request()
        self.status.setText(
            f"{name} timed out after {self.request_timeout_s}s total at {stage} "
            f"({stage_wait:.1f}s in this stage). Compatibility was not confirmed; "
            "a timeout does not establish model incompatibility. "
            "Copy Last check report in Advanced inference settings for analysis. "
            "No automatic retry; no desktop screenshot was sent."
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

        self._start("discover", discover_models, provider)

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
        self._start("probe", lambda: provider.probe(config.model), provider)

    def _result(self, job, result, error):
        if job != self.pending:
            return
        self.jobs.futures.pop(job, None)
        operation = self.operation
        self._check_record("failed" if error else "completed", error or operation)
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
