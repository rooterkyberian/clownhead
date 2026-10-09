"""Detect session launchers and remember ownership after their processes exit."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Mapping
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile

from clownhead.models import Process, Session
from clownhead.state import state_dir

KANDEV_HELPER = re.compile(r"agentctl-(?:darwin|linux)-(?:amd64|arm64)")


def process_owner(pid: int | None, processes: Mapping[int, Process]) -> str | None:
    """Find a recognised launcher in the session's process ancestry."""
    seen: set[int] = set()
    while pid is not None and pid > 1 and pid not in seen:
        seen.add(pid)
        process = processes.get(pid)
        if process is None:
            break
        if _is_kandev(process.command):
            return "kandev"
        pid = process.ppid
    return None


def restore(sessions: Iterable[Session]) -> list[Session]:
    """Remember detected owners and recover them for sessions with no process left."""
    path = state_dir() / "owners.json"
    remembered = _load(path)
    updated = dict(remembered)
    found = []
    for session in sessions:
        key = f"{session.harness.value}:{session.session_id}"
        owner = session.owner or remembered.get(key)
        if owner and session.session_id:
            updated[key] = owner
        found.append(session.model_copy(update={"owner": owner}) if owner != session.owner else session)
    if updated != remembered:
        _save(updated, path)
    return found


def _is_kandev(command: str) -> bool:
    try:
        lexer = shlex.shlex(command, posix=True)
        lexer.whitespace_split = True
        argument = lexer.get_token()
    except ValueError:
        return False
    if not argument:
        return False
    executable = Path(argument)
    name = executable.name.casefold()
    if name == "kandev" or KANDEV_HELPER.fullmatch(name):
        return True
    return name == "agentctl" and any(
        part.casefold() in {"kandev", ".kandev", "kandev.app"} for part in executable.parts
    )


def _load(path: Path) -> dict[str, str]:
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {key: owner for key, owner in stored.items() if isinstance(owner, str) and owner}


def _save(owners: Mapping[str, str], path: Path) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(owners, handle, sort_keys=True, indent=2)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
