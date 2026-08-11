from pathlib import Path

import pytest
from rich.markup import render as render_markup
from textual.widgets import DataTable, Input, Static, Switch

from clownhead import settings as settings_store
from clownhead import tui as tui_module
from clownhead.attention import SignalResult
from clownhead.discovery import Message
from clownhead.models import Session, Status
from clownhead.settings import Settings
from clownhead.terminal import ITerm2Terminal
from clownhead.tui import FleetApp, config_dir_notice, matches


@pytest.fixture(autouse=True)
def silent_pasteboard(monkeypatch):
    monkeypatch.setattr(tui_module, "copy_to_pasteboard", lambda text: True)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path / "state"))


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


class SilentTerminal(ITerm2Terminal):
    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def build_app(sessions=None, loader=None, terminal=None, include_closed=False, settings=None) -> FleetApp:
    return FleetApp(
        loader=loader or (lambda include_closed: fleet() if sessions is None else sessions),
        interval=3600,
        terminal=terminal or SilentTerminal(),
        include_closed=include_closed,
        settings=settings or Settings(),
    )


async def settle(app: FleetApp, pilot) -> None:
    await app.workers.wait_for_complete()
    await pilot.pause()


def table_of(app: FleetApp) -> DataTable:
    return app.query_one("#fleet", DataTable)


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
        assert "1 waiting on you" in title_of(app)


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
        assert "1 waiting on you" in title_of(app)


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

        assert title_of(app) == "2 sessions · [bold red]1 waiting on you[/]"


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
        assert "claude --resume 4e020900-df7c" in details_of(app)


def history_of(app: FleetApp) -> str:
    return render_markup(str(app.query_one("#history-body", Static).content)).plain


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


async def test_tui_enter_leaves_the_session_alone():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal, settings=Settings(paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("enter")

        assert terminal.written == []
        assert not app.query_one("#history").display


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


async def test_tui_tints_every_tab_as_it_reloads():
    terminal = SilentTerminal()
    app = build_app(terminal=terminal)

    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "brightness;220" in terminal.written[0]
        assert terminal.written[1] == "\033]6;1;bg;*;default\a"


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
        assert "1 closed" in title_of(app)

        await pilot.press("c")
        await settle(app, pilot)

        assert table_of(app).row_count == 2


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
    monkeypatch.setattr(tui_module, "terminate", lambda session: killed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert isinstance(app.screen, tui_module.ConfirmScreen)
        assert killed == []


async def test_tui_terminate_signals_the_session_once_confirmed(monkeypatch):
    killed: list[str] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session: killed.append(session.label))
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
    monkeypatch.setattr(tui_module, "terminate", lambda session: killed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

        assert killed == []


async def test_tui_terminate_reports_a_refusal(monkeypatch):
    def refuse(session):
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
    monkeypatch.setattr(tui_module, "terminate", lambda session: pytest.fail("must not signal"))
    app = build_app(sessions=[])

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()

        assert not isinstance(app.screen, tui_module.ConfirmScreen)


async def test_tui_terminate_leaves_the_tab_alone_by_default(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session: None)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: pytest.fail("must not wait"))
    closed: list[str] = []
    monkeypatch.setattr(tui_module.attention, "close_tab", lambda session, terminal: closed.append(session.label))
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert closed == []


async def test_tui_terminate_closes_the_tab_once_the_session_has_gone(monkeypatch):
    waited: list[int] = []
    closed: list[str] = []
    monkeypatch.setattr(tui_module, "terminate", lambda session: None)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: waited.append(pid) or True)
    monkeypatch.setattr(
        tui_module.attention,
        "close_tab",
        lambda session, terminal: closed.append(session.label) or SignalResult(session.label, None, True, "tab closed"),
    )
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert waited == [77730]
        assert closed == ["payments-api-7c"]


async def test_tui_keeps_the_tab_of_a_session_that_outlasts_the_wait(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session: None)
    monkeypatch.setattr(tui_module, "wait_for_exit", lambda pid: False)
    monkeypatch.setattr(tui_module.attention, "close_tab", lambda session, terminal: pytest.fail("must not close"))
    app = build_app(settings=Settings(close_tab_on_terminate=True, paint_tabs=False))

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("y")
        await settle(app, pilot)

        assert [notification.title for notification in app._notifications][-1] == "tab left open"


async def test_tui_terminate_says_the_tab_will_close_before_asking(monkeypatch):
    monkeypatch.setattr(tui_module, "terminate", lambda session: pytest.fail("must not signal"))
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
        await pilot.press("R")
        await settle(app, pilot)

        assert table_of(app).row_count == 2


async def test_tui_refresh_keeps_the_cursor_on_the_same_session():
    app = build_app()

    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("down")
        selected = app.selected_session

        await pilot.press("R")
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
