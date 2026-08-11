"""Terminal capability detection and OSC escape sequence emission.

Attention is delivered by writing OSC sequences to a session's controlling TTY. The
terminal emulator consumes those sequences before the running application sees them, so
they are safe to inject into a live TUI. Plain text is not — it would land in the
session's input stream.

Capabilities are per-emulator. The base class implements the portable subset (bell and
title); richer behaviour lives in subclasses so Linux emulators can be added without
touching callers.

Raising the emulator above other applications is the one signal no portable escape code
covers. iTerm2 has ``StealFocus``; everything else on macOS is asked through ``open``,
which activates a running application without opening a window.

Which implementation to use is a per-session question, not a per-machine one: a fleet
spans several terminals at once, so the application owning a session is resolved from
its process tree by :func:`terminal_for`. Reading it out of clownhead's own environment,
as :func:`detect_terminal` does, only describes the terminal it happens to run in.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ESC = "\033"
BEL = "\a"

ATTENTION_MARK = "\N{WARNING SIGN}"
BUNDLE_ID_VAR = "__CFBundleIdentifier"
OPEN_BINARY = "/usr/bin/open"
PBCOPY_BINARY = "/usr/bin/pbcopy"
ACTIVATION_TIMEOUT = 5.0


@dataclass(frozen=True)
class Rgb:
    """An 8-bit-per-channel colour."""

    red: int
    green: int
    blue: int


class Terminal:
    """A terminal emulator signalled by writing to a session's TTY.

    The base implementation assumes only the portable OSC subset. Unsupported operations
    degrade to a bell rather than raising, so callers can treat every terminal uniformly
    and consult the ``supports_*`` flags when they need to explain the difference.
    """

    name = "generic"
    bundle_id: str | None = None
    supports_attention = False
    supports_tab_color = False
    supports_notifications = False
    supports_foreground = False

    def __init__(self, bundle_id: str | None = None, app: Path | None = None, name: str | None = None) -> None:
        self.bundle_id = bundle_id or type(self).bundle_id
        self.app = app
        self.name = name or type(self).name
        self.supports_foreground = on_macos() and (self.app is not None or self.bundle_id is not None)

    def write(self, tty: Path, sequence: str) -> None:
        """Write a raw escape sequence to a TTY device."""
        with tty.open("w") as handle:
            handle.write(sequence)

    def bell(self, tty: Path) -> None:
        """Ring the terminal bell."""
        self.write(tty, BEL)

    def set_title(self, tty: Path, title: str) -> None:
        """Set the window or tab title."""
        self.write(tty, f"{ESC}]0;{title}{BEL}")

    def request_attention(self, tty: Path, text: str | None = None) -> None:
        """Ask the emulator to make itself noticed.

        With no attention escape code to fall back on, the title is marked as well: an
        emulator that ignores everything else still renames its tab, which is the only
        way a session buried among a dozen tabs can be picked out once its window is up.
        """
        self.bell(tty)
        if text is not None:
            self.set_title(tty, f"{ATTENTION_MARK} {text}")

    def foreground(self, tty: Path) -> None:
        """Raise the emulator above other applications; a no-op where unsupported."""
        target = self.app or self.bundle_id
        if target is not None and on_macos():
            raise_application(target)

    def set_tab_color(self, tty: Path, color: Rgb) -> None:
        """Tint the tab; a no-op where tab colours are unsupported."""

    def reset_tab_color(self, tty: Path) -> None:
        """Restore the default tab colour; a no-op where unsupported."""

    def notify(self, tty: Path, message: str) -> None:
        """Raise a desktop notification; a bell where unsupported."""
        self.bell(tty)


class ITerm2Terminal(Terminal):
    """iTerm2, which supports the full proprietary OSC 1337 and OSC 6 vocabulary."""

    name = "iterm2"
    bundle_id = "com.googlecode.iterm2"
    supports_attention = True
    supports_tab_color = True
    supports_notifications = True

    def request_attention(self, tty: Path, text: str | None = None) -> None:
        """Bounce the dock icon and flash the tab, leaving the title alone."""
        self.write(tty, f"{ESC}]1337;RequestAttention=yes{BEL}")

    def foreground(self, tty: Path) -> None:
        """Bring iTerm2 to the front, without leaving the TTY."""
        self.write(tty, f"{ESC}]1337;StealFocus{BEL}")

    def set_tab_color(self, tty: Path, color: Rgb) -> None:
        """Tint the tab with an RGB colour."""
        channels = (("red", color.red), ("green", color.green), ("blue", color.blue))
        self.write(tty, "".join(f"{ESC}]6;1;bg;{name};brightness;{value}{BEL}" for name, value in channels))

    def reset_tab_color(self, tty: Path) -> None:
        """Restore the tab to its default colour."""
        self.write(tty, f"{ESC}]6;1;bg;*;default{BEL}")

    def notify(self, tty: Path, message: str) -> None:
        """Post a notification through the emulator."""
        self.write(tty, f"{ESC}]9;{message}{BEL}")


class KittyTerminal(Terminal):
    """Kitty, which supports desktop notifications but not tab tinting via OSC."""

    name = "kitty"
    bundle_id = "net.kovidgoyal.kitty"
    supports_attention = True
    supports_notifications = True

    def notify(self, tty: Path, message: str) -> None:
        """Post a notification through OSC 99."""
        self.write(tty, f"{ESC}]99;;{message}{ESC}\\")

    def request_attention(self, tty: Path, text: str | None = None) -> None:
        """Kitty surfaces the bell as an urgency hint on the window."""
        self.bell(tty)


TERMINALS: dict[str, type[Terminal]] = {
    "iTerm.app": ITerm2Terminal,
    "kitty": KittyTerminal,
}

TERMINALS_BY_BUNDLE: dict[str, type[Terminal]] = {
    "com.googlecode.iterm2": ITerm2Terminal,
    "net.kovidgoyal.kitty": KittyTerminal,
}


def terminal_for(app: Path | None, env: Mapping[str, str] | None = None) -> Terminal:
    """The terminal implementation for the application that owns a session.

    An application clownhead has no escape codes for — an IDE with an embedded terminal,
    say — still gets the portable subset plus a foreground switch, which is the part that
    matters and the part no escape code would have delivered anyway.
    """
    if app is None:
        return detect_terminal(env)
    bundle_id = bundle_id_of(app)
    implementation = TERMINALS_BY_BUNDLE.get(bundle_id or "")
    if implementation is not None:
        return implementation(bundle_id, app=app)
    return Terminal(bundle_id, app=app, name=app.stem.lower())


@lru_cache(maxsize=64)
def bundle_id_of(app: Path) -> str | None:
    """Read an application bundle's identifier out of its ``Info.plist``."""
    try:
        info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return None
    identifier = info.get("CFBundleIdentifier")
    return identifier if isinstance(identifier, str) else None


