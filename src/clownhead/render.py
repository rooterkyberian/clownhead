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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from rich.console import Group, JustifyMethod, RenderableType
from rich.markup import escape
from rich.table import Table
from rich.text import Text

from clownhead.discovery import Message
from clownhead.models import Session, Status, split_worktree
from clownhead.pulls import APPROVED, CHANGES_REQUESTED, Checks, Pull
from clownhead.pulls import NONE as NO_REVIEW
from clownhead.pulls import Status as PullStatus
from clownhead.resume import resume_shell_command
from clownhead.search import PullRequest

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
YOUR_TURN_BACKGROUND = "on grey19"

CODE_SPAN = re.compile(r"`([^`\n]+)`")
STRONG = re.compile(r"\*\*([^*\n]+)\*\*")

NARROW_WIDTH = 100
DEFAULT_WIDTH = 160
NAME_CAP = 32
WORKTREE_CAP = 28
PRS_CAP = 24
WHERE_MIN = 14
RESUME_MIN = 24
MESSAGE_CAP = 110
NAMES_SHOWN = 3
"""How many of something a detail line names before it resorts to counting the rest."""
DETAIL_LABEL = 9
TITLE_CAP = 52
GAP = 2
UNKNOWN = "?"
"""What a cell says before its answer has arrived, which is not what it says when the
answer arrived and was nothing. The distinction is the point of both views that show pull
requests: a row printing ``0`` or ``-`` while the transcripts are still being read would be
asserting something nobody had looked up."""

DURATION_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class Column(StrEnum):
    """A column of the fleet table, named as ``--columns`` spells it."""

    STATUS = "status"
    NAME = "name"
    QUIET = "quiet"
    AGE = "age"
    PID = "pid"
    TTY = "tty"
    WORKTREE = "worktree"
    PRS = "prs"
    WHERE = "where"
    RESUME = "resume"


FLEXIBLE: dict[Column, tuple[int, int | None]] = {
    Column.NAME: (len("NAME"), NAME_CAP),
    Column.WORKTREE: (len("WORKTREE"), WORKTREE_CAP),
    Column.PRS: (len("PRS"), PRS_CAP),
    Column.WHERE: (WHERE_MIN, None),
    Column.RESUME: (RESUME_MIN, None),
}
RIGHT_ALIGNED = frozenset({Column.QUIET, Column.AGE, Column.PID})


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


def parse_duration(text: str) -> timedelta:
    """Read a duration written the way :func:`format_duration` writes one, e.g. ``7d``.

    The unit is required. A bare number means an hour to one reader and a day to the next,
    and the difference between those two answers is what a cleanup does or does not delete.
    """
    stripped = text.strip().lower()
    if len(stripped) < 2 or not stripped[:-1].isdigit() or stripped[-1] not in DURATION_UNITS:
        raise ValueError(f"'{text}' is not a duration like 30s, 10m, 4h or 7d")
    return timedelta(seconds=int(stripped[:-1]) * DURATION_UNITS[stripped[-1]])


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


def worktree_cell(session: Session) -> str:
    """The worktree a session is in, marked when the directory has gone.

    Whether the checkout is still on disk is the whole question a herd of worktrees raises,
    and it is a stat call — cheap enough for a board that redraws every few seconds. Whether
    it is *finished* with is not: that takes git, and it is what ``worktrees-cleanup``
    is for.
    """
    _, worktree = split_worktree(session.cwd)
    if worktree is None:
        return "-"
    return worktree if session.cwd.exists() else f"{worktree} (gone)"


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
    """One rendered fleet row, before width fitting, with a field per :class:`Column`."""

    status: str
    style: str
    name: str
    quiet: str
    age: str
    pid: str
    tty: str
    worktree: str
    prs: str
    where: str
    resume: str


def build_rows(
    sessions: Iterable[Session],
    moment: datetime,
    pulls: Mapping[str, Sequence[PullRequest]] | None = None,
) -> list[Row]:
    """Turn sessions into fully rendered cell strings.

    ``pulls`` is what the transcripts said each session was working on, and is looked up
    rather than required: reading them is the one thing here that touches the disk, so a
    caller that did not ask for the column never pays for it, and one that did can draw
    before the answer lands.
    """
    return [
        Row(
            status=session.reason,
            style=STATUS_STYLES.get(session.status, ""),
            name=session.label,
            quiet=format_duration(session.quiet_for(moment)),
            age=format_duration(session.age(moment)),
            pid=str(session.pid) if session.pid else "-",
            tty=session.tty.name if session.tty else "-",
            worktree=worktree_cell(session),
            prs=pulls_cell(session, pulls),
            where=shorten_path(session.cwd),
            resume=resume_shell_command(session),
        )
        for session in sessions
    ]


