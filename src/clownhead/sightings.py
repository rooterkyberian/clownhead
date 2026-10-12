"""When each session was last found running, and where that is remembered.

Neither harness writes down the moment a session stopped. A transcript's modification time
is the last thing the session said, which for a session left idle overnight is hours before
it went away, and the registry record carrying a heartbeat is deleted when a session exits
cleanly. So the note is clownhead's own: every listing that finds a session running stamps
it in ``seen.json`` under the state directory, and a session that has ended is handed back
carrying the last stamp it got as :attr:`~clownhead.models.Session.last_seen`.

The note is as fresh as the last listing. The board, the tray and every ``clownhead``
command list the fleet; a session that started and ended while none of them was running
was never seen, and has no stamp to filter on.

A stamp is rewritten once it is :data:`RESOLUTION` old, because the board lists the fleet
every few seconds and a minute is finer than any cutoff anybody types. Stamps older than
:data:`RETENTION` are dropped whenever the file is written.

Anything unreadable reads as no stamps at all, on the same grounds as
:mod:`clownhead.archive`, and a file that cannot be written leaves the listing alone.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from clownhead.models import Session, Status
from clownhead.state import state_dir

RESOLUTION = timedelta(minutes=1)
RETENTION = timedelta(days=30)


def sightings_path() -> Path:
    """Location of the persisted stamps."""
    return state_dir() / "seen.json"


def record(sessions: Iterable[Session], now: datetime | None = None, path: Path | None = None) -> list[Session]:
    """Stamp every running session as seen now, and hand each one that has ended its last stamp.

    A running session is handed back unchanged. It is being seen at this moment, so the
    question :attr:`~clownhead.models.Session.last_seen` answers is one only a session that
    has ended raises.
    """
    moment = now or datetime.now(tz=UTC)
    target = path or sightings_path()
    stored = load(target)
    stamps = {key: seen for key, seen in stored.items() if moment - seen <= RETENTION}
    found: list[Session] = []
    for session in sessions:
        key = _key(session)
        if session.is_finished:
            seen = stamps.get(key)
            found.append(session.model_copy(update={"last_seen": seen}) if seen != session.last_seen else session)
            continue
        if session.session_id and (key not in stamps or moment - stamps[key] >= RESOLUTION):
            stamps[key] = moment
        found.append(session)
    if stamps != stored:
        _save(stamps, target)
    return found


def seen_since(sessions: Iterable[Session], since: datetime) -> list[Session]:
    """The sessions that ended after being seen running at or after ``since``, latest first.

    Only a closed session qualifies. An archived one is a session somebody said they were
    done with, and terminating with the archive box ticked stamps a session moments before
    it goes, so a cutoff before that moment would bring it straight back. A completed
    background agent finished its job and has nothing to come back to.
    """
    stamped = [
        (session.last_seen, session)
        for session in sessions
        if session.status is Status.CLOSED and session.last_seen is not None and session.last_seen >= since
    ]
    return [session for _, session in sorted(stamped, key=lambda pair: pair[0], reverse=True)]


def load(path: Path | None = None) -> dict[str, datetime]:
    """Every stamp on disk, keyed by harness and session id."""
    source = path or sightings_path()
    try:
        stored = json.loads(source.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {key: seen for key, value in stored.items() if (seen := _parse(value)) is not None}


def _key(session: Session) -> str:
    return f"{session.harness.value}:{session.session_id}"


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        seen = datetime.fromisoformat(value)
    except ValueError:
        return None
    return seen if seen.tzinfo is not None else None


def _save(stamps: Mapping[str, datetime], path: Path) -> None:
    """Replace the file in one rename, since the board and the tray both write it."""
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump({key: seen.isoformat() for key, seen in stamps.items()}, handle, sort_keys=True, indent=2)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
