"""Always-on-top translucent HUD pinned to a corner of the screen.

Compact card showing:
  - active/idle dot + plan label
  - time remaining in the current 5-hour window
  - message-cap progress bar
  - token total + cost estimate
Drag to reposition; right-click for menu.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QRect, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from pricing import PLAN_LIMITS
from usage import Snapshot, fmt_duration, fmt_tokens

HUD_WIDTH = 300
MARGIN = 14


class Overlay(QWidget):
    move_finished = Signal(QPoint)
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self, locked: bool = False) -> None:
        super().__init__(None)
        self._locked = locked
        self._drag_origin: QPoint | None = None
        self._build()

    # ---------- window setup ----------
    def _build(self) -> None:
        self.setObjectName("Hud")
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(HUD_WIDTH)
        self.setMouseTracking(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(8)

        # top row: status dot + brand + plan + remaining
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = StatusDot()
        top.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.brand_lbl = QLabel("CLAUDE")
        self.brand_lbl.setObjectName("HudBrand")
        top.addWidget(self.brand_lbl)
        self.plan_lbl = QLabel("Max 5×")
        self.plan_lbl.setObjectName("HudPlan")
        top.addWidget(self.plan_lbl)
        top.addStretch()
        self.remain_lbl = QLabel("—")
        self.remain_lbl.setObjectName("HudRemain")
        top.addWidget(self.remain_lbl, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        # messages bar
        self.msg_bar = QProgressBar()
        self.msg_bar.setRange(0, 100)
        self.msg_bar.setTextVisible(False)
        self.msg_bar.setObjectName("HudBar")
        outer.addWidget(self.msg_bar)

        # bottom row: msg count · tokens · cost
        bot = QHBoxLayout()
        bot.setSpacing(0)
        self.msg_lbl = QLabel("0 msgs")
        self.msg_lbl.setObjectName("HudMeta")
        bot.addWidget(self.msg_lbl)
        bot.addStretch()
        self.tok_lbl = QLabel("0 tok")
        self.tok_lbl.setObjectName("HudMeta")
        bot.addWidget(self.tok_lbl)
        sep = QLabel(" · ")
        sep.setObjectName("HudMeta")
        bot.addWidget(sep)
        self.cost_lbl = QLabel("$0.00")
        self.cost_lbl.setObjectName("HudMeta")
        bot.addWidget(self.cost_lbl)
        outer.addLayout(bot)

    # ---------- painting ----------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        p.fillPath(path, QColor(30, 30, 46, 215))     # base, ~84% opacity
        p.setPen(QColor(69, 71, 90, 200))             # subtle border
        p.drawPath(path)

    # ---------- update ----------
    def update_view(self, snap: Snapshot, plan_key: str, show_cost: bool) -> None:
        plan = PLAN_LIMITS.get(plan_key, PLAN_LIMITS["max5"])
        self.plan_lbl.setText(plan["label"])

        if snap.session_active and snap.session_reset:
            self.dot.set_state("active")
            remaining = snap.session_reset - snap.now
            self.remain_lbl.setText(fmt_duration(remaining) + " left")
        else:
            self.dot.set_state("idle")
            self.remain_lbl.setText("idle")

        msgs = snap.session.messages
        cap = plan["msgs_5h"]
        if cap:
            pct = min(100, msgs * 100 // cap)
            self.msg_bar.setValue(pct)
            self.msg_lbl.setText(f"{msgs}/{cap} msgs · {pct}%")
            self._color_bar(pct)
        else:
            self.msg_bar.setValue(0)
            self.msg_lbl.setText(f"{msgs} msgs")
            self._color_bar(0)

        tok = snap.session.total_tokens
        self.tok_lbl.setText(f"{fmt_tokens(tok)} tok")
        if show_cost:
            self.cost_lbl.setText(f"${snap.session.cost:.2f}")
            self.cost_lbl.setVisible(True)
        else:
            self.cost_lbl.setVisible(False)

        self.adjustSize()

    def _color_bar(self, pct: int) -> None:
        name = "HudBar"
        if pct >= 90:
            name = "HudBarDanger"
        elif pct >= 75:
            name = "HudBarWarn"
        if self.msg_bar.objectName() != name:
            self.msg_bar.setObjectName(name)
            self.msg_bar.style().unpolish(self.msg_bar)
            self.msg_bar.style().polish(self.msg_bar)

    # ---------- positioning ----------
    def place_corner(self, corner: str) -> None:
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.adjustSize()
        w, h = self.width(), self.height()
        if corner == "tl":
            pos = QPoint(geo.left() + MARGIN, geo.top() + MARGIN)
        elif corner == "bl":
            pos = QPoint(geo.left() + MARGIN, geo.bottom() - h - MARGIN)
        elif corner == "br":
            pos = QPoint(geo.right() - w - MARGIN, geo.bottom() - h - MARGIN)
        else:  # tr
            pos = QPoint(geo.right() - w - MARGIN, geo.top() + MARGIN)
        self.move(pos)

    def restore_position(self, x: int | None, y: int | None, corner: str) -> None:
        if x is None or y is None:
            self.place_corner(corner)
            return
        # Clamp into the available geometry so we never restore offscreen.
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        self.adjustSize()
        x = max(geo.left() + MARGIN, min(x, geo.right() - self.width() - MARGIN))
        y = max(geo.top() + MARGIN, min(y, geo.bottom() - self.height() - MARGIN))
        self.move(x, y)

    def set_locked(self, locked: bool) -> None:
        self._locked = locked

    # ---------- mouse: drag + context ----------
    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton and not self._locked:
            self._drag_origin = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
        elif e.button() == Qt.RightButton:
            self._show_menu(e.globalPosition().toPoint())
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_origin and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag_origin)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        if self._drag_origin is not None:
            self._drag_origin = None
            self.move_finished.emit(self.pos())
            e.accept()

    def _show_menu(self, at: QPoint) -> None:
        m = QMenu(self)
        m.addAction(_act("Refresh", self.refresh_requested.emit, m))
        m.addSeparator()
        for label, corner in (("Top-right", "tr"), ("Top-left", "tl"),
                              ("Bottom-right", "br"), ("Bottom-left", "bl")):
            a = QAction(f"Snap → {label}", m)
            a.triggered.connect(lambda _=False, c=corner: self._snap(c))
            m.addAction(a)
        m.addSeparator()
        lock = QAction("Unlock drag" if self._locked else "Lock position", m, checkable=False)
        lock.triggered.connect(self._toggle_lock)
        m.addAction(lock)
        m.addSeparator()
        m.addAction(_act("Quit", self.quit_requested.emit, m))
        m.exec(at)

    def _snap(self, corner: str) -> None:
        self.place_corner(corner)
        self.move_finished.emit(self.pos())

    def _toggle_lock(self) -> None:
        self._locked = not self._locked


class StatusDot(QWidget):
    def __init__(self) -> None:
        super().__init__(None)
        self.setFixedSize(10, 10)
        self._state = "idle"

    def set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._state == "active":
            inner = QColor("#a6e3a1")
            outer = QColor(166, 227, 161, 80)
        else:
            inner = QColor("#7f849c")
            outer = QColor(127, 132, 156, 60)
        p.setBrush(outer)
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, 10, 10)
        p.setBrush(inner)
        p.drawEllipse(2, 2, 6, 6)


def _act(label: str, slot, parent) -> QAction:
    a = QAction(label, parent)
    a.triggered.connect(slot)
    return a
