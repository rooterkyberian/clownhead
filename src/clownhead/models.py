"""Domain models for the Claude Code sessions clownhead oversees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Status(StrEnum):
    """State a Claude Code session reports about itself."""

    BUSY = "busy"
    IDLE = "idle"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    UNKNOWN = "unknown"

    @classmethod
    def _missing_(cls, value: object) -> Status:
        return cls.UNKNOWN


class Kind(StrEnum):
    """Whether a session is an attached terminal or a detached background agent."""

    INTERACTIVE = "interactive"
    BACKGROUND = "background"

    @classmethod
    def _missing_(cls, value: object) -> Kind:
        return cls.INTERACTIVE


ATTENTION_STATES = frozenset({Status.WAITING, Status.BLOCKED, Status.FAILED})


def _epoch_millis_to_datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return None


class Session(BaseModel):
    """A single Claude Code session as reported by ``claude agents --json``.

    Interactive entries carry ``status``/``waitingFor`` while background entries carry
    ``state``; both are normalised onto :attr:`status`.
    """

    model_config = ConfigDict(populate_by_name=True)

    session_id: str
    cwd: Path
    kind: Kind = Kind.INTERACTIVE
    pid: int | None = None
    name: str | None = None
    status: Status = Status.UNKNOWN
    waiting_for: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    tty: Path | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalise_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "session_id" in data:
            return data
        return {
            "session_id": data.get("sessionId", data.get("id", "")),
            "cwd": data.get("cwd", "."),
            "kind": data.get("kind", "interactive"),
            "pid": data.get("pid"),
            "name": data.get("name"),
            "status": data.get("status", data.get("state", "unknown")),
            "waiting_for": data.get("waitingFor"),
            "started_at": _epoch_millis_to_datetime(data.get("startedAt")),
        }

    @property
    def label(self) -> str:
        """Human-facing identifier, falling back to the working directory name."""
        return self.name or f"{self.cwd.name}:{self.pid or self.session_id[:8]}"

    @property
    def short_id(self) -> str:
        """First segment of the session UUID, enough to disambiguate in practice."""
        return self.session_id.split("-")[0]

    @property
    def needs_attention(self) -> bool:
        """Whether this session is stalled waiting on a human."""
        return self.status in ATTENTION_STATES

    @property
    def reason(self) -> str:
        """Why the session needs attention, or its plain status when it does not."""
        return self.waiting_for or self.status.value

    def age(self, now: datetime | None = None) -> timedelta | None:
        """Time since the session process started."""
        if self.started_at is None:
            return None
        return (now or datetime.now(tz=UTC)) - self.started_at

    def quiet_for(self, now: datetime | None = None) -> timedelta | None:
        """Time since the session last updated its registry heartbeat."""
        if self.updated_at is None:
            return None
        return (now or datetime.now(tz=UTC)) - self.updated_at


class SnapshotEntry(BaseModel):
    """A session recorded for later resurrection."""

    session_id: str
    cwd: Path
    name: str | None = None

    @classmethod
    def from_session(cls, session: Session) -> SnapshotEntry:
        """Build a snapshot entry from a live session."""
        return cls(session_id=session.session_id, cwd=session.cwd, name=session.name)


class Snapshot(BaseModel):
    """A point-in-time record of the interactive fleet."""

    saved_at: datetime
    entries: list[SnapshotEntry]
