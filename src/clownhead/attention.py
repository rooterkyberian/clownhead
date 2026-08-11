"""Mapping session state onto terminal attention signals.

Every entry point takes an optional terminal. Left out, each session is signalled through
the application that actually owns it, which is the only way a fleet spread across
several terminals gets signalled correctly; passing one forces it for every session.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clownhead.models import Session, Status
from clownhead.terminal import Rgb, Terminal, terminal_for

STATUS_COLORS: dict[Status, Rgb | None] = {
    Status.WAITING: Rgb(220, 50, 47),
    Status.BLOCKED: Rgb(203, 75, 22),
    Status.FAILED: Rgb(203, 75, 22),
    Status.BUSY: Rgb(38, 139, 210),
    Status.IDLE: None,
    Status.COMPLETED: None,
    Status.CLOSED: None,
    Status.UNKNOWN: None,
}


@dataclass(frozen=True)
class SignalResult:
    """Outcome of signalling one session."""

    label: str
    tty: Path | None
    delivered: bool
    detail: str


def color_for(status: Status) -> Rgb | None:
    """Tab colour for a status, or ``None`` when the tab should be left alone."""
    return STATUS_COLORS.get(status)


def terminal_of(session: Session, terminal: Terminal | None = None) -> Terminal:
    """The terminal a session should be signalled through."""
    return terminal or terminal_for(session.app)


def paint(sessions: Iterable[Session], terminal: Terminal | None = None) -> list[SignalResult]:
    """Sync every session's tab colour to its current state.

    A session whose TTY has gone away is reported rather than raising, so one dead
    terminal cannot stop the rest of the fleet from being painted. A terminal that cannot
    tint a tab is reported too, and left untouched: painting is a status sync that repeats
    for as long as it is followed, and the only fallback signal is a bell, which nobody
    wants rung every few seconds on the strength of a session merely still being busy.
    """
    results: list[SignalResult] = []
    for session in sessions:
        if session.tty is None:
            results.append(SignalResult(session.label, None, False, "no tty"))
            continue
        emitter = terminal_of(session, terminal)
        if not emitter.supports_tab_color:
            results.append(SignalResult(session.label, session.tty, False, f"{emitter.name} has no tab colours"))
            continue
        color = color_for(session.status)
        try:
            if color is None:
                emitter.reset_tab_color(session.tty)
            else:
                emitter.set_tab_color(session.tty, color)
        except OSError as error:
            results.append(SignalResult(session.label, session.tty, False, str(error)))
            continue
        results.append(SignalResult(session.label, session.tty, True, session.status.value))
    return results


def reset(sessions: Iterable[Session], terminal: Terminal | None = None) -> list[SignalResult]:
    """Clear the tab colour of every session, leaving terminals as they were found."""
    results: list[SignalResult] = []
    for session in sessions:
        if session.tty is None:
            results.append(SignalResult(session.label, None, False, "no tty"))
            continue
        try:
            terminal_of(session, terminal).reset_tab_color(session.tty)
        except OSError as error:
            results.append(SignalResult(session.label, session.tty, False, str(error)))
            continue
        results.append(SignalResult(session.label, session.tty, True, "cleared"))
    return results


def focus(
    session: Session,
    terminal: Terminal | None = None,
    message: str | None = None,
    *,
    foreground: bool = True,
) -> SignalResult:
    """Demand attention from a single session's terminal and bring it to the front.

    Attention is requested before the emulator is raised, so the dock bounce, tab flash
    or renamed tab survives the switch and still marks the session once the window is up.
    A terminal without notifications is not asked for one: it would degrade to a second
    bell, and the message it would have carried is already in the marked tab title.
    """
    if session.tty is None:
        return SignalResult(session.label, None, False, "no tty")
    text = message or f"{session.label}: {session.reason}"
    emitter = terminal_of(session, terminal)
    try:
        emitter.request_attention(session.tty, text)
        if foreground:
            emitter.foreground(session.tty)
        if emitter.supports_notifications:
            emitter.notify(session.tty, text)
    except (OSError, subprocess.SubprocessError) as error:
        return SignalResult(session.label, session.tty, False, str(error))
    return SignalResult(session.label, session.tty, True, text)


def focus_stalled(
    sessions: Iterable[Session], terminal: Terminal | None = None, *, foreground: bool = True
) -> list[SignalResult]:
    """Demand attention from every session that is waiting on a human."""
    return [focus(session, terminal, foreground=foreground) for session in sessions if session.needs_attention]
