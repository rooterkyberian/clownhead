import json
import os
import signal
import socket
import subprocess
import threading
from pathlib import Path

import pytest

from clownhead import control
from clownhead.discovery import Process
from clownhead.models import Session, Status


def session(pid: int | None = 77730) -> Session:
    return Session(session_id="a-b", cwd=Path("/tmp/repo"), name="payments-api-7c", pid=pid)


@pytest.fixture
def listener(socket_dir):
    """A stand-in for a session's control socket, with whatever was sent to it.

    Serving happens on a thread, so the messages are only complete once it has finished —
    which is what ``delivered()`` waits for.
    """
    path = socket_dir / "77730.sock"
    received: list[dict] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(5)
    server.bind(str(path))
    server.listen(1)

    def serve() -> None:
        try:
            connection, _ = server.accept()
        except OSError:
            return
        with connection:
            received.extend(json.loads(line) for line in connection.recv(4096).decode().splitlines() if line)

    def delivered() -> list[dict]:
        thread.join(timeout=6)
        return received

    thread = threading.Thread(target=serve)
    thread.start()
    yield path, delivered
    thread.join(timeout=6)
    server.close()


def table(pid: int = 77730, command: str = "claude") -> dict[int, Process]:
    return {pid: Process(pid=pid, ppid=1, tty=Path("/dev/ttys004"), command=command)}


def test_terminate_sends_sigterm(monkeypatch):
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    control.terminate(session(), table())

    assert signalled == [(77730, signal.SIGTERM)]


def test_terminate_refuses_a_session_with_no_process(monkeypatch):
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: pytest.fail("must not signal"))

    with pytest.raises(LookupError, match="no process"):
        control.terminate(session(pid=None), table())


def test_terminate_refuses_a_process_that_has_gone(monkeypatch):
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: pytest.fail("must not signal"))

    with pytest.raises(LookupError, match="gone"):
        control.terminate(session(), {})


def test_terminate_refuses_a_process_id_that_has_been_reused(monkeypatch):
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: pytest.fail("must not signal"))

    with pytest.raises(LookupError, match="no longer"):
        control.terminate(session(), table(command="/usr/bin/postgres -D /data"))


def test_terminate_accepts_a_versioned_claude_binary(monkeypatch):
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    control.terminate(session(), table(command="/Users/x/.local/share/claude/versions/2.1.226 --resume"))

    assert signalled == [(77730, signal.SIGTERM)]


def tab(pid: int = 77730) -> dict[int, Process]:
    tty = Path("/dev/ttys004")
    return {
        pid: Process(pid=pid, ppid=55997, tty=tty, command="claude --resume"),
        55997: Process(pid=55997, ppid=55996, tty=tty, command="-zsh"),
        55996: Process(pid=55996, ppid=1123, tty=tty, command="login -fp maciej"),
    }


def test_shell_of_finds_the_shell_that_owns_the_tab():
    assert control.shell_of(session(), tab()) == tab()[55997]


def test_shell_of_refuses_a_session_with_no_process():
    with pytest.raises(LookupError, match="no process"):
        control.shell_of(session(pid=None), tab())


def test_shell_of_refuses_a_session_with_no_tab_of_its_own():
    with pytest.raises(LookupError, match="no tab of its own"):
        control.shell_of(session(), table())


def test_shell_of_reads_the_live_process_table_when_none_is_given(monkeypatch):
    monkeypatch.setattr(control, "process_table", tab)

    assert control.shell_of(session()).pid == 55997


def test_close_tab_hangs_up_the_shell(monkeypatch):
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))
    processes = tab()

    control.close_tab(processes[55997], processes)

    assert signalled == [(55997, signal.SIGHUP)]


def test_close_tab_refuses_a_shell_that_has_gone(monkeypatch):
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: pytest.fail("must not signal"))

    with pytest.raises(LookupError, match="gone"):
        control.close_tab(tab()[55997], {})


def test_close_tab_refuses_a_process_id_that_has_been_reused(monkeypatch):
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: pytest.fail("must not signal"))
    recycled = {55997: Process(pid=55997, ppid=1, tty=Path("/dev/ttys009"), command="-zsh")}

    with pytest.raises(LookupError, match="gone"):
        control.close_tab(tab()[55997], recycled)


def test_close_tab_reads_the_live_process_table_when_none_is_given(monkeypatch):
    monkeypatch.setattr(control, "process_table", tab)
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    control.close_tab(tab()[55997])

    assert signalled == [(55997, signal.SIGHUP)]


def test_rename_sends_a_control_message_naming_the_session(monkeypatch, listener):
    path, delivered = listener
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: path)

    control.rename(session(), "invoice-parser")

    assert delivered() == [
        {"type": "control", "action": "rename", "name": "invoice-parser", "session_id": "a-b"},
    ]


def test_rename_trims_the_name_before_sending_it(monkeypatch, listener):
    path, delivered = listener
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: path)

    control.rename(session(), "  invoice-parser  ")

    assert delivered()[0]["name"] == "invoice-parser"


@pytest.mark.parametrize("name", ["", "   "])
def test_rename_refuses_a_blank_name(monkeypatch, name):
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: pytest.fail("must not send"))

    with pytest.raises(ValueError, match="blank"):
        control.rename(session(), name)


def test_rename_refuses_a_session_that_has_ended(monkeypatch):
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: pytest.fail("must not send"))
    closed = session().model_copy(update={"status": Status.CLOSED})

    with pytest.raises(LookupError, match="has ended"):
        control.rename(closed, "invoice-parser")


def test_rename_refuses_a_session_with_no_control_channel(monkeypatch):
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: None)

    with pytest.raises(LookupError, match="not listening"):
        control.rename(session(), "invoice-parser")


def test_rename_refuses_a_socket_nobody_is_listening_on(monkeypatch, socket_dir):
    monkeypatch.setattr(control, "messaging_socket", lambda session_id, registry=None: socket_dir / "gone.sock")

    with pytest.raises(LookupError, match="nothing is listening"):
        control.rename(session(), "invoice-parser")


def test_terminate_reads_the_live_process_table_when_none_is_given(monkeypatch):
    monkeypatch.setattr(control, "process_table", lambda: table())
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    control.terminate(session())

    assert signalled == [(77730, signal.SIGTERM)]


def test_wait_for_exit_returns_at_once_for_a_process_that_has_gone():
    finished = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    finished.wait()

    assert control.wait_for_exit(finished.pid) is True


def test_wait_for_exit_gives_up_on_a_process_that_stays():
    assert control.wait_for_exit(os.getpid(), timeout=0.05, poll=0.01) is False


def test_wait_for_exit_waits_for_a_process_to_finish_shutting_down(monkeypatch):
    remaining = [True, True, False]
    monkeypatch.setattr(
        control.os,
        "kill",
        lambda pid, sig: None if remaining.pop(0) else _gone(),
    )

    assert control.wait_for_exit(4242, timeout=5.0, poll=0.01) is True
    assert remaining == []


def test_wait_for_exit_reads_a_refused_signal_as_still_running(monkeypatch):
    def refuse(pid, sig):
        raise PermissionError("not yours")

    monkeypatch.setattr(control.os, "kill", refuse)

    assert control.wait_for_exit(4242, timeout=0.05, poll=0.01) is False


def _gone():
    raise ProcessLookupError
