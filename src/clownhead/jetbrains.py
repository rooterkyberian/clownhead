"""Selecting one terminal tab inside a JetBrains IDE.

A terminal emulator gives every tab a TTY of its own, so a tab is addressable: write an
escape code to the device and the emulator acts on that tab and no other. An IDE's terminal
tool window is not built that way. It draws the strip itself, and the pty behind a tab
carries nothing that reaches it — there is no escape code for "come to the front", which is
why a session buried in a tool window can only be found by eye without this.

The accessibility API is what is left, and a JetBrains IDE answers it almost well enough:
each tab is an ``AXStaticText`` named with the title the session last set, carrying the
rectangle it is drawn in. What no tab carries is a way to ask for the selection. They expose
no ``AXPress`` action; ``AXSelected`` and ``AXFocused`` are both reported settable and then
ignored; and System Events' own ``click at`` resolves the point to an element and presses it,
which for an element with no actions is nothing at all.

A real mouse event is the one thing the strip does answer, so that is what this module posts
— through the same CoreGraphics call hardware clicks arrive by — and the pointer is put back
where it was found. Reading the tree and posting the click are both gated on the Accessibility
grant held by whichever application clownhead is running in, and a missing grant is reported
rather than raised, because a tab that could not be reached is worth saying out loud and not
worth failing a focus over: the window is up either way.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache

OSASCRIPT = "/usr/bin/osascript"
AX_TIMEOUT = 5.0
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
DENIED_MARKERS = ("assistive access", "-25211", "-1743")
TERMINAL_TOOL_WINDOW = "Terminal"
FRONTMOST = 1
CLICK_INSET = 0.25
CLICK_GAP = 0.02

LEFT_MOUSE_DOWN, LEFT_MOUSE_UP = 1, 2
LEFT_BUTTON = 0
HID_EVENT_TAP = 0

INVENTORY = """
function run(argv) {
  const events = Application("System Events");
  const running = events.applicationProcesses.whose({bundleIdentifier: argv[0]})();
  const labels = [];
  for (const target of running) {
    const windows = target.windows();
    for (let index = 0; index < windows.length; index++) {
      let groups;
      try { groups = windows[index].groups[0].groups(); } catch (error) { continue; }
      for (const group of groups) {
        let names, positions, sizes;
        try {
          names = group.staticTexts.name();
          positions = group.staticTexts.position();
          sizes = group.staticTexts.size();
        } catch (error) { continue; }
        for (let at = 0; at < names.length; at++) {
          if (names[at] === null || positions[at] === null || sizes[at] === null) continue;
          labels.push({name: names[at], window: index + 1, left: positions[at][0], top: positions[at][1],
                       width: sizes[at][0], height: sizes[at][1]});
        }
      }
    }
  }
  return JSON.stringify(labels);
}
"""

PRESS_BUTTON = """
function run(argv) {
  const events = Application("System Events");
  const running = events.applicationProcesses.whose({bundleIdentifier: argv[0]})();
  for (const target of running) {
    for (const window of target.windows()) {
      let groups;
      try { groups = window.groups[0].groups(); } catch (error) { continue; }
      for (const group of groups) {
        let buttons;
        try { buttons = group.buttons(); } catch (error) { continue; }
        for (const button of buttons) {
          try {
            if (button.description() !== argv[1]) continue;
            button.actions.byName("AXPress").perform();
            return "pressed";
          } catch (error) { continue; }
        }
      }
    }
  }
  return "";
}
"""

RAISE_WINDOW = """
function run(argv) {
  const events = Application("System Events");
  const running = events.applicationProcesses.whose({bundleIdentifier: argv[0]})();
  for (const target of running) {
    const wanted = target.windows()[parseInt(argv[1], 10) - 1];
    if (wanted !== undefined) wanted.actions.byName("AXRaise").perform();
    return "";
  }
  return "";
}
"""


class AccessibilityDenied(RuntimeError):
    """The accessibility API refused, because this application was never granted it."""


@dataclass(frozen=True)
class Tab:
    """One labelled tab, as the accessibility tree reports it."""

    name: str
    window: int
    left: float
    top: float
    width: float
    height: float

    @property
    def reachable(self) -> bool:
        """Whether the tab is drawn at all.

        A tool window that is not showing leaves its labels in the tree with an empty
        rectangle at the origin, and clicking the origin would hit whatever is there.
        """
        return self.width > 0 and self.height > 0

    @property
    def spot(self) -> tuple[float, float]:
        """Where to click it — left of centre, well clear of the close button."""
        return self.left + self.width * CLICK_INSET, self.top + self.height / 2


@dataclass(frozen=True)
class Selection:
    """What came of asking for a tab; ``reason`` answers for the ones that were missed."""

    selected: bool
    reason: str = ""


def select(bundle_id: str, title: str) -> Selection:
    """Bring the terminal tab titled ``title`` to the front, in the IDE ``bundle_id``.

    The application has to be active already, which is the caller's business: a click posted
    at a window behind another application's is spent activating that window and never
    reaches the strip.

    Every failure is reported rather than raised. The caller has already raised the window,
    and a tab it could not select is a detail to pass on rather than grounds for calling the
    focus itself a failure.
    """
    try:
        tab = _locate(bundle_id, title)
        if tab is None:
            return Selection(False, "no tab is titled that")
        _click(tab.spot)
    except AccessibilityDenied:
        return Selection(False, "needs accessibility access")
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        return Selection(False, str(error))
    return Selection(True)


def _locate(bundle_id: str, title: str) -> Tab | None:
    """The tab titled ``title``, showing and raising whatever hides it.

    A miss is worth one press of the button that opens the terminal tool window, since a
    tool window that is not showing has no tabs in the tree to find. The press is undone
    when the tab still is not there, to leave a window this could not reach exactly as it
    was found rather than with its tool window flapping.

    Raising a background window reorders the windows but moves none of them, so the
    rectangle read before the raise is still where the tab is drawn.
    """
    tab = _find(bundle_id, title)
    if tab is None and _press(bundle_id, TERMINAL_TOOL_WINDOW):
        tab = _find(bundle_id, title)
        if tab is None:
            _press(bundle_id, TERMINAL_TOOL_WINDOW)
    if tab is not None and tab.window != FRONTMOST:
        _raise_window(bundle_id, tab.window)
    return tab


def _find(bundle_id: str, title: str) -> Tab | None:
    """The first tab drawn under that title, out of every label in the IDE's windows.

    Names, positions and sizes are read a collection at a time rather than an element at a
    time, because each property read is an Apple event and a window full of labels costs
    three of them per label the other way round.
    """
    labels = (Tab(**entry) for entry in json.loads(_script(INVENTORY, bundle_id) or "[]"))
    return next((tab for tab in labels if tab.reachable and _titled(tab.name, title)), None)


def _titled(name: str, title: str) -> bool:
    """Whether a label reads as ``title``, allowing for a strip that ran out of room.

    A crowded strip elides the middle of a label, so what can be matched is the head and
    the tail either side of the ellipsis rather than the whole of it.
    """
    if name == title:
        return True
    head, ellipsis, tail = name.partition(ELLIPSIS)
    return bool(ellipsis) and title.startswith(head) and title.endswith(tail)


def _press(bundle_id: str, description: str) -> bool:
    """Press a button by the name a screen reader would read out, reporting whether it was there."""
    return _script(PRESS_BUTTON, bundle_id, description).strip() == "pressed"


def _raise_window(bundle_id: str, window: int) -> None:
    """Bring one of the application's windows above its siblings."""
    _script(RAISE_WINDOW, bundle_id, str(window))


