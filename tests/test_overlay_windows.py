"""Opt-in native renderer tests; no target app input or screenshots.

Token/IPC lifecycle evidence is not a visual assertion about the Start menu.
"""

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Native Windows UIAccess APIs required')


def test_ordinary_python_process_does_not_have_uiaccess():
    from monster.ui.overlay_windows import ui_access_enabled
    assert not ui_access_enabled()


def test_renderer_cannot_launch_from_an_untrusted_workspace(tmp_path):
    from monster.ui.overlay_windows import trusted_helper
    helper = tmp_path / 'MonsterOverlay.exe'
    helper.write_bytes(b'MZ')
    with pytest.raises(RuntimeError, match='Program Files'):
        trusted_helper(helper)


@pytest.mark.skipif(not os.environ.get('MONSTER_UIACCESS_HELPER'),
                    reason='Explicit installed signed helper path required; never build/install automatically')
def test_installed_signed_renderer_token_visibility_ack_and_shutdown():
    from monster.ui.overlay_client import OverlayClient
    client = OverlayClient(os.environ['MONSTER_UIACCESS_HELPER'])
    appearance = {'offset_x': 26, 'offset_y': 20, 'reduced_motion': False,
                  'companion_scale': 100, 'companion_position': 'cursor'}

    def wait(predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            client.pump()
            if predicate():
                return
            time.sleep(.02)
        raise AssertionError('Native renderer did not acknowledge: ' + str(client.error))

    try:
        client.update(state='thinking', character='monster', visible=False, paused=False, appearance=appearance)
        wait(lambda: client.active and client.hidden_confirmed)
        assert client.process.ui_access is True
        client.update(state='working', character='rabbit', visible=True, paused=False, appearance=appearance)
        wait(lambda: client.ack is not None and client.ack['hidden'] is False)
        client.update(state='working', character='rabbit', visible=False, paused=False, appearance=appearance)
        wait(lambda: client.hidden_confirmed)
    finally:
        client.close()
    assert not client.process.running and client.hidden_confirmed
