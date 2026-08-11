"""A fleet that is not there, so the board can be shown without one of your own.

``clownhead-demo`` opens the overseer on it. It is a script of its own rather than a flag
on ``clownhead`` because it answers with sessions that do not exist, which is a thing to
have asked for by name and not something the fleet command should be one typo away from.

Everything the board prints about a session is checked against the disk before it is
printed: a working directory that has gone is marked as gone, a worktree resumes from its
repository only while that repository still stands, and a signal is a write to a TTY. A
fleet invented in memory alone would therefore render as a board full of missing
directories, so this one is built rather than imagined — a home of its own holding the
projects, the worktrees, the terminal application and a stand-in file per TTY that its
sessions claim, with ``HOME`` pointed at it so paths shorten to ``~`` as they would
anywhere else.

Nothing here reaches a real terminal or a real process. The TTYs are ordinary files, so a
signal aimed at one lands in the demo's own directory rather than in somebody's terminal,
and no session carries a pid, so there is nothing for ``t`` to send SIGTERM to.

Durations are offsets from the moment the fleet is read rather than fixed dates, so every
recording of the board renders the same numbers.
"""

from __future__ import annotations

import os
import plistlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from clownhead import tui
from clownhead.discovery import CONFIG_DIR_VAR, Message, sort_key
from clownhead.models import Session, Status
from clownhead.settings import Settings

DEMO_HOME = Path("/tmp/clownhead-demo")  # noqa: S108
"""A short, well-known path, so the resume commands the board prints stay one line."""

DEMO_SETTINGS = Settings(interval=5.0, paint_tabs=False)
"""Tab tinting off: the demo has no tabs of its own to colour, and yours are not its to
take. Everything else is what a fresh install starts with."""

TERMINAL_APP = Path("Applications/iTerm.app")
ITERM2_BUNDLE_ID = "com.googlecode.iterm2"

PAYMENTS = Path("dev/payments-api")
WEB_PLATFORM = Path("dev/web-platform")
DESIGN_SYSTEM = Path("dev/design-system")
DATA_PLATFORM = Path("dev/data-platform")
NOTIFICATIONS = Path("dev/notifications")
WORKTREES = WEB_PLATFORM / ".claude/worktrees"
SEARCH_INDEX = WORKTREES / "search-index"
INVOICE_PARSER = WORKTREES / "invoice-parser"

PROJECTS = (
    PAYMENTS,
    WEB_PLATFORM,
    DESIGN_SYSTEM,
    DATA_PLATFORM,
    NOTIFICATIONS,
    SEARCH_INDEX,
    INVOICE_PARSER,
)
TTYS = ("ttys002", "ttys004", "ttys008", "ttys011", "ttys014", "ttys017")


PAYMENTS_SESSION = "4e020900-df7c-4665-a804-d973b14a1926"
SEARCH_INDEX_SESSION = "8b1c4f22-0d31-4f0a-9c2e-3a7b1e5d6f08"
BACKFILL_SESSION = "d9401e77-3a52-4c86-91bf-0b7c2e6a48d5"
INVOICE_SESSION = "c73de1a4-6b90-4d2f-8a15-2f9e0c4b7d31"
NOTIFICATIONS_SESSION = "6e2b0c93-58d1-4f7a-a3e9-c04d81b5726f"
WEB_PLATFORM_SESSION = "1f6a8e30-92c7-4b58-b0d4-6e5a3c81f947"
DESIGN_SYSTEM_SESSION = "a05f2b6c-4e18-4a73-9c62-8d1b7f0e5a24"

CONVERSATIONS: dict[str, tuple[tuple[str, timedelta, str], ...]] = {
    PAYMENTS_SESSION: (
        ("user", timedelta(days=12, minutes=4), "there are three migrations for one column"),
        (
            "assistant",
            timedelta(days=12, minutes=3),
            "All three add to `payment_intents`. Squashing them means rewriting the down migration too — shall I?",
        ),
        ("user", timedelta(days=12, minutes=1), "squash them, then run the suite"),
        (
            "assistant",
            timedelta(days=12),
            "Squashed into one migration. The suite needs a decision first: two fixtures disagree about whether "
            "`captured_at` is nullable, and I would rather ask than pick.",
        ),
    ),
    SEARCH_INDEX_SESSION: (
        ("user", timedelta(hours=1), "rebuild the index from the new analyzer"),
        (
            "assistant",
            timedelta(seconds=8),
            "Stage 3 of 5. Reindexed 1.2M of 3.4M documents; the analyzer change costs about 40% more per batch.",
        ),
    ),
}


def main() -> None:
    """Entry point for the ``clownhead-demo`` script."""
    typer.run(board)


def board() -> None:
    """Open the overseer on a fabricated fleet, which the README's recording is made from."""
    tui.run(loader=fabricated_fleet(), settings=DEMO_SETTINGS, reader=fabricated_conversation)


