"""Small task-scoped keyboard policy shared by generation and local validation."""

import re

NEW_TERMINAL_WINDOW = ['CTRL', 'SHIFT', 'N']
ROUTINE_CHARACTER_KEYS = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789') | {'ESC'}


def terminal_window_requested(instruction):
    return bool(re.fullmatch(
        r'\s*(?:(?:now|please)\s+){0,2}(?:open|launch|create)\s+(?:a\s+)?'
        r'(?:(?:new|fresh|another)\s+){1,2}(?:windows\s+)?(?:terminal|powershell)\s+window\s*[.!]?\s*',
        instruction, re.IGNORECASE,
    ))


def validate_shortcut_description(action, summary):
    if action.get('op') not in {'press', 'chord'}:
        return
    descriptions = re.findall(
        r'\b(?:CTRL|CONTROL|ALT|SHIFT|WIN|WINDOWS)(?:\s*\+\s*[A-Z0-9]+){1,3}\b',
        summary, re.IGNORECASE,
    )
    aliases = {'CONTROL': 'CTRL', 'WINDOWS': 'WIN'}
    claims = {tuple(aliases.get(key.strip().upper(), key.strip().upper())
                    for key in text.split('+')) for text in descriptions}
    if not claims:
        return
    keys = tuple(action.get('keys', ())) if action.get('op') == 'chord' else ()
    if (len(claims) != 1 or not keys or any(
            claim[-1] != keys[-1] or set(claim[:-1]) != set(keys[:-1]) for claim in claims)):
        raise ValueError('Shortcut description must match one complete chord action. '
                         'Do not emit a single letter/modifier or describe several alternative chords.')
