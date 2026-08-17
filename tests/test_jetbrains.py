import json
import subprocess

import pytest

from clownhead import jetbrains as jetbrains_module
from clownhead.jetbrains import INVENTORY, PRESS_BUTTON, RAISE_WINDOW, AccessibilityDenied, Tab, select

BUNDLE = "com.jetbrains.pycharm"
TITLE = "⚠ etl-94: input needed"


def label(name: str, window: int = 1, left: float = 500, width: float = 140) -> dict:
    return {"name": name, "window": window, "left": left, "top": 505, "width": width, "height": 30}


@pytest.fixture
def ide(monkeypatch):
    """A fake IDE: scripted answers per script, a record of the calls and of every click."""

    class Fake:
        def __init__(self):
            self.inventories: list[list[dict]] = []
            self.presses: list[str] = []
            self.raises: list[str] = []
            self.clicks: list[tuple[float, float]] = []
            self.pressable = True

        def script(self, source: str, *arguments: str) -> str:
            if source is INVENTORY:
                return json.dumps(self.inventories.pop(0))
            if source is PRESS_BUTTON:
                self.presses.append(arguments[1])
                return "pressed" if self.pressable else ""
            if source is RAISE_WINDOW:
                self.raises.append(arguments[1])
                return ""
            raise AssertionError(f"unexpected script {source}")

    fake = Fake()
    monkeypatch.setattr(jetbrains_module, "_script", fake.script)
    monkeypatch.setattr(jetbrains_module, "_click", fake.clicks.append)
    return fake


def test_select_clicks_the_tab_wearing_the_title(ide):
    ide.inventories = [[label("Terminal", left=433, width=79), label(TITLE, left=512)]]

    assert select(BUNDLE, TITLE) == jetbrains_module.Selection(True)
    assert ide.clicks == [(547.0, 520.0)]


def test_select_clicks_left_of_centre_to_miss_the_close_button(ide):
    ide.inventories = [[label(TITLE, left=100, width=200)]]

    select(BUNDLE, TITLE)

    assert ide.clicks == [(150.0, 520.0)]


def test_select_matches_a_title_the_strip_had_to_elide(ide):
    ide.inventories = [[label("⚠ etl-94: inp…needed")]]

    assert select(BUNDLE, TITLE).selected
    assert ide.clicks


def test_select_ignores_an_elision_that_does_not_fit_the_title(ide):
    ide.inventories = [[label("⚠ etl-95: inp…needed")]] * 2

    assert select(BUNDLE, TITLE) == jetbrains_module.Selection(False, "no tab is titled that")
    assert ide.clicks == []


def test_select_reports_a_title_no_tab_is_wearing(ide):
    ide.inventories = [[label("Local")]] * 2

    assert select(BUNDLE, TITLE) == jetbrains_module.Selection(False, "no tab is titled that")
    assert ide.clicks == []


def test_select_skips_a_tab_drawn_nowhere(ide):
    ide.inventories = [[label(TITLE, left=0, width=0)]] * 2

    assert not select(BUNDLE, TITLE).selected
    assert ide.clicks == []


def test_select_shows_a_collapsed_tool_window_and_looks_again(ide):
    ide.inventories = [[label("Project", left=0, width=0)], [label(TITLE)]]

    assert select(BUNDLE, TITLE).selected
    assert ide.presses == ["Terminal"]
    assert ide.clicks == [(535.0, 520.0)]


def test_select_puts_a_tool_window_back_when_the_tab_was_not_there_either(ide):
    ide.inventories = [[label("Project")], [label("Project")]]

    assert not select(BUNDLE, TITLE).selected
    assert ide.presses == ["Terminal", "Terminal"]
    assert ide.clicks == []


def test_select_leaves_a_stripe_it_could_not_press_alone(ide):
    ide.inventories = [[label("Project")]]
    ide.pressable = False

    assert not select(BUNDLE, TITLE).selected
    assert ide.presses == ["Terminal"]


def test_select_raises_a_background_window_before_clicking_the_rectangle_it_read(ide):
    ide.inventories = [[label(TITLE, window=3)]]

    assert select(BUNDLE, TITLE).selected
    assert ide.raises == ["3"]
    assert ide.clicks == [(535.0, 520.0)]


