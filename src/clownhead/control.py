"""Acting on a session's processes — ending one, closing the tab it leaves, renaming it."""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from clownhead.discovery import Process, is_claude, messaging_socket, owning_shell, process_table
from clownhead.models import Session

CONTROL_TIMEOUT = 2.0
EXIT_TIMEOUT = 10.0
EXIT_POLL = 0.2


def terminate(session: Session, processes: Mapping[int, Process] | None = None) -> None:
    """Ask a session's process to exit, by sending it SIGTERM.

    SIGTERM rather than SIGKILL: Claude Code writes its transcript as it goes, and a
    session given the chance to shut down cleanly leaves a file that can still be
    resumed.

    The process id is checked against the process table first. A session's pid comes
    from a listing that is already up to a refresh old, and a process that exited in the
    meantime may have had its id handed to something else — which would then be signalled
    in its place.

    Raises:
        LookupError: the session has no process, or no longer owns that one.
        OSError: the signal could not be delivered.
    """
    if session.pid is None:
        raise LookupError(f"{session.label} has no process to signal")
    table = processes if processes is not None else process_table()
    process = table.get(session.pid)
    if process is None:
        raise LookupError(f"pid {session.pid} is gone")
    if not is_claude(process.command):
        raise LookupError(f"pid {session.pid} is no longer {session.label}")
    os.kill(session.pid, signal.SIGTERM)


def wait_for_exit(pid: int, timeout: float = EXIT_TIMEOUT, poll: float = EXIT_POLL) -> bool:
    """Whether a process is gone, waiting up to ``timeout`` for it to go.

    Signal zero asks the kernel about a process without disturbing it. A process this one
    is not allowed to signal answers ``PermissionError``, which is still an answer: it
    exists.

    Anything that depends on a session having finished has to wait for it rather than
    assume it, because SIGTERM is a request. Claude Code takes it as one — it has a
    transcript to finish writing — and a caller that acted the moment the signal was sent
    would be acting while the session was still shutting down.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


def shell_of(session: Session, processes: Mapping[int, Process] | None = None) -> Process:
    """The shell whose exit closes the tab a session is running in.

    Asked for while the session is still there: once it has exited, its row is out of the
    process table and the trail from it up to its shell has gone with it.

    Raises:
        LookupError: the session has no tab of its own to close.
    """
    if session.pid is None:
        raise LookupError(f"{session.label} has no process to trace")
    table = processes if processes is not None else process_table()
    shell = owning_shell(session.pid, table)
    if shell is None:
        raise LookupError(f"{session.label} has no tab of its own to close")
    return shell


def close_tab(shell: Process, processes: Mapping[int, Process] | None = None) -> None:
    """Close the tab a session was running in, by hanging up the shell that owns it.

    SIGHUP rather than SIGTERM, and not because SIGHUP is the harsher of the two: an
    interactive shell ignores SIGTERM, so that a stray ``kill`` aimed at a job cannot take
    the prompt down with it. SIGHUP is the signal that says the terminal has gone away,
    which is what a shell is built to answer — it is what the emulator itself sends when a
    window is closed — and the emulator closes the tab of its own accord once nothing is
    left holding the pty open. Whether it does is the emulator's to decide: one that keeps
    a tab whose shell did not exit cleanly keeps this one, and there is nothing else to ask.

    The shell is looked up again first, for the reason :func:`terminate` looks a session up:
    it was resolved before the session was asked to exit, and a process id freed in the
    meantime may since have been handed to something else.

    Raises:
        LookupError: the shell is gone, or that process id is no longer it.
        OSError: the signal could not be delivered.
    """
    table = processes if processes is not None else process_table()
    if table.get(shell.pid) != shell:
        raise LookupError(f"the shell on {shell.tty} is gone")
    os.kill(shell.pid, signal.SIGHUP)


def rename(session: Session, name: str, registry: Path | None = None) -> None:
    """Rename a session, by asking the session itself to do it.

    Claude Code listens for control messages on a per-process socket, and the rename it
    performs there is the one ``/rename`` performs: the registry record, the transcript,
    the prompt box and the terminal title all follow, and the session is told its new
    name. Writing the registry record directly would move only the copy clownhead reads,
    leaving the session itself still answering to the old one.

    The session id travels with the request and Claude Code drops anything addressed to a
    session other than its own, so a socket left behind by a process id that has since
    been recycled refuses the rename rather than applying it to a stranger. That check is
    stronger than the process-table one :func:`terminate` needs, which is why there is no
    equivalent here.

    A session that has ended is refused outright. Its name lives on in its transcript,
    which is what the resume picker reads, but nothing is listening to be told about a new
    one — and the board takes its names from the live listing, so a rename written to disk
    would not show up here either.

    Raises:
        ValueError: the name is blank.
        LookupError: the session has ended, offers no control channel, or is not listening.
        OSError: the request could not be delivered.
    """
    label = name.strip()
    if not label:
        raise ValueError("a session name cannot be blank")
    if session.is_finished:
        raise LookupError(f"{session.label} has ended and cannot be renamed")
    path = messaging_socket(session.session_id, registry)
    if path is None:
        raise LookupError(f"{session.label} is not listening for control messages")
    _send(path, {"type": "control", "action": "rename", "name": label, "session_id": session.session_id})


def _send(path: Path, message: Mapping[str, Any]) -> None:
    """Deliver one newline-terminated JSON message to a session's control socket."""
    payload = json.dumps(message).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(CONTROL_TIMEOUT)
        try:
            client.connect(str(path))
        except (ConnectionRefusedError, FileNotFoundError) as error:
            raise LookupError(f"nothing is listening on {path}") from error
        client.sendall(payload)
