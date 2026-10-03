import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clownhead import gnome_terminal as gnome
from clownhead.jetbrains import Selection

TTY = Path("/dev/pts/7")
SCREEN = "77e017e7-69ec-4aa6-b944-4f1bfc536784"
TARGET = gnome.Target(":1.234", SCREEN)
ENV = {
    gnome.SERVICE_VAR: TARGET.service,
    gnome.SCREEN_VAR: f"{gnome.SCREEN_PREFIX}{SCREEN.replace('-', '_')}",
}


def test_tab_identity_comes_from_the_native_screen_path():
    assert gnome.target_from_env(ENV) == TARGET


@pytest.mark.parametrize(
    "env",
    [
        {},
        {gnome.SERVICE_VAR: TARGET.service},
        {**ENV, gnome.SERVICE_VAR: "--help"},
        {**ENV, gnome.SCREEN_VAR: "/unrelated/77e017e7_69ec_4aa6_b944_4f1bfc536784"},
        {**ENV, gnome.SCREEN_VAR: f"{gnome.SCREEN_PREFIX}not_a_uuid"},
    ],
)
def test_invalid_tab_identities_are_declined(env):
    assert gnome.target_from_env(env) is None


@pytest.fixture
def processes(monkeypatch, tmp_path):
    monkeypatch.setattr(gnome, "PROC_ROOT", tmp_path)
    parent = tmp_path / "900"
    parent.mkdir()
    (parent / "exe").symlink_to("/usr/libexec/gnome-terminal-server")
    run = MagicMock(return_value=subprocess.CompletedProcess([], 0, "101 900\n102 101\n103 900\n", ""))
    monkeypatch.setattr(gnome.subprocess, "run", run)

    def populate(pid: int, env: dict[str, str]) -> None:
        folder = tmp_path / str(pid)
        folder.mkdir()
        (folder / "environ").write_bytes(b"\0".join(f"{key}={value}".encode() for key, value in env.items()))

    return run, populate


def test_target_lookup_uses_processes_on_the_requested_tty(processes, monkeypatch):
    run, populate = processes
    populate(101, {"OTHER": "a=b", **ENV})
    populate(103, ENV)
    # The board's own tab must never become the target of another session's focus.
    monkeypatch.setenv(gnome.SERVICE_VAR, ":1.999")
    monkeypatch.setenv(gnome.SCREEN_VAR, "/org/gnome/Terminal/screen/00000000_0000_0000_0000_000000000000")

    assert gnome.target_for(TTY) == TARGET
    assert run.call_args.args[0] == ["ps", "-t", str(TTY), "-o", "pid=,ppid="]


def test_target_lookup_skips_exited_and_unreadable_processes(processes):
    _, populate = processes
    populate(103, ENV)

    assert gnome.target_for(TTY) == TARGET


def test_target_lookup_declines_conflicting_tab_identities(processes):
    _, populate = processes
    populate(101, ENV)
    populate(103, {**ENV, gnome.SERVICE_VAR: ":1.999"})

    assert gnome.target_for(TTY) is None


def test_target_lookup_cannot_borrow_the_callers_tab(processes, monkeypatch):
    monkeypatch.setenv(gnome.SERVICE_VAR, ENV[gnome.SERVICE_VAR])
    monkeypatch.setenv(gnome.SCREEN_VAR, ENV[gnome.SCREEN_VAR])

    assert gnome.target_for(TTY) is None


def test_another_emulator_cannot_borrow_an_inherited_gnome_tab(processes, monkeypatch):
    _, populate = processes
    populate(101, ENV)
    monkeypatch.setattr(Path, "readlink", lambda self: Path("/usr/bin/xterm"))

    assert gnome.target_for(TTY) is None


def test_a_child_cannot_replace_the_native_tab_identity(processes):
    _, populate = processes
    populate(101, ENV)
    populate(102, {**ENV, gnome.SERVICE_VAR: ":1.999"})

    assert gnome.target_for(TTY) == TARGET


def test_target_lookup_reports_an_empty_tty(processes):
    run, _ = processes
    run.return_value = subprocess.CompletedProcess([], 1, "", "")

    assert gnome.target_for(TTY) is None


@pytest.fixture
def dbus(monkeypatch):
    monkeypatch.setattr(gnome.shutil, "which", lambda _: "/usr/bin/gdbus")
    monkeypatch.setattr(gnome, "target_for", lambda tty: TARGET)
    run = MagicMock(
        side_effect=[
            subprocess.CompletedProcess([], 0, repr(([SCREEN],)), ""),
            subprocess.CompletedProcess([], 0, "()\n", ""),
        ]
    )
    monkeypatch.setattr(gnome.subprocess, "run", run)
    return run


def test_selection_activates_the_exact_live_screen_in_its_own_service(dbus):
    assert gnome.select(TTY) == Selection(True)
    assert len(dbus.call_args_list) == 2
    argv = dbus.call_args_list[-1].args[0]
    assert argv == [
        "/usr/bin/gdbus",
        "call",
        "--session",
        "--dest",
        TARGET.service,
        "--object-path",
        gnome.SEARCH_PATH,
        "--method",
        f"{gnome.SEARCH_INTERFACE}.ActivateResult",
        SCREEN,
        "[]",
        "0",
    ]
    assert dbus.call_args.kwargs["timeout"] == gnome.TIMEOUT
    assert dbus.call_args.kwargs["check"]


def test_selection_declines_a_screen_that_closed_before_activation(dbus):
    dbus.side_effect = [subprocess.CompletedProcess([], 0, "(['00000000-0000-0000-0000-000000000000'],)", "")]

    assert gnome.select(TTY) == Selection(False, "GNOME Terminal tab has closed")
    assert dbus.call_count == 1


@pytest.mark.parametrize("answer", ["()", "([],)", "(@as [],)"])
def test_selection_handles_no_live_tabs(dbus, answer):
    dbus.side_effect = [subprocess.CompletedProcess([], 0, answer, "")]

    assert not gnome.select(TTY).selected
    assert dbus.call_count == 1


@pytest.mark.parametrize("answer", ["garbage", "({},)", "['unexpected-shape']"])
def test_selection_declines_malformed_dbus_answers(dbus, answer):
    dbus.side_effect = [subprocess.CompletedProcess([], 0, answer, "")]

    assert not gnome.select(TTY).selected
    assert dbus.call_count == 1


def test_selection_explains_a_missing_search_provider(dbus):
    dbus.side_effect = subprocess.CalledProcessError(1, "gdbus", stderr="UnknownMethod: search provider unavailable")

    result = gnome.select(TTY)

    assert not result.selected
    assert "search provider unavailable" in result.reason
    assert dbus.call_count == 1


@pytest.mark.parametrize("error", [OSError("bus unavailable"), subprocess.TimeoutExpired("gdbus", 5)])
def test_selection_reports_bus_failures(dbus, error):
    dbus.side_effect = error

    assert not gnome.select(TTY).selected


def test_selection_reports_a_missing_gdbus(monkeypatch):
    monkeypatch.setattr(gnome.shutil, "which", lambda _: None)

    assert gnome.select(TTY) == Selection(False, "GNOME Terminal focus requires gdbus")


def test_selection_reports_a_tty_with_no_native_identity(dbus, monkeypatch):
    monkeypatch.setattr(gnome, "target_for", lambda tty: None)

    assert gnome.select(TTY) == Selection(False, "no unique GNOME Terminal tab owns that tty")
    dbus.assert_not_called()
