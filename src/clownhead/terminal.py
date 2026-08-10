"""Terminal capability detection and OSC escape sequence emission.

Attention is delivered by writing OSC sequences to a session's controlling TTY. The
terminal emulator consumes those sequences before the running application sees them, so
they are safe to inject into a live TUI. Plain text is not — it would land in the
session's input stream.

Capabilities are per-emulator. The base class implements the portable subset (bell and
title); richer behaviour lives in subclasses so Linux emulators can be added without
touching callers.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ESC = "\033"
BEL = "\a"


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
    supports_attention = False
    supports_tab_color = False
    supports_notifications = False

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

    def request_attention(self, tty: Path) -> None:
        """Ask the emulator to make itself noticed."""
        self.bell(tty)

    def set_tab_color(self, tty: Path, color: Rgb) -> None:
        """Tint the tab; a bell where tab colours are unsupported."""
        self.bell(tty)

    def reset_tab_color(self, tty: Path) -> None:
        """Restore the default tab colour; a no-op where unsupported."""

    def notify(self, tty: Path, message: str) -> None:
        """Raise a desktop notification; a bell where unsupported."""
        self.bell(tty)


class ITerm2Terminal(Terminal):
    """iTerm2, which supports the full proprietary OSC 1337 and OSC 6 vocabulary."""

    name = "iterm2"
    supports_attention = True
    supports_tab_color = True
    supports_notifications = True

    def request_attention(self, tty: Path) -> None:
        """Bounce the dock icon and flash the tab."""
        self.write(tty, f"{ESC}]1337;RequestAttention=yes{BEL}")

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
    supports_attention = True
    supports_notifications = True

    def notify(self, tty: Path, message: str) -> None:
        """Post a notification through OSC 99."""
        self.write(tty, f"{ESC}]99;;{message}{ESC}\\")

    def request_attention(self, tty: Path) -> None:
        """Kitty surfaces the bell as an urgency hint on the window."""
        self.bell(tty)


TERMINALS: dict[str, type[Terminal]] = {
    "iTerm.app": ITerm2Terminal,
    "kitty": KittyTerminal,
}


def detect_terminal(env: Mapping[str, str] | None = None) -> Terminal:
    """Pick the richest terminal implementation matching the environment."""
    environ = env if env is not None else os.environ
    program = environ.get("TERM_PROGRAM", "")
    if program in TERMINALS:
        return TERMINALS[program]()
    if "kitty" in environ.get("TERM", ""):
        return KittyTerminal()
    return Terminal()
