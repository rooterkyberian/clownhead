"""The interactive fleet overseer, which bare ``clownhead`` lands in.

Discovery shells out to ``claude agents --json`` and ``ps``, which is slow enough to
stutter a redraw, so reloads run on a worker thread and hand their result back to the
event loop. Loading is injected rather than imported so the app can be driven in tests
without a live fleet.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError
from rich.markup import escape
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.message_pump import MessagePump
from textual.notifications import SeverityLevel
from textual.screen import ModalScreen, Screen
from textual.widgets import DataTable, Footer, Input, Label, OptionList, Static, Switch

from clownhead import attention, checkouts, issues, pulls
from clownhead import settings as settings_store
from clownhead.control import close_tab, rename, shell_of, terminate, wait_for_exit
from clownhead.discovery import Message, Process, process_table, recent_messages, relocated_config_dir
from clownhead.issues import Unavailable
from clownhead.models import Session, Status, split_worktree
from clownhead.pulls import Pull
from clownhead.pulls import Status as PullStatus
from clownhead.render import (
    PULL_COLUMNS,
    build_pull_rows,
    build_rows,
    conversation,
    describe,
    describe_pull,
    format_duration,
    shorten_path,
    truncate,
)
from clownhead.resume import Launch, resume_plan, resume_shell_command, start_plan
from clownhead.search import (
    PullRequest,
    Reference,
    parse_reference,
    pulls_mentioned,
    sessions_by_pull,
    sessions_mentioning,
)
from clownhead.settings import Settings
from clownhead.terminal import Terminal, copy_to_pasteboard, open_url
from clownhead.worktrees import Candidate, survey
from clownhead.worktrees import remove as remove_worktree

BASE_COLUMNS = ("STATUS", "NAME", "QUIET", "AGE")
DEFAULT_INTERVAL = 5.0
CLOWN = "\N{CLOWN FACE}"
CONFIG_DIR_CAP = 40
CLEANUP_AGE = timedelta(0)
"""No age filter on a cleanup: being merged is the stronger answer, and a worktree whose
work is already upstream is finished with whether that happened this morning or last month."""


def _candidate_age(candidate: Candidate) -> str:
    """How long ago a worktree was last worked in, for a line that has room for little."""
    if candidate.last_used is None:
        return "unknown"
    return f"{format_duration(datetime.now(tz=UTC) - candidate.last_used)} ago"


class Loader(Protocol):
    """Fetches the fleet for the overseer."""

    def __call__(self, include_closed: bool, /) -> list[Session]:
        """Discover sessions, optionally including the ones that have ended."""
        ...


class Reader(Protocol):
    """Fetches the tail of a session's conversation."""

    def __call__(self, session_id: str, /, *, limit: int) -> list[Message]:
        """Read the last few turns of a session, oldest first."""
        ...


def config_dir_notice() -> str:
    """Name the Claude Code directory the board is reading, unless it is the default one.

    ``claude agents --json`` lists only the sessions belonging to the config directory it
    was invoked under, so a board opened from a shell with ``CLAUDE_CONFIG_DIR`` set is
    watching another fleet than one opened without it — and a board short of the sessions
    you expected looks exactly like a quiet machine. Empty when there is nothing worth
    saying, which is the usual case.
    """
    directory = relocated_config_dir()
    return "" if directory is None else truncate(shorten_path(directory), CONFIG_DIR_CAP)


def seeded_needle(target: Reference | None) -> str:
    """What the filter box shows on a board opened already pointed at a reference.

    Spelled as the URL rather than as the short ``repo#2``. The box is live, so whatever is
    put in it is read straight back out by the parser — and GitHub writes an issue and a
    pull request the same short way, so seeding an issue with its shorthand would hand back
    the pull request of the same number and search for that instead.
    """
    return target.prompt if target is not None else ""


def hand_back[T](pump: MessagePump, callback: Callable[[T], None], value: T) -> None:
    """Carry a worker thread's answer to the event loop, unless the board has gone.

    The one rule every thread here obeys: a node torn down while its worker was still
    running must not be called into. Written once because a third screen would otherwise
    copy whichever of two spellings it found first — and because the guard is the kind of
    thing that is only ever wrong once.

    Routed through ``pump.app`` so a Screen and the App itself are the same call; an App's
    ``app`` is itself.
    """
    if pump.is_running:
        pump.app.call_from_thread(callback, value)


def matches(session: Session, needle: str) -> bool:
    """Whether a session matches a filter string, case-insensitively."""
    if not needle:
        return True
    lowered = needle.lower()
    fields = (session.label, session.reason, str(session.cwd), session.short_id)
    return any(lowered in field.lower() for field in fields)


class FleetTable(DataTable[Any]):
    """The fleet table, with the arrow keys wired to the conversation beside it.

    ``DataTable`` binds left and right to horizontal scrolling and consumes them before
    the app ever sees them, so the override has to live on the table itself rather than
    as an app binding — which also keeps the arrows working normally inside text inputs.
    """

    BINDINGS = [
        Binding("right", "app.history", "history"),
        Binding("left", "app.close_history", "close", show=False),
    ]

    class RowClicked(TextualMessage):
        """Posted when a row is clicked, as opposed to chosen with the keyboard."""

    async def _on_click(self, event: events.Click) -> None:
        """Move the cursor onto the clicked row and ask to read it.

        ``DataTable`` turns a click on the already-highlighted row into ``RowSelected``,
        the very message Enter posts, so clicking a row twice would signal its terminal.
        Clicks on a row are answered here instead, leaving ``RowSelected`` to mean the
        keyboard and nothing else. Textual runs every handler of this name up the MRO,
        so the table's own must be prevented rather than simply overridden — and a click
        on anything but a row is left to it untouched.
        """
        row = event.style.meta.get("row", -1)
        if row < 0:
            return
        event.prevent_default()
        event.stop()
        self.move_cursor(row=row)
        self.post_message(self.RowClicked())


class HistoryPanel(VerticalScroll):
    """The conversation beside the fleet, which takes the keyboard while it is open.

    A conversation is longer than the panel it is read in, so the arrow keys have to move
    through it rather than through the fleet — which means focus, since that is the only
    thing that decides where a key lands. ``ScrollableContainer`` spends left and right on
    horizontal scrolling there is none of, so `←` is bought back for closing the panel,
    the same trade the fleet table makes for opening it.
    """

    BINDINGS = [Binding("left", "app.close_history", "close", show=False)]


class ConfirmScreen(ModalScreen[bool]):
    """A question that has to be answered before something irreversible happens."""

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #sheet {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "yes"),
        Binding("enter", "confirm", "yes"),
        Binding("n", "cancel", "no"),
        Binding("escape", "cancel", "no"),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        """Ask, and say which keys answer."""
        with Vertical(id="sheet"):
            yield Static(self._question)
            yield Static("[dim]y to confirm · esc to cancel[/]")

    def action_confirm(self) -> None:
        """Answer yes."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Answer no."""
        self.dismiss(False)


