from pathlib import Path

import pytest
from typer.testing import CliRunner

import clownhead
from clownhead import attention, cli, discovery
from clownhead import terminal as terminal_module
from clownhead.models import Session, Status
from clownhead.terminal import ITerm2Terminal, Terminal

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: True)


def use_terminal(monkeypatch, terminal: Terminal) -> Terminal:
    """Make every session in the fleet resolve to one terminal."""
    monkeypatch.setattr(terminal_module, "detect_terminal", lambda env=None: terminal)
    monkeypatch.setattr(cli, "detect_terminal", lambda: terminal)
    return terminal


def fleet() -> list[Session]:
    return [
        Session(
            session_id="4e020900-df7c",
            cwd=Path("/tmp/payments-api"),
            name="payments-api-7c",
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


@pytest.fixture
def live_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())


class SilentTerminal(ITerm2Terminal):
    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def test_no_arguments_launches_the_tui(live_fleet, monkeypatch):
    launched: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: launched.update(kwargs))

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    assert launched["interval"] is None
    assert launched["include_closed"] is None
    assert launched["loader"](False) == fleet()


def test_tui_command_scopes_the_loader(live_fleet, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: kwargs["loader"](False))
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **k: seen.setdefault("cwd", cwd) and [])

    result = runner.invoke(cli.app, ["tui", "--cwd", "/tmp/payments-api", "--interval", "1"])

    assert result.exit_code == 0
    assert seen["cwd"] == Path("/tmp/payments-api")


def test_tui_command_passes_the_closed_flag_through(live_fleet, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: kwargs["loader"](kwargs["include_closed"]))
    monkeypatch.setattr(
        discovery,
        "list_sessions",
        lambda cwd=None, **kwargs: seen.update(kwargs) or [],
    )

    result = runner.invoke(cli.app, ["tui", "--closed"])

    assert result.exit_code == 0
    assert seen["include_closed"] is True


def test_no_arguments_fails_loudly_when_peer_discovery_is_blocked(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: pytest.fail("tui must not launch"))

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 2


def test_ls_lists_the_fleet(live_fleet):
    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "input needed" in result.stdout


def test_ls_asks_for_closed_sessions(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    result = runner.invoke(cli.app, ["ls", "--closed"])

    assert result.exit_code == 0
    assert seen["include_closed"] is True


def test_ls_leaves_closed_sessions_out_by_default(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    runner.invoke(cli.app, ["ls"])

    assert seen["include_closed"] is False


def test_ls_reports_an_empty_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 0
    assert "no live sessions" in result.stdout


def test_ls_fails_loudly_when_peer_discovery_is_blocked(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 2


def test_paint_colours_every_tab(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert len(terminal.written) == 2


def test_paint_reset_clears_tabs_and_exits(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["paint", "--reset"])

    assert result.exit_code == 0
    assert "cleared 2 tabs" in result.stdout
    assert terminal.written == ["\033]6;1;bg;*;default\a"] * 2


def test_paint_warns_on_terminals_without_colour_support(live_fleet, monkeypatch):
    use_terminal(monkeypatch, Terminal())

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert "does not support tab colours" in result.stderr


def test_focus_without_a_name_targets_stalled_sessions(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "web-platform-1d" not in result.stdout


def test_focus_brings_the_terminal_to_the_front(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "payments-api-7c"])

    assert result.exit_code == 0
    assert "\033]1337;StealFocus\a" in terminal.written


def test_focus_can_leave_the_terminal_where_it_is(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "payments-api-7c", "--no-foreground"])

    assert result.exit_code == 0
    assert "\033]1337;StealFocus\a" not in terminal.written
    assert "\033]1337;RequestAttention=yes\a" in terminal.written


def test_focus_reports_a_calm_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [fleet()[1]])
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus"])

    assert "nothing is waiting on you" in result.stdout


def test_focus_by_name(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "web-platform-1d"])

    assert result.exit_code == 0
    assert "web-platform-1d" in result.stdout


def test_focus_by_short_id(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "4e020900"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout


def test_focus_unknown_name_fails(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "nope"])

    assert result.exit_code == 1


def test_doctor_reports_blocked_peer_discovery(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "blocked" in result.stdout


def test_version_prints_the_version_without_launching_the_tui(monkeypatch):
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: pytest.fail("tui must not launch"))

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert clownhead.__version__ in result.stdout


def test_attention_module_is_reachable_from_cli():
    assert cli.attention is attention
