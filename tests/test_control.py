import signal
from pathlib import Path

import pytest

from clownhead import control
from clownhead.discovery import Process
from clownhead.models import Session


def session(pid: int | None = 77730) -> Session:
    return Session(session_id="a-b", cwd=Path("/tmp/repo"), name="payments-api-7c", pid=pid)


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


def test_terminate_reads_the_live_process_table_when_none_is_given(monkeypatch):
    monkeypatch.setattr(control, "process_table", lambda: table())
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(control.os, "kill", lambda pid, sig: signalled.append((pid, sig)))

    control.terminate(session())

    assert signalled == [(77730, signal.SIGTERM)]
