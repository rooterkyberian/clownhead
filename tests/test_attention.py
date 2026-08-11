import subprocess
from pathlib import Path

import pytest

from clownhead import attention
from clownhead.models import Session, Status
from clownhead.terminal import ITerm2Terminal, Rgb, Terminal

TTY = Path("/dev/ttys004")
OWN_TTY = Path("/dev/ttys009")


class RecordingTerminal(ITerm2Terminal):
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def write(self, tty: Path, sequence: str) -> None:
        if self.fail:
            raise OSError("device not configured")
        self.calls.append((str(tty), sequence))


class PlainTerminal(Terminal):
    """An emulator with no attention escape code of its own, as an IDE terminal is."""

    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def session(status: Status, tty: Path | None = TTY, name: str = "one") -> Session:
    return Session(session_id="a-b", cwd=Path("/tmp/repo"), status=status, name=name, tty=tty)


def test_color_for_maps_attention_states():
    assert attention.color_for(Status.WAITING) == Rgb(220, 50, 47)
    assert attention.color_for(Status.BUSY) == Rgb(38, 139, 210)
    assert attention.color_for(Status.IDLE) is None


def test_paint_colours_waiting_and_resets_idle():
    terminal = RecordingTerminal()

    results = attention.paint([session(Status.WAITING), session(Status.IDLE, name="two")], terminal)

    assert [result.delivered for result in results] == [True, True]
    assert "brightness;220" in terminal.calls[0][1]
    assert terminal.calls[1][1] == "\033]6;1;bg;*;default\a"


def test_paint_skips_sessions_without_a_tty():
    terminal = RecordingTerminal()

    results = attention.paint([session(Status.WAITING, tty=None)], terminal)

    assert results[0].delivered is False
    assert results[0].detail == "no tty"
    assert terminal.calls == []


def test_paint_leaves_terminals_without_tab_colours_alone():
    terminal = PlainTerminal()

    results = attention.paint([session(Status.WAITING)], terminal)

    assert results[0].delivered is False
    assert "no tab colours" in results[0].detail
    assert terminal.written == []


def test_paint_reports_dead_ttys_without_raising():
    results = attention.paint([session(Status.WAITING)], RecordingTerminal(fail=True))

    assert results[0].delivered is False
    assert "device not configured" in results[0].detail


def test_paint_continues_after_a_failure():
    class FlakyTerminal(RecordingTerminal):
        def write(self, tty: Path, sequence: str) -> None:
            if str(tty).endswith("bad"):
                raise OSError("gone")
            self.calls.append((str(tty), sequence))

    terminal = FlakyTerminal()
    sessions = [session(Status.WAITING, tty=Path("/dev/bad")), session(Status.BUSY, name="two")]

    results = attention.paint(sessions, terminal)

    assert [result.delivered for result in results] == [False, True]


@pytest.fixture
def own_tty(monkeypatch):
    monkeypatch.setattr(attention, "own_tty", lambda: OWN_TTY)
    return OWN_TTY


def test_paint_self_tints_the_boards_own_tab(own_tty):
    terminal = RecordingTerminal()

    result = attention.paint_self(terminal)

    assert result.delivered is True
    assert result.tty == own_tty
    assert terminal.calls[0][0] == str(own_tty)
    assert "brightness;108" in terminal.calls[0][1]


def test_paint_self_wears_a_colour_no_status_wears():
    assert attention.OVERSEER_COLOR not in attention.STATUS_COLORS.values()


def test_paint_self_without_a_terminal_of_its_own(monkeypatch):
    monkeypatch.setattr(attention, "own_tty", lambda: None)

    result = attention.paint_self(RecordingTerminal())

    assert result.delivered is False
    assert result.detail == "no tty"


def test_paint_self_leaves_a_terminal_without_tab_colours_alone(own_tty):
    terminal = PlainTerminal()

    result = attention.paint_self(terminal)

    assert result.delivered is False
    assert "no tab colours" in result.detail
    assert terminal.written == []


def test_paint_self_reports_a_dead_tty_without_raising(own_tty):
    result = attention.paint_self(RecordingTerminal(fail=True))

    assert result.delivered is False
    assert "device not configured" in result.detail


def test_paint_self_asks_the_terminal_clownhead_runs_in(monkeypatch, own_tty):
    detected = RecordingTerminal()
    monkeypatch.setattr(attention, "detect_terminal", lambda: detected)
    monkeypatch.setattr(attention, "terminal_for", lambda app: pytest.fail("no session owns this tab"))

    assert attention.paint_self().delivered is True
    assert detected.calls


