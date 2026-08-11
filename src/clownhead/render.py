"""Rendering the fleet as a terminal table, and one session in full.

Column widths are computed here rather than delegated to Rich. Rich shrinks every column
proportionally once a table overflows its console, which truncates the status text — the
one column that must stay readable. Sizing the table to fit up front means Rich never has
to shrink anything.

What the table has no room for — the full session id, the untruncated path, the terminal
it belongs to, the command that brings it back — is what :func:`describe` renders for the
one session under the cursor.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.markup import escape
from rich.table import Table

from clownhead.discovery import Message
from clownhead.models import Session, Status, split_worktree
from clownhead.resume import resume_shell_command

STATUS_STYLES: dict[Status, str] = {
    Status.WAITING: "bold red",
    Status.BLOCKED: "bold dark_orange",
    Status.FAILED: "bold dark_orange",
    Status.BUSY: "cyan",
    Status.IDLE: "dim",
    Status.COMPLETED: "dim",
    Status.CLOSED: "dim",
    Status.UNKNOWN: "dim",
}

SPEAKERS = {"user": "you", "assistant": "claude"}
SPEAKER_STYLES = {"user": "bold", "assistant": "bold cyan"}

CODE_SPAN = re.compile(r"`([^`\n]+)`")
STRONG = re.compile(r"\*\*([^*\n]+)\*\*")

NARROW_WIDTH = 100
DEFAULT_WIDTH = 160
NAME_CAP = 32
WHERE_MIN = 14
MESSAGE_CAP = 110
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
    repo, worktree = split_worktree(Path(text))
    return f"{repo.name} ⇢ {worktree}" if worktree else text


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
    pid: str
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
            pid=str(session.pid) if session.pid else "-",
            tty=session.tty.name if session.tty else "-",
            where=shorten_path(session.cwd),
        )
        for session in sessions
    ]


def describe(session: Session, now: datetime | None = None, terminal: str | None = None) -> str:
    """Render the facts about one session, as Rich markup lines.

    Values are escaped rather than trusted: a path may contain square brackets, which
    Rich would otherwise read as markup and swallow.
    """
    moment = now or datetime.now(tz=UTC)
    style = STATUS_STYLES.get(session.status, "")
    process = " · ".join(
        part
        for part in (
            f"pid {session.pid}" if session.pid else None,
            session.tty.name if session.tty else None,
            terminal,
        )
        if part
    )
    timing = " · ".join(
        (
            f"started {format_duration(session.age(moment))} ago",
            f"quiet {format_duration(session.quiet_for(moment))}",
        )
    )
    rows = (
        ("session", session.session_id),
        ("where", str(session.cwd) if session.cwd.exists() else f"{session.cwd} (gone)"),
        ("process", process or "gone"),
        ("timing", timing),
        ("resume", resume_shell_command(session)),
    )
    header = f"[bold]{escape(session.label)}[/]  [{style}]{escape(session.reason)}[/]"
    body = "\n".join(f"[dim]{label:<8}[/]{escape(value)}" for label, value in rows)
    return f"{header}\n{body}"


def conversation(messages: Iterable[Message], now: datetime | None = None) -> str:
    """Render a session's recent turns, newest last, as Rich markup.

    A transcript will sooner or later contain anything at all, so every turn is escaped
    before Rich sees it.

    Turns are stacked without a blank line between them. The panel is a narrow column
    beside the fleet, and a line naming the speaker and how long ago they said it already
    parts one turn from the next; spacing them as well costs a third of the panel to say
    the same thing again.
    """
    moment = now or datetime.now(tz=UTC)
    turns = [f"{_attribution(message, moment)}\n{_spoken(message.text)}" for message in messages]
    return "\n".join(turns) if turns else "[dim]nothing said yet[/]"


def _attribution(message: Message, moment: datetime) -> str:
    speaker = SPEAKERS.get(message.role, message.role)
    style = SPEAKER_STYLES.get(message.role, "bold")
    if message.at is None:
        return f"[{style}]{speaker}[/]"
    said = max(moment - message.at, timedelta(0))
    return f"[{style}]{speaker}[/] [dim]{format_duration(said)} ago[/]"


def _spoken(text: str) -> str:
    """Escape a turn and pick the spans back out that a reader's eye should land on.

    Escaping first is what makes the substitutions safe: everything Rich would have read
    as markup is inert by the time the only markup in the string is the markup put there
    here.
    """
    marked = CODE_SPAN.sub(r"[cyan]\1[/]", escape(text))
    return STRONG.sub(r"[bold]\1[/]", marked)


def _column_width(header: str, values: Sequence[str], cap: int | None = None) -> int:
    widest = max((len(value) for value in values), default=0)
    width = max(len(header), widest)
    return min(width, cap) if cap else width


def build_table(
    sessions: Iterable[Session],
    now: datetime | None = None,
    width: int | None = None,
    show_pid: bool = False,
    show_tty: bool = False,
) -> Table:
    """Build the fleet status table, fitted to ``width``.

    PID and TTY are off unless asked for: they matter when a session needs killing or
    signalling, not when you are reading the board, and every column they take is one
    the path loses. Below :data:`NARROW_WIDTH` the timing columns go too; losing whole
    columns reads better than truncating the start of every cell.
    """
    moment = now or datetime.now(tz=UTC)
    rows = build_rows(sessions, moment)
    total = width or DEFAULT_WIDTH
    compact = total < NARROW_WIDTH

    status_width = _column_width("STATUS", [row.status for row in rows])
    optional: list[tuple[str, str, int]] = []
    if not compact:
        optional.append(("QUIET", "quiet", _column_width("QUIET", [row.quiet for row in rows])))
        optional.append(("AGE", "age", _column_width("AGE", [row.age for row in rows])))
        if show_pid:
            optional.append(("PID", "pid", _column_width("PID", [row.pid for row in rows])))
        if show_tty:
            optional.append(("TTY", "tty", _column_width("TTY", [row.tty for row in rows])))

    columns = 3 + len(optional)
    fixed = status_width + sum(column_width for _, _, column_width in optional)
    available = total - fixed - GAP * (columns - 1)

    name_width = _column_width("NAME", [row.name for row in rows], cap=NAME_CAP)
    name_width = max(len("NAME"), min(name_width, available - WHERE_MIN))
    where_width = max(WHERE_MIN, available - name_width)

    table = Table(box=None, pad_edge=False, header_style="bold", padding=(0, 1))
    table.add_column("STATUS", no_wrap=True, width=status_width)
    table.add_column("NAME", no_wrap=True, width=name_width)
    for header, _, column_width in optional:
        table.add_column(header, justify="left" if header == "TTY" else "right", no_wrap=True, width=column_width)
    table.add_column("WHERE", no_wrap=True, width=where_width)

    for row in rows:
        table.add_row(
            f"[{row.style}]{row.status}[/]",
            truncate(row.name, name_width),
            *(getattr(row, field) for _, field, _ in optional),
            truncate(row.where, where_width),
        )
    return table
