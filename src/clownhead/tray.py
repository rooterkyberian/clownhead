"""Live agent counts for the macOS menu bar and Linux indicator panel."""

from __future__ import annotations

import importlib
import math
import multiprocessing
import os
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from multiprocessing.connection import Connection
from pathlib import Path
from threading import Event, Thread
from typing import Any

from clownhead.models import ATTENTION_STATES, Session, Status

type Loader = Callable[[], list[Session]]

MACOS_COLORS = {
    "!": (0xC15F3C, 0xC15F3C),
    "○": (0xB1ADA1, 0x68645D),
    "▶": (0x7EAA6E, 0x4A8238),
    "?": (0xB1ADA1, 0x68645D),
}
"""Dark and light colors from cctop's Claude palette."""
LINUX_SYMBOLS = {"!": "🟠", "○": "⚪", "▶": "🟢", "?": "?"}


class Unavailable(RuntimeError):
    """The desktop or its optional tray dependencies are unavailable."""


@dataclass(frozen=True)
class Counts:
    """Live sessions grouped into the tray's three activity states."""

    blocked: int = 0
    idle: int = 0
    busy: int = 0
    unknown: int = 0

    @classmethod
    def from_sessions(cls, sessions: Iterable[Session]) -> Counts:
        """Count attention states together and exclude finished sessions."""
        counts = Counter(session.status for session in sessions if not session.is_finished)
        return cls(
            blocked=sum(counts[state] for state in ATTENTION_STATES),
            idle=counts[Status.IDLE],
            busy=counts[Status.BUSY] + counts[Status.SHELL],
            unknown=counts[Status.UNKNOWN],
        )

    @property
    def counters(self) -> tuple[tuple[str, int], ...]:
        """Nonzero counters in blocked, idle, busy, unknown order."""
        return tuple(
            (symbol, count)
            for symbol, count in (("!", self.blocked), ("○", self.idle), ("▶", self.busy), ("?", self.unknown))
            if count
        )

    @property
    def title(self) -> str:
        """Compact counters in blocked, idle, busy order."""
        return " ".join(f"{symbol}{count}" for symbol, count in self.counters) or "🤡"

    @property
    def summary(self) -> str:
        """The meaning of each visible counter."""
        text = f"Blocked: {self.blocked}   Idle: {self.idle}   Busy: {self.busy}"
        return f"{text}   Unknown: {self.unknown}" if self.unknown else text


@dataclass(frozen=True)
class Snapshot:
    """A reading, or the reason counters cannot currently be shown."""

    counts: Counts | None = None
    problem: str = "Reading sessions…"

    @property
    def title(self) -> str:
        """Use question marks until discovery has produced a valid reading."""
        return self.counts.title if self.counts is not None else "?"

    @property
    def linux_title(self) -> str:
        """Colored status symbols for panels whose labels accept plain text."""
        if self.counts is None or not self.counts.counters:
            return self.title
        return " ".join(f"{LINUX_SYMBOLS[symbol]}{count}" for symbol, count in self.counts.counters)

    @property
    def summary(self) -> str:
        """Counts or the current discovery problem for the menu."""
        return self.counts.summary if self.counts is not None else self.problem


class Monitor:
    """Poll serially on a worker so discovery never blocks the desktop event loop."""

    def __init__(self, loader: Loader, interval: float) -> None:
        self.loader = loader
        self.interval = interval
        self.snapshot = Snapshot()
        self._stop = Event()
        self._refresh = Event()
        self._worker = Thread(target=self._poll, name="clownhead-tray", daemon=True)

    def start(self) -> None:
        """Read immediately and keep refreshing until stopped."""
        self._worker.start()

    def refresh(self) -> None:
        """Request a reading without overlapping a read already in progress."""
        self._refresh.set()

    def close(self) -> None:
        """Wake the worker for shutdown without waiting for a discovery timeout."""
        self._stop.set()
        self._refresh.set()

    @property
    def closed(self) -> bool:
        """Whether the owning application has stopped the monitor."""
        return self._stop.is_set()

    quit_label = "Quit clownhead"
    can_focus = False

    def focus(self) -> None:
        """Focus the associated TUI, when this monitor belongs to one."""

    def read(self) -> None:
        """Replace the whole snapshot, recovering on the next poll after errors."""
        try:
            snapshot = Snapshot(Counts.from_sessions(self.loader()))
        except Exception as error:
            snapshot = Snapshot(problem=f"Discovery failed: {error}")
        self.snapshot = snapshot

    def _poll(self) -> None:
        while not self._stop.is_set():
            self._refresh.clear()
            self.read()
            self._refresh.wait(self.interval)


