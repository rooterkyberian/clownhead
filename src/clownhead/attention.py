"""Mapping session state onto terminal attention signals."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clownhead.models import Session, Status
from clownhead.terminal import Rgb, Terminal

STATUS_COLORS: dict[Status, Rgb | None] = {
    Status.WAITING: Rgb(220, 50, 47),
    Status.BLOCKED: Rgb(203, 75, 22),
    Status.FAILED: Rgb(203, 75, 22),
    Status.BUSY: Rgb(38, 139, 210),
    Status.IDLE: None,
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


def paint(sessions: Iterable[Session], terminal: Terminal) -> list[SignalResult]:
    """Sync every session's tab colour to its current state.

    A session whose TTY has gone away is reported rather than raising, so one dead
    terminal cannot stop the rest of the fleet from being painted.
    """
    results: list[SignalResult] = []
    for session in sessions:
        if session.tty is None:
            results.append(SignalResult(session.label, None, False, "no tty"))
            continue
        color = color_for(session.status)
        try:
            if color is None:
                terminal.reset_tab_color(session.tty)
            else:
                terminal.set_tab_color(session.tty, color)
        except OSError as error:
            results.append(SignalResult(session.label, session.tty, False, str(error)))
            continue
        results.append(SignalResult(session.label, session.tty, True, session.status.value))
    return results


def reset(sessions: Iterable[Session], terminal: Terminal) -> list[SignalResult]:
    """Clear the tab colour of every session, leaving terminals as they were found."""
    results: list[SignalResult] = []
    for session in sessions:
        if session.tty is None:
            results.append(SignalResult(session.label, None, False, "no tty"))
            continue
        try:
            terminal.reset_tab_color(session.tty)
        except OSError as error:
            results.append(SignalResult(session.label, session.tty, False, str(error)))
            continue
        results.append(SignalResult(session.label, session.tty, True, "cleared"))
    return results


def ping(session: Session, terminal: Terminal, message: str | None = None) -> SignalResult:
    """Demand attention from a single session's terminal."""
    if session.tty is None:
        return SignalResult(session.label, None, False, "no tty")
    text = message or f"{session.label}: {session.reason}"
    try:
        terminal.request_attention(session.tty)
        terminal.notify(session.tty, text)
    except OSError as error:
        return SignalResult(session.label, session.tty, False, str(error))
    return SignalResult(session.label, session.tty, True, text)


def ping_stalled(sessions: Iterable[Session], terminal: Terminal) -> list[SignalResult]:
    """Demand attention from every session that is waiting on a human."""
    return [ping(session, terminal) for session in sessions if session.needs_attention]
