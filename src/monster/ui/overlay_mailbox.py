"""Appearance-only local mailbox. No text, screenshots, target commands or file paths."""

import hashlib
import json
import struct

from monster.characters import CHARACTERS
from monster.lifecycle import State

CHANNEL_SIZE = 8192
MAILBOX_SIZE = CHANNEL_SIZE * 2
MAX_PAYLOAD = 4096
HEADER = struct.Struct('<I32s')
APPEARANCE = ('offset_x', 'offset_y', 'reduced_motion', 'companion_scale', 'companion_position')


def validate_state(value):
    if type(value) is not dict or set(value) != {
        'version', 'seq', 'timestamp_ns', 'state', 'character', 'visible', 'paused', 'closing', 'appearance',
    }:
        raise ValueError('Invalid overlay state fields.')
    if (type(value['version']) is not int or value['version'] != 1
            or type(value['seq']) is not int or not 1 <= value['seq'] < 2**53
            or type(value['timestamp_ns']) is not int or not 0 <= value['timestamp_ns'] < 2**63
            or type(value['state']) is not str or value['state'] not in {state.value for state in State}
            or type(value['character']) is not str or value['character'] not in CHARACTERS
            or type(value['visible']) is not bool or type(value['paused']) is not bool
            or type(value['closing']) is not bool):
        raise ValueError('Invalid overlay state values.')
    prefs = value['appearance']
    if type(prefs) is not dict or set(prefs) != set(APPEARANCE):
        raise ValueError('Invalid overlay appearance fields.')
    for field, lower, upper in (('offset_x', 16, 80), ('offset_y', 12, 80), ('companion_scale', 75, 150)):
        if type(prefs[field]) is not int or not lower <= prefs[field] <= upper:
            raise ValueError('Invalid overlay appearance values.')
    if (type(prefs['reduced_motion']) is not bool or type(prefs['companion_position']) is not str
            or prefs['companion_position'] not in ('cursor', 'top_right', 'bottom_right')):
        raise ValueError('Invalid overlay appearance values.')
    return value


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate key.')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('Nonfinite value.')


class Mailbox:
    """Checksummed double-read snapshots; corrupt/torn data is never acted on.

    Channels have one writer each. Hashing detects torn writes, not a hostile local
    user. This visual-only protocol is not an authorization/security boundary.
    """
    def __init__(self, memory):
        if len(memory) != MAILBOX_SIZE:
            raise ValueError('Invalid mailbox size.')
        self.memory = memory

    def write(self, channel, value):
        if type(channel) is not int or channel not in (0, 1):
            raise ValueError('Invalid channel.')
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
        if not 1 <= len(raw) <= MAX_PAYLOAD:
            raise ValueError('Overlay message exceeds limit.')
        data = HEADER.pack(len(raw), hashlib.sha256(raw).digest()) + raw
        start = channel * CHANNEL_SIZE
        self.memory[start:start + len(data)] = data

    def read(self, channel):
        if type(channel) is not int or channel not in (0, 1):
            raise ValueError('Invalid channel.')
        start = channel * CHANNEL_SIZE
        data = bytes(self.memory[start:start + CHANNEL_SIZE])
        if data != bytes(self.memory[start:start + CHANNEL_SIZE]):
            return None
        length, digest = HEADER.unpack_from(data)
        if not 1 <= length <= MAX_PAYLOAD:
            return None
        raw = data[HEADER.size:HEADER.size + length]
        if hashlib.sha256(raw).digest() != digest:
            return None
        try:
            value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
        except (ValueError, RecursionError, UnicodeError):
            return None
        return value if type(value) is dict else None


class RendererLease:
    def __init__(self, now_ns):
        self.last_valid = now_ns
        self.last_seq = 0

    def accept(self, data, now_ns):
        if data is None:
            return 'close' if now_ns - self.last_valid > 2_500_000_000 else 'wait'
        try:
            validate_state(data)
        except ValueError:
            return 'close'
        if (now_ns - data['timestamp_ns'] > 2_500_000_000
                or data['timestamp_ns'] - now_ns > 500_000_000):
            return 'close'
        if data['seq'] < self.last_seq:
            return 'wait'
        self.last_valid, self.last_seq = now_ns, data['seq']
        return 'apply'