def pulls_cell(session: Session, pulls: Mapping[str, Sequence[PullRequest]] | None) -> str:
    """What a session was working on, in the width of a column.

    The freshest one by name and the rest by count, because the column is narrow and the
    most recently named is the one the session belongs to — the detail pane below has the
    room to list the others. Written without the owner: a column of ``acme/data-platform#362``
    truncates to ``acme/data-platfo…`` and loses the number, which is the only part anybody
    reads a pull request by.

    ``?`` where the transcripts have not been read, ``-`` where they were and named
    nothing. A board that printed ``-`` for both would be answering a question it had not
    asked yet, and the answer takes long enough to arrive to be worth being honest about.
    """
    if pulls is None:
        return UNKNOWN
    named = pulls.get(session.session_id)
    if named is None:
        return UNKNOWN
    if not named:
        return "-"
    first = f"{named[0].repo}#{named[0].number}"
    rest = len(named) - 1
    return shorten_reference(f"{first} +{rest}" if rest else first, PRS_CAP)


def shorten_reference(text: str, limit: int) -> str:
    """Fit a pull request reference into a column without losing the number.

    Plain truncation takes the tail, and the tail is the number: ``ai-development-toolkit#464``
    becomes ``ai-development-toolkit#…``, which names no pull request at all. A repository
    cut short is still recognisable next to the one number that identifies the thing, so
    the repository is what gives way.

    Falls back to ordinary truncation where even the number will not fit, which is a column
    too narrow to say anything useful either way.
    """
    if len(text) <= limit:
        return text
    repo, marker, tail = text.partition("#")
    if marker and len(tail) + 2 <= limit:
        return f"{truncate(repo, limit - len(tail) - 1)}#{tail}"
    return truncate(text, limit)


