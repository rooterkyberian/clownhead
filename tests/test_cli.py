from pathlib import Path

import pytest
from typer.testing import CliRunner

from clownhead import attention, cli, discovery, snapshot
from clownhead.models import Session, Status
from clownhead.terminal import ITerm2Terminal, Terminal

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: True)


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
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def test_ls_lists_the_fleet(live_fleet):
    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "input needed" in result.stdout


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
    terminal = SilentTerminal()
    monkeypatch.setattr(cli, "detect_terminal", lambda: terminal)

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert len(terminal.written) == 2


def test_paint_reset_clears_tabs_and_exits(live_fleet, monkeypatch):
    terminal = SilentTerminal()
    monkeypatch.setattr(cli, "detect_terminal", lambda: terminal)

    result = runner.invoke(cli.app, ["paint", "--reset"])

    assert result.exit_code == 0
    assert "cleared 2 tabs" in result.stdout
    assert terminal.written == ["\033]6;1;bg;*;default\a"] * 2


def test_paint_warns_on_terminals_without_colour_support(live_fleet, monkeypatch):
    monkeypatch.setattr(cli, "detect_terminal", Terminal)

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert "does not support tab colours" in result.stderr


def test_ping_without_a_name_targets_stalled_sessions(live_fleet, monkeypatch):
    monkeypatch.setattr(cli, "detect_terminal", SilentTerminal)

    result = runner.invoke(cli.app, ["ping"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "web-platform-1d" not in result.stdout


def test_ping_reports_a_calm_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [fleet()[1]])
    monkeypatch.setattr(cli, "detect_terminal", SilentTerminal)

    result = runner.invoke(cli.app, ["ping"])

    assert "nothing is waiting on you" in result.stdout


def test_ping_by_name(live_fleet, monkeypatch):
    monkeypatch.setattr(cli, "detect_terminal", SilentTerminal)

    result = runner.invoke(cli.app, ["ping", "web-platform-1d"])

    assert result.exit_code == 0
    assert "web-platform-1d" in result.stdout


def test_ping_by_short_id(live_fleet, monkeypatch):
    monkeypatch.setattr(cli, "detect_terminal", SilentTerminal)

    result = runner.invoke(cli.app, ["ping", "4e020900"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout


def test_ping_unknown_name_fails(live_fleet, monkeypatch):
    monkeypatch.setattr(cli, "detect_terminal", SilentTerminal)

    result = runner.invoke(cli.app, ["ping", "nope"])

    assert result.exit_code == 1


def test_snapshot_then_restore_prints_resume_commands(live_fleet):
    saved = runner.invoke(cli.app, ["snapshot"])
    assert saved.exit_code == 0
    assert "saved" in saved.stdout

    restored = runner.invoke(cli.app, ["restore"])

    assert restored.exit_code == 0
    assert "claude --resume 4e020900-df7c" in restored.stdout
    assert "env -u ANTHROPIC_API_KEY" in restored.stdout


def test_restore_output_is_pipeable_into_a_shell(live_fleet):
    runner.invoke(cli.app, ["snapshot"])

    lines = [line for line in runner.invoke(cli.app, ["restore"]).stdout.splitlines() if line.strip()]

    assert len(lines) == 2
    assert all(line.startswith("(cd ") and line.endswith(")") for line in lines)


def test_restore_without_a_snapshot_fails():
    result = runner.invoke(cli.app, ["restore"])

    assert result.exit_code == 1
    assert "no snapshot" in result.stderr


def test_restore_tmux_spawns_a_window_per_entry(live_fleet, monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli.subprocess, "run", lambda argv, **kwargs: calls.append(argv))

    runner.invoke(cli.app, ["snapshot"])
    result = runner.invoke(cli.app, ["restore", "--tmux"])

    assert result.exit_code == 0
    assert len(calls) == 2
    assert calls[0][:2] == ["tmux", "new-window"]


def test_doctor_flags_a_leaked_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-whatever")

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "ANTHROPIC_API_KEY set" in result.stdout


def test_doctor_confirms_subscription_auth(monkeypatch):
    for name in snapshot.BILLING_SENSITIVE_VARS:
        monkeypatch.delenv(name, raising=False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "subscription auth" in result.stdout


def test_doctor_reports_blocked_peer_discovery(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "blocked" in result.stdout


def test_attention_module_is_reachable_from_cli():
    assert cli.attention is attention
