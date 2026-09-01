"""Reaching a session through whatever owns the pane it is drawn in.

Two things can be asked to act on a pane's behalf. tmux owns the pty of every pane it
draws, whatever emulator it is itself running in, and answers over its own socket. iTerm2
owns the pty where no multiplexer stands between them, and answers over AppleScript.
Anything else owns a pty nobody can ask about, which is a no that callers are given
plainly.

The two jobs here are the ones the message channel cannot do. Typing puts a line in a
session as its own keyboard would, which is what makes `/compact` run rather than be read
out, and opening puts a resumed session in a pane of its own. Both are found by the tty the
board already resolved, never by the window in front, because a line typed into the wrong
session is a command run in the wrong repository.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

from clownhead.attention import terminal_of
from clownhead.models import Session
from clownhead.resume import Launch
from clownhead.settings import ResumeIn
from clownhead.terminal import ITerm2Terminal, Terminal, copy_to_pasteboard

TMUX_BINARY = "tmux"
TMUX_VAR = "TMUX"
PANE_FORMAT = "#{pane_tty}\t#{pane_id}"
TMUX_TIMEOUT = 5.0
UNNAMEABLE = str.maketrans({".": "-", ":": "-", " ": "-"})


def type_into(session: Session, text: str, terminal: Terminal | None = None) -> str:
    """Type one line into a live session, saying what typed it.

    The line goes in as a keystroke would, so a slash command runs as the command it names
    and the session's own history keeps it. What the board loses in exchange is every
    answer the socket gives: a pane accepts the keys whatever the session behind it is
    doing with them, and a session sitting on a permission prompt takes the line as the
    answer to that prompt.

    Raises:
        ValueError: the line is blank.
        LookupError: the session has ended, has no tty, or runs somewhere nothing can type.
        OSError: the keys could not be delivered.
    """
    line = text.strip()
    if not line:
        raise ValueError("a line cannot be blank")
    if session.is_finished:
        raise LookupError(f"{session.label} has ended and cannot be typed in")
    if session.tty is None:
        raise LookupError(f"{session.label} has no terminal to type in")
    pane = tmux_pane(session.tty)
    if pane is not None:
        send_keys(pane, line)
        return "tmux"
    emitter = terminal_of(session, terminal)
    if emitter.type_text(session.tty, line):
        return emitter.name
    raise LookupError(f"{session.label} runs in {emitter.name}, which nothing can type into")


def open_session(launch: Launch, how: ResumeIn, name: str, terminal: Terminal | None = None) -> str:
    """Put a session where the settings say, saying where that was.

    The clipboard is the route that cannot fail for want of an application: it hands back
    the command for you to paste where you want it, which is the answer for a terminal
    neither tmux nor iTerm2 can be asked about. The other two spend the command themselves
    and leave the board where it is, which is what separates this from `enter`.

    What comes back is the place, phrased to follow whatever the caller did: the same three
    routes carry a session being resumed and a session being forked, and only the caller
    knows which of those it asked for.

    Raises:
        LookupError: the chosen application declined, or is not running.
        OSError: the command could not be handed over.
    """
    if how is ResumeIn.TMUX:
        return f"in tmux {open_window(launch, name)}"
    if how is ResumeIn.ITERM2:
        emitter = terminal or ITerm2Terminal()
        if emitter.open_tab(launch.shell_command):
            return f"in a new {emitter.name} tab"
        raise LookupError(f"{emitter.name} did not open a tab for it")
    if copy_to_pasteboard(launch.shell_command):
        return "as a command on the clipboard"
    raise OSError("the clipboard would not take the command")


def tmux_pane(tty: Path) -> str | None:
    """The tmux pane drawn on ``tty``, or ``None`` where tmux has none.

    Every pane on the machine is listed rather than the ones in some session, because the
    board adopts sessions it did not start: the pane wanted may be in a tmux session
    nobody has attached since the reboot, which `list-panes` without `-a` never reaches.

    No tmux, no server running, and a tty tmux does not own all answer the same way. Each
    of them means the same thing to a caller, which is that this route is not the one.
    """
    try:
        result = subprocess.run(  # noqa: S603
            [TMUX_BINARY, "list-panes", "-a", "-F", PANE_FORMAT],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=TMUX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        pane_tty, _, pane = line.partition("\t")
        if pane_tty == str(tty) and pane:
            return pane
    return None


def send_keys(pane: str, line: str) -> None:
    """Type a line into a tmux pane and press return.

    The text goes in literally and the return follows as a key of its own, which is the
    pair that survives a line beginning with a slash: keys named on the command line are
    looked up by name, so `/compact` sent as a key would be hunted for in the key table
    and dropped when it was not found.

    Raises:
        OSError: tmux refused the keys or could not be run.
    """
    _tmux("send-keys", "-t", pane, "-l", "--", line)
    _tmux("send-keys", "-t", pane, "Enter")


def open_window(launch: Launch, name: str, environ: Mapping[str, str] | None = None) -> str:
    """Run a launch under tmux, saying what it was put in.

    A board already inside tmux gets a window in the server it is running in, which is one
    prefix key away from where you were. A board outside tmux gets a detached session
    instead: there is no client here to switch, and a session that outlives the board is
    the one still there to attach to afterwards.

    The launch's environment is passed to tmux rather than left to be inherited. A tmux
    server outlives the shells that talk to it and keeps the environment it was first
    started with, so a `CLAUDE_CONFIG_DIR` set in this shell reaches the new pane only by
    being said out loud.

    Raises:
        OSError: tmux refused the command or could not be run.
    """
    label = name.translate(UNNAMEABLE)
    passed = [word for variable, value in launch.env for word in ("-e", f"{variable}={value}")]
    place = (environ if environ is not None else os.environ).get(TMUX_VAR)
    if place:
        _tmux("new-window", "-n", label, "-c", str(launch.directory), *passed, "--", *launch.argv)
        return f"window {label}"
    _tmux("new-session", "-d", "-s", label, "-c", str(launch.directory), *passed, "--", *launch.argv)
    return f"session {label}, waiting to be attached"


def _tmux(*arguments: str) -> None:
    """Run one tmux command, turning its refusal into an error naming what it said."""
    try:
        result = subprocess.run(  # noqa: S603
            [TMUX_BINARY, *arguments],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
            timeout=TMUX_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise OSError(f"tmux could not be run: {error}") from error
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f"tmux exited {result.returncode}")
