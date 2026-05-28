"""User config loaded from ~/.config/claude-tray/config.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "claude-tray" / "config.toml"

DEFAULT: dict = {
    "plan": "max5",          # pro | max5 | max20 | api
    "refresh_seconds": 30,
    "show_tokens": True,
    "show_cost": True,
    "display_mode": "percent",  # percent | raw | both
    "opacity": 0.84,         # 0.30 .. 1.00
    "corner": "tr",          # tr | tl | br | bl  — used when no saved position
    "pos_x": None,
    "pos_y": None,
    "locked": False,
    "tray_mode": "always",   # always | hover  — left-click tray to toggle
}


def load() -> dict:
    cfg = dict(DEFAULT)
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open("rb") as fh:
                data = tomllib.load(fh)
            for k in DEFAULT:
                if k in data:
                    cfg[k] = data[k]
        except (OSError, tomllib.TOMLDecodeError):
            pass
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Claude tray HUD config",
        '# plan: "pro" | "max5" | "max20" | "api"',
        f'plan = "{cfg["plan"]}"',
        f"refresh_seconds = {int(cfg['refresh_seconds'])}",
        f"show_tokens = {'true' if cfg['show_tokens'] else 'false'}",
        f"show_cost = {'true' if cfg['show_cost'] else 'false'}",
        f'display_mode = "{cfg["display_mode"]}"',
        f"opacity = {float(cfg['opacity']):.2f}",
        f'corner = "{cfg["corner"]}"',
        f"locked = {'true' if cfg['locked'] else 'false'}",
        f'tray_mode = "{cfg["tray_mode"]}"',
    ]
    if cfg.get("pos_x") is not None and cfg.get("pos_y") is not None:
        lines.append(f"pos_x = {int(cfg['pos_x'])}")
        lines.append(f"pos_y = {int(cfg['pos_y'])}")
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_default_if_missing() -> None:
    if not CONFIG_PATH.exists():
        save(DEFAULT)