class CleanupScreen(ModalScreen[bool | None]):
    """The merged worktrees, and one question over the lot of them.

    A list rather than a picker on purpose. Everything offered has already passed every
    guard, so choosing between them is a decision without a difference, and a screen that
    invited one would turn a cleanup into thirteen keystrokes — which is the thing that
    made the worktrees pile up in the first place. Anything being kept is named underneath
    with its reason, because a cleanup that silently skipped half of what it found would
    read as having tidied more than it had.

    The branches are the second question, because they are a second thing to lose. A
    worktree is a checkout that can be made again from a branch; the branch is where the
    work is. Both are only ever offered for worktrees whose work is already in the default
    branch, so the answer is safe either way — but it is asked rather than assumed, and it
    starts at no.

    It answers ``True`` for the worktrees and their branches, ``False`` for the worktrees
    alone, and ``None`` for neither.
    """

    CSS = """
    CleanupScreen {
        align: center middle;
    }
    #sheet {
        width: 78;
        max-height: 80%;
        height: auto;
        padding: 1 2;
        border: round $error;
        background: $surface;
    }
    #cleanup-list {
        height: auto;
        max-height: 20;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "yes"),
        Binding("enter", "confirm", "yes"),
        Binding("b", "toggle_branches", "branches"),
        Binding("n", "cancel", "no"),
        Binding("escape", "cancel", "no"),
    ]

    def __init__(self, going: Sequence[Candidate], kept: Sequence[Candidate]) -> None:
        super().__init__()
        self._going = list(going)
        self._kept = list(kept)
        self._branches = False

    def compose(self) -> ComposeResult:
        """Ask once, over a list of what the answer applies to."""
        count = len(self._going)
        with Vertical(id="sheet"):
            yield Static(f"[bold]Remove {count} merged worktree{'' if count == 1 else 's'}?[/]")
            with VerticalScroll(id="cleanup-list"):
                yield Static(self._listing())
            yield Static(self._branch_line(), id="branches")
            yield Static("[dim]y to remove · b for branches too · esc to cancel[/]")

    def action_toggle_branches(self) -> None:
        """Take the branches as well, or stop taking them."""
        self._branches = not self._branches
        self.query_one("#branches", Static).update(self._branch_line())

    def action_confirm(self) -> None:
        """Answer yes, to whatever the branch line currently says."""
        self.dismiss(self._branches)

    def action_cancel(self) -> None:
        """Answer no."""
        self.dismiss(None)

    def _branch_line(self) -> str:
        if self._branches:
            return "[bold]branches go too[/]"
        return "[dim]branches stay; only the checkouts go[/]"

    def _listing(self) -> str:
        lines = [
            f"  {escape(candidate.worktree.name)}  [dim]{_candidate_age(candidate)}[/]"
            f"  [dim]{escape(candidate.worktree.branch or 'detached')}[/]"
            for candidate in self._going
        ]
        if self._kept:
            lines.append("")
            lines.extend(
                f"  [dim]{escape(candidate.worktree.name)} · kept · {escape(candidate.kept_for or '')}[/]"
                for candidate in self._kept
            )
        return "\n".join(lines)


class PromptScreen(ModalScreen[str | None]):
    """A single line of text, handed back when it is submitted and ``None`` if abandoned."""

    CSS = """
    PromptScreen {
        align: center middle;
    }
    #sheet {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $surface;
    }
    #answer {
        margin: 1 0;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, question: str, value: str = "") -> None:
        super().__init__()
        self._question = question
        self._value = value

    def compose(self) -> ComposeResult:
        """Ask, with whatever it is now ready to be edited."""
        with Vertical(id="sheet"):
            yield Static(self._question)
            yield Input(value=self._value, id="answer")
            yield Static("[dim]enter to confirm · esc to cancel[/]")

    def on_mount(self) -> None:
        """Put the cursor in the box, past the value already in it."""
        box = self.query_one("#answer", Input)
        box.focus()
        box.action_end()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hand back what was typed, treating a blank answer as no answer.

        The message is stopped rather than left to bubble: every screen's inputs reach
        the overseer, which has a submit handler of its own for the filter box.
        """
        event.stop()
        self.dismiss(event.value.strip() or None)

    def action_cancel(self) -> None:
        """Leave without changing anything."""
        self.dismiss(None)


@dataclass(frozen=True)
class StartChoice:
    """Where a new session was chosen to be started, and whether to run it or copy it."""

    repo: Path
    copy: bool = False


class StartScreen(ModalScreen[StartChoice | None]):
    """Where to start a session for a reference, and the command that would do it.

    The name is generated rather than asked for, so the command is shown in full: a
    worktree is about to be made and a branch named after it, and the one thing worse than
    being asked to name it is finding out afterwards what it was called.

    Which repository is a real question and only sometimes an open one. A pull request
    names its own, and matching that against what ``origin`` says is about as sure as this
    gets — so the list starts on the best answer, and a fleet spread across one repository
    has nothing to choose between. A Jira key names nothing at all, which is exactly when
    the list earns its place.

    ``y`` copies the command instead of running it, which is the way out for a session
    that belongs in another window, or on another machine entirely.
    """

    CSS = """
    StartScreen {
        align: center middle;
    }
    #sheet {
        width: 78;
        max-height: 80%;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $surface;
    }
    #repos {
        height: auto;
        max-height: 10;
        margin: 1 0;
    }
    #command {
        margin: 1 0;
    }
    """

    BINDINGS = [
        Binding("enter", "start", "start"),
        Binding("y", "copy", "copy"),
        Binding("escape", "cancel", "cancel"),
    ]

    def __init__(self, reference: Reference, name: str, repos: Sequence[Path]) -> None:
        super().__init__()
        self._reference = reference
        self._start_name = name
        self._repos = list(repos)
        self._index = 0

    def compose(self) -> ComposeResult:
        """Show what is about to be run, and where the choice of repository is one."""
        with Vertical(id="sheet"):
            yield Static(f"[bold]Start a session for {escape(str(self._reference))}[/]")
            if len(self._repos) > 1:
                yield OptionList(*(escape(shorten_path(repo)) for repo in self._repos), id="repos")
            else:
                yield Static(f"[dim]in[/] {escape(shorten_path(self._repos[0]))}", id="repos")
            yield Static(self._command_line(), id="command")
            yield Static("[dim]enter to start · y to copy · esc to cancel[/]")

    def on_mount(self) -> None:
        """Put the cursor on the best guess, which is the first one."""
        if len(self._repos) > 1:
            self.query_one("#repos", OptionList).focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Re-render the command for whichever repository is now under the cursor.

        The list highlights its first entry as it mounts, which is before the line showing
        the command exists — so the line is updated where there is one and the index kept
        either way, rather than the sheet failing to open over the order things arrive in.
        """
        event.stop()
        self._index = event.option_index
        for command in self.query("#command").results(Static):
            command.update(self._command_line())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Answer the sheet with the repository chosen from the list.

        ``OptionList`` binds enter to its own selection and consumes it before the screen's
        binding is reached, so the one key that starts a session has to be answered here as
        well as there — otherwise it works with one repository and does nothing with two.
        """
        event.stop()
        self._index = event.option_index
        self.action_start()

    def action_start(self) -> None:
        """Hand back the chosen repository, to be started in."""
        self.dismiss(StartChoice(self._chosen()))

    def action_copy(self) -> None:
        """Hand back the chosen repository, to be copied rather than run."""
        self.dismiss(StartChoice(self._chosen(), copy=True))

    def action_cancel(self) -> None:
        """Leave without starting anything."""
        self.dismiss(None)

    def _chosen(self) -> Path:
        return self._repos[self._index]

    def _command_line(self) -> str:
        plan = start_plan(self._chosen(), name=self._start_name, prompt=self._reference.prompt)
        return f"[dim]{escape(plan.shell_command)}[/]"


class SettingsScreen(ModalScreen[Settings | None]):
    """The settings sheet, dismissed with whatever it was left showing.

    Editing happens against a copy so that leaving with escape changes nothing; the
    caller decides what to do with the result, which is what keeps saving out of here.
    """

    CSS = """
    SettingsScreen {
        align: center middle;
    }
    #sheet {
        width: 60;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $surface;
    }
    .row {
        height: 3;
    }
    .row Label {
        width: 1fr;
        padding: 1 0;
    }
    #sheet Input {
        width: 12;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "close"),
        Binding("enter", "close", "close"),
    ]

    TOGGLES = (
        ("show_pid", "PID column"),
        ("show_tty", "TTY column"),
        ("show_worktree", "WORKTREE column"),
        ("show_prs", "PRS column, read from the transcripts"),
        ("show_closed", "closed sessions at startup"),
        ("foreground", "raise the window on focus"),
        ("paint_tabs", "tint session tabs, and the board's own"),
        ("close_tab_on_terminate", "close the tab when a session is terminated"),
    )

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings.model_copy()

    def compose(self) -> ComposeResult:
        """Lay out one row per setting."""
        with Vertical(id="sheet"):
            yield Static("[bold]settings[/]")
            for field, label in self.TOGGLES:
                with Horizontal(classes="row"):
                    yield Label(label)
                    yield Switch(value=getattr(self._settings, field), id=field)
            with Horizontal(classes="row"):
                yield Label("refresh every (seconds)")
                yield Input(value=str(self._settings.interval), id="interval", type="number")
            with Horizontal(classes="row"):
                yield Label("turns of history")
                yield Input(value=str(self._settings.history_turns), id="history_turns", type="integer")
            yield Static("[dim]esc to close[/]")

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Apply a flipped switch to the copy being edited."""
        self._settings = self._settings.model_copy(update={str(event.switch.id): event.value})

    def on_input_changed(self, event: Input.Changed) -> None:
        """Apply a typed number, ignoring anything half-typed or out of range."""
        field = str(event.input.id)
        try:
            value = float(event.value) if field == "interval" else int(event.value)
            self._settings = Settings.model_validate(self._settings.model_dump() | {field: value})
        except (ValueError, ValidationError):
            return

    def action_close(self) -> None:
        """Hand the edited settings back to the overseer."""
        self.dismiss(self._settings)


@dataclass(frozen=True)
class Worked:
    """A pull request the board is being pointed at, and what is already known about it.

    ``sessions`` is ``None`` where the transcripts were never read, which is the difference
    between handing the board an answer and handing it an empty one — the board would take
    the second as fact and show nothing.
    """

    reference: PullRequest
    sessions: list[str] | None
    read: list[Session]


class PullChoiceScreen(ModalScreen[PullRequest | None]):
    """Which of a session's pull requests to open, when it named more than one.

    A session that ran long enough names the pull request it was for, the one it was based
    on, and two it read in passing. Opening all of them is four tabs nobody asked for, and
    opening the first is a guess — so the list is offered, freshest first, which is the
    order :func:`clownhead.search.pulls_mentioned` already put them in.
    """

    CSS = """
    PullChoiceScreen {
        align: center middle;
    }
    #sheet {
        width: 78;
        max-height: 80%;
        height: auto;
        padding: 1 2;
        border: round $panel;
        background: $surface;
    }
    #choices {
        height: auto;
        max-height: 12;
        margin: 1 0;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, session: Session, references: Sequence[PullRequest]) -> None:
        super().__init__()
        self._session = session
        self._references = list(references)

    def compose(self) -> ComposeResult:
        """Offer the pull requests this session named, the most recent one highlighted."""
        with Vertical(id="sheet"):
            yield Static(f"[bold]Open a pull request for {escape(self._session.label)}[/]")
            yield OptionList(*(escape(str(reference)) for reference in self._references), id="choices")
            yield Static("[dim]enter to open in the browser · esc to cancel[/]")

    def on_mount(self) -> None:
        """Put the cursor on the one the session named last."""
        self.query_one("#choices", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Answer the sheet with whichever reference was chosen."""
        event.stop()
        self.dismiss(self._references[event.option_index])

    def action_cancel(self) -> None:
        """Leave without opening anything."""
        self.dismiss(None)


class PullsScreen(ModalScreen[Worked | None]):
    """The pull requests you have open, and which sessions on this machine worked on them.

    The board's usual question runs the other way — here is a session, what was it for —
    and answering it needs a reference to search for. This is where the references come
    from: GitHub is asked what you have open, so that the list is complete rather than
    limited to whatever a transcript happened to mention, and a pull request opened from
    the web or by somebody else is on it too.

    Three answers arrive separately and the table is redrawn as each does. The list is one
    request and lands first. The statuses are a request apiece and trickle in over several
    seconds. The sessions come from a pass over every transcript, which is a second or so
    and entirely local — so it usually beats GitHub, and the counts are filled in while the
    checks are still blank. Nothing waits for anything else: a board that held its first
    frame until the slowest of the three answered would spend ten seconds looking broken.

    Modal rather than a screen of its own so that the board's keys stop at it. `t` is
    terminate and `n` starts a session, and neither should reach a fleet nobody can see.
    """

    CSS = """
    PullsScreen {
        background: $surface;
    }
    #pulls-bar {
        height: 1;
        background: $panel;
        padding: 0 1;
    }
    #pulls {
        height: 1fr;
        width: 1fr;
        overflow-x: hidden;
    }
    #pull-details {
        height: auto;
        padding: 0 1;
        border-top: solid $panel;
    }
    """

    BINDINGS = [
        Binding("enter", "sessions", "sessions"),
        Binding("o", "open", "open on github"),
        Binding("y", "copy", "copy url"),
        Binding("escape", "back", "back"),
        Binding("q", "back", "back", show=False),
        Binding("ctrl+r", "refresh", "refresh", show=False),
    ]

    def __init__(self, loader: Loader, author: str = pulls.MINE, limit: int = pulls.DEFAULT_LIMIT) -> None:
        super().__init__()
        self._loader = loader
        self._author = author
        self._limit = limit
        self._pulls: list[Pull] = []
        self._statuses: dict[PullRequest, PullStatus] = {}
        self._holders: dict[PullRequest, list[str]] | None = None
        self._sessions: dict[str, Session] = {}
        self._visible: list[Pull] = []
        self._failure: str | None = None
        self._listing = True
        self._enriching = False

    def compose(self) -> ComposeResult:
        """Lay out the summary bar, the pull request table, the detail pane and the hints."""
        yield Static(id="pulls-bar")
        yield DataTable[Any](id="pulls", cursor_type="row", zebra_stripes=True)
        yield Static(id="pull-details")
        yield Footer()

    def on_mount(self) -> None:
        """Ask GitHub and the transcripts at once, and draw whichever answers first."""
        table = self.query_one("#pulls", DataTable)
        table.add_columns(*(header for header, _ in PULL_COLUMNS))
        table.focus()
        self._draw()
        self._fetch()
        self._scan()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Describe whichever pull request the cursor has moved onto.

        Stopped here rather than left to bubble: the board underneath answers the same
        message by describing a session, and would do it into a pane that is not on screen.
        """
        event.stop()
        self._draw_details()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Treat choosing a row as asking for its sessions."""
        event.stop()
        self.action_sessions()

    @property
    def selected(self) -> Pull | None:
        """The pull request under the cursor, if the list is not empty."""
        table = self.query_one("#pulls", DataTable)
        if not self._visible or not 0 <= table.cursor_row < len(self._visible):
            return None
        return self._visible[table.cursor_row]

    def action_sessions(self) -> None:
        """Leave, pointing the board at the sessions that worked on this pull request.

        The answer goes back with the reference rather than being left behind. This screen
        has just read every transcript on the machine to build it, and the board's own
        search would otherwise read them all again — measured at three seconds — to arrive
        at the list already in hand.
        """
        pull = self.selected
        if pull is None:
            self.notify("nothing selected", severity="warning")
            return
        found = None if self._holders is None else self._holders.get(pull.reference, [])
        self.dismiss(Worked(pull.reference, found, list(self._sessions.values())))

    def action_open(self) -> None:
        """Open the selected pull request on GitHub."""
        pull = self.selected
        if pull is None:
            self.notify("nothing selected", severity="warning")
            return
        if not open_url(pull.url):
            self.notify(pull.url, title="could not open a browser", severity="warning")

    def action_copy(self) -> None:
        """Put the selected pull request's URL on the clipboard."""
        pull = self.selected
        if pull is None:
            self.notify("nothing selected", severity="warning")
            return
        self.app.copy_to_clipboard(pull.url)
        copy_to_pasteboard(pull.url)
        self.notify(pull.url, title=str(pull.reference))

    def action_refresh(self) -> None:
        """Ask GitHub and the transcripts again, from nothing."""
        if self._listing or self._enriching:
            return
        self._pulls = []
        self._statuses = {}
        self._holders = None
        self._failure = None
        self._listing = True
        self._draw()
        self._fetch()
        self._scan()

    def action_back(self) -> None:
        """Leave the pull requests and go back to the fleet."""
        self.dismiss(None)

    @work(thread=True, group="pulls")
    def _fetch(self) -> None:
        """Ask GitHub for the list, then for each status, off the event loop."""
        try:
            listed = pulls.mine(self._author, self._limit)
        except Unavailable as error:
            hand_back(self, self._unavailable, str(error))
            return
        hand_back(self, self._listed, listed)
        for pull, status in pulls.stream_statuses(listed):
            hand_back(self, self._enriched, (pull, status))
        hand_back(self, self._enriched_all, None)

    @work(thread=True, group="pull-sessions")
    def _scan(self) -> None:
        """Read every transcript once for every pull request it names.

        The sessions that have ended are read too, whatever the board is showing. Work on a
        pull request is usually finished, so the session that did it has usually ended, and
        a count of zero on a merged pull request would be an answer about the board's `c`
        rather than about the machine.
        """
        try:
            sessions = self._loader(True)
            holders = sessions_by_pull(sessions)
        except OSError as error:
            hand_back(self, self._scan_failed, str(error))
            return
        hand_back(self, self._scanned, (sessions, holders))

    def _unavailable(self, detail: str) -> None:
        self._listing = False
        self._failure = detail
        self._draw()

    def _listed(self, listed: list[Pull]) -> None:
        self._listing = False
        self._enriching = bool(listed)
        self._pulls = listed
        self._draw()

    def _enriched(self, found: tuple[Pull, PullStatus]) -> None:
        pull, status = found
        self._statuses[pull.reference] = status
        self._draw()

    def _enriched_all(self, _: None) -> None:
        self._enriching = False
        self._draw()

    def _scanned(self, found: tuple[list[Session], dict[PullRequest, list[str]]]) -> None:
        sessions, holders = found
        self._sessions = {session.session_id: session for session in sessions}
        self._holders = holders
        self._draw()

    def _scan_failed(self, detail: str) -> None:
        self._holders = {}
        self.notify(detail, title="could not read the transcripts", severity="warning")
        self._draw()

    def _draw(self) -> None:
        """Redraw the table, keeping the cursor wherever its reader left it.

        The order changes underneath the cursor here in a way it never does on the fleet
        board: a pull request whose checks have just come back red climbs from the middle
        to the top, and every row below it shifts. So the cursor follows the pull request
        it was on rather than the position it was at — except at the top, which is a place
        rather than a row. Somebody who has not moved yet is reading the most urgent thing
        there is, and should still be reading it once the board knows what that is.
        """
        table = self.query_one("#pulls", DataTable)
        previous = None if table.cursor_row <= 0 else self.selected
        self._visible = pulls.ranked(self._pulls, self._statuses)

        table.clear()
        for row in build_pull_rows(self._visible, self._statuses, self._holders):
            table.add_row(*row.cells)
        if previous is not None:
            restored = next((i for i, p in enumerate(self._visible) if p.reference == previous.reference), None)
            if restored is not None:
                table.move_cursor(row=restored)

        self.query_one("#pulls-bar", Static).update(self._summary())
        self._draw_details()

    def _draw_details(self) -> None:
        pane = self.query_one("#pull-details", Static)
        pull = self.selected
        if pull is None:
            pane.update("[dim]no pull request selected[/]")
            return
        holding = (
            None
            if self._holders is None
            else [
                self._sessions[session_id]
                for session_id in self._holders.get(pull.reference, ())
                if session_id in self._sessions
            ]
        )
        pane.update(describe_pull(pull, self._statuses.get(pull.reference), holding))

    def _summary(self) -> str:
        if self._failure:
            return f"[bold red]github could not be asked[/] {escape(self._failure)}"
        if self._listing:
            return "[dim]asking github what you have open…[/]"
        if not self._pulls:
            return f"[dim]no open pull requests for {escape(self._author)}[/]"
        parts = [f"{len(self._pulls)} open"]
        if self._enriching:
            parts.append(f"[dim]reading status {len(self._statuses)}/{len(self._pulls)}…[/]")
        if self._holders is None:
            parts.append("[dim]reading transcripts…[/]")
        else:
            worked = sum(1 for pull in self._pulls if self._holders.get(pull.reference))
            parts.append(f"{worked} with sessions here")
        return " · ".join(parts)