def describe(
    session: Session,
    now: datetime | None = None,
    terminal: str | None = None,
    pulls: Sequence[PullRequest] | None = None,
) -> str:
    """Render the facts about one session, as Rich markup lines.

    The resume command is not among them. It is the longest thing the board prints and the
    only line here that wraps, and it is already a keystroke away — `y` copies it, and
    ``ls --columns resume`` prints it — so a pane read at a glance is where it earns least.

    Pull requests are, because nothing else on the board can say which work a session
    belongs to and this is the only place with the width to name one. They are shown as
    soon as they are known and left out entirely until then, rather than held open with a
    placeholder: the answer costs a transcript read, and a row that reserved a line for it
    would shuffle every line below as each session came into focus. The most recently
    named comes first — see :func:`clownhead.search.pulls_mentioned` — and the rest are
    counted, since a long-lived session names more of them than a line can hold.

    Escaping is :func:`_detail_pane`'s, which both panes share.
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
    return _detail_pane(
        f"[bold]{escape(session.label)}[/]  [{style}]{escape(session.reason)}[/]",
        (
            ("session", session.session_id),
            ("where", str(session.cwd) if session.cwd.exists() else f"{session.cwd} (gone)"),
            *((("prs", _pull_line(pulls)),) if pulls else ()),
            ("process", process or "gone"),
            ("timing", timing),
        ),
    )


def conversation(messages: Iterable[Message], now: datetime | None = None) -> RenderableType:
    """Render a session's recent turns, newest last, as a stack of renderables.

    A transcript will sooner or later contain anything at all, so every turn is escaped
    before Rich sees it.

    Turns are stacked without a blank line between them. The panel is a narrow column
    beside the fleet, and a line naming the speaker and how long ago they said it already
    parts one turn from the next; spacing them as well costs a third of the panel to say
    the same thing again.
    """
    moment = now or datetime.now(tz=UTC)
    turns = [_turn(message, moment) for message in messages]
    return Group(*turns) if turns else Text.from_markup("[dim]nothing said yet[/]")


def _turn(message: Message, moment: datetime) -> Text:
    """Render one turn, with your own words laid on a background of their own.

    What you asked for is what a reader scans back through, and in a column of stacked
    turns a speaker line alone is a thin thing to look for. The background is what makes
    a question findable at a glance.

    It is justified rather than left as it falls: Rich pads a justified line out to the
    console width, so the tint reaches the edge of the panel and the turn reads as one
    block instead of a smear that stops wherever the sentence happened to end.
    """
    markup = f"{_attribution(message, moment)}\n{_spoken(message.text)}"
    if message.role != "user":
        return Text.from_markup(markup)
    return Text.from_markup(markup, style=YOUR_TURN_BACKGROUND, justify="left")


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


def parse_columns(selection: str) -> tuple[Column, ...]:
    """Read a comma-separated column selection, kept in the order it was written.

    The order is the caller's, not a canonical one: a selection is as much about what to
    read first as about what to leave out.
    """
    names = [part.strip().lower() for part in selection.split(",") if part.strip()]
    if not names:
        raise ValueError("no columns named")
    unknown = [name for name in names if name not in set(Column)]
    if unknown:
        raise ValueError(f"unknown column{'' if len(unknown) == 1 else 's'} {', '.join(unknown)}")
    return tuple(Column(name) for name in names)


def default_columns(
    width: int,
    show_pid: bool = False,
    show_tty: bool = False,
    show_worktree: bool = False,
    show_prs: bool = False,
) -> tuple[Column, ...]:
    """The columns to show when none were asked for.

    PID, TTY, WORKTREE and PRS are off unless asked for: the first two matter when a
    session needs killing or signalling, the third only in a repository that uses worktrees
    at all, where ``where`` already says ``repo ⇢ worktree``, and the last costs a read of
    every transcript the board is showing — cheap enough to offer, not cheap enough to
    charge everybody for. Below :data:`NARROW_WIDTH` the
    timing and resume columns go too; losing whole columns reads better than truncating the
    start of every cell, and a resume command cut to fit is worse than absent — it looks
    copyable and is not. A selection made by hand is never thinned this way, since dropping
    a column somebody named would answer a narrow terminal by ignoring them.
    """
    if width < NARROW_WIDTH:
        return (Column.STATUS, Column.NAME, Column.WHERE)
    optional = (
        (Column.PID,) * show_pid
        + (Column.TTY,) * show_tty
        + (Column.WORKTREE,) * show_worktree
        + (Column.PRS,) * show_prs
    )
    return (Column.STATUS, Column.NAME, Column.QUIET, Column.AGE, *optional, Column.WHERE, Column.RESUME)


def build_table(
    sessions: Iterable[Session],
    now: datetime | None = None,
    width: int | None = None,
    columns: Sequence[Column] | None = None,
    pulls: Mapping[str, Sequence[PullRequest]] | None = None,
) -> Table:
    """Build the fleet status table, fitted to ``width``."""
    moment = now or datetime.now(tz=UTC)
    total = width or DEFAULT_WIDTH
    chosen = tuple(columns) if columns is not None else default_columns(total)
    rows = build_rows(sessions, moment, pulls)
    widths = _fit(chosen, rows, total)

    table = Table(box=None, pad_edge=False, header_style="bold", padding=(0, 1))
    for column in chosen:
        justify: JustifyMethod = "right" if column in RIGHT_ALIGNED else "left"
        table.add_column(column.value.upper(), justify=justify, no_wrap=True, width=widths[column])
    for row in rows:
        table.add_row(*(_cell(row, column, widths[column]) for column in chosen))
    return table


def _cell(row: Row, column: Column, width: int) -> str:
    if column is Column.STATUS:
        return f"[{row.style}]{row.status}[/]"
    if column is Column.PRS:
        return shorten_reference(row.prs, width)
    return truncate(str(getattr(row, column.value)), width)


def _fit(columns: Sequence[Column], rows: Sequence[Row], total: int) -> dict[Column, int]:
    """Width per column, sized to content and then squared with the space there is.

    Columns that hold a word or a duration are simply as wide as their widest cell. The
    ones that hold a name, a path or a command cannot be: any of them will outgrow any
    terminal. Those take what is left over, the last of them absorbing the difference in
    either direction — the surplus when the row comes up short, the shortfall when it runs
    long — and the one before it giving up the rest only once the last has hit its floor.
    """
    widths = {column: _natural_width(column, rows) for column in columns}
    flexible = [column for column in columns if column in FLEXIBLE]
    if not flexible:
        return widths

    fixed = sum(width for column, width in widths.items() if column not in FLEXIBLE)
    available = total - fixed - GAP * (len(columns) - 1)
    slack = available - sum(widths[column] for column in flexible)
    for column in reversed(flexible):
        given = max(FLEXIBLE[column][0], widths[column] + slack)
        slack -= given - widths[column]
        widths[column] = given
        if slack >= 0:
            break
    return widths


def _natural_width(column: Column, rows: Sequence[Row]) -> int:
    cap = FLEXIBLE[column][1] if column in FLEXIBLE else None
    return _column_width(column.value.upper(), [str(getattr(row, column.value)) for row in rows], cap)


def _column_width(header: str, values: Sequence[str], cap: int | None = None) -> int:
    widest = max((len(value) for value in values), default=0)
    width = max(len(header), widest)
    return min(width, cap) if cap else width


PULL_COLUMNS: tuple[tuple[str, JustifyMethod], ...] = (
    ("PR", "left"),
    ("TITLE", "left"),
    ("CHECKS", "left"),
    ("REVIEW", "left"),
    ("SESSIONS", "right"),
    ("UPDATED", "right"),
)
"""The pull request columns and how each sits in its cell, for both tables that draw them."""

CHECK_STYLES = {Checks.FAILING: "bold red", Checks.RUNNING: "cyan", Checks.PASSING: "green", Checks.NONE: "dim"}
REVIEW_WORDS = {APPROVED: "approved", CHANGES_REQUESTED: "changes", "REVIEW_REQUIRED": "required", NO_REVIEW: "—"}
DRAFT_STYLE = "dim"


@dataclass(frozen=True)
class PullRow:
    """One rendered pull request row, with a cell per :data:`PULL_COLUMNS`."""

    reference: str
    title: str
    checks: str
    style: str
    review: str
    sessions: str
    updated: str

    @property
    def cells(self) -> tuple[RenderableType, ...]:
        """The row as :data:`PULL_COLUMNS` orders it, styled where the state deserves colour.

        One spelling for both tables. Rich's ``Table`` and Textual's ``DataTable`` take the
        same ``add_row(*cells)``, so a column added, reordered or restyled here reaches the
        board and the listing together — where two copies would drift and the TUI would
        quietly keep the old order.
        """
        return (
            Text(self.reference, style=self.style),
            self.title,
            Text(self.checks, style=self.style),
            self.review,
            self.sessions,
            self.updated,
        )


def summarise(status: PullStatus | None) -> str:
    """The checks column: how they went, and how many are not fine, in the width of a word."""
    if status is None:
        return UNKNOWN
    if status.checks is Checks.FAILING:
        return f"✗ {len(status.failing)}"
    if status.checks is Checks.RUNNING:
        return f"⟳ {len(status.running)}"
    return "✓" if status.checks is Checks.PASSING else "—"


def review_of(status: PullStatus | None) -> str:
    """The review column, with GitHub's shouting reduced to something a table can hold."""
    if status is None:
        return UNKNOWN
    return REVIEW_WORDS.get(status.review, status.review.lower())