def test_select_does_not_raise_the_window_already_in_front(ide):
    ide.inventories = [[label(TITLE)]]

    select(BUNDLE, TITLE)

    assert ide.raises == []


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (AccessibilityDenied("osascript is not allowed assistive access. (-25211)"), "needs accessibility access"),
        (OSError("System Events got an error"), "System Events got an error"),
    ],
)
def test_select_reports_why_the_accessibility_api_could_not_answer(monkeypatch, error, reason):
    def refused(source: str, *arguments: str) -> str:
        raise error

    monkeypatch.setattr(jetbrains_module, "_script", refused)

    assert select(BUNDLE, TITLE) == jetbrains_module.Selection(False, reason)


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("System Events got an error: osascript is not allowed assistive access. (-25211)", AccessibilityDenied),
        ("syntax error", OSError),
    ],
)
def test_script_tells_a_refused_permission_from_any_other_failure(monkeypatch, stderr, expected):
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)
    monkeypatch.setattr(jetbrains_module.subprocess, "run", lambda argv, **kwargs: completed)

    with pytest.raises(expected):
        jetbrains_module._script(INVENTORY, BUNDLE)


def test_script_hands_back_what_the_accessibility_api_printed(monkeypatch):
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
    calls: list[list[str]] = []

    def run(argv, **kwargs):
        calls.append(argv)
        return completed

    monkeypatch.setattr(jetbrains_module.subprocess, "run", run)

    assert jetbrains_module._script(PRESS_BUTTON, BUNDLE, "Terminal") == "[]"
    assert calls == [["/usr/bin/osascript", "-l", "JavaScript", "-e", PRESS_BUTTON, BUNDLE, "Terminal"]]


def test_find_reads_an_empty_answer_as_no_tabs(monkeypatch):
    monkeypatch.setattr(jetbrains_module, "_script", lambda source, *arguments: "")

    assert jetbrains_module._find(BUNDLE, TITLE) is None


@pytest.mark.parametrize(
    ("width", "height", "reachable"),
    [(140, 30, True), (0, 30, False), (140, 0, False)],
)
def test_a_tab_is_reachable_only_when_it_is_drawn(width, height, reachable):
    assert Tab(TITLE, 1, 0, 0, width, height).reachable is reachable


class FakeQuartz:
    """The CoreGraphics calls a click is made of, recorded instead of posted."""

    def __init__(self):
        self.posted: list[int] = []
        self.released = 0
        self.warped: list[tuple[float, float]] = []
        self.here = jetbrains_module._CGPoint(11, 22)

    def CGEventCreate(self, source):  # noqa: N802
        return 1

    def CGEventGetLocation(self, event):  # noqa: N802
        return self.here

    def CGEventCreateMouseEvent(self, source, kind, point, button):  # noqa: N802
        self.posted.append(kind)
        return 2

    def CGEventPost(self, tap, event):  # noqa: N802
        pass

    def CGWarpMouseCursorPosition(self, point):  # noqa: N802
        self.warped.append((point.x, point.y))

    def CFRelease(self, event):  # noqa: N802
        self.released += 1


def test_click_presses_and_releases_then_puts_the_pointer_back(monkeypatch):
    quartz = FakeQuartz()
    monkeypatch.setattr(jetbrains_module, "_quartz", lambda: quartz)
    monkeypatch.setattr(jetbrains_module.time, "sleep", lambda seconds: None)

    jetbrains_module._click((300.0, 520.0))

    assert quartz.posted == [jetbrains_module.LEFT_MOUSE_DOWN, jetbrains_module.LEFT_MOUSE_UP]
    assert quartz.warped == [(11, 22)]
    assert quartz.released == 3


def test_quartz_reports_a_host_without_the_framework(monkeypatch):
    jetbrains_module._quartz.cache_clear()
    monkeypatch.setattr(jetbrains_module.ctypes.util, "find_library", lambda name: None)

    with pytest.raises(OSError, match="ApplicationServices"):
        jetbrains_module._quartz()

    jetbrains_module._quartz.cache_clear()