def test_reset_self_hands_the_tab_back(own_tty):
    terminal = RecordingTerminal()

    result = attention.reset_self(terminal)

    assert result.delivered is True
    assert terminal.calls == [(str(own_tty), "\033]6;1;bg;*;default\a")]


def test_reset_clears_every_tab():
    terminal = RecordingTerminal()

    results = attention.reset([session(Status.WAITING), session(Status.BUSY, name="two")], terminal)

    assert all(result.delivered for result in results)
    assert terminal.calls == [(str(TTY), "\033]6;1;bg;*;default\a")] * 2


def test_reset_skips_sessions_without_a_tty():
    results = attention.reset([session(Status.BUSY, tty=None)], RecordingTerminal())

    assert results[0].delivered is False


def test_reset_reports_dead_ttys():
    results = attention.reset([session(Status.BUSY)], RecordingTerminal(fail=True))

    assert results[0].delivered is False


def test_focus_requests_attention_foregrounds_and_notifies():
    terminal = RecordingTerminal()

    result = attention.focus(session(Status.WAITING), terminal)

    assert result.delivered
    assert [call[1] for call in terminal.calls] == [
        "\033]1337;RequestAttention=yes\a",
        "\033]1337;StealFocus\a",
        "\033]9;one: waiting\a",
    ]


def test_focus_marks_the_tab_of_a_terminal_that_cannot_flash():
    terminal = PlainTerminal()

    attention.focus(session(Status.WAITING), terminal)

    assert terminal.written == ["\a", "\033]0;⚠ one: waiting\a"]


def test_focus_puts_a_custom_message_in_the_tab_title():
    terminal = PlainTerminal()

    attention.focus(session(Status.WAITING), terminal, "deploy finished")

    assert "\033]0;⚠ deploy finished\a" in terminal.written


def test_focus_can_skip_the_foreground_switch():
    terminal = RecordingTerminal()

    attention.focus(session(Status.WAITING), terminal, foreground=False)

    assert "\033]1337;StealFocus\a" not in [call[1] for call in terminal.calls]


def test_focus_uses_custom_message():
    terminal = RecordingTerminal()

    attention.focus(session(Status.WAITING), terminal, "wake up")

    assert terminal.calls[-1][1] == "\033]9;wake up\a"


def test_focus_reports_a_failed_foreground_switch():
    class UnraisableTerminal(RecordingTerminal):
        def foreground(self, tty: Path) -> None:
            raise subprocess.TimeoutExpired(cmd="open", timeout=5.0)

    result = attention.focus(session(Status.WAITING), UnraisableTerminal())

    assert result.delivered is False
    assert "open" in result.detail


def test_focus_signals_through_the_application_that_owns_the_session(monkeypatch):
    owned = RecordingTerminal()
    asked: list[Path | None] = []
    monkeypatch.setattr(attention, "terminal_for", lambda app: asked.append(app) or owned)
    pycharm = session(Status.WAITING).model_copy(update={"app": Path("/Applications/PyCharm.app")})

    result = attention.focus(pycharm)

    assert asked == [Path("/Applications/PyCharm.app")]
    assert result.delivered
    assert owned.calls


def test_paint_signals_each_session_through_its_own_terminal(monkeypatch):
    asked: list[Path | None] = []
    monkeypatch.setattr(attention, "terminal_for", lambda app: asked.append(app) or RecordingTerminal())
    sessions = [
        session(Status.WAITING).model_copy(update={"app": Path("/Applications/iTerm.app")}),
        session(Status.BUSY, name="two").model_copy(update={"app": Path("/Applications/PyCharm.app")}),
    ]

    attention.paint(sessions)

    assert asked == [Path("/Applications/iTerm.app"), Path("/Applications/PyCharm.app")]


def test_an_explicit_terminal_overrides_the_owning_application(monkeypatch):
    monkeypatch.setattr(attention, "terminal_for", lambda app: pytest.fail("must not resolve"))
    terminal = RecordingTerminal()

    attention.focus(session(Status.WAITING), terminal)

    assert terminal.calls


def test_focus_without_tty_is_reported():
    result = attention.focus(session(Status.WAITING, tty=None), RecordingTerminal())

    assert result.delivered is False


def test_focus_stalled_only_targets_attention_states():
    terminal = RecordingTerminal()
    sessions = [session(Status.WAITING), session(Status.IDLE, name="two"), session(Status.BLOCKED, name="three")]

    results = attention.focus_stalled(sessions, terminal)

    assert [result.label for result in results] == ["one", "three"]
