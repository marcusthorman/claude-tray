"""Parse ~/.claude/projects/**/*.jsonl into usage stats.

Computes:
  - the active 5-hour Claude Code session (start, elapsed, reset-at)
  - per-session token + cost totals
  - per-day token + cost totals
  - per-project breakdown for today
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pricing import cost_for, model_key

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SESSION_WINDOW = timedelta(hours=5)


@dataclass
class Entry:
    ts: datetime
    kind: str            # "user" | "assistant"
    model: str
    project: str
    in_tok: int = 0
    out_tok: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0


@dataclass
class Totals:
    messages: int = 0    # user prompts only — matches Anthropic's metered count
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cost: float = 0.0
    by_model: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    by_project: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read + self.cache_write

    def add(self, e: Entry) -> None:
        if e.kind == "user":
            self.messages += 1
            return
        # assistant entry
        self.input_tokens += e.in_tok
        self.output_tokens += e.out_tok
        self.cache_read += e.cache_read
        self.cache_write += e.cache_write
        self.cost += e.cost
        self.by_model[model_key(e.model)] += e.in_tok + e.out_tok
        self.by_project[e.project] += e.cost


@dataclass
class Snapshot:
    now: datetime
    session_active: bool
    session_start: datetime | None
    session_reset: datetime | None
    session: Totals
    today: Totals
    week: Totals
    last_message: datetime | None
    # active-hours estimate over the last 7d, split by model family
    week_minutes_opus: float = 0.0
    week_minutes_sonnet: float = 0.0
    week_minutes_haiku: float = 0.0
    week_reset_at: datetime | None = None  # rolling 7d reset point


def _decode_project_dir(name: str) -> str:
    return name.replace("-", "/") if name.startswith("-") else name


def _iter_jsonl_files() -> list[Path]:
    if not PROJECTS_DIR.exists():
        return []
    return sorted(PROJECTS_DIR.rglob("*.jsonl"))


def _is_real_user_prompt(msg: dict) -> bool:
    """User records may be a real prompt (str/text content) or an auto-injected
    tool_result block. Only the former counts toward Anthropic's message cap."""
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if isinstance(content, list):
        return not any(
            isinstance(c, dict) and c.get("type") == "tool_result"
            for c in content
        )
    return False


def _parse_entry(rec: dict, project: str) -> Entry | None:
    rec_type = rec.get("type")
    ts_raw = rec.get("timestamp")
    if not ts_raw or rec_type not in ("assistant", "user"):
        return None
    try:
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    msg = rec.get("message") or {}

    if rec_type == "user":
        if not _is_real_user_prompt(msg):
            return None
        return Entry(ts=ts, kind="user", model="", project=project)

    usage = msg.get("usage")
    if not usage:
        return None
    model = msg.get("model") or rec.get("model") or "claude-sonnet-4-6"
    cc = usage.get("cache_creation") or {}
    write = (cc.get("ephemeral_5m_input_tokens", 0) or 0) + (cc.get("ephemeral_1h_input_tokens", 0) or 0)
    if not write:
        write = usage.get("cache_creation_input_tokens", 0) or 0
    return Entry(
        ts=ts,
        kind="assistant",
        model=model,
        project=project,
        in_tok=usage.get("input_tokens", 0) or 0,
        out_tok=usage.get("output_tokens", 0) or 0,
        cache_read=usage.get("cache_read_input_tokens", 0) or 0,
        cache_write=write,
        cost=cost_for(model, usage),
    )


def load_entries() -> list[Entry]:
    out: list[Entry] = []
    for path in _iter_jsonl_files():
        # project name = first directory under PROJECTS_DIR
        try:
            rel = path.relative_to(PROJECTS_DIR)
        except ValueError:
            continue
        project = _decode_project_dir(rel.parts[0])
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    e = _parse_entry(rec, project)
                    if e:
                        out.append(e)
        except OSError:
            continue
    out.sort(key=lambda e: e.ts)
    return out


ACTIVE_GAP = timedelta(minutes=5)  # adjacent entries within this gap count as active


def _model_family(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


def _active_minutes_by_family(entries: list[Entry], since: datetime) -> dict[str, float]:
    """Walk entries in time order; sum the gap to the prior entry for every
    pair within ACTIVE_GAP. Attribute each interval to the model family of the
    most recent assistant entry on or before it (so user-prompt intervals
    inherit the model that answered them)."""
    mins = {"opus": 0.0, "sonnet": 0.0, "haiku": 0.0}
    prev: Entry | None = None
    last_asst_family = "sonnet"
    for e in entries:
        if e.kind == "assistant":
            last_asst_family = _model_family(e.model)
        if prev is not None and e.ts >= since:
            gap = e.ts - prev.ts
            if gap <= ACTIVE_GAP:
                mins[last_asst_family] += gap.total_seconds() / 60.0
        prev = e
    return mins


def compute(now: datetime | None = None) -> Snapshot:
    if now is None:
        now = datetime.now(timezone.utc)
    entries = load_entries()

    # Claude Code 5-hour windows: the first message starts a window; once that
    # window ends, the next message after it opens a fresh window. So a session
    # resets when a message arrives at-or-after `session_start + 5h`.
    session_start: datetime | None = None
    for e in entries:
        if session_start is None or (e.ts - session_start) >= SESSION_WINDOW:
            session_start = e.ts

    session_active = bool(
        session_start and (now - session_start) < SESSION_WINDOW
    )
    session_reset = session_start + SESSION_WINDOW if session_start else None

    session = Totals()
    today = Totals()
    week = Totals()

    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = now - timedelta(days=7)

    last_message: datetime | None = None
    for e in entries:
        last_message = e.ts
        if session_active and session_start and e.ts >= session_start:
            session.add(e)
        if e.ts >= today_start:
            today.add(e)
        if e.ts >= week_start:
            week.add(e)

    active = _active_minutes_by_family(entries, week_start)
    # Rolling 7-day window: it "resets" continuously. We surface the moment
    # the oldest minute drops off — i.e. earliest-in-window timestamp + 7d.
    earliest_in_week = next((e.ts for e in entries if e.ts >= week_start), None)
    week_reset_at = (earliest_in_week + timedelta(days=7)) if earliest_in_week else None

    return Snapshot(
        now=now,
        session_active=session_active,
        session_start=session_start if session_active else None,
        session_reset=session_reset if session_active else None,
        session=session,
        today=today,
        week=week,
        last_message=last_message,
        week_minutes_opus=active["opus"],
        week_minutes_sonnet=active["sonnet"],
        week_minutes_haiku=active["haiku"],
        week_reset_at=week_reset_at,
    )


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def fmt_duration(td: timedelta) -> str:
    total = int(td.total_seconds())
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h {m:02d}m"