def fabricated_fleet() -> Callable[[bool], list[Session]]:
    """Build the demo's world and return a loader that reads the fleet living in it.

    ``CLAUDE_CONFIG_DIR`` goes with the shell it was set in: the board names a relocated
    config directory in its top bar, and the demo opens none at all, so a shell that had
    one would have the board reporting a directory it never read — and, in a recording,
    naming somebody's home directory besides.
    """
    _build_home()
    os.environ["HOME"] = str(DEMO_HOME)
    os.environ.pop(CONFIG_DIR_VAR, None)
    return _fleet


def fabricated_conversation(session_id: str, /, *, limit: int) -> list[Message]:
    """The turns a demo session has to show, oldest first.

    Only the two sessions worth opening have anything to say. The rest answer as a session
    whose transcript has nothing a human would recognise as a conversation does — with
    nothing, which the panel says plainly.
    """
    now = datetime.now(tz=UTC)
    turns = CONVERSATIONS.get(session_id, ())
    return [Message(role=role, text=text, at=now - ago) for role, ago, text in turns][-limit:]


def _build_home() -> None:
    for project in PROJECTS:
        (DEMO_HOME / project).mkdir(parents=True, exist_ok=True)
    tty_dir = DEMO_HOME / "tty"
    tty_dir.mkdir(parents=True, exist_ok=True)
    for tty in TTYS:
        (tty_dir / tty).touch()
    info = DEMO_HOME / TERMINAL_APP / "Contents/Info.plist"
    info.parent.mkdir(parents=True, exist_ok=True)
    info.write_bytes(plistlib.dumps({"CFBundleIdentifier": ITERM2_BUNDLE_ID}))


def _fleet(include_closed: bool) -> list[Session]:
    """The demo fleet, ordered the way discovery orders a real one."""
    now = datetime.now(tz=UTC)
    sessions = [
        Session(
            session_id=PAYMENTS_SESSION,
            cwd=DEMO_HOME / PAYMENTS,
            name="payments-api-7c",
            status=Status.WAITING,
            waiting_for="input needed",
            started_at=now - timedelta(days=12),
            updated_at=now - timedelta(days=12),
            tty=DEMO_HOME / "tty/ttys004",
            app=DEMO_HOME / TERMINAL_APP,
        ),
        Session(
            session_id=SEARCH_INDEX_SESSION,
            cwd=DEMO_HOME / SEARCH_INDEX,
            name="index-rebuild-stage-3",
            status=Status.BUSY,
            started_at=now - timedelta(hours=1),
            updated_at=now,
            tty=DEMO_HOME / "tty/ttys011",
            app=DEMO_HOME / TERMINAL_APP,
        ),
        Session(
            session_id=BACKFILL_SESSION,
            cwd=DEMO_HOME / DATA_PLATFORM,
            name="backfill-rerun",
            status=Status.BUSY,
            started_at=now - timedelta(minutes=26),
            updated_at=now - timedelta(seconds=3),
            tty=DEMO_HOME / "tty/ttys008",
            app=DEMO_HOME / TERMINAL_APP,
        ),
        Session(
            session_id=INVOICE_SESSION,
            cwd=DEMO_HOME / INVOICE_PARSER,
            name="invoice-parser",
            status=Status.IDLE,
            started_at=now - timedelta(hours=1, minutes=5),
            updated_at=now - timedelta(minutes=32),
            tty=DEMO_HOME / "tty/ttys017",
            app=DEMO_HOME / TERMINAL_APP,
        ),
        Session(
            session_id=NOTIFICATIONS_SESSION,
            cwd=DEMO_HOME / NOTIFICATIONS,
            name="notifications-svc",
            status=Status.IDLE,
            started_at=now - timedelta(hours=2, minutes=10),
            updated_at=now - timedelta(hours=2),
            tty=DEMO_HOME / "tty/ttys014",
            app=DEMO_HOME / TERMINAL_APP,
        ),
        Session(
            session_id=WEB_PLATFORM_SESSION,
            cwd=DEMO_HOME / WEB_PLATFORM,
            name="web-platform-1d",
            status=Status.IDLE,
            started_at=now - timedelta(days=4),
            updated_at=now - timedelta(days=4),
            tty=DEMO_HOME / "tty/ttys002",
            app=DEMO_HOME / TERMINAL_APP,
        ),
    ]
    if include_closed:
        sessions.append(
            Session(
                session_id=DESIGN_SYSTEM_SESSION,
                cwd=DEMO_HOME / DESIGN_SYSTEM,
                name="design-system-0b",
                status=Status.CLOSED,
                started_at=now - timedelta(days=6),
                updated_at=now - timedelta(days=6),
            )
        )
    return sorted(sessions, key=sort_key)
