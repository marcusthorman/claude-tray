#!/usr/bin/env python3
"""Entry point for `claude-tray`."""

import os

# Force XWayland: Wayland forbids clients setting their own window position,
# which a corner-pinned HUD requires. Under XWayland, KWin honors the X11
# `_NET_WM_STATE_ABOVE` hint, `Qt.Tool` skip-taskbar behavior, and `move()`.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

from tray import main

if __name__ == "__main__":
    raise SystemExit(main())
