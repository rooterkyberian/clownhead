"""Which sessions have been put away, and where that is remembered.

Archiving is clownhead's own note about a session rather than a state Claude Code
publishes: the transcript that makes a closed session resumable is left exactly where it
was, and the note is only a session id in a file under the state directory. Recording the
id rather than the session is what lets a session be archived while it is still being
signalled — see :meth:`clownhead.tui.FleetApp.action_terminate` — and lets the note
outlive the registry record it was taken from.

A session that turns up alive again leaves the archive, because activity is the end of
being done with something. :func:`clownhead.discovery.list_sessions` is what enforces
that: it hands the live fleet to :func:`restore` on every listing, so a session brought
back from the board, from a terminal of its own, or by anything else that runs
``claude --resume`` is out of the archive by the time it is drawn.

Anything unreadable reads as an empty archive, on the same grounds as
:mod:`clownhead.settings`: losing the order sessions are listed in is a smaller failure
than refusing to list them.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from clownhead.state import state_dir


def archive_path() -> Path:
    """Location of the persisted archive."""
    return state_dir() / "archived.json"


def load(path: Path | None = None) -> set[str]:
    """The session ids that have been archived."""
    source = path or archive_path()
    try:
        stored = json.loads(source.read_text())
    except (OSError, ValueError):
        return set()
    if not isinstance(stored, list):
        return set()
    return {entry for entry in stored if isinstance(entry, str)}


def archive(session_id: str, path: Path | None = None) -> set[str]:
    """Put a session away, and hand back the archive it joined."""
    archived = load(path) | {session_id}
    save(archived, path)
    return archived


def restore(*session_ids: str, path: Path | None = None) -> set[str]:
    """Take sessions back out of the archive, and hand back what is left in it.

    The file is rewritten only when one of them was in it, because the whole live fleet is
    handed to this on every reload: activity is what takes a session back out, and the
    board finds out about activity by listing it.
    """
    archived = load(path)
    kept = archived - set(session_ids)
    if kept != archived:
        save(kept, path)
    return kept


def save(session_ids: Iterable[str], path: Path | None = None) -> Path:
    """Write the archive to disk, creating the state directory if needed."""
    target = path or archive_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(sorted(session_ids), indent=2))
    return target
