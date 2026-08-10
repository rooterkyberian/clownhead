"""Rendering the fleet as a terminal table.

Column widths are computed here rather than delegated to Rich. Rich shrinks every column
proportionally once a table overflows its console, which truncates the status text — the
one column that must stay readable. Sizing the table to fit up front means Rich never has
to shrink anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.table import Table

from clownhead.models import Session, Status

STATUS_STYLES: dict[Status, str] = {
    Status.WAITING: "bold red",
    Status.BLOCKED: "bold dark_orange",
    Status.FAILED: "bold dark_orange",
    Status.BUSY: "cyan",
    Status.IDLE: "dim",
    Status.UNKNOWN: "dim",
}

WORKTREE_MARKER = "/.claude/worktrees/"

NARROW_WIDTH = 100
DEFAULT_WIDTH = 160
NAME_CAP = 32
WHERE_MIN = 14
GAP = 2


def format_duration(delta: timedelta | None) -> str:
    """Render a duration compactly, e.g. ``4m``, ``12h``, ``6d``."""
    if delta is None:
        return "-"
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def shorten_path(cwd: Path, home: Path | None = None) -> str:
    """Compress a working directory to the part that distinguishes it.

    Worktrees are rendered as ``repo ⇢ worktree`` so the parent repository stays visible.
    """
    text = str(cwd)
    root = str(home or Path.home())
    if text.startswith(root):
        text = "~" + text[len(root) :]
    if WORKTREE_MARKER in text:
        repo, _, worktree = text.partition(WORKTREE_MARKER)
        return f"{Path(repo).name} ⇢ {worktree}"
    return text


def truncate(text: str, limit: int) -> str:
    """Cut text to ``limit`` columns, marking elision with an ellipsis."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1] + "…"


@dataclass(frozen=True)
class Row:
    """One rendered fleet row, before width fitting."""

    status: str
    style: str
    name: str
    quiet: str
    age: str
    tty: str
    where: str


def build_rows(sessions: Iterable[Session], moment: datetime) -> list[Row]:
    """Turn sessions into fully rendered cell strings."""
    return [
        Row(
            status=session.reason,
            style=STATUS_STYLES.get(session.status, ""),
            name=session.label,
            quiet=format_duration(session.quiet_for(moment)),
            age=format_duration(session.age(moment)),
            tty=session.tty.name if session.tty else "-",
            where=shorten_path(session.cwd),
        )
        for session in sessions
    ]


def _column_width(header: str, values: Sequence[str], cap: int | None = None) -> int:
    widest = max((len(value) for value in values), default=0)
    width = max(len(header), widest)
    return min(width, cap) if cap else width


def build_table(sessions: Iterable[Session], now: datetime | None = None, width: int | None = None) -> Table:
    """Build the fleet status table, fitted to ``width``.

    Below :data:`NARROW_WIDTH` the timing and TTY columns are dropped entirely; losing
    whole columns reads better than truncating the start of every cell.
    """
    moment = now or datetime.now(tz=UTC)
    rows = build_rows(sessions, moment)
    total = width or DEFAULT_WIDTH
    compact = total < NARROW_WIDTH

    status_width = _column_width("STATUS", [row.status for row in rows])
    quiet_width = _column_width("QUIET", [row.quiet for row in rows])
    age_width = _column_width("AGE", [row.age for row in rows])
    tty_width = _column_width("TTY", [row.tty for row in rows])

    columns = 3 if compact else 6
    fixed = status_width if compact else status_width + quiet_width + age_width + tty_width
    available = total - fixed - GAP * (columns - 1)

    name_width = _column_width("NAME", [row.name for row in rows], cap=NAME_CAP)
    name_width = max(len("NAME"), min(name_width, available - WHERE_MIN))
    where_width = max(WHERE_MIN, available - name_width)

    table = Table(box=None, pad_edge=False, header_style="bold", padding=(0, 1))
    table.add_column("STATUS", no_wrap=True, width=status_width)
    table.add_column("NAME", no_wrap=True, width=name_width)
    if not compact:
        table.add_column("QUIET", justify="right", no_wrap=True, width=quiet_width)
        table.add_column("AGE", justify="right", no_wrap=True, width=age_width)
        table.add_column("TTY", no_wrap=True, width=tty_width)
    table.add_column("WHERE", no_wrap=True, width=where_width)

    for row in rows:
        timings = () if compact else (row.quiet, row.age, row.tty)
        table.add_row(
            f"[{row.style}]{row.status}[/]",
            truncate(row.name, name_width),
            *timings,
            truncate(row.where, where_width),
        )
    return table
