import pytest


def packet(seq=1, visible=False):
    return {'version': 1, 'seq': seq, 'timestamp_ns': 100,
            'state': 'thinking', 'character': 'rabbit', 'visible': visible, 'paused': False,
            'closing': False, 'appearance': {'offset_x': 26, 'offset_y': 20,
            'reduced_motion': False, 'companion_scale': 100, 'companion_position': 'cursor'}}


def test_mailbox_rejects_torn_or_corrupt_messages_and_keeps_channels_separate():
    from monster.ui.overlay_mailbox import MAILBOX_SIZE, Mailbox
    memory = bytearray(MAILBOX_SIZE)
    box = Mailbox(memory)
    box.write(0, packet())
    box.write(1, {'seq': 1, 'hidden': True})
    assert box.read(0) == packet() and box.read(1)['hidden'] is True
    memory[50] ^= 1
    assert box.read(0) is None and box.read(1)['hidden'] is True


@pytest.mark.parametrize('field,value', [('state', 'exec'), ('character', '../private.png'),
                                        ('visible', 'true'), ('seq', True), ('timestamp_ns', -1)])
def test_renderer_protocol_is_appearance_only(field, value):
    from monster.ui.overlay_mailbox import validate_state
    data = packet()
    data[field] = value
    with pytest.raises(ValueError):
        validate_state(data)


def test_renderer_rejects_input_payloads_and_invalid_appearance():
    from monster.ui.overlay_mailbox import validate_state
    data = packet()
    data['action'] = {'op': 'press', 'key': 'ENTER'}
    with pytest.raises(ValueError):
        validate_state(data)
    data = packet()
    data['appearance']['companion_scale'] = 999
    with pytest.raises(ValueError):
        validate_state(data)


def test_companion_capture_requires_external_renderer_hidden_ack(qtbot):
    from monster.capture import CaptureIsolation
    from monster.config import Preferences
    from monster.ui.companion import Companion

    class Renderer:
        active = True
        hidden_confirmed = False

        def update(self, **kwargs):
            self.last = kwargs

        def pump(self):
            pass

        def close(self):
            pass

    renderer = Renderer()
    companion = Companion(Preferences())
    qtbot.addWidget(companion)
    companion.attach_renderer(renderer)
    companion.show()
    isolation = CaptureIsolation([companion])
    calls, failures = [], []
    isolation.failed.connect(failures.append)
    isolation.capture(lambda: calls.append(True), settle_ms=50)
    qtbot.waitUntil(lambda: not isolation.busy)
    assert failures and not calls
    renderer.hidden_confirmed = True
    isolation.capture(lambda: calls.append(True), settle_ms=50)
    qtbot.waitUntil(lambda: not isolation.busy)
    assert calls == [True]


def test_renderer_lease_tolerates_a_torn_read_but_closes_after_heartbeat_loss():
    from monster.ui.overlay_mailbox import RendererLease
    lease = RendererLease(100)
    assert lease.accept(packet(), 100) == 'apply'
    assert lease.accept(None, 200) == 'wait'
    assert lease.accept(None, 3_000_000_200) == 'close'


def test_hidden_ack_is_bound_to_visibility_transition_not_every_heartbeat():
    from types import SimpleNamespace

    from monster.ui.overlay_client import OverlayClient
    from monster.ui.overlay_mailbox import MAILBOX_SIZE, Mailbox

    client = OverlayClient.__new__(OverlayClient)
    client.closed = False
    client.seq = client.hide_seq = 0
    client.desired = {key: value for key, value in packet().items()
                      if key not in ('version', 'seq', 'timestamp_ns', 'closing')}
    client.box = Mailbox(bytearray(MAILBOX_SIZE))
    client.active = True
    client.error = None
    client._last_visibility = None
    client.process = SimpleNamespace(running=True)
    client._publish()
    hidden_at = client.hide_seq
    client.box.write(1, {'seq': hidden_at, 'hidden': True, 'ready': True})
    client._publish()
    assert client.hide_seq == hidden_at and client.hidden_confirmed