class FleetApp(App[None]):
    """Full-screen status board over the live Claude Code fleet."""

    TITLE = f"{CLOWN} clownhead"

    CSS = """
    #bar {
        height: 1;
        background: $panel;
    }
    #clown {
        width: auto;
        padding: 0 1;
    }
    #title {
        width: 1fr;
        padding: 0 1;
        link-color: $text-muted;
        link-style: none;
        link-color-hover: $text;
        link-background-hover: $boost;
    }
    #tick {
        width: auto;
        padding: 0 1;
        color: $text-muted;
    }
    #board {
        height: 1fr;
    }
    #fleet {
        width: 1fr;
        overflow-x: hidden;
    }
    #history {
        display: none;
        width: 45%;
        border-left: solid $panel;
        padding: 0 1;
    }
    #history:focus {
        border-left: solid $accent;
    }
    #details {
        height: auto;
        padding: 0 1;
        border-top: solid $panel;
    }
    #filter {
        display: none;
        border: none;
        height: 1;
        padding: 0 1;
    }
    #filter:focus {
        border: none;
    }
    """

    BINDINGS = [
        Binding("enter", "go", "go"),
        Binding("f", "focus_session", "focus"),
        Binding("o", "open_pull_request", "open pr"),
        Binding("slash", "filter", "filter"),
        Binding("p", "pull_requests", "pull requests"),
        Binding("n", "start", "new session"),
        Binding("c", "toggle_closed", "closed", show=False),
        Binding("y", "copy_resume", "copy resume"),
        Binding("r", "rename", "rename"),
        Binding("t", "terminate", "terminate"),
        Binding("comma", "settings", "settings"),
        Binding("q", "quit", "quit"),
        Binding("ctrl+r", "refresh", "refresh", show=False),
        Binding("right", "history", "→ history", show=False),
        Binding("left", "close_history", "← close", show=False),
        Binding("escape", "dismiss_panel", "clear filter", show=False),
    ]
    """Ordered by how much use each key gets, because the footer is narrower than they are.

    Truncation is what actually edits that line, so the order decides what a narrow board
    keeps: what a session is doing, then the ways of acting on it, and `q` last — every TUI
    quits on `q`, so it is the one binding nobody needs told. `^r` and `escape` are hidden
    rather than dropped: the board reloads on its own interval and escape is contextual, so
    neither is worth the width, and both still answer. `c` is hidden because the top bar
    says it better — the count of closed sessions is the switch, and a switch that shows
    the number it would fold in needs no key advertised beside it.

    `enter` leads because it is the one key that means the same thing on every row — get me
    into this session — and the board answers it by whichever route that row needs, rather
    than by asking which of `f` and `y` the row happens to want. It is the only key that
    ends the board, which is what running something in its terminal has to cost.

    `o` sits beside `f` because it is the same kind of key: both take the session under the
    cursor somewhere else, one to its terminal and one to the pull request it was for. `p`
    sits beside `/` for the matching reason — both change what the board is a board of, and
    the two are halves of one question, since `/` searches for a pull request you can name
    and `p` is where you go when you cannot.

    Retiring a worktree has no key at all, and is in the palette instead — see
    :meth:`FleetApp.get_system_commands`, which carries everything here by name as well. A
    key is for what you do to a session while reading the board, and worktrees are not
    that: they are tidied occasionally, on purpose, and a letter spent on them is a letter
    that can be pressed by accident.
    """

    def __init__(
        self,
        loader: Loader,
        interval: float | None = None,
        terminal: Terminal | None = None,
        include_closed: bool | None = None,
        settings: Settings | None = None,
        reader: Reader | None = None,
        target: Reference | None = None,
    ) -> None:
        super().__init__()
        self._loader = loader
        self._reader: Reader = reader if reader is not None else recent_messages
        self._settings = settings if settings is not None else settings_store.load()
        self._interval = interval if interval is not None else self._settings.interval
        self._terminal = terminal
        self._show_closed = include_closed if include_closed is not None else self._settings.show_closed
        self._sessions: list[Session] = []
        self._visible: list[Session] = []
        self._previews: dict[str, list[Message]] = {}
        self._preview_asked: set[str] = set()
        self._session_pulls: dict[str, list[PullRequest]] = {}
        self._pulls_asked: set[str] = set()
        self._needle = seeded_needle(target)
        self._target: Reference | None = target
        self._read: dict[Reference, set[str]] = {}
        self._named: dict[Reference, set[str]] = {}
        self._searching: Reference | None = None
        self._failure: str | None = None
        self._loading = False
        self._own_tab_tinted = False
        self._launch: Launch | None = None
        self._starting = False

    def compose(self) -> ComposeResult:
        """Lay out the summary bar, the fleet table, the detail pane, the filter and hints."""
        with Horizontal(id="bar"):
            yield Static(CLOWN, id="clown")
            yield Static(id="title")
            yield Static(f"⟳ {format_duration(timedelta(seconds=self._interval))}", id="tick")
        with Horizontal(id="board"):
            yield FleetTable(id="fleet", cursor_type="row", zebra_stripes=True)
            with HistoryPanel(id="history"):
                yield Static(id="history-body")
        yield Static(id="details")
        yield Input(
            value=self._needle,
            placeholder="filter sessions, or paste a pull request or issue url",
            id="filter",
        )
        yield Footer()

    def on_mount(self) -> None:
        """Prepare the table, tint the board's own tab and start the refresh loop."""
        self._rebuild_columns()
        self.query_one("#fleet", DataTable).focus()
        self._tint_own_tab(self._settings.paint_tabs)
        self._draw()
        self.start_reload()
        self._ticker = self.set_interval(self._interval, self.start_reload)

    def on_unmount(self) -> None:
        """Give the board's own tab its colour back on the way out.

        A tint outliving the process it stood for is a tab claiming to be a board that is
        no longer there, which is worse than no tint at all.
        """
        self._tint_own_tab(False)

    def get_system_commands(self, screen: Screen[Any]) -> Iterable[SystemCommand]:
        """Everything the board can do, by name.

        A key is for what you reach for while reading the board, and the footer has room
        for a handful of them. The palette is the other half of the same list: you arrive
        at a command having typed its name, which suits the occasional and the destructive,
        and gives the keys the footer had to truncate away somewhere they are still
        findable. Every entry says what it does rather than restating its own title, since
        a palette is read by somebody who does not already know.
        """
        yield from super().get_system_commands(screen)
        yield SystemCommand(
            "Reload the board",
            "Read the fleet again now rather than on the next tick",
            self.action_refresh,
        )
        yield SystemCommand(
            "Settings",
            "Columns, refresh interval, history, and whether tabs are tinted",
            self.action_settings,
        )
        yield SystemCommand(
            "Closed sessions",
            "Fold the sessions that have ended into the board, or back out of it",
            self.action_toggle_closed,
        )
        yield SystemCommand(
            "Go to this session",
            "Focus the terminal of a live one, or resume an ended one right here",
            self.action_go,
        )
        yield SystemCommand(
            "Pull requests",
            "What you have open on GitHub, and which sessions here worked on each",
            self.action_pull_requests,
        )
        yield SystemCommand(
            "Open this session's pull request",
            "Open what its transcript says it was working on, in the browser",
            self.action_open_pull_request,
        )
        yield SystemCommand(
            "Start a new session for this reference",
            "Make a worktree named after the pull request or issue being filtered on",
            self.action_start,
        )
        yield SystemCommand(
            "Focus this session's terminal",
            "Signal it, and bring the window it is running in to the front",
            self.action_focus_session,
        )
        yield SystemCommand(
            "Rename this session",
            "Ask the session itself for a name that says what the job is",
            self.action_rename,
        )
        yield SystemCommand(
            "Copy this session's resume command",
            "Put the command that brings it back on the clipboard",
            self.action_copy_resume,
        )
        if self._can_terminate():
            yield SystemCommand(
                "Terminate this session",
                "Send its process SIGTERM, once it has been confirmed",
                self.action_terminate,
            )
        yield SystemCommand(
            "Retire this session's worktree",
            "Remove the checkout it worked in, leaving its branch behind",
            self.action_remove_worktree,
        )
        yield SystemCommand(
            "Cleanup worktrees",
            "Remove every worktree whose work is already in its default branch, branches optional",
            self.action_cleanup_worktrees,
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Whether an action is offered for the session under the cursor.

        `t` is the one that ever answers no. A session that has ended keeps its transcript
        and whatever the registry remembers, and the process id both once held is dropped
        from them precisely because that id may since have been handed to something else —
        so SIGTERM has nowhere to go and the key could only answer with a refusal. Hidden
        rather than greyed out, which is Textual's other answer: the footer truncates as
        it is, and the width is worth more to a key that still does something.
        """
        if action != "terminate":
            return True
        return self._can_terminate()

    def _can_terminate(self) -> bool:
        """Whether the session under the cursor still has a process to send SIGTERM to."""
        session = self.selected_session
        return session is not None and session.pid is not None

    @property
    def columns(self) -> tuple[str, ...]:
        """Table headers, with the optional columns only where they were asked for."""
        optional = (
            ("PID",) * self._settings.show_pid
            + ("TTY",) * self._settings.show_tty
            + ("WORKTREE",) * self._settings.show_worktree
            + ("PRS",) * self._settings.show_prs
        )
        return (*BASE_COLUMNS, *optional, "WHERE")

    @property
    def launch(self) -> Launch | None:
        """The command the board was left in order to run, if it was left for one."""
        return self._launch

    @property
    def selected_session(self) -> Session | None:
        """The session under the table cursor, if the fleet is not empty."""
        table = self.query_one("#fleet", DataTable)
        if not self._visible or not 0 <= table.cursor_row < len(self._visible):
            return None
        return self._visible[table.cursor_row]

    def start_reload(self) -> None:
        """Kick off a reload unless one is already in flight."""
        if self._loading:
            return
        self._loading = True
        self._reload()

    def action_refresh(self) -> None:
        """Reload the fleet now, and read the transcripts again if one is being searched.

        What a transcript search found is otherwise remembered for as long as the board is
        open, because re-reading the fleet's transcripts on every interval would be an
        expensive way to learn nothing. Asking for a reload by hand is the moment to look
        again — a session that has since started talking about the reference is exactly
        what someone pressing this is hoping to catch.
        """
        self._read.clear()
        self._named.clear()
        self._session_pulls.clear()
        self._pulls_asked.clear()
        self._searching = None
        self.start_reload()

    def action_go(self) -> None:
        """Get into the selected session, by whichever route it still has.

        A session with a process is somewhere on this machine already, so going to it means
        its terminal: the same signal `f` sends. One that has ended is a transcript and
        nothing else, and the only way back into it is to run it — so the board stands down
        and hands the terminal over, which is why this is the one key that quits.

        Textual has to have finished with the terminal before anything else is given it, so
        the command is put down here and run by whoever called :func:`run`, after the app
        has returned. Doing it from inside would exec out of a screen still in raw mode and
        leave the shell wearing it.
        """
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        if not session.is_finished:
            self.action_focus_session()
            return
        self._launch = resume_plan(session)
        self.exit()

    def action_focus_session(self) -> None:
        """Demand attention from the selected session's terminal and raise its window.

        Named around the session rather than plainly ``focus``: ``App`` already has an
        ``action_focus`` that moves focus to a widget by id.
        """
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        result = attention.focus(session, self._terminal, foreground=self._settings.foreground)
        reached = result.delivered and not result.tab_note
        severity: SeverityLevel = "information" if reached else "warning"
        self.notify(f"{result.label}: {result.detail}{result.tab_note}", severity=severity)

    def action_start(self) -> None:
        """Start a new session for whatever the board is filtered to.

        Only ever for a reference, because the reference is the whole of what the new
        session would be told. A board showing everything has nothing to seed one with, and
        a session started with no prompt is one you would have got faster by typing
        ``claude``.

        Resolving where to start it asks git for a remote per repository and GitHub for a
        title, so the work happens on a thread and the sheet opens when it answers.
        """
        if self._target is None:
            self.notify("paste a pull request or issue url first", severity="warning")
            return
        if self._starting:
            return
        self._starting = True
        self.notify(f"finding a repository for {self._target}…")
        self._resolve_start(self._target, list(self._sessions), self._named.get(self._target, set()))

    @work(thread=True, group="start")
    def _resolve_start(self, reference: Reference, sessions: list[Session], named: set[str]) -> None:
        """Ask git where this could be started and GitHub what to call it, off the event loop."""
        repos = checkouts.repos_for(reference, sessions, named)
        name = issues.slug(reference.base_slug, issues.fetch_title(reference.title_query))
        hand_back(self, self._ask_start, (reference, name, repos))

    def _ask_start(self, resolved: tuple[Reference, str, list[Path]]) -> None:
        reference, name, repos = resolved
        self._starting = False
        if not repos:
            self.notify(
                "no repository to start one in — the fleet names none",
                title=str(reference),
                severity="warning",
            )
            return
        self.push_screen(StartScreen(reference, name, repos), partial(self._start_chosen, reference, name))

    def _start_chosen(self, reference: Reference, name: str, choice: StartChoice | None) -> None:
        """Run the start command in this terminal, or put it on the clipboard instead."""
        if choice is None:
            return
        plan = start_plan(choice.repo, name=name, prompt=reference.prompt)
        if choice.copy:
            self.copy_to_clipboard(plan.shell_command)
            copy_to_pasteboard(plan.shell_command)
            self.notify(plan.shell_command, title=f"start {name}")
            return
        self._launch = plan
        self.exit()

    def action_pull_requests(self) -> None:
        """Open the board of pull requests you have open, and come back pointed at one.

        Leaving it with `enter` seeds the filter with the chosen pull request, which is the
        same route as pasting its URL into `/` — the board has one way of showing the
        sessions for a reference and this arrives at it rather than inventing a second.
        The sessions that have ended are folded in on the way, because work on a pull
        request has usually finished and a board that made you press `c` to see the session
        you just went looking for would be asking you to guess that it was there.
        """
        self.push_screen(PullsScreen(self._loader), self._pull_chosen)

    def _pull_chosen(self, worked: Worked | None) -> None:
        """Point the board at the chosen pull request, keeping the answer that came with it."""
        if worked is None:
            return
        if worked.sessions is not None:
            self._read.setdefault(worked.reference, set()).update(s.session_id for s in worked.read)
            self._named.setdefault(worked.reference, set()).update(worked.sessions)
        box = self.query_one("#filter", Input)
        box.display = True
        box.value = worked.reference.prompt
        self._needle = box.value
        if not self._show_closed:
            self._show_closed = True
            self.start_reload()
        self._retarget(worked.reference)
        self._draw()

    def action_open_pull_request(self) -> None:
        """Open what the selected session was working on, in the browser.

        A session names as many pull requests as ever scrolled past it, so more than one is
        the normal case and the choice is offered rather than guessed at. The read is the
        same one the detail pane already asked for, so by the time anybody has looked at a
        row long enough to press this the answer is usually already in hand.
        """
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        references = self._session_pulls.get(session.session_id)
        if references is None:
            self._ensure_pulls([session])
            self.notify("reading its transcript — try again in a moment", title=session.label)
            return
        if not references:
            self.notify("its transcript names no pull request", title=session.label, severity="warning")
            return
        if len(references) == 1:
            self._open_pull(references[0])
            return
        self.push_screen(PullChoiceScreen(session, references), self._open_pull)

    def _open_pull(self, reference: PullRequest | None) -> None:
        """Open a pull request in the browser, saying so either way.

        ``url`` rather than ``prompt``: the two part company for an owner-less reference,
        where ``prompt`` is the ``widgets#309`` shorthand — the right thing to hand a
        session standing in the repository, and not something a browser can open.
        """
        if reference is None:
            return
        if not reference.url:
            self.notify(str(reference), title="no url to open — the reference names no owner", severity="warning")
            return
        if open_url(reference.url):
            self.notify(reference.url, title="opened")
            return
        self.notify(reference.url, title="could not open a browser", severity="warning")

    def action_history(self) -> None:
        """Open the conversation of the selected session beside the fleet, and read it."""
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        panel = self.query_one("#history", HistoryPanel)
        panel.display = True
        self.query_one("#history-body", Static).update("[dim]reading transcript…[/]")
        panel.focus()
        self._load_history(session.session_id)

    def action_close_history(self) -> None:
        """Close the conversation and hand focus back to the fleet."""
        self.query_one("#history").display = False
        self.query_one("#fleet", DataTable).focus()

    def action_dismiss_panel(self) -> None:
        """Close the conversation if it is open, otherwise drop the filter."""
        if self.query_one("#history").display:
            self.action_close_history()
            return
        self.action_clear_filter()

    def action_settings(self) -> None:
        """Edit the overseer's settings without leaving it."""
        self.push_screen(SettingsScreen(self._settings), self._settings_changed)

    def action_terminate(self) -> None:
        """Ask the selected session's process to exit, once it has been confirmed."""
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        where = f"pid {session.pid}" if session.pid else "no process"
        question = f"[bold]Send SIGTERM to {session.label}?[/]\n[dim]{where} · {session.reason}[/]"
        if self._settings.close_tab_on_terminate:
            question += "\n[dim]its terminal tab closes once it has exited[/]"
        self.push_screen(ConfirmScreen(question), partial(self._terminate, session))

    def _terminate(self, session: Session, confirmed: bool | None) -> None:
        if not confirmed:
            return
        processes = process_table()
        try:
            terminate(session, processes)
        except (LookupError, OSError) as error:
            self.notify(str(error), title="not terminated", severity="error")
            return
        self.notify(f"SIGTERM sent to {session.label}", severity="warning")
        if self._settings.close_tab_on_terminate and session.pid is not None:
            self._close_tab(session, session.pid, processes)
        self.start_reload()

    @work(thread=True, group="close-tab")
    def _close_tab(self, session: Session, pid: int, processes: Mapping[int, Process]) -> None:
        """Close the session's tab, once the session has actually gone.

        The shell that owns the tab is found in the process table as it stood before the
        session was signalled, because the trail that leads to it runs through the session
        itself — which, by the time there is a tab worth closing, is no longer there.

        Waiting is the whole point: SIGTERM is a request, and a tab closed while Claude
        Code was still writing its transcript would take away the thing that makes the
        session resumable. A session that outlasts the wait keeps its tab, and says so.
        """
        try:
            shell = shell_of(session, processes)
        except LookupError as error:
            hand_back(self, self._tab_left_open, str(error))
            return
        if not wait_for_exit(pid):
            hand_back(self, self._tab_left_open, f"{session.label} is still running")
            return
        try:
            close_tab(shell)
        except (LookupError, OSError) as error:
            hand_back(self, self._tab_left_open, f"{session.label}: {error}")

    def _tab_left_open(self, detail: str) -> None:
        self.notify(detail, title="tab left open", severity="warning")

    def action_remove_worktree(self) -> None:
        """Retire the worktree the selected session worked in, once it has been confirmed.

        What protects a worktree takes git to find out, so the question is not asked until
        the answer is known: a confirmation offered and then refused would be a worse way
        of saying "this one has uncommitted changes" than simply saying it.
        """
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        _, worktree = split_worktree(session.cwd)
        if worktree is None:
            self.notify(f"{session.label} is not in a worktree", severity="warning")
            return
        self._inspect_worktree(session)

    @work(thread=True, group="worktree")
    def _inspect_worktree(self, session: Session) -> None:
        found = survey([session], older_than=CLEANUP_AGE, only=session.cwd)
        if not found:
            hand_back(self, self._worktree_kept, f"git does not know {shorten_path(session.cwd)}")
            return
        candidate = found[0]
        if candidate.kept_for is not None:
            hand_back(self, self._worktree_kept, f"{candidate.worktree.name}: {candidate.kept_for}")
            return
        hand_back(self, self._ask_remove_worktree, candidate)

    def _ask_remove_worktree(self, candidate: Candidate) -> None:
        branch = candidate.worktree.branch or "a detached HEAD"
        question = (
            f"[bold]Remove the worktree {escape(candidate.worktree.name)}?[/]\n"
            f"[dim]{escape(str(candidate.worktree.path))} · last worked in {_candidate_age(candidate)}[/]\n"
            f"[dim]{escape(branch)} stays; only the checkout goes[/]"
        )
        self.push_screen(ConfirmScreen(question), partial(self._confirmed_worktree, [candidate]))

    def action_cleanup_worktrees(self) -> None:
        """Retire every worktree whose work is already in its default branch.

        The board is a herd of repositories, not one, so this asks about all of them at
        once — which is the shape the problem has: worktrees pile up across every checkout
        a fleet touches, and clearing one repository at a time is how they got here.
        """
        self._cleanup_worktrees()

    @work(thread=True, group="worktree")
    def _cleanup_worktrees(self) -> None:
        candidates = [candidate for candidate in survey(self._sessions, older_than=CLEANUP_AGE) if candidate.merged]
        hand_back(self, self._ask_cleanup, candidates)

    def _ask_cleanup(self, candidates: Sequence[Candidate]) -> None:
        going = [candidate for candidate in candidates if candidate.removable]
        kept = [candidate for candidate in candidates if not candidate.removable]
        if not going:
            detail = f"{len(kept)} merged, all kept" if kept else "no merged worktrees"
            self.notify(detail, title="nothing to clean up", severity="information")
            return
        self.push_screen(CleanupScreen(going, kept), partial(self._cleaned_up, going))

    def _confirmed_worktree(self, going: Sequence[Candidate], confirmed: bool | None) -> None:
        if not confirmed:
            return
        self._remove_all(list(going), branches=False)

    def _cleaned_up(self, going: Sequence[Candidate], branches: bool | None) -> None:
        """Act on the cleanup screen's answer, which says whether the branches go too."""
        if branches is None:
            return
        self._remove_all(list(going), branches=branches)

    @work(thread=True, group="worktree")
    def _remove_all(self, going: Sequence[Candidate], branches: bool = False) -> None:
        removed = 0
        for candidate in going:
            try:
                remove_worktree(candidate.worktree, branch=branches)
            except (LookupError, OSError) as error:
                hand_back(self, self._worktree_kept, f"{candidate.worktree.name}: {error}")
                continue
            removed += 1
        if removed:
            hand_back(self, self._worktrees_removed, (removed, branches))

    def _worktrees_removed(self, outcome: tuple[int, bool]) -> None:
        removed, branches = outcome
        what = "worktree and branch" if branches else "worktree"
        self.notify(f"removed {removed} {what}{'' if removed == 1 else 's'}", severity="warning")
        self.start_reload()

    def _worktree_kept(self, detail: str) -> None:
        self.notify(detail, title="worktree kept", severity="warning")

    def action_rename(self) -> None:
        """Give the selected session a new name, in the session itself."""
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        self.push_screen(
            PromptScreen(f"[bold]Rename {session.label}[/]", session.name or ""),
            partial(self._rename, session),
        )

    def _rename(self, session: Session, name: str | None) -> None:
        if name is None or name == session.name:
            return
        try:
            rename(session, name)
        except (ValueError, LookupError, OSError) as error:
            self.notify(str(error), title="not renamed", severity="error")
            return
        self.notify(f"{session.label} is now {name}")
        self.start_reload()

    def action_toggle_closed(self) -> None:
        """Show or hide the sessions that have ended but are still resumable."""
        self._show_closed = not self._show_closed
        self.start_reload()

    def action_copy_resume(self) -> None:
        """Copy the shell command that resumes the selected session where it left off."""
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        command = resume_shell_command(session)
        self.copy_to_clipboard(command)
        copy_to_pasteboard(command)
        self.notify(command, title=f"resume {session.label}")

    def action_filter(self) -> None:
        """Reveal the filter box and type into it."""
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    def action_clear_filter(self) -> None:
        """Drop the filter and hand focus back to the table."""
        box = self.query_one("#filter", Input)
        box.value = ""
        box.display = False
        self.query_one("#fleet", DataTable).focus()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Describe whichever session the cursor has moved onto."""
        self._draw_details()

    def on_fleet_table_row_clicked(self, event: FleetTable.RowClicked) -> None:
        """Treat clicking a row as asking to read it, like `→`."""
        self.action_history()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Treat choosing a row with the keyboard as asking to go to it.

        ``FleetTable`` keeps clicks out of this message on purpose, so it means Enter and
        nothing else — a click is for reading a session, not for being handed the terminal.
        """
        event.stop()
        self.action_go()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the table as the needle is typed.

        Every screen's inputs bubble up to the app, so the settings sheet would filter
        the fleet with whatever was typed into it if this did not check who was asking.
        """
        if event.input.id != "filter":
            return
        self._needle = event.value
        self._retarget(parse_reference(event.value))
        self._draw()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Keep the filter but return to the table."""
        if event.input.id == "filter":
            self.query_one("#fleet", DataTable).focus()

    @work(thread=True, group="reload")
    def _reload(self) -> None:
        """Load the fleet and, unless told not to, tint the tabs it came from.

        Painting is a write to every session's TTY, so it belongs on this thread beside
        the discovery it follows rather than in the redraw. Its per-session refusals are
        dropped: the board repaints on an interval, and a terminal that could not be
        reached once would otherwise announce itself every few seconds.
        """
        try:
            sessions = self._loader(self._show_closed)
        except Exception as error:
            hand_back(self, self._reload_failed, str(error))
            return
        if self._settings.paint_tabs:
            attention.paint(sessions, self._terminal)
        hand_back(self, self._reload_finished, sessions)

    def _reload_finished(self, sessions: list[Session]) -> None:
        self._loading = False
        self._failure = None
        self._sessions = sessions
        self._preview_asked.clear()
        self._ensure_search()
        self._draw()

    def _reload_failed(self, detail: str) -> None:
        self._loading = False
        self._failure = detail
        self._draw()
        self.notify(detail, title="discovery failed", severity="error")

    def _retarget(self, reference: Reference | None) -> None:
        """Point the filter at a pull request or an issue, or back at the metadata it reads.

        Which of either a session was for is only ever in what it said, so answering that
        means reading transcripts — of whichever sessions the board is showing, and no
        others. Finished work is usually in a session that has ended, but whether those are
        on the board is what `c` is for, and a filter is not the place to overrule it.
        """
        if reference == self._target:
            return
        self._target = reference
        self._ensure_search()

    def _ensure_search(self) -> None:
        """Read whatever the reference being filtered on has not been looked for in yet.

        Sessions already read for it are skipped, so folding the closed ones in costs only
        the transcripts that arrived with them. One search runs at a time: the first is
        slow enough that the refresh interval would otherwise start a second over the same
        transcripts before it had finished with them.
        """
        if self._target is None or self._searching is not None:
            return
        read = self._read.setdefault(self._target, set())
        pending = [session for session in self._sessions if session.session_id not in read]
        if not pending:
            return
        self._searching = self._target
        self._search(self._target, pending)

    @work(thread=True, group="search")
    def _search(self, reference: Reference, sessions: list[Session]) -> None:
        """Read transcripts for a reference off the event loop, where they cannot stutter it."""
        try:
            named = sessions_mentioning(reference, sessions)
        except OSError:
            named = set()
        read = {session.session_id for session in sessions}
        hand_back(self, self._search_finished, (reference, read, named))

    def _search_finished(self, found: tuple[Reference, set[str], set[str]]) -> None:
        reference, read, named = found
        self._searching = None
        self._read.setdefault(reference, set()).update(read)
        self._named.setdefault(reference, set()).update(named)
        self._draw()
        self._ensure_search()

    def _draw(self) -> None:
        previous = self.selected_session
        self._visible = self._filtered()

        if self._settings.show_prs:
            self._ensure_pulls(self._visible)

        table = self.query_one("#fleet", DataTable)
        table.clear()
        for row in build_rows(self._visible, datetime.now(tz=UTC), self._session_pulls):
            optional = (
                ((row.pid,) if self._settings.show_pid else ())
                + ((row.tty,) if self._settings.show_tty else ())
                + ((row.worktree,) if self._settings.show_worktree else ())
                + ((row.prs,) if self._settings.show_prs else ())
            )
            table.add_row(Text(row.status, style=row.style), row.name, row.quiet, row.age, *optional, row.where)
        if previous is not None:
            restored = next((i for i, s in enumerate(self._visible) if s.session_id == previous.session_id), None)
            if restored is not None:
                table.move_cursor(row=restored)

        self.query_one("#title", Static).update(self._summary())
        self._draw_details()

    def _filtered(self) -> list[Session]:
        """The fleet the filter leaves, by transcript for a reference and by metadata otherwise.

        A search still running shows nothing rather than everything: the rows that survive
        it are a small handful of the fleet, and a table that emptied out as the answer
        arrived would invite acting on a session that was never a match.
        """
        if self._target is None:
            return [session for session in self._sessions if matches(session, self._needle)]
        named = self._named.get(self._target, set())
        return [session for session in self._sessions if session.session_id in named]

    def _draw_details(self) -> None:
        session = self.selected_session
        self.refresh_bindings()
        if session is None:
            self.query_one("#details", Static).update("[dim]no session selected[/]")
            return
        self._ensure_pulls([session])
        terminal = attention.terminal_of(session, self._terminal) if session.app else None
        self.query_one("#details", Static).update(
            describe(
                session,
                terminal=terminal.name if terminal else None,
                pulls=self._session_pulls.get(session.session_id),
            )
        )

    def _ensure_pulls(self, sessions: Iterable[Session]) -> None:
        """Read what pull requests these sessions named, once per session per board.

        Asked of one session or of the whole visible board, depending on who needs the
        answer: the detail pane wants the row under the cursor, and the PRS column wants
        every row at once. Either way it is one worker over the sessions not yet read, so
        scrolling a long board does not spawn a thread per row.

        What is found is kept until `^r`, for the same reason a transcript search is: the
        answer changes only when a session says something new, and re-reading it every five
        seconds is an expensive way to learn nothing.
        """
        pending = [session.session_id for session in sessions if session.session_id not in self._pulls_asked]
        if not pending:
            return
        self._pulls_asked.update(pending)
        self._read_pulls(pending)

    @work(thread=True, group="session-pulls")
    def _read_pulls(self, session_ids: list[str]) -> None:
        """Scan transcripts for pull requests, off the event loop."""
        hand_back(self, self._pulls_read, {session_id: pulls_mentioned(session_id) for session_id in session_ids})

    def _pulls_read(self, found: dict[str, list[PullRequest]]) -> None:
        """Fold in what the transcripts named and redraw, since a column may be showing it."""
        self._session_pulls.update(found)
        self._draw()

    def _repaint(self) -> None:
        """Answer a flipped tab setting now rather than at the next reload.

        Turning it off clears the tabs the overseer tinted, so opting out leaves the
        terminals as they were found instead of freezing them on the last state seen.
        """
        painter = attention.paint if self._settings.paint_tabs else attention.reset
        painter(self._sessions, self._terminal)
        self._tint_own_tab(self._settings.paint_tabs)

    def _tint_own_tab(self, tinted: bool) -> None:
        """Take the board's own tab, or hand it back, without doing either twice.

        Only a tab the overseer tinted is cleared again: with tinting switched off its own
        tab is never touched, and a colour someone set on it themselves is theirs to keep.
        """
        if tinted == self._own_tab_tinted:
            return
        if tinted:
            self._own_tab_tinted = attention.paint_self(self._terminal).delivered
            return
        attention.reset_self(self._terminal)
        self._own_tab_tinted = False

    def _rebuild_columns(self) -> None:
        table = self.query_one("#fleet", DataTable)
        table.clear(columns=True)
        table.add_columns(*self.columns)

    def _settings_changed(self, settings: Settings | None) -> None:
        if settings is None:
            return
        columns = (settings.show_pid, settings.show_tty, settings.show_worktree, settings.show_prs)
        rebuild = columns != (
            self._settings.show_pid,
            self._settings.show_tty,
            self._settings.show_worktree,
            self._settings.show_prs,
        )
        retime = settings.interval != self._settings.interval
        repaint = settings.paint_tabs != self._settings.paint_tabs
        self._settings = settings
        settings_store.save(settings)
        if rebuild:
            self._rebuild_columns()
        if repaint:
            self._repaint()
        if retime:
            self._interval = settings.interval
            self._ticker.stop()
            self._ticker = self.set_interval(self._interval, self.start_reload)
            self.query_one("#tick", Static).update(f"⟳ {format_duration(timedelta(seconds=self._interval))}")
        self._draw()

    @work(thread=True, group="history")
    def _load_history(self, session_id: str) -> None:
        try:
            messages = self._reader(session_id, limit=self._settings.history_turns)
        except Exception:
            messages = []
        hand_back(self, self._history_loaded, (session_id, messages))

    def _history_loaded(self, loaded: tuple[str, list[Message]]) -> None:
        session_id, messages = loaded
        self._previews[session_id] = messages
        session = self.selected_session
        if session is None or session.session_id != session_id:
            return
        self.query_one("#history-body", Static).update(conversation(messages))
        panel = self.query_one("#history", HistoryPanel)
        self.call_after_refresh(panel.scroll_end, animate=False)

    def _summary(self) -> str:
        """What the fleet amounts to, and — where it is not the usual one — where it came from.

        The config directory rides at the end rather than in a corner of its own so that a
        bar too narrow for both loses the directory and keeps the count of what is waiting
        on you, which is the number the board exists to show.
        """
        parts = self._fleet_counts()
        notice = config_dir_notice()
        if notice:
            parts.append(f"[dim]{notice}[/]")
        return " · ".join(parts)

    def _fleet_counts(self) -> list[str]:
        if self._failure:
            return [f"[bold red]discovery failed[/] {self._failure}"]
        if not self._sessions:
            return ["[dim]no live sessions[/]", self._closed_switch()]

        total = len(self._sessions)
        scope = f"{len(self._visible)} of {total}" if self._needle else str(total)
        parts = [f"{scope} session{'' if total == 1 else 's'}"]
        if self._target is not None:
            parts.append(f"[cyan]{self._target}[/]")
            if self._searching is not None:
                parts.append("[dim]reading transcripts…[/]")
            elif not self._visible and not self._show_closed:
                parts.append("[bold]c[/][dim] searches the ones that have ended too[/]")
        waiting = sum(1 for session in self._visible if session.needs_attention)
        if waiting:
            parts.append(f"[bold red]{waiting} waiting[/]")
        parts.append(self._closed_switch())
        return parts

    def _closed_switch(self) -> str:
        """The count of sessions that have ended, which is also the switch that folds them in.

        It stays in the bar whether they are shown or not. A count that only appeared once
        you had already found them would be a switch you could turn off and never back on,
        so the hidden state offers the thing to click instead of a number — the sessions
        that have ended are not loaded while they are hidden, so there is none to give.

        The bracketed ``c`` names the key that does the same thing, which is why the footer
        no longer spends a column saying so. The brackets are escaped: Rich would otherwise
        read them as the markup that surrounds them.
        """
        if not self._show_closed:
            return r"[@click=app.toggle_closed]show \[c]losed[/]"
        finished = sum(1 for session in self._visible if session.status is Status.CLOSED)
        return rf"[@click=app.toggle_closed]{finished} \[c]losed[/]"


def run(
    loader: Loader,
    interval: float | None = None,
    terminal: Terminal | None = None,
    include_closed: bool | None = None,
    settings: Settings | None = None,
    reader: Reader | None = None,
    target: Reference | None = None,
) -> Launch | None:
    """Launch the fleet overseer, block until the user quits, and say what to run next.

    Anything left unset falls back to the saved settings, which the overseer can edit.

    A board left by `enter` hands back the command it was left for, to be run once this
    has returned and Textual has given the terminal back. Nothing is run from inside the
    app on purpose: the whole point of that key is to become the session, and a process
    replaced while a screen is still up would inherit a terminal in raw mode.
    """
    app = FleetApp(
        loader=loader,
        interval=interval,
        terminal=terminal,
        include_closed=include_closed,
        settings=settings,
        reader=reader,
        target=target,
    )
    app.run()
    return app.launch