def build_pull_rows(
    pulls: Iterable[Pull],
    found: Mapping[PullRequest, PullStatus],
    holders: Mapping[PullRequest, Sequence[str]] | None,
    now: datetime | None = None,
) -> list[PullRow]:
    """Turn pull requests, their statuses and their sessions into rendered cell strings.

    The three arrive separately and at different times — the list in one request, the
    statuses one apiece, the sessions from a pass over the transcripts — so each is looked
    up rather than required. A row renders with whatever has landed, which is what lets the
    board draw its first frame a second in rather than ten.

    ``holders`` of ``None`` means the transcripts have not been read, and the sessions cell
    says so rather than counting the nothing it has: every row would otherwise claim ``0``
    for the first second of a board, and for the whole of a listing that was told not to
    look. A status not yet read is ``?`` for the same reason, so the row is consistent
    about which of its cells are answers.

    A draft is dimmed whole rather than labelled, because the label would cost a column
    that every other row would leave blank.
    """
    moment = now or datetime.now(tz=UTC)
    return [
        PullRow(
            reference=str(pull.reference),
            title=truncate(pull.title, TITLE_CAP),
            checks=summarise(found.get(pull.reference)),
            style=_pull_style(pull, found.get(pull.reference)),
            review=review_of(found.get(pull.reference)),
            sessions=UNKNOWN if holders is None else str(len(holders.get(pull.reference, ()))),
            updated=format_duration(moment - pull.updated_at) if pull.updated_at else "-",
        )
        for pull in pulls
    ]