def fake_client(monkeypatch, ui_access=True):
    from types import SimpleNamespace

    from monster.ui import overlay_client as module
    from monster.ui.overlay_mailbox import MAILBOX_SIZE

    class Memory(bytearray):
        def close(self):
            pass

    process = SimpleNamespace(running=True, ui_access=ui_access)

    def close():
        process.running = False
        return True

    process.close = close
    now = [20.0]
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    monkeypatch.setattr(module, 'mmap', SimpleNamespace(mmap=lambda *a, **kw: Memory(MAILBOX_SIZE)))
    monkeypatch.setattr(module, 'OverlayProcess', lambda *a: process)
    monkeypatch.setattr(module, 'time', SimpleNamespace(monotonic=lambda: now[0],
                        monotonic_ns=lambda: int(now[0] * 1e9)))
    client = module.OverlayClient('explicit-helper-path')
    client.update(**{key: value for key, value in packet(visible=True).items()
                     if key not in ('version', 'seq', 'timestamp_ns', 'closing')})
    return client, now, process


def test_renderer_rejects_stale_ready_ack_and_falls_back_if_unresponsive(monkeypatch):
    client, now, process = fake_client(monkeypatch)
    client.box.write(1, {'seq': client.seq, 'hidden': True, 'ready': True})
    client.pump()
    assert client.active
    now[0] += 3
    client.pump()
    assert not client.active and not process.running
    assert client.error == 'renderer_unresponsive'


def test_helper_ready_message_cannot_replace_os_uiaccess_verification(monkeypatch):
    client, _, _ = fake_client(monkeypatch, ui_access=False)
    client.box.write(1, {'seq': client.seq, 'hidden': True, 'ready': True})
    client.pump()
    assert client.active is False
    assert client.box.read(0)['visible'] is False


def test_renderer_import_graph_does_not_load_agent_or_input_worker():
    import os
    import subprocess
    import sys
    code = ('import sys; import monster.ui.overlay_host; '
            'assert "monster.agent.controller" not in sys.modules; '
            'assert "monster.computer.worker" not in sys.modules; '
            'assert "monster.providers.ollama" not in sys.modules')
    result = subprocess.run([sys.executable, '-c', code], check=False, capture_output=True,
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'}, timeout=15)
    assert result.returncode == 0, result.stderr.decode()


def test_renderer_manifest_has_uiaccess_but_does_not_request_administrator():
    import xml.etree.ElementTree as ET
    from pathlib import Path
    root = ET.parse(Path(__file__).parents[1] / 'packaging/windows/overlay.manifest').getroot()
    level = root.find('.//{urn:schemas-microsoft-com:asm.v3}requestedExecutionLevel')
    assert level.attrib == {'level': 'asInvoker', 'uiAccess': 'true'}


def test_transient_torn_ack_after_startup_does_not_kill_healthy_renderer(monkeypatch):
    client, now, process = fake_client(monkeypatch)
    client.box.write(1, {'seq': client.seq, 'hidden': True, 'ready': True})
    client.pump()
    now[0] += 10
    client._publish()
    client.box.write(1, {'seq': client.seq, 'hidden': False, 'ready': True})
    client.pump()
    monkeypatch.setattr(client.box, 'read', lambda channel: None)
    client.pump()
    assert client.active and process.running


def test_remote_character_preserves_pause_and_request_activity_priority(qtbot):
    from monster.config import Preferences
    from monster.ui.companion import Companion

    class Recorder:
        active = True
        hidden_confirmed = True
        error = None

        def update(self, **state):
            self.state = state

        def pump(self):
            pass

        def close(self):
            pass

    renderer = Recorder()
    widget = Companion(Preferences())
    qtbot.addWidget(widget)
    widget.attach_renderer(renderer)
    widget.set_paused(True)
    assert renderer.state['paused'] is True and renderer.state['state'] == 'waiting'
    widget.set_activity('Vision test')
    assert renderer.state['paused'] is False and renderer.state['state'] == 'thinking'
