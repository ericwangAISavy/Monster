"""Host preferences only; never stores guest ledgers, endpoints, images or keys."""

from pathlib import Path

from pydantic import Field

from monster.mission.contracts import Contract

from .contracts import WorkspaceDescriptor, new_id


class RegistryRecord(Contract):
    guests: tuple[WorkspaceDescriptor, ...] = Field(default=(), max_length=1)


class ManualGuestWorkspace:
    def __init__(self, path):
        self.path = Path(path)
        self.record = RegistryRecord()
        if self.path.exists():
            if self.path.stat().st_size > 4096:
                raise ValueError("invalid_registry")
            self.record = RegistryRecord.model_validate_json(self.path.read_bytes())

    def register(self, display_name):
        if self.record.guests:
            raise ValueError("one_guest_only")
        descriptor = WorkspaceDescriptor(workspace_id=new_id(), display_name=display_name)
        previous = self.record
        self.record = RegistryRecord(guests=(descriptor,))
        try:
            self._save()
        except OSError:
            self.record = previous
            raise
        return descriptor

    def forget(self):
        previous = self.record
        self.record = RegistryRecord()
        try:
            self._save()
        except OSError:
            self.record = previous
            raise

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(self.record.model_dump_json(), encoding="utf-8")
        temp.replace(self.path)


def native_availability():
    # Honest platform gate, never attempts elevation or Hyper-V installation.
    import sys

    return {
        "available": False,
        "qualification": "PENDING",
        "reason": "native_integration_pending"
        if sys.platform == "win32"
        else "Windows_11_Pro_or_Enterprise_guest_required",
        "hyper_v_home_supported": False,
        "auto_provision": False,
    }
