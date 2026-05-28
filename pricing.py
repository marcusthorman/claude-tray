"""Token pricing per model. Prices in USD per 1M tokens.

Anthropic published rates (knowledge cutoff Jan 2026). Override in
~/.config/claude-tray/config.toml under [pricing.<model>] if needed.
"""

from __future__ import annotations

PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-7":     {"input": 15.0, "output": 75.0},
    "claude-opus-4-6":     {"input": 15.0, "output": 75.0},
    "claude-opus-4":       {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6":   {"input": 3.0,  "output": 15.0},
    "claude-sonnet-4-5":   {"input": 3.0,  "output": 15.0},
    "claude-sonnet-4":     {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":    {"input": 1.0,  "output": 5.0},
    "claude-3-5-sonnet":   {"input": 3.0,  "output": 15.0},
    "claude-3-5-haiku":    {"input": 0.80, "output": 4.0},
}

DEFAULT = {"input": 3.0, "output": 15.0}

CACHE_WRITE_5M_MULT = 1.25
CACHE_WRITE_1H_MULT = 2.0
CACHE_READ_MULT = 0.1


def model_key(model: str | None) -> str:
    if not model:
        return "claude-sonnet-4-6"
    m = model.lower()
    for k in PRICING:
        if m.startswith(k):
            return k
    if "opus" in m:
        return "claude-opus-4-7"
    if "haiku" in m:
        return "claude-haiku-4-5"
    return "claude-sonnet-4-6"


def cost_for(model: str | None, usage: dict) -> float:
    rates = PRICING.get(model_key(model), DEFAULT)
    inp = rates["input"]
    out = rates["output"]

    base_in = usage.get("input_tokens", 0) or 0
    out_tok = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0

    cc = usage.get("cache_creation") or {}
    write_5m = cc.get("ephemeral_5m_input_tokens", 0) or 0
    write_1h = cc.get("ephemeral_1h_input_tokens", 0) or 0
    if not (write_5m or write_1h):
        write_5m = usage.get("cache_creation_input_tokens", 0) or 0

    per = 1_000_000
    return (
        base_in   * inp / per
        + out_tok * out / per
        + cache_read * inp * CACHE_READ_MULT / per
        + write_5m   * inp * CACHE_WRITE_5M_MULT / per
        + write_1h   * inp * CACHE_WRITE_1H_MULT / per
    )


PLAN_LIMITS = {
    "pro":     {"label": "Pro",     "msgs_5h": 45,  "weekly_sonnet_h": 80,  "weekly_opus_h": 0},
    "max5":    {"label": "Max 5×",  "msgs_5h": 225, "weekly_sonnet_h": 280, "weekly_opus_h": 35},
    "max20":   {"label": "Max 20×", "msgs_5h": 900, "weekly_sonnet_h": 480, "weekly_opus_h": 40},
    "api":     {"label": "API",     "msgs_5h": 0,   "weekly_sonnet_h": 0,   "weekly_opus_h": 0},
}
