"""The interactive fleet overseer, which bare ``clownhead`` lands in.

Discovery shells out to ``claude agents --json`` and ``ps``, which is slow enough to
stutter a redraw, so reloads run on a worker thread and hand their result back to the
event loop. Loading is injected rather than imported so the app can be driven in tests
without a live fleet.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any, Protocol

from pydantic import ValidationError
from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.notifications import SeverityLevel
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Label, Static, Switch

from clownhead import attention
from clownhead import settings as settings_store
from clownhead.control import close_tab, rename, shell_of, terminate, wait_for_exit
from clownhead.discovery import Message, Process, process_table, recent_messages, relocated_config_dir
from clownhead.models import Session, Status
from clownhead.render import build_rows, conversation, describe, format_duration, shorten_path, truncate
from clownhead.resume import resume_shell_command
from clownhead.search import PullRequest, parse_pull_request, sessions_mentioning
from clownhead.settings import Settings
from clownhead.terminal import Terminal, copy_to_pasteboard

BASE_COLUMNS = ("STATUS", "NAME", "QUIET", "AGE")
DEFAULT_INTERVAL = 5.0
CLOWN = "\N{CLOWN FACE}"
CONFIG_DIR_CAP = 40


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
        Binding("f", "focus_session", "focus"),
        Binding("slash", "filter", "filter"),
        Binding("c", "toggle_closed", "closed", show=False),
        Binding("y", "copy_resume", "copy resume"),
        Binding("r", "rename", "rename"),
        Binding("t", "terminate", "terminate"),
        Binding("comma", "settings", "settings"),
        Binding("q", "quit", "quit"),
        Binding("R", "refresh", "refresh", show=False),
        Binding("right", "history", "→ history", show=False),
        Binding("left", "close_history", "← close", show=False),
        Binding("escape", "dismiss_panel", "clear filter", show=False),
    ]
    """Ordered by how much use each key gets, because the footer is narrower than they are.

    Truncation is what actually edits that line, so the order decides what a narrow board
    keeps: what a session is doing, then the ways of acting on it, and `q` last — every TUI
    quits on `q`, so it is the one binding nobody needs told. `R` and `escape` are hidden
    rather than dropped: the board reloads on its own interval and escape is contextual, so
    neither is worth the width, and both still answer. `c` is hidden because the top bar
    says it better — the count of closed sessions is the switch, and a switch that shows
    the number it would fold in needs no key advertised beside it.
    """

    def __init__(
        self,
        loader: Loader,
        interval: float | None = None,
        terminal: Terminal | None = None,
        include_closed: bool | None = None,
        settings: Settings | None = None,
        reader: Reader | None = None,
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
        self._needle = ""
        self._pull_request: PullRequest | None = None
        self._read: dict[PullRequest, set[str]] = {}
        self._named: dict[PullRequest, set[str]] = {}
        self._searching: PullRequest | None = None
        self._failure: str | None = None
        self._loading = False
        self._own_tab_tinted = False

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
        yield Input(placeholder="filter sessions, or paste a pull request url", id="filter")
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

    @property
    def columns(self) -> tuple[str, ...]:
        """Table headers, with the process columns only where they were asked for."""
        optional = ("PID",) * self._settings.show_pid + ("TTY",) * self._settings.show_tty
        return (*BASE_COLUMNS, *optional, "WHERE")

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

        What a pull request search found is otherwise remembered for as long as the board
        is open, because re-reading the fleet's transcripts on every interval would be an
        expensive way to learn nothing. Asking for a reload by hand is the moment to look
        again — a session that has since started talking about the pull request is exactly
        what someone pressing this is hoping to catch.
        """
        self._read.clear()
        self._named.clear()
        self._searching = None
        self.start_reload()

    def action_focus_session(self) -> None:
        """Demand attention from the selected session's terminal and raise its window.

        Named around the session rather than plainly ``focus``: ``App`` already has an
        ``action_focus`` that moves focus to a widget by id.
        """
        session = self.selected_session
        if session is None:
            self.notify("nothing selected", severity="warning")
            return
        result = attention.focus(session, self._terminal)
        severity: SeverityLevel = "information" if result.delivered else "warning"
        self.notify(f"{result.label}: {result.detail}", severity=severity)

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
            self._hand_back(self._tab_left_open, str(error))
            return
        if not wait_for_exit(pid):
            self._hand_back(self._tab_left_open, f"{session.label} is still running")
            return
        try:
            close_tab(shell)
        except (LookupError, OSError) as error:
            self._hand_back(self._tab_left_open, f"{session.label}: {error}")

    def _tab_left_open(self, detail: str) -> None:
        self.notify(detail, title="tab left open", severity="warning")

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

    def on_input_changed(self, event: Input.Changed) -> None:
        """Re-filter the table as the needle is typed.

        Every screen's inputs bubble up to the app, so the settings sheet would filter
        the fleet with whatever was typed into it if this did not check who was asking.
        """
        if event.input.id != "filter":
            return
        self._needle = event.value
        self._retarget(parse_pull_request(event.value))
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
            self._hand_back(self._reload_failed, str(error))
            return
        if self._settings.paint_tabs:
            attention.paint(sessions, self._terminal)
        self._hand_back(self._reload_finished, sessions)

    def _hand_back[T](self, callback: Callable[[T], None], value: T) -> None:
        if self.is_running:
            self.call_from_thread(callback, value)

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

    def _retarget(self, pull_request: PullRequest | None) -> None:
        """Point the filter at a pull request, or back at the metadata it usually reads.

        Which pull request a session was for is only ever in what it said, so answering
        that means reading transcripts — of whichever sessions the board is showing, and
        no others. Finished work is usually in a session that has ended, but whether those
        are on the board is what `c` is for, and a filter is not the place to overrule it.
        """
        if pull_request == self._pull_request:
            return
        self._pull_request = pull_request
        self._ensure_search()

    def _ensure_search(self) -> None:
        """Read whatever the pull request being filtered on has not been looked for in yet.

        Sessions already read for it are skipped, so folding the closed ones in costs only
        the transcripts that arrived with them. One search runs at a time: the first is
        slow enough that the refresh interval would otherwise start a second over the same
        transcripts before it had finished with them.
        """
        if self._pull_request is None or self._searching is not None:
            return
        read = self._read.setdefault(self._pull_request, set())
        pending = [session for session in self._sessions if session.session_id not in read]
        if not pending:
            return
        self._searching = self._pull_request
        self._search(self._pull_request, pending)

    @work(thread=True, group="search")
    def _search(self, pull_request: PullRequest, sessions: list[Session]) -> None:
        """Read transcripts for a pull request off the event loop, where they cannot stutter it."""
        try:
            named = sessions_mentioning(pull_request, sessions)
        except OSError:
            named = set()
        read = {session.session_id for session in sessions}
        self._hand_back(self._search_finished, (pull_request, read, named))

    def _search_finished(self, found: tuple[PullRequest, set[str], set[str]]) -> None:
        pull_request, read, named = found
        self._searching = None
        self._read.setdefault(pull_request, set()).update(read)
        self._named.setdefault(pull_request, set()).update(named)
        self._draw()
        self._ensure_search()

    def _draw(self) -> None:
        previous = self.selected_session
        self._visible = self._filtered()

        table = self.query_one("#fleet", DataTable)
        table.clear()
        for row in build_rows(self._visible, datetime.now(tz=UTC)):
            optional = ((row.pid,) if self._settings.show_pid else ()) + ((row.tty,) if self._settings.show_tty else ())
            table.add_row(Text(row.status, style=row.style), row.name, row.quiet, row.age, *optional, row.where)
        if previous is not None:
            restored = next((i for i, s in enumerate(self._visible) if s.session_id == previous.session_id), None)
            if restored is not None:
                table.move_cursor(row=restored)

        self.query_one("#title", Static).update(self._summary())
        self._draw_details()

    def _filtered(self) -> list[Session]:
        """The fleet the filter leaves, by transcript for a pull request and by metadata otherwise.

        A search still running shows nothing rather than everything: the rows that survive
        it are a small handful of the fleet, and a table that emptied out as the answer
        arrived would invite acting on a session that was never a match.
        """
        if self._pull_request is None:
            return [session for session in self._sessions if matches(session, self._needle)]
        named = self._named.get(self._pull_request, set())
        return [session for session in self._sessions if session.session_id in named]

    def _draw_details(self) -> None:
        session = self.selected_session
        if session is None:
            self.query_one("#details", Static).update("[dim]no session selected[/]")
            return
        terminal = attention.terminal_of(session, self._terminal) if session.app else None
        self.query_one("#details", Static).update(describe(session, terminal=terminal.name if terminal else None))

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
        rebuild = (settings.show_pid, settings.show_tty) != (self._settings.show_pid, self._settings.show_tty)
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
        self._hand_back(self._history_loaded, (session_id, messages))

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
        if self._pull_request is not None:
            parts.append(f"[cyan]{self._pull_request}[/]")
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
) -> None:
    """Launch the fleet overseer and block until the user quits.

    Anything left unset falls back to the saved settings, which the overseer can edit.
    """
    FleetApp(
        loader=loader,
        interval=interval,
        terminal=terminal,
        include_closed=include_closed,
        settings=settings,
        reader=reader,
    ).run()
