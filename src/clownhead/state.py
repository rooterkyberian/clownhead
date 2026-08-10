"""Where clownhead keeps what it remembers between runs."""

from __future__ import annotations

import os
from pathlib import Path


def state_dir() -> Path:
    """Directory holding clownhead state, overridable via ``CLOWNHEAD_STATE_DIR``."""
    override = os.environ.get("CLOWNHEAD_STATE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "clownhead"
