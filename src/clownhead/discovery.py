"""Discovery of live Claude Code sessions and the metadata that enriches them.

``claude agents --json`` is the only built-in listing that includes interactive
sessions, so it is the source of truth here. Everything else in this module layers
extra facts on top of that payload: the controlling TTY from ``ps`` and the registry
heartbeat from ``~/.claude/sessions``.

I/O and parsing are deliberately separate so the parsing half stays trivially testable.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clownhead.models import Kind, Session, Status

SOCKET_DIR = Path("/tmp/cc-socks")  # noqa: S108
SESSION_REGISTRY = Path.home() / ".claude" / "sessions"
NO_TTY = frozenset({"?", "??", "-", ""})


def claude_binary() -> str:
    """Path to the Claude Code CLI, overridable for tests via ``CLOWNHEAD_CLAUDE_BIN``."""
    return os.environ.get("CLOWNHEAD_CLAUDE_BIN", "claude")


def peer_discovery_available() -> bool:
    """Whether the peer socket directory is listable.

    Interactive sessions are discovered through per-process sockets. A sandboxed shell
    can read the CLI but not the socket directory, in which case ``claude agents --json``
    silently degrades to background agents only. Callers should warn rather than report
    an empty fleet.
    """
    try:
        if not SOCKET_DIR.is_dir():
            return True
        list(SOCKET_DIR.iterdir())
    except OSError:
        return False
    return True


def fetch_payload(cwd: Path | None = None, *, include_completed: bool = False) -> list[dict[str, Any]]:
    """Run ``claude agents --json`` and return the decoded payload."""
    args = [claude_binary(), "agents", "--json"]
    if include_completed:
        args.append("--all")
    if cwd is not None:
        args.extend(["--cwd", str(cwd)])
    completed = subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603
    decoded: list[dict[str, Any]] = json.loads(completed.stdout)
    return decoded


def parse_sessions(payload: Iterable[dict[str, Any]]) -> list[Session]:
    """Turn a raw ``claude agents --json`` payload into session models."""
    return [Session.model_validate(entry) for entry in payload]


def parse_ps_output(text: str) -> dict[int, Path]:
    """Map process ids to controlling TTY device paths from ``ps -axo pid=,tty=`` output."""
    mapping: dict[int, Path] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        pid_text, tty_text = fields
        if tty_text in NO_TTY or not pid_text.isdigit():
            continue
        mapping[int(pid_text)] = Path("/dev") / tty_text
    return mapping


def tty_map() -> dict[int, Path]:
    """Controlling TTY per process id for every process on the machine."""
    completed = subprocess.run(  # noqa: S603
        ["ps", "-axo", "pid=,tty="],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_ps_output(completed.stdout)


def registry_heartbeats(registry: Path | None = None) -> dict[int, datetime]:
    """Last heartbeat per process id, read from the interactive session registry.

    The registry keeps a file per process and never deletes stale ones, so entries here
    are only meaningful when joined against sessions the CLI still reports as live.
    """
    directory = registry or SESSION_REGISTRY
    heartbeats: dict[int, datetime] = {}
    if not directory.is_dir():
        return heartbeats
    for path in directory.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        pid = entry.get("pid")
        updated_at = entry.get("updatedAt")
        if isinstance(pid, int) and isinstance(updated_at, int | float):
            heartbeats[pid] = datetime.fromtimestamp(updated_at / 1000, tz=UTC)
    return heartbeats


def enrich(
    sessions: Iterable[Session],
    *,
    ttys: Mapping[int, Path] | None = None,
    heartbeats: Mapping[int, datetime] | None = None,
) -> list[Session]:
    """Attach TTY and heartbeat facts to sessions that have a process id."""
    enriched: list[Session] = []
    for session in sessions:
        if session.pid is None:
            enriched.append(session)
            continue
        enriched.append(
            session.model_copy(
                update={
                    "tty": (ttys or {}).get(session.pid, session.tty),
                    "updated_at": (heartbeats or {}).get(session.pid, session.updated_at),
                }
            )
        )
    return enriched


def sort_key(session: Session) -> tuple[int, float]:
    """Order sessions attention-first, then oldest-first within each group."""
    rank = 0 if session.needs_attention else 1 if session.status is Status.BUSY else 2
    started = session.started_at.timestamp() if session.started_at else 0.0
    return rank, started


def list_sessions(
    cwd: Path | None = None,
    *,
    interactive_only: bool = False,
    include_completed: bool = False,
) -> list[Session]:
    """Discover live sessions and enrich them with TTY and heartbeat metadata."""
    sessions = parse_sessions(fetch_payload(cwd, include_completed=include_completed))
    if interactive_only:
        sessions = [session for session in sessions if session.kind is Kind.INTERACTIVE]
    sessions = enrich(sessions, ttys=tty_map(), heartbeats=registry_heartbeats())
    return sorted(sessions, key=sort_key)
