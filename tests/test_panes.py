import subprocess
from pathlib import Path

import pytest

from clownhead import panes
from clownhead.models import Session, Status
from clownhead.resume import Launch
from clownhead.settings import ResumeIn
from clownhead.terminal import ITerm2Terminal, Terminal


def session(tty: Path | None = Path("/dev/ttys004")) -> Session:
    return Session(session_id="a-b", cwd=Path("/tmp/repo"), name="payments-api-7c", pid=77730, tty=tty)


class Ran:
    """A stand-in for ``subprocess.run`` that records its calls and answers to script."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, self.returncode, stdout=self.stdout, stderr="denied")


PANES = "/dev/ttys001\t%3\n/dev/ttys004\t%7\n/dev/ttys009\t%11\n"


def test_tmux_pane_finds_the_pane_on_a_tty(monkeypatch):
    monkeypatch.setattr(panes.subprocess, "run", Ran(stdout=PANES))

    assert panes.tmux_pane(Path("/dev/ttys004")) == "%7"


def test_tmux_pane_lists_every_pane_on_the_machine(monkeypatch):
    ran = Ran(stdout=PANES)
    monkeypatch.setattr(panes.subprocess, "run", ran)

    panes.tmux_pane(Path("/dev/ttys004"))

    assert ran.calls == [["tmux", "list-panes", "-a", "-F", panes.PANE_FORMAT]]


def test_tmux_pane_is_none_for_a_tty_tmux_does_not_own(monkeypatch):
    monkeypatch.setattr(panes.subprocess, "run", Ran(stdout=PANES))

    assert panes.tmux_pane(Path("/dev/ttys017")) is None


def test_tmux_pane_is_none_when_no_server_is_running(monkeypatch):
    monkeypatch.setattr(panes.subprocess, "run", Ran(stdout="", returncode=1))

    assert panes.tmux_pane(Path("/dev/ttys004")) is None


def test_tmux_pane_is_none_when_tmux_is_not_installed(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError("no tmux")

    monkeypatch.setattr(panes.subprocess, "run", missing)

    assert panes.tmux_pane(Path("/dev/ttys004")) is None


def test_send_keys_types_the_line_then_presses_return(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(panes.subprocess, "run", ran)

    panes.send_keys("%7", "/compact")

    assert ran.calls == [
        ["tmux", "send-keys", "-t", "%7", "-l", "--", "/compact"],
        ["tmux", "send-keys", "-t", "%7", "Enter"],
    ]


def test_send_keys_raises_what_tmux_said(monkeypatch):
    monkeypatch.setattr(panes.subprocess, "run", Ran(returncode=1))

    with pytest.raises(OSError, match="denied"):
        panes.send_keys("%7", "/compact")


def test_type_into_prefers_tmux(monkeypatch):
    typed: list[tuple[str, str]] = []
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: "%7")
    monkeypatch.setattr(panes, "send_keys", lambda pane, line: typed.append((pane, line)))

    assert panes.type_into(session(), "/compact", ITerm2Terminal()) == "tmux"
    assert typed == [("%7", "/compact")]


def test_type_into_falls_back_to_the_emulator(monkeypatch):
    typed: list[tuple[Path, str]] = []
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: None)
    terminal = ITerm2Terminal()
    monkeypatch.setattr(terminal, "type_text", lambda tty, text: bool(typed.append((tty, text))) or True)

    assert panes.type_into(session(), "/compact", terminal) == "iterm2"
    assert typed == [(Path("/dev/ttys004"), "/compact")]


def test_type_into_trims_the_line(monkeypatch):
    typed: list[tuple[str, str]] = []
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: "%7")
    monkeypatch.setattr(panes, "send_keys", lambda pane, line: typed.append((pane, line)))

    panes.type_into(session(), "  /compact  ", ITerm2Terminal())

    assert typed == [("%7", "/compact")]


@pytest.mark.parametrize("text", ["", "   "])
def test_type_into_refuses_a_blank_line(monkeypatch, text):
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: pytest.fail("must not type"))

    with pytest.raises(ValueError, match="blank"):
        panes.type_into(session(), text, ITerm2Terminal())


def test_type_into_refuses_a_session_that_has_ended(monkeypatch):
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: pytest.fail("must not type"))
    closed = session().model_copy(update={"status": Status.CLOSED})

    with pytest.raises(LookupError, match="has ended"):
        panes.type_into(closed, "/compact", ITerm2Terminal())


def test_type_into_refuses_a_session_with_no_tty(monkeypatch):
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: pytest.fail("must not type"))

    with pytest.raises(LookupError, match="no terminal"):
        panes.type_into(session(tty=None), "/compact", ITerm2Terminal())


def test_type_into_names_the_terminal_that_cannot_be_typed_into(monkeypatch):
    monkeypatch.setattr(panes, "tmux_pane", lambda tty: None)

    with pytest.raises(LookupError, match="generic, which nothing can type into"):
        panes.type_into(session(), "/compact", Terminal())


def launch(env: tuple[tuple[str, str], ...] = ()) -> Launch:
    return Launch(Path("/tmp/payments-api"), ("claude", "--resume", "4e02"), env)


def test_open_window_makes_a_window_inside_tmux(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(panes.subprocess, "run", ran)

    assert panes.open_window(launch(), "payments-api-7c", {"TMUX": "/tmp/tmux-501/default,1,0"}) == (
        "window payments-api-7c"
    )
    assert ran.calls == [
        ["tmux", "new-window", "-n", "payments-api-7c", "-c", "/tmp/payments-api", "--", "claude", "--resume", "4e02"],
    ]


def test_open_window_makes_a_detached_session_outside_tmux(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(panes.subprocess, "run", ran)

    assert panes.open_window(launch(), "payments-api-7c", {}) == "session payments-api-7c, waiting to be attached"
    assert ran.calls[0][:4] == ["tmux", "new-session", "-d", "-s"]


def test_open_window_says_the_environment_out_loud(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(panes.subprocess, "run", ran)

    panes.open_window(launch((("CLAUDE_CONFIG_DIR", "/home/me/.claude-work"),)), "payments-api-7c", {"TMUX": "x"})

    assert "-e" in ran.calls[0]
    assert "CLAUDE_CONFIG_DIR=/home/me/.claude-work" in ran.calls[0]


def test_open_window_makes_a_name_tmux_will_take(monkeypatch):
    ran = Ran()
    monkeypatch.setattr(panes.subprocess, "run", ran)

    panes.open_window(launch(), "web.platform:1d 2", {"TMUX": "x"})

    assert ran.calls[0][3] == "web-platform-1d-2"


def test_open_session_puts_it_in_tmux(monkeypatch):
    monkeypatch.setattr(panes, "open_window", lambda plan, name, *args: "window payments-api-7c")

    assert panes.open_session(launch(), ResumeIn.TMUX, "payments-api-7c") == "in tmux window payments-api-7c"


def test_open_session_opens_an_iterm2_tab(monkeypatch):
    opened: list[str] = []
    terminal = ITerm2Terminal()
    monkeypatch.setattr(terminal, "open_tab", lambda command: bool(opened.append(command)) or True)

    assert panes.open_session(launch(), ResumeIn.ITERM2, "payments-api-7c", terminal) == "in a new iterm2 tab"
    assert opened == ["(cd /tmp/payments-api && claude --resume 4e02)"]


def test_open_session_reports_an_iterm2_that_would_not(monkeypatch):
    terminal = ITerm2Terminal()
    monkeypatch.setattr(terminal, "open_tab", lambda command: False)

    with pytest.raises(LookupError, match="did not open a tab"):
        panes.open_session(launch(), ResumeIn.ITERM2, "payments-api-7c", terminal)


def test_open_session_copies_the_command(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(panes, "copy_to_pasteboard", lambda text: bool(copied.append(text)) or True)

    assert panes.open_session(launch(), ResumeIn.CLIPBOARD, "payments-api-7c") == "as a command on the clipboard"
    assert copied == ["(cd /tmp/payments-api && claude --resume 4e02)"]


def test_open_session_reports_a_clipboard_that_would_not(monkeypatch):
    monkeypatch.setattr(panes, "copy_to_pasteboard", lambda text: False)

    with pytest.raises(OSError, match="clipboard"):
        panes.open_session(launch(), ResumeIn.CLIPBOARD, "payments-api-7c")
