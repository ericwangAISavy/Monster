"""One observation -> one model decision, never connected to InputSession."""

from PySide6.QtCore import Signal

from monster.lifecycle import State, TaskController
from monster.providers.base import ProviderError
from monster.providers.jobs import ProviderJobs
from monster.providers.ollama import OllamaProvider


class PreviewTaskController(TaskController):
    capture_requested = Signal()
    preview_ready = Signal(object)

    def __init__(self, config, provider_factory=OllamaProvider, parent=None):
        super().__init__(parent)
        self.config = config
        self.provider_factory = provider_factory
        self.jobs = ProviderJobs(self)
        self.jobs.completed.connect(self._result)
        self.active, self.paused = False, False
        self.job = None
        self.instruction = ""
        self.verified = False
        self.has_observation = False
        self.generation = 0

    def submit(self, instruction):
        if self.active or not instruction.strip():
            return False
        self.generation += 1
        self.instruction = instruction.strip()
        self.active, self.paused = True, False
        self.verified, self.has_observation = False, False
        self.active_changed.emit(True)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.THINKING)
        # A fresh compatibility check avoids stale persisted model qualifications.
        self.job = self.jobs.start(
            lambda: self.provider_factory(self.config).probe(self.config.model)
        )
        return True

    def _result(self, job, result, error):
        if not self.active or self.paused or job != self.job:
            return
        self.jobs.futures.pop(job, None)
        self.job = None
        if error:
            self.capture_failed(error)
        elif not self.verified:
            self.verified = True
            self.state_changed.emit(State.WORKING)
            self.capture_requested.emit()
        else:
            self.active = False
            self.active_changed.emit(False)
            self.state_changed.emit(State.COMPLETED)
            self.preview_ready.emit(result)
            self.finished.emit("Preview ready · no computer actions performed")
            self.instruction = ""

    def observation_ready(self, observation, generation):
        if not self.active or self.paused or not self.verified or generation != self.generation:
            return
        if self.job is not None or self.has_observation:
            return
        self.has_observation = True
        self.state_changed.emit(State.THINKING)
        instruction = self.instruction
        self.job = self.jobs.start(
            lambda: self.provider_factory(self.config).decide(
                self.config.model,
                instruction,
                observation.png,
                observation.bounds.width,
                observation.bounds.height,
            )
        )

    def capture_failed(self, error):
        if not self.active:
            return
        self.cancel(silent=True)
        self.state_changed.emit(State.ERROR)
        self.failed.emit(error)

    def cancel(self, silent=False):
        self.generation += 1
        if self.job:
            self.jobs.cancel(self.job)
        self.job = None
        self.active, self.paused = False, False
        self.instruction = ""
        self.active_changed.emit(False)
        self.paused_changed.emit(False)
        self.state_changed.emit(State.IDLE)
        if not silent:
            self.status.emit("Preview stopped · no input was sent")

    def toggle_pause(self):
        if not self.active:
            return
        if not self.paused:
            if self.job:
                self.jobs.cancel(self.job)
            self.job = None
            self.generation += 1
            self.paused = True
            self.paused_changed.emit(True)
            self.state_changed.emit(State.WAITING)
            self.status.emit("Preview paused · resume captures a fresh observation")
        else:
            instruction = self.instruction
            self.active = False
            self.submit(instruction)

    def respond(self, request_id, answer):
        raise ProviderError("Preview mode has no executable approval requests.")

    def close(self):
        self.cancel(silent=True)
        self.jobs.close()
