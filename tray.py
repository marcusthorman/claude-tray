"""HUD overlay app. The system tray icon is a small handle for show/hide/quit;
the always-visible overlay is the primary surface."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon
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
        self.hud = Overlay(locked=bool(self.cfg["locked"]),
                           opacity=float(self.cfg["opacity"]))
        self.hud.restore_position(self.cfg["pos_x"], self.cfg["pos_y"], self.cfg["corner"])
        self.hud.move_finished.connect(self._on_moved)
        self.hud.refresh_requested.connect(self.refresh)
        self.hud.quit_requested.connect(self.app.quit)

        # Tray icon (handle for show/hide + settings + quit)
        icon = QIcon(str(ROOT / "icon.svg"))
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("Claude usage HUD")
        self.tray.setContextMenu(self._build_menu())
        self.tray.activated.connect(self._on_tray_clicked)

        self.snapshot = None
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(self.cfg["refresh_seconds"]) * 1000)

        self.refresh()
        self.tray.show()
        self.hud.show()

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self.toggle_act = QAction(
            "Hide HUD" if self.hud.isVisible() else "Show HUD", self.tray)
        self.toggle_act.triggered.connect(self._toggle_visible)
        menu.addAction(self.toggle_act)
        refresh_act = QAction("Refresh", self.tray)
        refresh_act.triggered.connect(self.refresh)
        menu.addAction(refresh_act)
        menu.addSeparator()

        # Display mode (percent / raw / both)
        disp = menu.addMenu("Display")
        self._mode_group = QActionGroup(self.tray)
        self._mode_group.setExclusive(True)
        for label, key in (("Percentage", "percent"), ("Raw values", "raw"),
                           ("Both", "both")):
            a = QAction(label, self.tray, checkable=True)
            a.setChecked(self.cfg["display_mode"] == key)
            a.triggered.connect(lambda _=False, k=key: self._set_display_mode(k))
            self._mode_group.addAction(a)
            disp.addAction(a)

        # Toggles: tokens, cost
        self._tok_act = QAction("Show tokens", self.tray, checkable=True)
        self._tok_act.setChecked(bool(self.cfg["show_tokens"]))
        self._tok_act.triggered.connect(
            lambda c: self._set_flag("show_tokens", c))
        menu.addAction(self._tok_act)
        self._cost_act = QAction("Show price", self.tray, checkable=True)
        self._cost_act.setChecked(bool(self.cfg["show_cost"]))
        self._cost_act.triggered.connect(
            lambda c: self._set_flag("show_cost", c))
        menu.addAction(self._cost_act)
        self._burn_act = QAction("Show burnrate", self.tray, checkable=True)
        self._burn_act.setChecked(bool(self.cfg["show_burnrate"]))
        self._burn_act.triggered.connect(
            lambda c: self._set_flag("show_burnrate", c))
        menu.addAction(self._burn_act)
        menu.addSeparator()

        # Opacity (DBusMenu can't transport an embedded slider, so it lives as
        # a submenu of discrete radio options).
        op_menu = menu.addMenu("Opacity")
        self._op_group = QActionGroup(self.tray)
        self._op_group.setExclusive(True)
        current_pct = int(round(float(self.cfg["opacity"]) * 100))
        for pct in (30, 40, 50, 60, 70, 80, 90, 100):
            a = QAction(f"{pct}%", self.tray, checkable=True)
            a.setChecked(pct == current_pct)
            a.triggered.connect(lambda _=False, p=pct: self._set_opacity(p))
            self._op_group.addAction(a)
            op_menu.addAction(a)
        menu.addSeparator()

        # Position
        for label, corner in (("Snap top-right", "tr"), ("Snap top-left", "tl"),
                              ("Snap bottom-right", "br"), ("Snap bottom-left", "bl")):
            a = QAction(label, self.tray)
            a.triggered.connect(lambda _=False, c=corner: self._snap(c))
            menu.addAction(a)
        menu.addSeparator()

        quit_act = QAction("Quit", self.tray)
        quit_act.triggered.connect(self.app.quit)
        menu.addAction(quit_act)
        return menu

    def _set_display_mode(self, mode: str) -> None:
        self.cfg["display_mode"] = mode
        config.save(self.cfg)
        self.refresh()

    def _set_flag(self, key: str, value: bool) -> None:
        self.cfg[key] = bool(value)
        config.save(self.cfg)
        self.refresh()

    def _set_opacity(self, value_0_100: int) -> None:
        opacity = max(0.30, min(1.0, value_0_100 / 100.0))
        self.cfg["opacity"] = opacity
        self.hud.set_opacity(opacity)
        config.save(self.cfg)

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
        self.hud.update_view(
            self.snapshot,
            self.cfg["plan"],
            show_tokens=bool(self.cfg["show_tokens"]),
            show_cost=bool(self.cfg["show_cost"]),
            show_burnrate=bool(self.cfg["show_burnrate"]),
            display_mode=str(self.cfg["display_mode"]),
        )
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
