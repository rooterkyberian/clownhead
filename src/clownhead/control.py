"""Acting on a session itself — ending it, renaming it — as opposed to signalling its terminal."""

from __future__ import annotations

import json
import os
import signal
import socket
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from clownhead.discovery import Process, messaging_socket, process_table
from clownhead.models import Session

CLAUDE_COMMAND = "claude"
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
    if CLAUDE_COMMAND not in process.command:
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