def detect_terminal(env: Mapping[str, str] | None = None) -> Terminal:
    """Pick the richest terminal implementation matching the environment.

    macOS stamps the owning application's bundle id into the environment of every
    process it launches, which is how an unrecognised emulator still gets raised.
    """
    environ = env if env is not None else os.environ
    bundle_id = environ.get(BUNDLE_ID_VAR)
    program = environ.get("TERM_PROGRAM", "")
    if program in TERMINALS:
        return TERMINALS[program](bundle_id)
    if "kitty" in environ.get("TERM", ""):
        return KittyTerminal(bundle_id)
    return Terminal(bundle_id)


def raise_application(target: Path | str) -> None:
    """Bring a macOS application to the front without opening a window.

    A bundle path is preferred over a bundle id where one is known: it names the exact
    copy the session is running under, which a LaunchServices lookup does not when
    several versions of the same application are installed.
    """
    flag = "-a" if isinstance(target, Path) else "-b"
    subprocess.run(  # noqa: S603
        [OPEN_BINARY, flag, str(target)],
        capture_output=True,
        check=False,
        timeout=ACTIVATION_TIMEOUT,
    )


def copy_to_pasteboard(text: str) -> bool:
    """Put text on the macOS pasteboard, reporting whether it landed.

    The terminal's own OSC 52 clipboard is refused by default in some emulators, so on
    macOS the pasteboard is written directly as well.
    """
    if not on_macos():
        return False
    try:
        subprocess.run(  # noqa: S603
            [PBCOPY_BINARY],
            input=text,
            text=True,
            check=True,
            timeout=ACTIVATION_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def on_macos() -> bool:
    """Whether this host can raise applications by bundle id."""
    return sys.platform == "darwin"
