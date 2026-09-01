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

from clownhead.jetbrains import Selection
from clownhead.models import Session, Status
from clownhead.terminal import Rgb, Terminal, detect_terminal, own_tty, terminal_for

OVERSEER_LABEL = "clownhead"
OVERSEER_COLOR = Rgb(108, 113, 196)

STATUS_COLORS: dict[Status, Rgb | None] = {
    Status.WAITING: Rgb(220, 50, 47),
    Status.BLOCKED: Rgb(203, 75, 22),
    Status.FAILED: Rgb(203, 75, 22),
    Status.BUSY: Rgb(38, 139, 210),
    Status.SHELL: None,
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
    tab: Selection | None = None

    @property
    def tab_note(self) -> str:
        """Why the session was left behind its own tab, and nothing at all when it was not.

        A signal that reached the terminal but not the tab is half a focus, so it is worth
        a caller reading this before it calls the whole thing a success.
        """
        return "" if self.tab is None or self.tab.selected else f" · {self.tab.reason}"


def color_for(status: Status) -> Rgb | None:
    """Tab colour for a status, or ``None`` when the tab should be left alone.

    A tab is coloured for what it is doing on your behalf, so ``shell`` is left alone
    alongside ``idle``: the turn is over and the session is yours to type into, whatever
    its background command is still doing. The board says ``shell`` in the status column
    for anyone who wants to know which of the two it is.
    """
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


def paint_self(terminal: Terminal | None = None) -> SignalResult:
    """Tint the tab clownhead is itself running in.

    The board sits in the tab bar it paints and is the one tab there that is not a
    session; the colour is what says so. It is a colour no status wears, so a tab the
    board holds is never read as a session in some state.

    The terminal is clownhead's own rather than one resolved from a process tree: no
    session owns this tab, only the shell the board was started from.
    """
    return _signal_own_tab(terminal, OVERSEER_COLOR)


def reset_self(terminal: Terminal | None = None) -> SignalResult:
    """Hand clownhead's own tab back the colour it had before the board took it."""
    return _signal_own_tab(terminal, None)


def _signal_own_tab(terminal: Terminal | None, color: Rgb | None) -> SignalResult:
    tty = own_tty()
    if tty is None:
        return SignalResult(OVERSEER_LABEL, None, False, "no tty")
    emitter = terminal or detect_terminal()
    if not emitter.supports_tab_color:
        return SignalResult(OVERSEER_LABEL, tty, False, f"{emitter.name} has no tab colours")
    try:
        if color is None:
            emitter.reset_tab_color(tty)
        else:
            emitter.set_tab_color(tty, color)
    except OSError as error:
        return SignalResult(OVERSEER_LABEL, tty, False, str(error))
    return SignalResult(OVERSEER_LABEL, tty, True, "tinted" if color else "cleared")


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
    select_tab: bool = True,
) -> SignalResult:
    """Demand attention from a single session's terminal and bring it to the front.

    Attention is requested before the emulator is raised, so the dock bounce, tab flash
    or renamed tab survives the switch and still marks the session once the window is up.
    A terminal without notifications is not asked for one: it would degrade to a second
    bell, and the message it would have carried is already in the marked tab title.

    An application that draws its own tabs is then told which of them was meant, unless
    ``select_tab`` says a session ahead of this one has already claimed that window.
    """
    if session.tty is None:
        return SignalResult(session.label, None, False, "no tty")
    text = message or f"{session.label}: {session.reason}"
    emitter = terminal_of(session, terminal)
    tab: Selection | None = None
    try:
        emitter.request_attention(session.tty, text)
        if foreground:
            emitter.foreground(session.tty)
            if select_tab and emitter.supports_tab_focus:
                tab = emitter.select_tab(session.tty, text)
        if emitter.supports_notifications:
            emitter.notify(session.tty, text)
    except (OSError, subprocess.SubprocessError) as error:
        return SignalResult(session.label, session.tty, False, str(error))
    return SignalResult(session.label, session.tty, True, text, tab)


def focus_stalled(
    sessions: Iterable[Session], terminal: Terminal | None = None, *, foreground: bool = True
) -> list[SignalResult]:
    """Demand attention from every session that is waiting on a human.

    One window can only show one tab, so the first stalled session in an application takes
    it and the rest are signalled without asking for theirs. Asking anyway would have each
    selection undo the one before it, at the cost of a round trip through the accessibility
    API per session, to end on whichever happened to be last.
    """
    results: list[SignalResult] = []
    claimed: set[str] = set()
    for session in sessions:
        if not session.needs_attention:
            continue
        emitter = terminal_of(session, terminal)
        owner = emitter.bundle_id if emitter.supports_tab_focus else None
        results.append(focus(session, emitter, foreground=foreground, select_tab=owner not in claimed))
        if owner is not None:
            claimed.add(owner)
    return results
