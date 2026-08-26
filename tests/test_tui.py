import json
from pathlib import Path

import pytest
from rich.console import Console
from rich.markup import render as render_markup
from textual.widgets import DataTable, Input, Static, Switch

from clownhead import settings as settings_store
from clownhead import tui as tui_module
from clownhead.discovery import Message, Process
from clownhead.issues import Issue, Tracker
from clownhead.models import Session, Status
from clownhead.pulls import Status as PullStatus
from clownhead.settings import Settings
from clownhead.terminal import ITerm2Terminal
from clownhead.tui import FleetApp, PullChoiceScreen, config_dir_notice, matches
from clownhead.worktrees import Candidate, Worktree

OWN_TTY = Path("/dev/ttys009")
CLEARED = "\033]6;1;bg;*;default\a"


@pytest.fixture(autouse=True)
def silent_pasteboard(monkeypatch):
    monkeypatch.setattr(tui_module, "copy_to_pasteboard", lambda text: True)


@pytest.fixture(autouse=True)
def process_snapshot(monkeypatch) -> dict[int, Process]:
    """The process table the overseer reads before it signals anything, in place of ``ps``."""
    snapshot = {77730: Process(pid=77730, ppid=55997, tty=Path("/dev/ttys004"), command="claude --resume")}
    monkeypatch.setattr(tui_module, "process_table", lambda: snapshot)
    return snapshot


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def headless_board(monkeypatch):
    """A board with no tab of its own, so only the fleet's tabs are signalled.

    Whether the suite has a terminal behind it is up to how it was run, and a board that
    tinted its own tab under `pytest -s` would answer the same assertion differently.
    """
    monkeypatch.setattr(tui_module.attention, "own_tty", lambda: None)


@pytest.fixture
def own_tab(monkeypatch):
    monkeypatch.setattr(tui_module.attention, "own_tty", lambda: OWN_TTY)
    return OWN_TTY


