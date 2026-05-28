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

    def __init__(self, locked: bool = False, opacity: float = 0.84) -> None:
        super().__init__(None)
        self._locked = locked
        self._drag_origin: QPoint | None = None
        self._opacity = float(opacity)
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

        # session (5h) bar row: label · bar · value
        self.session_row, self.session_lead, self.msg_bar, self.session_trail = \
            self._make_bar_row("5H")
        outer.addLayout(self.session_row)

        # weekly bar row
        self.week_row, self.week_lead, self.week_bar, self.week_trail = \
            self._make_bar_row("WK")
        outer.addLayout(self.week_row)

        # burn rate row
        self.burn_row = QHBoxLayout()
        self.burn_row.setSpacing(0)
        self.burn_lbl = QLabel("burn —")
        self.burn_lbl.setObjectName("HudMeta")
        self.burn_row.addWidget(self.burn_lbl)
        self.burn_row.addStretch()
        self.burn_proj_lbl = QLabel("")
        self.burn_proj_lbl.setObjectName("HudMeta")
        self.burn_row.addWidget(self.burn_proj_lbl)
        outer.addLayout(self.burn_row)

        # bottom row: tokens · cost
        bot = QHBoxLayout()
        bot.setSpacing(0)
        self.tok_lbl = QLabel("0 tok")
        self.tok_lbl.setObjectName("HudMeta")
        bot.addWidget(self.tok_lbl)
        bot.addStretch()
        self.cost_lbl = QLabel("$0.00")
        self.cost_lbl.setObjectName("HudMeta")
        bot.addWidget(self.cost_lbl)
        outer.addLayout(bot)

    def _make_bar_row(self, lead_text: str):
        row = QHBoxLayout()
        row.setSpacing(8)
        lead = QLabel(lead_text)
        lead.setObjectName("HudRowLead")
        lead.setFixedWidth(22)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setObjectName("HudBar")
        trail = QLabel("—")
        trail.setObjectName("HudRowTrail")
        trail.setMinimumWidth(88)
        trail.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row.addWidget(lead, 0, Qt.AlignVCenter)
        row.addWidget(bar, 1)
        row.addWidget(trail, 0, Qt.AlignVCenter)
        return row, lead, bar, trail

    # ---------- painting ----------
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 14, 14)
        bg_alpha = max(0, min(255, int(round(self._opacity * 255))))
        border_alpha = int(bg_alpha * 200 / 215)
        p.fillPath(path, QColor(30, 30, 46, bg_alpha))
        p.setPen(QColor(69, 71, 90, border_alpha))
        p.drawPath(path)

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.3, min(1.0, float(opacity)))
        self.update()

    # ---------- update ----------
    def update_view(self, snap: Snapshot, plan_key: str, *,
                    show_tokens: bool, show_cost: bool, show_burnrate: bool,
                    display_mode: str) -> None:
        plan = PLAN_LIMITS.get(plan_key, PLAN_LIMITS["max5"])
        self.plan_lbl.setText(plan["label"])

        if snap.session_active and snap.session_reset:
            self.dot.set_state("active")
            remaining = snap.session_reset - snap.now
            self.remain_lbl.setText(fmt_duration(remaining) + " left")
        else:
            self.dot.set_state("idle")
            self.remain_lbl.setText("idle")

        # 5-hour session bar
        msgs = snap.session.messages
        cap = plan["msgs_5h"]
        if cap:
            pct = min(100, msgs * 100 // cap)
            self.msg_bar.setValue(pct)
            self.session_trail.setText(_fmt_trail(display_mode, pct, f"{msgs}/{cap}"))
            self._color_bar(self.msg_bar, pct)
        else:
            self.msg_bar.setValue(0)
            self.session_trail.setText(f"{msgs} msgs")
            self._color_bar(self.msg_bar, 0)

        # weekly bar — pick whichever model is the binding constraint
        pick = self._pick_weekly(snap, plan)
        if pick is None:
            self.week_lead.setVisible(False)
            self.week_bar.setVisible(False)
            self.week_trail.setVisible(False)
        else:
            family, hours, cap_h = pick
            self.week_lead.setVisible(True)
            self.week_bar.setVisible(True)
            self.week_trail.setVisible(True)
            pct_w = min(100, int(hours / cap_h * 100)) if cap_h else 0
            self.week_bar.setValue(pct_w)
            self.week_trail.setText(_fmt_trail(display_mode, pct_w, f"{hours:.1f}/{cap_h}h"))
            self._color_bar(self.week_bar, pct_w)

        self.tok_lbl.setText(f"{fmt_tokens(snap.session.total_tokens)} tok")
        self.cost_lbl.setText(f"${snap.session.cost:.2f}")
        self.tok_lbl.setVisible(show_tokens)
        self.cost_lbl.setVisible(show_cost)

        # burn rate
        show_burn = show_burnrate and snap.session_active and snap.session_msg_per_hour > 0
        self.burn_lbl.setVisible(show_burn)
        self.burn_proj_lbl.setVisible(show_burn)
        if show_burn:
            rate = snap.session_msg_per_hour
            self.burn_lbl.setText(f"burn  {rate:.0f} msg/h")
            if cap and snap.session_reset:
                remaining_h = (snap.session_reset - snap.now).total_seconds() / 3600.0
                projected_msgs = snap.session.messages + rate * remaining_h
                proj_pct = int(projected_msgs / cap * 100)
                colour = "#a6e3a1"                  # green
                if proj_pct >= 100:
                    colour = "#f38ba8"              # red
                elif proj_pct >= 80:
                    colour = "#f9e2af"              # amber
                self.burn_proj_lbl.setText(f"proj  {proj_pct}%")
                self.burn_proj_lbl.setStyleSheet(f"color: {colour};")
            else:
                self.burn_proj_lbl.setText("")

        self.adjustSize()

    def _pick_weekly(self, snap: Snapshot, plan: dict):
        opus_h = snap.week_minutes_opus / 60.0
        sonnet_h = snap.week_minutes_sonnet / 60.0
        opus_cap = plan.get("weekly_opus_h", 0) or 0
        sonnet_cap = plan.get("weekly_sonnet_h", 0) or 0
        if opus_cap == 0 and sonnet_cap == 0:
            return None
        opus_pct = (opus_h / opus_cap) if opus_cap else -1.0
        sonnet_pct = (sonnet_h / sonnet_cap) if sonnet_cap else -1.0
        if opus_pct >= sonnet_pct and opus_cap:
            return ("opus", opus_h, opus_cap)
        return ("sonnet", sonnet_h, sonnet_cap)

    def _color_bar(self, bar: QProgressBar, pct: int) -> None:
        name = "HudBar"
        if pct >= 90:
            name = "HudBarDanger"
        elif pct >= 75:
            name = "HudBarWarn"
        if bar.objectName() != name:
            bar.setObjectName(name)
            bar.style().unpolish(bar)
            bar.style().polish(bar)

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


def _fmt_trail(mode: str, pct: int, raw: str) -> str:
    if mode == "raw":
        return raw
    if mode == "both":
        return f"{pct}%  ·  {raw}"
    return f"{pct}%"