def _script(source: str, *arguments: str) -> str:
    """Run one JXA script over the accessibility API and hand back what it printed.

    Raises:
        AccessibilityDenied: this application holds no Accessibility grant.
        OSError: the script failed for any other reason.
    """
    result = subprocess.run(  # noqa: S603
        [OSASCRIPT, "-l", "JavaScript", "-e", source, *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=AX_TIMEOUT,
    )
    if result.returncode == 0:
        return result.stdout
    message = result.stderr.strip()
    if any(marker in message for marker in DENIED_MARKERS):
        raise AccessibilityDenied(message)
    raise OSError(message or f"osascript exited {result.returncode}")


class _CGPoint(ctypes.Structure):
    _fields_ = (("x", ctypes.c_double), ("y", ctypes.c_double))


def _click(spot: tuple[float, float]) -> None:
    """Post a left click at a point on screen, putting the pointer back afterwards.

    The click is posted where a hardware click would enter, so the application cannot tell
    it apart from one. The pointer does travel there — a posted mouse event carries the
    cursor with it — so it is warped home once the click has landed.
    """
    quartz = _quartz()
    point = _CGPoint(*spot)
    origin = _pointer(quartz)
    for kind in (LEFT_MOUSE_DOWN, LEFT_MOUSE_UP):
        event = quartz.CGEventCreateMouseEvent(None, kind, point, LEFT_BUTTON)
        quartz.CGEventPost(HID_EVENT_TAP, event)
        quartz.CFRelease(event)
        if kind == LEFT_MOUSE_DOWN:
            time.sleep(CLICK_GAP)
    quartz.CGWarpMouseCursorPosition(origin)


def _pointer(quartz: ctypes.CDLL) -> _CGPoint:
    """Where the pointer is now."""
    event = quartz.CGEventCreate(None)
    point: _CGPoint = quartz.CGEventGetLocation(event)
    quartz.CFRelease(event)
    return point


@lru_cache(maxsize=1)
def _quartz() -> ctypes.CDLL:
    """The CoreGraphics entry points, bound through the framework that exports them.

    Raises:
        OSError: the framework could not be loaded, which is every host but macOS.
    """
    path = ctypes.util.find_library("ApplicationServices")
    if path is None:
        raise OSError("ApplicationServices is not available on this host")
    library = ctypes.CDLL(path)
    library.CGEventCreate.restype = ctypes.c_void_p
    library.CGEventCreate.argtypes = [ctypes.c_void_p]
    library.CGEventCreateMouseEvent.restype = ctypes.c_void_p
    library.CGEventCreateMouseEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint32, _CGPoint, ctypes.c_uint32]
    library.CGEventGetLocation.restype = _CGPoint
    library.CGEventGetLocation.argtypes = [ctypes.c_void_p]
    library.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    library.CGWarpMouseCursorPosition.argtypes = [_CGPoint]
    library.CFRelease.argtypes = [ctypes.c_void_p]
    return library
