"""HUD overlay app. The system tray icon is a small handle for show/hide/quit;
the always-visible overlay is the primary surface."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import usage
from overlay import Overlay

ROOT = Path(__file__).resolve().parent


class HudApp:
    def __init__(self) -> None:
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)
        self.app.setApplicationName("claude-tray")
        self.app.setDesktopFileName("claude-tray")
        self.app.setStyleSheet((ROOT / "style.qss").read_text(encoding="utf-8"))

        config.write_default_if_missing()
        self.cfg = config.load()

        # Overlay
        self.hud = Overlay(locked=bool(self.cfg["locked"]))
        self.hud.restore_position(self.cfg["pos_x"], self.cfg["pos_y"], self.cfg["corner"])
        self.hud.move_finished.connect(self._on_moved)
        self.hud.refresh_requested.connect(self.refresh)
        self.hud.quit_requested.connect(self.app.quit)

        # Tray icon (handle for show/hide + quit)
        icon = QIcon(str(ROOT / "icon.svg"))
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("Claude usage HUD")

        menu = QMenu()
        self.toggle_act = QAction("Hide HUD", self.tray)
        self.toggle_act.triggered.connect(self._toggle_visible)
        menu.addAction(self.toggle_act)
        refresh_act = QAction("Refresh", self.tray)
        refresh_act.triggered.connect(self.refresh)
        menu.addAction(refresh_act)
        menu.addSeparator()
        for label, corner in (("Snap top-right", "tr"), ("Snap top-left", "tl"),
                              ("Snap bottom-right", "br"), ("Snap bottom-left", "bl")):
            a = QAction(label, self.tray)
            a.triggered.connect(lambda _=False, c=corner: self._snap(c))
            menu.addAction(a)
        menu.addSeparator()
        quit_act = QAction("Quit", self.tray)
        quit_act.triggered.connect(self.app.quit)
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_clicked)

        self.snapshot = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(self.cfg["refresh_seconds"]) * 1000)

        self.refresh()
        self.tray.show()
        self.hud.show()

    def _on_tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick):
            self._toggle_visible()

    def _toggle_visible(self) -> None:
        if self.hud.isVisible():
            self.hud.hide()
            self.toggle_act.setText("Show HUD")
        else:
            self.hud.show()
            self.toggle_act.setText("Hide HUD")

    def _snap(self, corner: str) -> None:
        self.hud.place_corner(corner)
        self.cfg["corner"] = corner
        self.cfg["pos_x"] = self.hud.x()
        self.cfg["pos_y"] = self.hud.y()
        config.save(self.cfg)

    def _on_moved(self, pos: QPoint) -> None:
        from PySide6.QtGui import QGuiApplication
        geo = QGuiApplication.primaryScreen().availableGeometry()
        x, y = pos.x(), pos.y()
        if not (geo.left() - 50 <= x <= geo.right() + 50 and
                geo.top() - 50 <= y <= geo.bottom() + 50):
            return  # ignore out-of-screen coords (stale events / spurious moves)
        self.cfg["pos_x"] = x
        self.cfg["pos_y"] = y
        config.save(self.cfg)

    def refresh(self) -> None:
        self.snapshot = usage.compute()
        self.hud.update_view(self.snapshot, self.cfg["plan"], self.cfg["show_cost"])
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        if not self.snapshot:
            return
        s = self.snapshot
        if s.session_active and s.session_reset:
            remaining = s.session_reset - s.now
            mins = max(0, int(remaining.total_seconds() // 60))
            h, m = divmod(mins, 60)
            tip = (f"Claude · active session\n"
                   f"resets in {h}h {m:02d}m\n"
                   f"{s.session.messages} msgs · {usage.fmt_tokens(s.session.total_tokens)} tok")
        else:
            tip = f"Claude · idle\ntoday: {s.today.messages} msgs · {usage.fmt_tokens(s.today.total_tokens)} tok"
        self.tray.setToolTip(tip)

    def run(self) -> int:
        return self.app.exec()


def main() -> int:
    return HudApp().run()


if __name__ == "__main__":
    sys.exit(main())
