"""User settings, editable from inside the overseer.

Settings are read fresh rather than cached: the file is small, and a stale copy would
have the overseer disagreeing with the settings screen that just wrote it. Anything
unreadable falls back to defaults, because losing preferences is a smaller failure than
refusing to start.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from clownhead.state import state_dir

MIN_INTERVAL = 1.0
MAX_INTERVAL = 3600.0


class ResumeIn(StrEnum):
    """Where a resumed session is put.

    The clipboard is the default because it is the one that works everywhere: a command on
    the pasteboard is a command you paste wherever you want it, and the two that open the
    session for you need the terminal to be one of the two that can be asked.
    """

    CLIPBOARD = "clipboard"
    ITERM2 = "iterm2"
    TMUX = "tmux"


class Settings(BaseModel):
    """What the overseer remembers about how you like it."""

    interval: float = Field(default=5.0, ge=MIN_INTERVAL, le=MAX_INTERVAL)
    show_pid: bool = False
    show_tty: bool = False
    show_worktree: bool = False
    show_prs: bool = False
    show_closed: bool = False
    foreground: bool = True
    paint_tabs: bool = True
    close_tab_on_terminate: bool = False
    resume_in: ResumeIn = ResumeIn.CLIPBOARD
    history_turns: int = Field(default=20, ge=1, le=200)


def settings_path() -> Path:
    """Location of the persisted settings file."""
    return state_dir() / "settings.json"


def load(path: Path | None = None) -> Settings:
    """Read settings from disk, falling back to defaults for anything unreadable."""
    source = path or settings_path()
    try:
        return Settings.model_validate_json(source.read_text())
    except (OSError, ValidationError):
        return Settings()


def save(settings: Settings, path: Path | None = None) -> Path:
    """Write settings to disk, creating the state directory if needed."""
    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(settings.model_dump_json(indent=2))
    return target