@pytest.fixture(autouse=True)
def default_config_dir(monkeypatch):
    """Whatever config directory the suite is run from, the board sees the default one."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)


def fleet() -> list[Session]:
    return [
        Session(
            session_id="4e020900-df7c",
            cwd=Path("/tmp/payments-api"),
            name="payments-api-7c",
            pid=77730,
            status=Status.WAITING,
            waiting_for="input needed",
            tty=Path("/dev/ttys004"),
        ),
        Session(
            session_id="cef6830d-aaaa",
            cwd=Path("/tmp/web-platform"),
            name="web-platform-1d",
            status=Status.IDLE,
            tty=Path("/dev/ttys017"),
        ),
    ]


def closed_session() -> Session:
    return Session(
        session_id="9a1b2c3d-eeee",
        cwd=Path("/tmp/invoice-parser"),
        name="invoice-parser-3d",
        status=Status.CLOSED,
    )


WORKTREE_CWD = Path("/tmp/web-platform/.claude/worktrees/search-index")


def worktree_fleet() -> list[Session]:
    """A fleet whose first row is a session that worked in a worktree."""
    return [
        Session(session_id="8b1c4f22-0d31", cwd=WORKTREE_CWD, name="index-rebuild", status=Status.CLOSED),
        *fleet(),
    ]


def candidate(name: str = "search-index", **overrides) -> Candidate:
    entry = Worktree(
        path=Path("/tmp/web-platform/.claude/worktrees") / name,
        repo=Path("/tmp/web-platform"),
        name=name,
        branch=f"feature/{name}",
        head="a" * 40,
    )
    fields = {"worktree": entry, "last_used": None, "merged": True, "kept_for": None}
    return Candidate(**{**fields, **overrides})


class SilentTerminal(ITerm2Terminal):
    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def build_app(
    sessions=None,
    loader=None,
    terminal=None,
    include_closed=False,
    settings=None,
    target=None,
) -> FleetApp:
    return FleetApp(
        loader=loader or (lambda include_closed: fleet() if sessions is None else sessions),
        interval=3600,
        terminal=terminal or SilentTerminal(),
        include_closed=include_closed,
        settings=settings or Settings(),
        target=target,
    )


async def settle(app: FleetApp, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def table_of(app: FleetApp) -> DataTable:
    return app.query_one("#fleet", DataTable)


def footer_keys(app: FleetApp) -> set[str]:
    return {binding.action for _, binding, _, _ in app.screen.active_bindings.values() if binding.show}


def notified(app: FleetApp) -> str:
    return "\n".join(str(notification.message) for notification in app._notifications)


def title_of(app: FleetApp) -> str:
    return str(app.query_one("#title", Static).content)


@pytest.mark.parametrize(
    ("needle", "expected"),
    [("", True), ("payments", True), ("PAYMENTS", True), ("4e020900", True), ("input needed", True), ("nope", False)],
)
def test_matches_searches_name_reason_path_and_id(needle, expected):
    assert matches(fleet()[0], needle) is expected


async def test_tui_lists_the_fleet():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert table_of(app).row_count == 2
        assert "payments-api-7c" in str(table_of(app).get_row_at(0))
        assert "2 sessions" in title_of(app)
        assert "1 waiting" in title_of(app)


async def test_tui_wears_a_clown():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert str(app.query_one("#clown", Static).content) == "\N{CLOWN FACE}"


def test_config_dir_notice_names_a_relocated_directory(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-personal")

    assert config_dir_notice() == "~/.claude-personal"


@pytest.mark.parametrize("override", [None, "", "~/.claude"])
def test_config_dir_notice_says_nothing_about_the_default_directory(monkeypatch, override):
    if override is not None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", override)

    assert config_dir_notice() == ""


def test_config_dir_notice_truncates_a_long_directory(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", f"/{'nested/' * 12}claude")

    assert config_dir_notice().endswith("…")
    assert len(config_dir_notice()) == tui_module.CONFIG_DIR_CAP


async def test_tui_names_a_relocated_config_dir_after_the_counts(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-personal")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert title_of(app).endswith("· [dim]~/.claude-personal[/]")
        assert "1 waiting" in title_of(app)


async def test_tui_names_a_relocated_config_dir_an_empty_fleet_came_out_of(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-personal")
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "no live sessions" in title_of(app)
        assert "~/.claude-personal" in title_of(app)


async def test_tui_says_nothing_about_the_default_config_dir():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert title_of(app) == r"2 sessions · [bold red]1 waiting[/] · [@click=app.toggle_closed]show \[c]losed[/]"


async def test_tui_reports_an_empty_fleet():
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert table_of(app).row_count == 0
        assert "no live sessions" in title_of(app)


async def test_tui_filters_the_fleet():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("slash", *"payments")

        assert app.query_one("#filter", Input).has_focus
        assert table_of(app).row_count == 1
        assert "1 of 2 sessions" in title_of(app)


async def test_tui_escape_clears_the_filter():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("slash", *"payments")
        await pilot.press("escape")

        assert table_of(app).row_count == 2
        assert table_of(app).has_focus


def transcript(root: Path, session_id: str, said: str, cwd: str = "/tmp/payments-api") -> Path:
    project = root / "projects" / cwd.replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text(json.dumps({"sessionId": session_id, "cwd": cwd, "type": "user", "message": {"content": said}}))
    return path


async def filter_by(app: FleetApp, pilot, needle: str) -> None:
    """Put a whole needle in the filter box, which a pasted URL is faster typed as."""
    app.query_one("#filter", Input).value = needle
    await settle(app, pilot)
    await settle(app, pilot)


async def test_tui_filters_the_fleet_by_pull_request(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    transcript(tmp_path, "cef6830d-aaaa", "widgets#420 is somebody else's", cwd="/tmp/web-platform")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "https://github.com/acme/widgets/pull/42")

        assert table_of(app).row_count == 1
        assert "payments-api-7c" in str(table_of(app).get_row_at(0))
        assert "acme/widgets#42" in title_of(app)


async def test_tui_pull_request_filter_leaves_the_closed_setting_alone(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    asked: list[bool] = []
    app = build_app(loader=lambda include_closed: asked.append(include_closed) or fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "acme/widgets#42")
        await pilot.press("escape")
        await settle(app, pilot)

        assert not any(asked)


async def test_tui_pull_request_filter_points_at_the_sessions_it_did_not_search(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "acme/widgets#42")

        assert table_of(app).row_count == 0
        assert "[bold]c[/][dim] searches the ones that have ended too" in title_of(app)


async def test_tui_pull_request_filter_says_nothing_about_closed_once_they_are_shown(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app(include_closed=True)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "acme/widgets#42")

        assert "c searches" not in title_of(app)


async def test_tui_filter_still_matches_metadata_when_it_is_not_a_pull_request():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "payments")

        assert table_of(app).row_count == 1
        assert "widgets" not in title_of(app)


async def test_tui_filters_the_fleet_by_issue(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "picking up https://github.com/acme/widgets/issues/2")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await filter_by(app, pilot, "https://github.com/acme/widgets/issues/2")

        assert table_of(app).row_count == 1
        assert "payments-api-7c" in str(table_of(app).get_row_at(0))
        assert "acme/widgets#2" in title_of(app)


async def test_tui_opens_already_pointed_at_a_reference(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://craft.atlassian.net/browse/PLAT-4471 it is")
    ticket = Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="craft.atlassian.net")
    app = build_app(target=ticket)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert table_of(app).row_count == 1
        assert "PLAT-4471" in title_of(app)
        assert app.query_one("#filter", Input).value == "https://craft.atlassian.net/browse/PLAT-4471"


async def test_tui_opened_on_an_issue_does_not_read_it_back_as_a_pull_request(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert app._target == ISSUE


def resolved(monkeypatch, repos, title="Open a session"):
    monkeypatch.setattr(tui_module.checkouts, "repos_for", lambda reference, sessions, named: list(repos))
    monkeypatch.setattr(tui_module.issues, "fetch_title", lambda query: title)


ISSUE = Issue(tracker=Tracker.GITHUB, key="2", repo="widgets", owner="acme")


async def test_tui_start_offers_the_command_for_the_reference_it_is_filtered_to(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets")])
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)

        assert isinstance(app.screen, tui_module.StartScreen)
        shown = str(app.screen.query_one("#command", Static).content)
        assert "--worktree issue-2-open-a-session" in shown
        assert "--name issue-2-open-a-session" in shown
        assert "https://github.com/acme/widgets/issues/2" in shown


async def test_tui_start_leaves_the_board_with_the_command_to_run(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets")])
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()

    assert app.launch is not None
    assert app.launch.directory == Path("/tmp/widgets")
    assert app.launch.argv == (
        "claude",
        "--permission-mode",
        "plan",
        "--worktree",
        "issue-2-open-a-session",
        "--name",
        "issue-2-open-a-session",
        "https://github.com/acme/widgets/issues/2",
    )


async def test_tui_start_copies_the_command_rather_than_running_it(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets")])
    copied: list[str] = []
    monkeypatch.setattr(tui_module, "copy_to_pasteboard", lambda text: bool(copied.append(text)))
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)
        await pilot.press("y")
        await settle(app, pilot)

        assert app.launch is None
        assert copied == [
            "(cd /tmp/widgets && claude --permission-mode plan --worktree issue-2-open-a-session "
            "--name issue-2-open-a-session https://github.com/acme/widgets/issues/2)"
        ]


async def test_tui_start_lets_you_pick_between_checkouts(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets"), Path("/tmp/other")])
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert app.launch is not None
    assert app.launch.directory == Path("/tmp/other")


async def test_tui_start_can_be_abandoned_without_starting_anything(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets")])
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.StartScreen)
        assert app.launch is None
        assert app.is_running


async def test_tui_start_names_the_worktree_for_the_issue_alone_when_gh_says_nothing(monkeypatch):
    resolved(monkeypatch, [Path("/tmp/widgets")], title=None)
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)

        assert "--worktree issue-2 " in str(app.screen.query_one("#command", Static).content)


async def test_tui_start_says_so_when_the_fleet_names_no_repository(monkeypatch):
    resolved(monkeypatch, [])
    app = build_app(target=ISSUE)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.StartScreen)
        assert "no repository" in notified(app)


async def test_tui_start_needs_a_reference_to_start_anything_for(monkeypatch):
    monkeypatch.setattr(
        tui_module.checkouts,
        "repos_for",
        lambda reference, sessions, named: pytest.fail("a repository was resolved without a reference"),
    )
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("n")
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.StartScreen)
        assert "paste a pull request or issue url" in notified(app)


def headers_of(app: FleetApp) -> list[str]:
    return [str(column.label) for column in table_of(app).columns.values()]


async def test_tui_hides_the_process_columns_by_default():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert headers_of(app) == ["STATUS", "NAME", "QUIET", "AGE", "WHERE"]
        assert "77730" not in str(table_of(app).get_row_at(0))


async def test_tui_shows_the_owning_process_id_when_settings_ask():
    app = build_app(settings=Settings(show_pid=True, show_tty=True))

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert headers_of(app) == ["STATUS", "NAME", "QUIET", "AGE", "PID", "TTY", "WHERE"]
        assert "77730" in str(table_of(app).get_row_at(0))


def details_of(app: FleetApp) -> str:
    return render_markup(str(app.query_one("#details", Static).content)).plain


async def test_tui_details_describe_the_session_under_the_cursor():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "payments-api-7c" in details_of(app)
        assert "4e020900-df7c" in details_of(app)
        assert "/tmp/payments-api" in details_of(app)


def history_of(app: FleetApp) -> str:
    console = Console(width=200, no_color=True)
    with console.capture() as capture:
        console.print(app.query_one("#history-body", Static).content)
    return capture.get()


async def test_tui_right_arrow_opens_the_conversation_beside_the_fleet(monkeypatch):
    asked: list[tuple[str, int]] = []

    def history(session_id: str, limit: int) -> list[Message]:
        asked.append((session_id, limit))
        return [
            Message(role="user", text="show history in detail view"),
            Message(role="assistant", text="Enter opens it beside the fleet."),
        ]

    monkeypatch.setattr(tui_module, "recent_messages", history)
    app = build_app(settings=Settings(history_turns=12))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert not app.query_one("#history").display

        await pilot.press("right")
        await settle(app, pilot)

        assert app.query_one("#history").display
        assert asked == [("4e020900-df7c", 12)]
        assert "show history in detail view" in history_of(app)
        assert "Enter opens it beside the fleet." in history_of(app)


def long_history() -> list[Message]:
    return [Message(role="user" if turn % 2 else "assistant", text=f"turn {turn}") for turn in range(40)]


def history_panel(app: FleetApp) -> tui_module.HistoryPanel:
    return app.query_one("#history", tui_module.HistoryPanel)


async def test_tui_history_opens_on_the_newest_turn(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: long_history())
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await settle(app, pilot)

        assert history_panel(app).has_focus
        assert history_panel(app).scroll_offset.y > 0


async def test_tui_arrows_scroll_the_open_conversation_instead_of_the_fleet(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: long_history())
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await settle(app, pilot)
        bottom = history_panel(app).scroll_offset.y
        selected = app.selected_session

        await pilot.press("up")
        await pilot.pause()

        assert history_panel(app).scroll_offset.y < bottom
        assert selected is not None
        assert app.selected_session is not None
        assert app.selected_session.session_id == selected.session_id


async def test_tui_closing_the_conversation_hands_the_arrows_back(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: long_history())
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await settle(app, pilot)
        await pilot.press("left")
        await pilot.press("down")

        assert table_of(app).has_focus
        assert app.selected_session is not None
        assert app.selected_session.name == "web-platform-1d"


async def test_tui_clicking_a_row_reads_it_and_leaves_its_terminal_alone(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: [])
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.click(table_of(app), offset=(4, 2))
        await settle(app, pilot)

        assert app.query_one("#history").display
        assert terminal.written == []


async def test_tui_clicking_the_selected_row_still_does_not_focus_it(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: [])
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.click(table_of(app), offset=(4, 1))
        await pilot.click(table_of(app), offset=(4, 1))
        await settle(app, pilot)

        assert terminal.written == []


async def test_tui_clicking_moves_the_cursor_to_that_row(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: [])
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.click(table_of(app), offset=(4, 2))
        await settle(app, pilot)

        assert app.selected_session is not None
        assert app.selected_session.name == "web-platform-1d"
        assert "web-platform-1d" in details_of(app)


@pytest.mark.parametrize("key", ["left", "escape"])
async def test_tui_closes_the_conversation(monkeypatch, key):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: [])
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await settle(app, pilot)
        await pilot.press(key)

        assert not app.query_one("#history").display
        assert table_of(app).has_focus


async def test_tui_enter_signals_a_live_session_rather_than_reading_it():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("enter")

        assert "RequestAttention" in "".join(terminal.written)
        assert app.launch is None
        assert not app.query_one("#history").display


async def test_tui_enter_on_a_closed_session_leaves_the_board_to_resume_it():
    ended = Session(session_id="87e26be1-0000", cwd=Path("/tmp/design-system"), status=Status.CLOSED)
    app = build_app(sessions=[ended], settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("enter")
        await pilot.pause()

    assert app.launch is not None
    assert app.launch.directory == Path("/tmp/design-system")
    assert app.launch.argv == ("claude", "--resume", "87e26be1-0000")


async def test_tui_clicking_a_row_reads_it_rather_than_going_to_it(monkeypatch):
    monkeypatch.setattr(tui_module, "recent_messages", lambda session_id, limit: [])
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.click(table_of(app), offset=(4, 2))
        await settle(app, pilot)

        assert app.query_one("#history").display
        assert app.launch is None
        assert terminal.written == []


async def test_tui_arrow_keys_leave_the_filter_alone():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("slash", *"payments", "left", "right")

        assert app.query_one("#filter", Input).value == "payments"
        assert not app.query_one("#history").display


async def test_tui_history_survives_a_transcript_that_cannot_be_read(monkeypatch):
    def explode(session_id: str, limit: int) -> list[Message]:
        raise OSError("transcript gone")

    monkeypatch.setattr(tui_module, "recent_messages", explode)
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("right")
        await settle(app, pilot)

        assert "nothing said yet" in history_of(app)
        assert "payments-api-7c" in details_of(app)


async def test_tui_details_follow_the_cursor():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")

        assert "web-platform-1d" in details_of(app)
        assert "payments-api-7c" not in details_of(app)


async def test_tui_details_report_an_empty_fleet():
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "no session selected" in details_of(app)


async def test_tui_focus_signals_the_selected_session():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("f")

        assert app.selected_session is not None
        assert app.selected_session.name == "payments-api-7c"
        assert "\033]1337;StealFocus\a" in terminal.written


async def test_tui_focus_honours_the_foreground_setting():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(foreground=False, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("f")

        assert "\033]1337;RequestAttention=yes\a" in terminal.written
        assert "\033]1337;StealFocus\a" not in terminal.written


async def test_tui_tints_every_tab_as_it_reloads():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "brightness;220" in terminal.written[0]
        assert terminal.written[1] == "\033]6;1;bg;*;default\a"


async def test_tui_tints_its_own_tab_before_it_paints_the_fleet(own_tab):
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "brightness;108" in terminal.written[0]


async def test_tui_hands_its_own_tab_back_when_it_exits(own_tab):
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)

    assert terminal.written[-1] == CLEARED


async def test_tui_leaves_its_own_tab_alone_when_tinting_is_opted_out_of(own_tab):
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)

    assert terminal.written == []


async def test_tui_clears_its_own_tab_when_the_setting_is_turned_off(own_tab):
    terminal = SilentTerminal()
    app = build_app(sessions=[], terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#paint_tabs", Switch).toggle()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert "brightness;108" in terminal.written[0]
        assert terminal.written[1:] == [CLEARED]


async def test_tui_leaves_the_tabs_alone_when_tinting_is_opted_out_of():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert terminal.written == []


async def test_tui_clears_the_tabs_it_tinted_when_the_setting_is_turned_off():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#paint_tabs", Switch).toggle()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert terminal.written[-2:] == ["\033]6;1;bg;*;default\a"] * 2
        assert settings_store.load().paint_tabs is False


async def test_tui_tints_the_tabs_the_moment_the_setting_is_turned_on():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#paint_tabs", Switch).toggle()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert "brightness;220" in terminal.written[0]
        assert settings_store.load().paint_tabs is True


async def test_tui_toggles_closed_sessions():
    def loader(include_closed: bool) -> list[Session]:
        return [*fleet(), closed_session()] if include_closed else fleet()

    app = build_app(loader=loader)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert table_of(app).row_count == 2

        await pilot.press("c")
        await settle(app, pilot)

        assert table_of(app).row_count == 3
        assert r"1 \[c]losed" in title_of(app)

        await pilot.press("c")
        await settle(app, pilot)

        assert table_of(app).row_count == 2


async def test_tui_folds_closed_sessions_in_by_clicking_the_top_bar():
    """The switch the footer no longer advertises a key for."""

    def loader(include_closed: bool) -> list[Session]:
        return [*fleet(), closed_session()] if include_closed else fleet()

    app = build_app(loader=loader)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert r"show \[c]losed" in title_of(app)

        await app.run_action("toggle_closed")
        await settle(app, pilot)

        assert table_of(app).row_count == 3
        assert r"1 \[c]losed" in title_of(app)


async def test_tui_offers_the_closed_switch_on_an_empty_fleet():
    """An empty board is exactly when the ended ones are worth folding in."""
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "no live sessions" in title_of(app)
        assert r"show \[c]losed" in title_of(app)


async def test_tui_starts_with_closed_sessions_when_asked():
    def loader(include_closed: bool) -> list[Session]:
        return [closed_session()] if include_closed else []

    app = build_app(loader=loader, include_closed=True)

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert table_of(app).row_count == 1


async def test_tui_focus_on_an_empty_fleet_signals_nothing():
    terminal = SilentTerminal()
    app = build_app(sessions=[], terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("f")

        assert app.selected_session is None
        assert terminal.written == []


async def test_tui_copies_a_resume_command_for_a_closed_session(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(tui_module, "copy_to_pasteboard", copied.append)
    app = build_app(sessions=[closed_session()])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("y")

        expected = "(cd /tmp/invoice-parser && claude --resume 9a1b2c3d-eeee)"
        assert copied == [expected]
        assert app._clipboard == expected


async def test_tui_copy_resume_on_an_empty_fleet_copies_nothing(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(tui_module, "copy_to_pasteboard", copied.append)
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("y")

        assert copied == []


async def test_tui_terminate_asks_before_signalling_anything(monkeypatch):
    killed: list[str] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: killed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert isinstance(app.screen, tui_module.ConfirmScreen)
        assert killed == []


async def test_tui_terminate_signals_the_session_once_confirmed(monkeypatch):
    killed: list[str] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: killed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert killed == ["payments-api-7c"]


@pytest.mark.parametrize("key", ["escape", "n"])
async def test_tui_terminate_is_dropped_when_the_question_is_declined(monkeypatch, key):
    killed: list[str] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: killed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

        assert killed == []


async def test_tui_terminate_reports_a_refusal(monkeypatch):
    def refuse(session, processes):
        raise LookupError("pid 77730 is gone")

    monkeypatch.setattr(tui_module, "terminate", refuse)
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()

        assert [notification.message for notification in app._notifications] == ["pid 77730 is gone"]


async def test_tui_terminate_on_an_empty_fleet_asks_nothing(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: pytest.fail("must not signal"))
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert not isinstance(app.screen, tui_module.ConfirmScreen)


@pytest.fixture
def retired(monkeypatch) -> list[str]:
    """Worktree removal recorded rather than performed, with one worktree to survey."""
    removed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate()])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    return removed


async def test_tui_remove_worktree_asks_before_removing_anything(retired):
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)

        assert isinstance(app.screen, tui_module.ConfirmScreen)
        assert retired == []


async def test_tui_remove_worktree_removes_it_once_confirmed(retired):
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)
        await pilot.press("y")
        await settle(app, pilot)

        assert retired == [("search-index", False)]


@pytest.mark.parametrize("key", ["escape", "n"])
async def test_tui_remove_worktree_is_dropped_when_the_question_is_declined(retired, key):
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)

        assert retired == []


async def test_tui_remove_worktree_says_what_is_keeping_it_rather_than_asking(monkeypatch):
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate(kept_for="uncommitted changes")])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: pytest.fail("must not remove"))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.ConfirmScreen)
        assert [n.message for n in app._notifications] == ["search-index: uncommitted changes"]


async def test_tui_remove_worktree_on_a_session_outside_one_asks_nothing(monkeypatch):
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: pytest.fail("git must not be asked"))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.ConfirmScreen)
        assert [n.message for n in app._notifications] == ["payments-api-7c is not in a worktree"]


async def test_tui_remove_worktree_on_an_empty_fleet_asks_nothing():
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)

        assert [n.message for n in app._notifications] == ["nothing selected"]


async def test_tui_remove_worktree_reports_a_refusal(monkeypatch):
    def refuse(entry, branch=False):
        raise LookupError("git refused")

    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate()])
    monkeypatch.setattr(tui_module, "remove_worktree", refuse)
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_remove_worktree()
        await settle(app, pilot)
        await pilot.press("y")
        await settle(app, pilot)

        assert [n.message for n in app._notifications] == ["search-index: git refused"]


async def test_tui_cleanup_lists_the_merged_worktrees_and_what_is_being_kept(monkeypatch):
    surveyed = [candidate("done"), candidate("busy", kept_for="uncommitted changes")]
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: surveyed)
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: pytest.fail("must not remove"))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)

        assert isinstance(app.screen, tui_module.CleanupScreen)
        listing = "\n".join(str(widget.content) for widget in app.screen.query(Static))
        assert "Remove 1 merged worktree?" in listing
        assert "done" in listing
        assert "busy · kept · uncommitted changes" in listing


async def test_tui_cleanup_removes_only_what_it_offered(monkeypatch):
    removed: list[str] = []
    surveyed = [candidate("done"), candidate("busy", kept_for="uncommitted changes"), candidate("plain", merged=False)]
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: surveyed)
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)
        await pilot.press("y")
        await settle(app, pilot)

        assert removed == [("done", False)]


async def test_tui_cleanup_leaves_the_branches_unless_asked(monkeypatch):
    removed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate("done")])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)

        assert "branches stay" in str(app.screen.query_one("#branches", Static).content)
        await pilot.press("y")
        await settle(app, pilot)

        assert removed == [("done", False)]


async def test_tui_cleanup_takes_the_branches_when_asked(monkeypatch):
    removed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate("done")])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)
        await pilot.press("b")
        await pilot.pause()

        assert "branches go too" in str(app.screen.query_one("#branches", Static).content)
        await pilot.press("y")
        await settle(app, pilot)

        assert removed == [("done", True)]


async def test_tui_cleanup_branches_can_be_asked_for_and_taken_back(monkeypatch):
    removed: list[tuple[str, bool]] = []
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate("done")])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)
        await pilot.press("b")
        await pilot.press("b")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert removed == [("done", False)]


async def test_tui_cleanup_names_the_branch_each_worktree_is_on(monkeypatch):
    """The branches are what `b` would take, so the answer needs them on the screen."""
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate("done")])
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)

        listing = "\n".join(str(widget.content) for widget in app.screen.query(Static))
        assert "feature/done" in listing


@pytest.mark.parametrize("key", ["escape", "n"])
async def test_tui_cleanup_removes_nothing_when_declined(monkeypatch, key):
    removed: list[str] = []
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate("done")])
    monkeypatch.setattr(tui_module, "remove_worktree", lambda entry, branch=False: removed.append((entry.name, branch)))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)
        await pilot.press(key)
        await settle(app, pilot)

        assert removed == []


async def test_tui_cleanup_with_nothing_merged_asks_nothing(monkeypatch):
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: [candidate(merged=False)])
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)

        assert not isinstance(app.screen, tui_module.CleanupScreen)
        assert [n.message for n in app._notifications] == ["no merged worktrees"]


async def test_tui_cleanup_says_when_every_merged_worktree_is_being_kept(monkeypatch):
    held = [candidate(kept_for="a live session is in it")]
    monkeypatch.setattr(tui_module, "survey", lambda sessions, **kwargs: held)
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.action_cleanup_worktrees()
        await settle(app, pilot)

        assert [n.message for n in app._notifications] == ["1 merged, all kept"]


async def test_the_palette_carries_every_action_the_board_has():
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        offered = {command.title: command.callback for command in app.get_system_commands(app.screen)}

        assert set(offered) >= {
            "Reload the board",
            "Settings",
            "Closed sessions",
            "Focus this session's terminal",
            "Rename this session",
            "Copy this session's resume command",
            "Terminate this session",
            "Retire this session's worktree",
            "Cleanup worktrees",
        }
        assert offered["Settings"] == app.action_settings
        assert offered["Terminate this session"] == app.action_terminate
        assert offered["Retire this session's worktree"] == app.action_remove_worktree
        assert offered["Cleanup worktrees"] == app.action_cleanup_worktrees


async def test_the_palette_leaves_out_terminating_a_session_with_no_process():
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        offered = {command.title for command in app.get_system_commands(app.screen)}

        assert "Terminate this session" not in offered
        assert "Copy this session's resume command" in offered


async def test_terminate_leaves_the_footer_when_the_process_is_gone():
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "terminate" not in footer_keys(app)
        assert "copy_resume" in footer_keys(app)

        await pilot.press("down")

        assert "terminate" in footer_keys(app)


async def test_terminate_signals_nothing_when_the_process_is_gone(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: pytest.fail("must not signal"))
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert not isinstance(app.screen, tui_module.ConfirmScreen)


async def test_every_command_the_palette_offers_explains_itself():
    """A palette is read by somebody who does not already know what the entry does."""
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)

        for command in app.get_system_commands(app.screen):
            assert command.help
            assert command.help.lower() != command.title.lower()


async def test_the_worktree_commands_are_on_no_key():
    app = build_app(sessions=worktree_fleet())

    assert not [binding for binding in app.BINDINGS if "worktree" in str(binding.action)]


async def test_a_worktree_command_run_from_the_palette_still_knows_the_selected_session(retired):
    """The palette is a screen of its own, and the row the cursor was on has to survive it."""
    app = build_app(sessions=worktree_fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("ctrl+p")
        await pilot.pause()
        app.screen.query_one(Input).value = "Retire this session"
        await pilot.pause(0.5)
        await pilot.press("enter")
        await settle(app, pilot)

        assert isinstance(app.screen, tui_module.ConfirmScreen)
        await pilot.press("y")
        await settle(app, pilot)

        assert retired == [("search-index", False)]


async def test_tui_worktree_column_appears_when_the_setting_is_on():
    app = build_app(sessions=worktree_fleet(), settings=Settings(show_worktree=True))

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "WORKTREE" in headers_of(app)
        assert "search-index" in str(table_of(app).get_row_at(0))


SHELL = Process(pid=55997, ppid=55996, tty=Path("/dev/ttys004"), command="-zsh")


async def test_tui_terminate_leaves_the_tab_alone_by_default(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: pytest.fail("must not wait"))
    hung_up: list[int] = []
    monkeypatch.setattr(tui_module, "close_tab", lambda shell: hung_up.append(shell.pid))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert hung_up == []


async def test_tui_terminate_hangs_up_the_shell_once_the_session_has_gone(monkeypatch):
    waited: list[int] = []
    hung_up: list[int] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "shell_of", lambda session, processes: SHELL)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: waited.append(pid) or True)
    monkeypatch.setattr(tui_module, "close_tab", lambda shell: hung_up.append(shell.pid))
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert waited == [77730]
        assert hung_up == [55997]


async def test_tui_traces_the_shell_before_the_session_is_signalled(monkeypatch, process_snapshot):
    traced: list[dict] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "shell_of", lambda session, processes: traced.append(processes) or SHELL)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: True)
    monkeypatch.setattr(tui_module, "close_tab", lambda shell: None)
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert traced == [process_snapshot]


async def test_tui_keeps_the_tab_of_a_session_that_outlasts_the_wait(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "shell_of", lambda session, processes: SHELL)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: False)
    monkeypatch.setattr(tui_module, "close_tab", lambda shell: pytest.fail("must not close"))
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert [notification.title for notification in app._notifications][-1] == "tab left open"


async def test_tui_keeps_the_tab_of_a_session_with_no_shell_of_its_own(monkeypatch):
    def untraceable(session, processes):
        raise LookupError("payments-api-7c has no tab of its own to close")

    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "shell_of", untraceable)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: pytest.fail("must not wait"))
    monkeypatch.setattr(tui_module, "close_tab", lambda shell: pytest.fail("must not close"))
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert [notification.title for notification in app._notifications][-1] == "tab left open"
        assert "no tab of its own" in [notification.message for notification in app._notifications][-1]


async def test_tui_reports_a_shell_that_would_not_hang_up(monkeypatch):
    def refuse(shell):
        raise LookupError("the shell on /dev/ttys004 is gone")

    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: None)
    monkeypatch.setattr(tui_module, "shell_of", lambda session, processes: SHELL)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: True)
    monkeypatch.setattr(tui_module, "close_tab", refuse)
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert "the shell on /dev/ttys004 is gone" in [note.message for note in app._notifications][-1]


async def test_tui_terminate_says_the_tab_will_close_before_asking(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session, processes: pytest.fail("must not signal"))
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert "terminal tab closes" in str(app.screen.query_one(Static).content)


async def test_tui_rename_offers_the_current_name_for_editing():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()

        assert isinstance(app.screen, tui_module.PromptScreen)
        assert app.screen.query_one("#answer", Input).value == "payments-api-7c"


async def test_tui_rename_asks_the_session_once_submitted(monkeypatch):
    renamed: list[tuple[str, str]] = []
    monkeypatch.setattr(tui_module, "rename", lambda session, name: renamed.append((session.label, name)))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#answer", Input).value = "invoice-parser"
        await pilot.press("enter")
        await pilot.pause()

        assert renamed == [("payments-api-7c", "invoice-parser")]


async def test_tui_rename_is_dropped_when_the_prompt_is_abandoned(monkeypatch):
    monkeypatch.setattr(tui_module, "rename", lambda session, name: pytest.fail("must not rename"))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#answer", Input).value = "invoice-parser"
        await pilot.press("escape")
        await pilot.pause()

        assert not isinstance(app.screen, tui_module.PromptScreen)


@pytest.mark.parametrize("answer", ["payments-api-7c", "", "   "])
async def test_tui_rename_sends_nothing_when_the_name_would_not_change(monkeypatch, answer):
    monkeypatch.setattr(tui_module, "rename", lambda session, name: pytest.fail("must not rename"))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#answer", Input).value = answer
        await pilot.press("enter")
        await pilot.pause()


async def test_tui_rename_typing_does_not_filter_the_fleet():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#answer", Input).value = "invoice"
        await pilot.pause()

        assert table_of(app).row_count == 2


async def test_tui_rename_reports_a_refusal(monkeypatch):
    def refuse(session, name):
        raise LookupError("payments-api-7c is not listening for control messages")

    monkeypatch.setattr(tui_module, "rename", refuse)
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        app.screen.query_one("#answer", Input).value = "invoice-parser"
        await pilot.press("enter")
        await pilot.pause()

        messages = [notification.message for notification in app._notifications]
        assert messages == ["payments-api-7c is not listening for control messages"]


async def test_tui_rename_on_an_empty_fleet_asks_nothing(monkeypatch):
    monkeypatch.setattr(tui_module, "rename", lambda session, name: pytest.fail("must not rename"))
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("r")
        await pilot.pause()

        assert not isinstance(app.screen, tui_module.PromptScreen)


async def test_tui_settings_screen_edits_and_saves(monkeypatch):
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#show_pid", Switch).toggle()
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert "PID" in headers_of(app)
        assert settings_store.load().show_pid is True


async def test_tui_settings_typing_does_not_filter_the_fleet():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#interval", Input).value = "30"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert "2 sessions" in title_of(app)
        assert table_of(app).row_count == 2


async def test_tui_settings_screen_rejects_a_half_typed_interval():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#interval", Input).value = "not a number"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app._settings.interval == Settings().interval


async def test_tui_settings_screen_changes_the_refresh_interval():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("comma")
        await pilot.pause()

        app.screen.query_one("#interval", Input).value = "30"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

        assert app._settings.interval == 30
        assert "30s" in str(app.query_one("#tick", Static).content)
        assert settings_store.load().interval == 30


async def test_tui_surfaces_a_failing_loader():
    def explode(include_closed: bool) -> list[Session]:
        raise RuntimeError("claude: command not found")

    app = build_app(loader=explode)

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "discovery failed" in title_of(app)
        assert "claude: command not found" in title_of(app)


async def test_tui_refresh_picks_up_new_sessions():
    sessions: list[Session] = []
    app = build_app(loader=lambda include_closed: list(sessions))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert table_of(app).row_count == 0

        sessions.extend(fleet())
        await pilot.press("ctrl+r")
        await settle(app, pilot)

        assert table_of(app).row_count == 2


async def test_tui_refresh_keeps_the_cursor_on_the_same_session():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        selected = app.selected_session

        await pilot.press("ctrl+r")
        await settle(app, pilot)

        assert selected is not None
        assert app.selected_session is not None
        assert app.selected_session.session_id == selected.session_id


async def test_tui_quits_on_q():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("q")
        await pilot.pause()

    assert not app.is_running


@pytest.fixture
def browser(monkeypatch):
    """Whatever the board asked to be opened, instead of a browser opening it."""
    opened: list[str] = []
    monkeypatch.setattr(tui_module, "open_url", lambda url: bool(opened.append(url)) or True)
    return opened


def pulls_table(app: FleetApp) -> DataTable:
    """The pull request table, asked of the screen on top rather than the board beneath it."""
    return app.screen.query_one("#pulls", DataTable)


def pulls_bar(app: FleetApp) -> str:
    return str(app.screen.query_one("#pulls-bar", Static).content)


def pull_details(app: FleetApp) -> str:
    return render_markup(str(app.screen.query_one("#pull-details", Static).content)).plain


async def test_tui_p_opens_the_pull_requests_github_says_are_open(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert pulls_table(app).row_count == 1
        assert "acme/widgets#42" in str(pulls_table(app).get_row_at(0))
        assert "1 open" in pulls_bar(app)


async def test_tui_pull_requests_count_the_sessions_that_worked_on_each(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert "1" in str(pulls_table(app).get_row_at(0))
        assert "1 with sessions here" in pulls_bar(app)
        assert "payments-api-7c" in pull_details(app)


async def test_tui_pull_requests_read_the_ended_sessions_whatever_the_board_shows(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    asked: list[bool] = []
    app = build_app(loader=lambda include_closed: asked.append(include_closed) or fleet())

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert True in asked


async def test_tui_enter_on_a_pull_request_points_the_board_at_its_sessions(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    transcript(tmp_path, "cef6830d-aaaa", "nothing to do with it", cwd="/tmp/web-platform")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)
        await settle(app, pilot)

        assert app.query_one("#filter", Input).value == "https://github.com/acme/widgets/pull/42"
        assert table_of(app).row_count == 1
        assert "payments-api-7c" in str(table_of(app).get_row_at(0))


async def test_tui_arriving_from_the_pull_requests_folds_the_ended_sessions_in(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("enter")
        await settle(app, pilot)

        assert app._show_closed


async def test_tui_escape_leaves_the_pull_requests_without_filtering_anything(monkeypatch, tmp_path, github):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)

        assert app.query_one("#filter", Input).value == ""
        assert table_of(app).row_count == 2


async def test_tui_pull_requests_say_github_could_not_be_asked(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    def refuse(author, limit):
        raise tui_module.Unavailable("gh: not logged in")

    monkeypatch.setattr(tui_module.pulls, "mine", refuse)
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert "github could not be asked" in pulls_bar(app)
        assert "not logged in" in pulls_bar(app)


async def test_tui_pull_requests_tell_an_empty_list_from_a_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(tui_module.pulls, "mine", lambda author, limit: [])
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert "no open pull requests" in pulls_bar(app)


async def test_tui_o_on_a_pull_request_opens_it_in_a_browser(monkeypatch, tmp_path, github, browser):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("o")

        assert browser == ["https://github.com/acme/widgets/pull/42"]


async def test_tui_details_name_the_pull_requests_the_session_worked_on(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert "acme/widgets#42" in details_of(app)


async def test_tui_details_say_nothing_about_pull_requests_for_a_session_that_named_none(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "just talking")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert "prs" not in details_of(app)


async def test_tui_o_opens_what_the_selected_session_was_working_on(monkeypatch, tmp_path, browser):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)

        assert browser == ["https://github.com/acme/widgets/pull/42"]


async def test_tui_o_offers_the_choice_when_a_session_named_several(monkeypatch, tmp_path, browser):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(
        tmp_path,
        "4e020900-df7c",
        "https://github.com/acme/widgets/pull/42 then https://github.com/acme/gadgets/pull/9",
    )
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)

        assert isinstance(app.screen, PullChoiceScreen)

        await pilot.press("enter")
        await settle(app, pilot)

        assert browser == ["https://github.com/acme/gadgets/pull/9"]


async def test_tui_o_says_so_when_the_transcript_names_no_pull_request(monkeypatch, tmp_path, browser):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "just talking")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)

        assert browser == []
        assert "names no pull request" in notified(app)


async def test_tui_o_reports_a_browser_that_would_not_open(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(tui_module, "open_url", lambda url: False)
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        await pilot.press("o")
        await settle(app, pilot)

        assert any("could not open a browser" in str(note.title) for note in app._notifications)


async def test_tui_refresh_reads_the_transcripts_for_pull_requests_again(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "just talking")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)
        assert "prs" not in details_of(app)

        transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
        await pilot.press("ctrl+r")
        await settle(app, pilot)
        await settle(app, pilot)

        assert "acme/widgets#42" in details_of(app)


async def test_tui_pull_requests_keep_the_top_of_the_board_under_the_cursor(monkeypatch, tmp_path, a_pull):
    """The order changes as the checks come back, and the cursor has not been moved yet."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    stale, fresh = a_pull(1), a_pull(2)
    monkeypatch.setattr(tui_module.pulls, "mine", lambda author, limit: [fresh, stale])
    monkeypatch.setattr(
        tui_module.pulls,
        "stream_statuses",
        lambda listed: [(stale, PullStatus(failing=("test",)))],
    )
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)

        assert "acme/widgets#1" in str(pulls_table(app).get_row_at(0))
        assert "acme/widgets#1" in pull_details(app)


