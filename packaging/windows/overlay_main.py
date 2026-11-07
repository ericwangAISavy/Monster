"""Frozen renderer bootstrap. Sanitize plugin/import environment before Qt loads."""

import os
import sys
from pathlib import Path

for key in tuple(os.environ):
    if key.startswith(('QT_', 'QML_', 'QML2_', 'PYSIDE_', 'PYTHON')):
        os.environ.pop(key, None)

# Restore only the frozen bundle's trusted Qt plugin path after clearing inherited
# overrides. PyInstaller's Qt runtime hook normally supplies this environment.
bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)).resolve()
for relative in ('PySide6/plugins', 'PySide6/Qt/plugins'):
    plugins = bundle / relative
    if plugins.is_dir() and plugins.resolve().is_relative_to(bundle):
        os.environ['QT_PLUGIN_PATH'] = str(plugins)
        os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(plugins / 'platforms')
        break

from monster.ui.overlay_host import main

if __name__ == '__main__':
    raise SystemExit(main())
