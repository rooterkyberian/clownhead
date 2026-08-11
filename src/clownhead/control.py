"""Ending a session, as opposed to signalling the terminal it runs in."""

from __future__ import annotations

import os
import signal
from collections.abc import Mapping

from clownhead.discovery import Process, process_table
from clownhead.models import Session

CLAUDE_COMMAND = "claude"


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
