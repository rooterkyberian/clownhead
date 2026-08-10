from pathlib import Path

from clownhead import attention
from clownhead.models import Session, Status
from clownhead.terminal import ITerm2Terminal, Rgb

TTY = Path("/dev/ttys004")


class RecordingTerminal(ITerm2Terminal):
    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def write(self, tty: Path, sequence: str) -> None:
        if self.fail:
            raise OSError("device not configured")
        self.calls.append((str(tty), sequence))


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


def test_ping_requests_attention_and_notifies():
    terminal = RecordingTerminal()

    result = attention.ping(session(Status.WAITING), terminal)

    assert result.delivered
    assert terminal.calls[0][1] == "\033]1337;RequestAttention=yes\a"
    assert terminal.calls[1][1].startswith("\033]9;")


def test_ping_uses_custom_message():
    terminal = RecordingTerminal()

    attention.ping(session(Status.WAITING), terminal, "wake up")

    assert terminal.calls[1][1] == "\033]9;wake up\a"


def test_ping_without_tty_is_reported():
    result = attention.ping(session(Status.WAITING, tty=None), RecordingTerminal())

    assert result.delivered is False


def test_ping_stalled_only_targets_attention_states():
    terminal = RecordingTerminal()
    sessions = [session(Status.WAITING), session(Status.IDLE, name="two"), session(Status.BLOCKED, name="three")]

    results = attention.ping_stalled(sessions, terminal)

    assert [result.label for result in results] == ["one", "three"]