async def test_tui_pull_requests_follow_the_row_its_reader_moved_to(monkeypatch, tmp_path, a_pull):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    listing = [a_pull(1), a_pull(2), a_pull(3)]
    monkeypatch.setattr(tui_module.pulls, "mine", lambda author, limit: listing)
    monkeypatch.setattr(tui_module.pulls, "stream_statuses", lambda listed: [])
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("p")
        await settle(app, pilot)
        await pilot.press("down", "down")
        await settle(app, pilot)
        chosen = app.screen.selected

        app.screen._draw()
        await pilot.pause()

        assert app.screen.selected == chosen


async def test_tui_shows_the_prs_column_when_it_is_switched_on(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")
    app = build_app(settings=Settings(show_prs=True))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert headers_of(app) == ["STATUS", "NAME", "QUIET", "AGE", "PRS", "WHERE"]
        assert "widgets#42" in str(table_of(app).get_row_at(0))


async def test_tui_prs_column_reads_every_visible_row_not_just_the_selected_one(monkeypatch, tmp_path):
    """The detail pane wants one session; a column wants them all, in one pass."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42")
    transcript(tmp_path, "cef6830d-aaaa", "https://github.com/acme/gadgets/pull/9", cwd="/tmp/web-platform")
    app = build_app(settings=Settings(show_prs=True))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert "widgets#42" in str(table_of(app).get_row_at(0))
        assert "gadgets#9" in str(table_of(app).get_row_at(1))


async def test_tui_prs_column_says_a_session_named_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "just talking")
    app = build_app(settings=Settings(show_prs=True))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert "-" in str(table_of(app).get_row_at(0))


async def test_tui_leaves_the_transcripts_alone_when_the_column_is_off(monkeypatch, tmp_path):
    """Only the row under the cursor is read, which is what keeps the column optional."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42")
    transcript(tmp_path, "cef6830d-aaaa", "https://github.com/acme/gadgets/pull/9", cwd="/tmp/web-platform")
    app = build_app(settings=Settings(show_prs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await settle(app, pilot)

        assert set(app._session_pulls) == {"4e020900-df7c"}


async def test_tui_switching_the_prs_column_on_rebuilds_the_table(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42")
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert "PRS" not in headers_of(app)

        app._settings_changed(Settings(show_prs=True))
        await settle(app, pilot)
        await settle(app, pilot)

        assert "PRS" in headers_of(app)
        assert "widgets#42" in str(table_of(app).get_row_at(0))