class Companion:
    """A native tray process receiving the TUI's discovery results."""

    def __init__(self) -> None:
        context = multiprocessing.get_context("spawn")
        self._connection, self._child = context.Pipe()
        self._process = context.Process(target=_run_companion, args=(self._child,), daemon=True)
        self._started = False
        self._closed = False

    def start(self) -> None:
        """Start a fresh interpreter with its own GUI event loop."""
        try:
            self._process.start()
            self._started = True
        finally:
            self._child.close()

    def publish(self, snapshot: Snapshot) -> None:
        """Send a reading from the TUI's discovery worker."""
        if self._closed:
            return
        with suppress(OSError):
            self._connection.send(snapshot)

    def messages(self) -> list[str]:
        """Read focus requests and startup failures without blocking the TUI."""
        messages: list[str] = []
        if self._closed:
            return messages
        try:
            while self._connection.poll():
                message = self._connection.recv()
                if isinstance(message, str):
                    messages.append(message)
        except (EOFError, OSError):
            self.close()
        return messages

    def close(self) -> None:
        """Close the pipe and reap the tray process, terminating it if necessary."""
        if self._closed:
            return
        self._closed = True
        self._connection.close()
        self._child.close()
        if self._started:
            self._process.join(timeout=1)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1)
            if self._process.is_alive():
                self._process.kill()
                self._process.join()
        self._process.close()


class _IncomingMonitor(Monitor):
    quit_label = "Close tray"
    can_focus = True

    def __init__(self, connection: Connection) -> None:
        super().__init__(lambda: [], 5)
        self._connection = connection

    def focus(self) -> None:
        """Ask the TUI to focus its terminal session."""
        try:
            self._connection.send("focus")
        except OSError:
            self.close()

    def _poll(self) -> None:
        try:
            while not self.closed:
                if self._connection.poll(0.25):
                    snapshot = self._connection.recv()
                    if isinstance(snapshot, Snapshot):
                        self.snapshot = snapshot
        except (EOFError, OSError):
            self.close()


def run(*, loader: Loader, interval: float) -> None:
    """Run the native desktop event loop on the calling main thread."""
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("Refresh interval must be a finite number greater than zero.")
    _run_monitor(Monitor(loader, interval))


def _run_companion(connection: Connection) -> None:
    with Path(os.devnull).open("w") as output:
        os.dup2(output.fileno(), 1)
        os.dup2(output.fileno(), 2)
    try:
        _run_monitor(_IncomingMonitor(connection))
    except Exception as error:
        with suppress(OSError):
            connection.send(f"Tray unavailable: {error}")
    finally:
        connection.close()


def _run_monitor(monitor: Monitor) -> None:
    if sys.platform == "darwin":
        backend = _run_macos
    elif sys.platform == "linux":
        backend = _run_linux
    else:
        raise Unavailable("The tray supports macOS and Linux.")
    try:
        backend(monitor)
    finally:
        monitor.close()


def _run_macos(monitor: Monitor) -> None:
    try:
        rumps = importlib.import_module("rumps")
        appkit = importlib.import_module("AppKit")
    except ImportError as error:
        raise Unavailable('Install macOS tray support with: uv tool install --force "clownhead[tray]"') from error
    appkit.NSApplication.sharedApplication().setActivationPolicy_(appkit.NSApplicationActivationPolicyAccessory)
    app = rumps.App("clownhead", title=monitor.snapshot.title, quit_button=None)
    summary = rumps.MenuItem(monitor.snapshot.summary)

    def quit_app(_: Any) -> None:
        monitor.close()
        rumps.quit_application()

    items = [summary, None]
    if monitor.can_focus:
        items.append(rumps.MenuItem("Focus TUI", callback=lambda _: monitor.focus()))
    items.append(rumps.MenuItem(monitor.quit_label, callback=quit_app))
    app.menu = items

    def clicked() -> None:
        event = appkit.NSApplication.sharedApplication().currentEvent()
        if event.type() == appkit.NSEventTypeRightMouseUp:
            app._nsapp.nsstatusitem.popUpStatusItemMenu_(app.menu._menu)
        else:
            monitor.focus()

    target = _macos_action_class(appkit).alloc().init() if monitor.can_focus else None
    if target is not None:
        target.callback = clicked

    def configure_click() -> None:
        if target is not None:
            item = app._nsapp.nsstatusitem
            item.setMenu_(None)
            button = item.button()
            button.setTarget_(target)
            button.setAction_("clicked:")
            button.sendActionOn_(appkit.NSEventMaskLeftMouseUp | appkit.NSEventMaskRightMouseUp)

    rumps.events.before_start.register(configure_click)

    def update(_: Any) -> None:
        if monitor.closed:
            rumps.quit_application()
            return
        snapshot = monitor.snapshot
        app.title = snapshot.title
        button = app._nsapp.nsstatusitem.button()
        appearance = button.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            [appkit.NSAppearanceNameDarkAqua, appkit.NSAppearanceNameAqua]
        )
        button.setAttributedTitle_(_macos_title(snapshot, appkit, dark=appearance == appkit.NSAppearanceNameDarkAqua))
        summary.title = snapshot.summary

    timer = rumps.Timer(update, 0.25)
    monitor.start()
    timer.start()
    try:
        app.run()
    finally:
        timer.stop()
        rumps.events.before_start.unregister(configure_click)