def build_pull_table(
    pulls: Iterable[Pull],
    found: Mapping[PullRequest, PullStatus],
    holders: Mapping[PullRequest, Sequence[str]] | None,
    now: datetime | None = None,
) -> Table:
    """Build the pull request table for a terminal that wanted the list rather than the board.

    The title is the one cell that will outgrow any terminal, so it is the one given the
    slack — everything else is a word, a count or a duration, and sized to its widest cell.
    """
    table = Table(box=None, pad_edge=False, header_style="bold", padding=(0, 1), expand=True)
    for header, justify in PULL_COLUMNS:
        table.add_column(
            header,
            justify=justify,
            no_wrap=True,
            overflow="ellipsis" if header == "TITLE" else "fold",
            ratio=1 if header == "TITLE" else None,
        )
    for row in build_pull_rows(pulls, found, holders, now):
        table.add_row(*row.cells)
    return table


def describe_pull(pull: Pull, status: PullStatus | None, sessions: Sequence[Session] | None = None) -> str:
    """Render the facts about one pull request, as Rich markup lines.

    What the table had no room for: the URL, the names of the checks that went red, and
    which sessions on this machine worked on it — the answer the board exists to give,
    since it is the one thing GitHub cannot be asked.
    """
    state = " · ".join(
        part
        for part in (
            "draft" if pull.is_draft else None,
            f"review {review_of(status)}",
            status.merge_state.lower() if status and status.merge_state != "UNKNOWN" else None,
        )
        if part
    )
    return _detail_pane(
        f"[bold]{escape(str(pull.reference))}[/]  {escape(pull.title)}",
        (
            ("url", pull.url),
            ("state", state),
            ("checks", _check_line(status)),
            ("sessions", _session_line(sessions)),
        ),
    )


def _detail_pane(header: str, rows: Sequence[tuple[str, str]]) -> str:
    """A header and a labelled block beneath it, which is what every detail pane here is.

    Values are escaped rather than trusted: a path, a title or a check name may contain
    square brackets, which Rich would otherwise read as the markup surrounding them and
    swallow. Shared so that the escaping is a property of the pane rather than a rule each
    caller has to remember, and so the label column stays one width across all of them.
    """
    body = "\n".join(f"[dim]{label:<{DETAIL_LABEL}}[/]{escape(value)}" for label, value in rows)
    return f"{header}\n{body}"


def _pull_style(pull: Pull, status: PullStatus | None) -> str:
    if pull.is_draft:
        return DRAFT_STYLE
    return CHECK_STYLES[status.checks] if status else ""


def _pull_line(pulls: Sequence[PullRequest]) -> str:
    """The pull requests a session named, the freshest few by name and the rest by count."""
    return _named_few([str(reference) for reference in pulls], " · ")


def _check_line(status: PullStatus | None) -> str:
    """Which checks are red or still going, named as far as a pane can carry them.

    A matrix build goes red eleven jobs at a time, and eleven names wrap into a paragraph
    that pushes the line below it off the bottom of the board — so the first few are named
    and the rest counted, the same bargain the pull requests on a session's line strike.
    Naming a few is what tells a flaky shard from a broken build; naming all of them tells
    you the same thing and costs the rest of the pane.
    """
    if status is None:
        return "not read"
    if status.failing:
        return f"✗ {_named_few(status.failing)}"
    if status.running:
        return f"⟳ {_named_few(status.running)}"
    return "✓ all passing" if status.checks is Checks.PASSING else "none"


def _named_few(names: Sequence[str], separator: str = ", ") -> str:
    """The first few of something, and how many more there were."""
    shown = separator.join(names[:NAMES_SHOWN])
    rest = len(names) - NAMES_SHOWN
    return f"{shown} (+{rest} more)" if rest > 0 else shown


def _session_line(sessions: Sequence[Session] | None) -> str:
    if sessions is None:
        return "reading transcripts…"
    if not sessions:
        return "none on this machine"
    return " · ".join(session.label for session in sessions)
