"""Focus a GNOME Terminal tab through its native D-Bus search provider.

GNOME Terminal gives its children a service name and a screen object path. Reading those
from a process on the target TTY identifies the tab even when its title changes or it is
moved to another window. The search provider's ActivateResult selects that screen and
presents its window together, without sending any keyboard input to the session.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from clownhead.jetbrains import Selection

SERVICE_VAR = "GNOME_TERMINAL_SERVICE"
SCREEN_VAR = "GNOME_TERMINAL_SCREEN"
SCREEN_PREFIX = "/org/gnome/Terminal/screen/"
SEARCH_PATH = "/org/gnome/Terminal/SearchProvider"
SEARCH_INTERFACE = "org.gnome.Shell.SearchProvider2"
TIMEOUT = 5.0
PROC_ROOT = Path("/proc")
SERVICE_NAME = re.compile(r":\d+\.\d+|org\.gnome\.Terminal(?:\.[A-Za-z0-9_-]+)*")


@dataclass(frozen=True)
class Target:
    """The running terminal service and the UUID of one of its tabs."""

    service: str
    screen: str


def target_from_env(env: Mapping[str, str]) -> Target | None:
    """Read a valid GNOME Terminal tab identity from a process environment."""
    service = env.get(SERVICE_VAR, "")
    screen = env.get(SCREEN_VAR, "")
    if SERVICE_NAME.fullmatch(service) is None or not screen.startswith(SCREEN_PREFIX):
        return None
    try:
        identifier = UUID(screen.removeprefix(SCREEN_PREFIX).replace("_", "-"))
    except ValueError:
        return None
    return Target(service, str(identifier))


def target_for(tty: Path) -> Target | None:
    """Find the tab identity on the requested TTY, never in the caller's environment.

    Standard input can be redirected, so the controlling TTY reported by ps is used
    instead of a process's file descriptors. Only a direct child of GNOME Terminal is
    trusted: another emulator or tmux can inherit the variables while creating a different
    TTY. Unreadable or exited processes are skipped; conflicting identities are declined.
    """
    result = subprocess.run(  # noqa: S603
        ["ps", "-t", str(tty), "-o", "pid=,ppid="],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        timeout=TIMEOUT,
    )
    if result.returncode:
        return None
    targets: set[Target] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2 or not all(field.isdecimal() for field in fields):
            continue
        pid, parent = fields
        try:
            if (PROC_ROOT / parent / "exe").readlink().name != "gnome-terminal-server":
                continue
            contents = (PROC_ROOT / pid / "environ").read_bytes()
        except OSError:
            continue
        env = {}
        for entry in contents.split(b"\0"):
            key, _, value = entry.partition(b"=")
            if key in (SERVICE_VAR.encode(), SCREEN_VAR.encode()):
                env[key.decode()] = value.decode(errors="replace")
        target = target_from_env(env)
        if target is not None:
            targets.add(target)
    return next(iter(targets)) if len(targets) == 1 else None


def select(tty: Path) -> Selection:
    """Select the tab on ``tty`` and raise its window, reporting an unavailable target."""
    binary = shutil.which("gdbus")
    if binary is None:
        return Selection(False, "GNOME Terminal focus requires gdbus")
    try:
        target = target_for(tty)
        if target is None:
            return Selection(False, "no unique GNOME Terminal tab owns that tty")
        # ActivateResult silently accepts a UUID that has already closed. Check the
        # live screen list first so a stale identity is not reported as a focus.
        # GVariant prints an explicit array type when there are no strings to infer it from.
        answer = ast.literal_eval(_call(binary, target, "GetInitialResultSet", "[]").replace("@as ", ""))
        if not isinstance(answer, tuple) or len(answer) != 1 or not isinstance(answer[0], list):
            return Selection(False, "GNOME Terminal returned an invalid tab list")
        if target.screen not in answer[0]:
            return Selection(False, "GNOME Terminal tab has closed")
        _call(binary, target, "ActivateResult", target.screen, "[]", "0")
    except subprocess.CalledProcessError as error:
        reason = error.stderr.strip() if isinstance(error.stderr, str) and error.stderr.strip() else str(error)
        return Selection(False, f"GNOME Terminal focus failed: {reason}")
    except (OSError, subprocess.SubprocessError, ValueError, SyntaxError) as error:
        return Selection(False, f"GNOME Terminal focus failed: {error}")
    return Selection(True)


def _call(binary: str, target: Target, method: str, *arguments: str) -> str:
    result = subprocess.run(  # noqa: S603
        [
            binary,
            "call",
            "--session",
            "--dest",
            target.service,
            "--object-path",
            SEARCH_PATH,
            "--method",
            f"{SEARCH_INTERFACE}.{method}",
            *arguments,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=TIMEOUT,
    )
    return result.stdout
