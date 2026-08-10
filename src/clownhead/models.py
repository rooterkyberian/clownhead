"""Domain models for the Claude Code sessions clownhead oversees."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class Status(StrEnum):
    """State a Claude Code session reports about itself.

    ``COMPLETED`` is a background agent the CLI reports as finished; ``CLOSED`` is an
    interactive session the registry remembers but the CLI no longer lists.
    """

    BUSY = "busy"
    IDLE = "idle"
    WAITING = "waiting"
    BLOCKED = "blocked"
    FAILED = "failed"
    COMPLETED = "completed"
    CLOSED = "closed"
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
FINISHED_STATES = frozenset({Status.COMPLETED, Status.CLOSED})

WORKTREE_MARKER = "/.claude/worktrees/"


def split_worktree(cwd: Path) -> tuple[Path, str | None]:
    """The repository a worktree belongs to and its name, or the path and ``None``."""
    repo, marker, name = str(cwd).partition(WORKTREE_MARKER)
    return (Path(repo), name) if marker else (cwd, None)


def _epoch_millis_to_datetime(value: Any) -> datetime | None:
    if isinstance(value, int | float):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    return None


class Session(BaseModel):
    """A single Claude Code session as reported by ``claude agents --json``.

    Interactive entries carry ``status``/``waitingFor`` while background entries carry
    ``state``; both are normalised onto :attr:`status`. :attr:`tty` and :attr:`app` are
    not in the payload at all — they are discovered from the process table.
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
    app: Path | None = None

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
            "updated_at": _epoch_millis_to_datetime(data.get("updatedAt")),
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
    def is_finished(self) -> bool:
        """Whether this session has ended, by completing or by losing its terminal."""
        return self.status in FINISHED_STATES

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
