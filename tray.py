"""HUD overlay app. The system tray icon is a small handle for show/hide/quit;
the always-visible overlay is the primary surface."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QSystemTrayIcon,
    QWidget,
    QWidgetAction,
)

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

        # Hover-mode polling: KDE doesn't give us tray-icon hover events, so we
        # poll cursor position against the panel corner that hosts the tray.
        self._hover_timer = QTimer()
        self._hover_timer.setInterval(180)
        self._hover_timer.timeout.connect(self._hover_tick)

        self.refresh()
        self.tray.show()
        self._apply_tray_mode(initial=True)

    def _build_menu(self) -> QMenu:
        menu = QMenu()

        self.hover_mode_act = QAction("Hover-only mode", self.tray, checkable=True)
        self.hover_mode_act.setChecked(self.cfg["tray_mode"] == "hover")
        self.hover_mode_act.triggered.connect(
            lambda c: self._set_tray_mode("hover" if c else "always"))
        menu.addAction(self.hover_mode_act)
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
        menu.addSeparator()

        # Opacity slider (QWidgetAction)
        menu.addAction(_opacity_slider(menu, self.cfg["opacity"], self._set_opacity))
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

    def _set_opacity(self, value_0_100: int, *, save: bool = False) -> None:
        opacity = max(0.30, min(1.0, value_0_100 / 100.0))
        self.cfg["opacity"] = opacity
        self.hud.set_opacity(opacity)
        if save:
            config.save(self.cfg)

    def _on_tray_clicked(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.MiddleClick):
            self._set_tray_mode("hover" if self.cfg["tray_mode"] == "always" else "always")

    def _set_tray_mode(self, mode: str) -> None:
        if mode not in ("always", "hover"):
            return
        self.cfg["tray_mode"] = mode
        self.hover_mode_act.setChecked(mode == "hover")
        config.save(self.cfg)
        self._apply_tray_mode()

    def _apply_tray_mode(self, initial: bool = False) -> None:
        if self.cfg["tray_mode"] == "hover":
            self.hud.hide()
            self._hover_timer.start()
        else:
            self._hover_timer.stop()
            self.hud.show()

    def _hover_tick(self) -> None:
        cursor = QCursor.pos()
        in_tray_zone = _tray_zone().contains(cursor)
        hud_geo = self.hud.frameGeometry() if self.hud.isVisible() else QRect()
        in_hud = self.hud.isVisible() and hud_geo.adjusted(-8, -8, 8, 8).contains(cursor)
        if in_tray_zone or in_hud:
            if not self.hud.isVisible():
                self.hud.show()
        else:
            if self.hud.isVisible():
                self.hud.hide()

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


def _tray_zone() -> QRect:
    """Best-effort estimate of the screen region occupied by the system-tray
    portion of the Plasma panel. We can't query the tray-icon's actual
    geometry on KDE (Qt returns an invalid rect for SNI items), so we
    locate the panel band from screen-vs-workarea diff and take the
    corner-side slice that normally hosts the tray."""
    screen = QGuiApplication.primaryScreen()
    if not screen:
        return QRect()
    full = screen.geometry()
    avail = screen.availableGeometry()
    panel_h_bottom = full.bottom() - avail.bottom()
    panel_h_top = avail.top() - full.top()
    panel_w_right = full.right() - avail.right()
    panel_w_left = avail.left() - full.left()
    zone = 360  # slice length along the panel's long axis
    if panel_h_bottom > 0:                          # bottom panel
        return QRect(full.right() - zone, avail.bottom() + 1, zone, panel_h_bottom)
    if panel_h_top > 0:                             # top panel
        return QRect(full.right() - zone, full.top(), zone, panel_h_top)
    if panel_w_right > 0:                           # right panel
        return QRect(avail.right() + 1, full.bottom() - zone, panel_w_right, zone)
    if panel_w_left > 0:                            # left panel
        return QRect(full.left(), full.bottom() - zone, panel_w_left, zone)
    return QRect()


def _opacity_slider(parent: QMenu, current: float, on_change) -> QWidgetAction:
    """Slider embedded in a menu item via QWidgetAction. Updates the HUD live;
    persists to config only on slider release."""
    container = QWidget(parent)
    lay = QHBoxLayout(container)
    lay.setContentsMargins(12, 4, 12, 4)
    lay.setSpacing(8)
    label = QLabel("Opacity")
    label.setObjectName("MenuLabel")
    label.setFixedWidth(60)
    slider = QSlider(Qt.Horizontal)
    slider.setMinimum(30)
    slider.setMaximum(100)
    slider.setValue(int(round(float(current) * 100)))
    slider.setFixedWidth(140)
    pct = QLabel(f"{slider.value()}%")
    pct.setFixedWidth(36)
    pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

    def _on_val(v):
        pct.setText(f"{v}%")
        on_change(v, save=False)

    def _on_release():
        on_change(slider.value(), save=True)

    slider.valueChanged.connect(_on_val)
    slider.sliderReleased.connect(_on_release)

    lay.addWidget(label)
    lay.addWidget(slider, 1)
    lay.addWidget(pct)

    wa = QWidgetAction(parent)
    wa.setDefaultWidget(container)
    return wa


def main() -> int:
    return HudApp().run()


if __name__ == "__main__":
    sys.exit(main())
