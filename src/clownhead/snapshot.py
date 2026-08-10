"""Persisting the fleet so it can be rebuilt after a reboot.

A Claude Code session is a transcript on disk, not a process: killing the terminal loses
nothing, and ``claude --resume <id>`` in the original directory brings the conversation
back. What does not survive a reboot is the mapping from session id to the directory it
belonged to, which is what a snapshot records.

Resume commands strip ``ANTHROPIC_API_KEY`` from the child environment. Claude Code
prefers an API key over subscription OAuth when both are present, so a stray key in a
shell profile would silently move a restored fleet onto metered billing.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from clownhead.models import Session, Snapshot, SnapshotEntry

BILLING_SENSITIVE_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def state_dir() -> Path:
    """Directory holding clownhead state, overridable via ``CLOWNHEAD_STATE_DIR``."""
    override = os.environ.get("CLOWNHEAD_STATE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "clownhead"


def snapshot_path() -> Path:
    """Location of the persisted fleet snapshot."""
    return state_dir() / "fleet.json"


def capture(sessions: Iterable[Session], now: datetime | None = None) -> Snapshot:
    """Build a snapshot from the sessions worth resurrecting."""
    entries = [SnapshotEntry.from_session(session) for session in sessions if session.session_id]
    return Snapshot(saved_at=now or datetime.now(tz=UTC), entries=entries)


def save(snapshot: Snapshot, path: Path | None = None) -> Path:
    """Write a snapshot to disk, creating the state directory if needed."""
    target = path or snapshot_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(snapshot.model_dump_json(indent=2))
    return target


def load(path: Path | None = None) -> Snapshot:
    """Read a previously saved snapshot."""
    source = path or snapshot_path()
    return Snapshot.model_validate_json(source.read_text())


def resume_argv(entry: SnapshotEntry) -> list[str]:
    """Argument vector that resumes one session with billing-sensitive vars stripped."""
    unset: list[str] = []
    for name in BILLING_SENSITIVE_VARS:
        unset.extend(["-u", name])
    return ["env", *unset, "claude", "--resume", entry.session_id]


def resume_shell_command(entry: SnapshotEntry) -> str:
    """Single shell line that resumes one session in its original directory."""
    return f"(cd {entry.cwd} && {' '.join(resume_argv(entry))})"


def tmux_argv(entry: SnapshotEntry, session_name: str = "clownhead") -> list[str]:
    """``tmux new-window`` invocation that resumes one session in its own window."""
    window = entry.name or entry.cwd.name
    return [
        "tmux",
        "new-window",
        "-t",
        session_name,
        "-c",
        str(entry.cwd),
        "-n",
        window,
        " ".join(resume_argv(entry)),
    ]