@lru_cache(maxsize=1)
def _macos_action_class(appkit: Any) -> Any:
    def clicked(self: Any, sender: Any) -> None:
        self.callback()

    return type("ClownheadTrayAction", (appkit.NSObject,), {"clicked_": clicked})


def _macos_title(snapshot: Snapshot, appkit: Any, *, dark: bool) -> Any:
    title = appkit.NSMutableAttributedString.alloc().initWithString_("")
    counters = snapshot.counts.counters if snapshot.counts is not None else ()
    colors = {symbol: pair[0 if dark else 1] for symbol, pair in MACOS_COLORS.items()}
    parts = [(f"{symbol}{count}", colors[symbol]) for symbol, count in counters]
    for index, (text, rgb) in enumerate(parts or [(snapshot.title, colors["?"])]):
        color = appkit.NSColor.colorWithSRGBRed_green_blue_alpha_(
            ((rgb >> 16) & 0xFF) / 255, ((rgb >> 8) & 0xFF) / 255, (rgb & 0xFF) / 255, 1
        )
        attributes = {appkit.NSForegroundColorAttributeName: color}
        part = appkit.NSAttributedString.alloc().initWithString_attributes_(f"{' ' if index else ''}{text}", attributes)
        title.appendAttributedString_(part)
    return title


def _linux_modules() -> tuple[Any, Any, Any]:
    try:
        gi = importlib.import_module("gi")
        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            indicator = importlib.import_module("gi.repository.AyatanaAppIndicator3")
        except (ImportError, ValueError):
            gi.require_version("AppIndicator3", "0.1")
            indicator = importlib.import_module("gi.repository.AppIndicator3")
        return importlib.import_module("gi.repository.Gtk"), importlib.import_module("gi.repository.GLib"), indicator
    except (ImportError, ValueError) as error:
        raise Unavailable(
            "Linux tray support needs PyGObject, GTK 3 and Ayatana AppIndicator 3. "
            "See the README's Tray section for system packages and installation."
        ) from error


def _run_linux(monitor: Monitor) -> None:
    gtk, glib, appindicator = _linux_modules()
    initialized, _ = gtk.init_check([])
    if not initialized:
        raise Unavailable("The tray needs a graphical Linux desktop session (X11 or Wayland).")
    indicator = appindicator.Indicator.new(
        "clownhead", "utilities-terminal", appindicator.IndicatorCategory.APPLICATION_STATUS
    )
    indicator.set_title("clownhead")
    menu = gtk.Menu()
    summary = gtk.MenuItem(label=monitor.snapshot.summary)
    summary.set_sensitive(False)
    menu.append(summary)
    menu.append(gtk.SeparatorMenuItem())
    focus_item = None
    if monitor.can_focus:
        focus_item = gtk.MenuItem(label="Focus TUI")
        focus_item.connect("activate", lambda _: monitor.focus())
        menu.append(focus_item)
        with suppress(TypeError):
            indicator.connect("activate", lambda *_: monitor.focus())
    quit_item = gtk.MenuItem(label=monitor.quit_label)
    quit_item.connect("activate", lambda _: gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)
    if focus_item is not None:
        indicator.set_secondary_activate_target(focus_item)
    indicator.set_status(appindicator.IndicatorStatus.ACTIVE)

    def update() -> bool:
        if monitor.closed:
            gtk.main_quit()
            return True
        snapshot = monitor.snapshot
        indicator.set_label(snapshot.linux_title, "")
        summary.set_label(snapshot.summary)
        return True

    update()
    monitor.start()
    timer = glib.timeout_add(250, update)
    try:
        gtk.main()
    finally:
        glib.source_remove(timer)
        indicator.set_status(appindicator.IndicatorStatus.PASSIVE)
