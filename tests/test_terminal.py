import plistlib
from pathlib import Path

import pytest

from clownhead import terminal as terminal_module
from clownhead.jetbrains import Selection
from clownhead.terminal import (
    ITerm2Terminal,
    JetBrainsTerminal,
    KittyTerminal,
    Rgb,
    Terminal,
    attention_title,
    bundle_id_of,
    copy_to_pasteboard,
    detect_terminal,
    own_tty,
    terminal_for,
)


class RecordingTerminal(Terminal):
    def __init__(self, bundle_id: str | None = None):
        super().__init__(bundle_id)
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


class RecordingITerm2(ITerm2Terminal):
    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


class RecordingJetBrains(JetBrainsTerminal):
    def __init__(self, bundle_id: str):
        super().__init__(bundle_id)
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


@pytest.fixture
def raised(monkeypatch) -> list[str]:
    activated: list[str] = []
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)
    monkeypatch.setattr(terminal_module, "raise_application", activated.append)
    return activated


TTY = Path("/dev/ttys004")


def test_generic_terminal_rings_the_bell():
    terminal = RecordingTerminal()

    terminal.request_attention(TTY)

    assert terminal.written == ["\a"]


def test_generic_terminal_marks_the_tab_it_cannot_flash():
    terminal = RecordingTerminal()

    terminal.request_attention(TTY, "etl-94: input needed")

    assert terminal.written == ["\a", "\033]0;⚠ etl-94: input needed\a"]


def test_iterm2_leaves_the_title_alone():
    terminal = RecordingITerm2()

    terminal.request_attention(TTY, "etl-94: input needed")

    assert terminal.written == ["\033]1337;RequestAttention=yes\a"]


def test_generic_terminal_reset_tab_color_is_a_noop():
    terminal = RecordingTerminal()

    terminal.reset_tab_color(TTY)

    assert terminal.written == []


def test_generic_terminal_set_tab_color_is_a_noop():
    terminal = RecordingTerminal()

    terminal.set_tab_color(TTY, Rgb(220, 50, 47))

    assert terminal.written == []


def test_generic_terminal_sets_title():
    terminal = RecordingTerminal()

    terminal.set_title(TTY, "hello")

    assert terminal.written == ["\033]0;hello\a"]


def test_iterm2_request_attention_uses_osc_1337():
    terminal = RecordingITerm2()

    terminal.request_attention(TTY)

    assert terminal.written == ["\033]1337;RequestAttention=yes\a"]


def test_iterm2_sets_all_three_colour_channels():
    terminal = RecordingITerm2()

    terminal.set_tab_color(TTY, Rgb(220, 50, 47))

    assert terminal.written == [
        "\033]6;1;bg;red;brightness;220\a\033]6;1;bg;green;brightness;50\a\033]6;1;bg;blue;brightness;47\a"
    ]


def test_iterm2_resets_tab_colour():
    terminal = RecordingITerm2()

    terminal.reset_tab_color(TTY)

    assert terminal.written == ["\033]6;1;bg;*;default\a"]


def test_iterm2_notification_uses_osc_9():
    terminal = RecordingITerm2()

    terminal.notify(TTY, "needs input")

    assert terminal.written == ["\033]9;needs input\a"]


def test_iterm2_foreground_uses_steal_focus():
    terminal = RecordingITerm2()

    terminal.foreground(TTY)

    assert terminal.written == ["\033]1337;StealFocus\a"]


def test_foreground_raises_the_owning_application_by_bundle_id(raised):
    terminal = RecordingTerminal(bundle_id="com.apple.Terminal")

    terminal.foreground(TTY)

    assert raised == ["com.apple.Terminal"]
    assert terminal.written == []


def test_foreground_is_a_noop_without_a_known_application(raised):
    RecordingTerminal().foreground(TTY)

    assert raised == []


def test_foreground_is_a_noop_off_macos(monkeypatch):
    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)
    monkeypatch.setattr(terminal_module, "raise_application", lambda bundle_id: pytest.fail("must not shell out"))

    RecordingTerminal(bundle_id="com.apple.Terminal").foreground(TTY)


def test_foreground_support_needs_macos_and_an_application(monkeypatch):
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)

    assert ITerm2Terminal().supports_foreground
    assert Terminal("com.mitchellh.ghostty").supports_foreground
    assert not Terminal().supports_foreground

    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)

    assert not ITerm2Terminal().supports_foreground


def test_detect_picks_up_the_owning_application(monkeypatch):
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)

    terminal = detect_terminal({"TERM_PROGRAM": "ghostty", "__CFBundleIdentifier": "com.mitchellh.ghostty"})

    assert terminal.bundle_id == "com.mitchellh.ghostty"
    assert terminal.supports_foreground


def test_detect_iterm2():
    assert isinstance(detect_terminal({"TERM_PROGRAM": "iTerm.app"}), ITerm2Terminal)


def test_detect_kitty_by_term_program():
    assert isinstance(detect_terminal({"TERM_PROGRAM": "kitty"}), KittyTerminal)


def test_detect_kitty_by_term():
    assert isinstance(detect_terminal({"TERM": "xterm-kitty"}), KittyTerminal)


def test_own_tty_reads_the_terminal_behind_the_standard_descriptors(monkeypatch):
    monkeypatch.setattr(terminal_module.os, "ttyname", {1: "/dev/ttys009"}.__getitem__)

    assert own_tty() == Path("/dev/ttys009")


def test_own_tty_falls_through_a_redirected_stream(monkeypatch):
    def ttyname(descriptor: int) -> str:
        if descriptor != 2:
            raise OSError("Inappropriate ioctl for device")
        return "/dev/ttys009"

    monkeypatch.setattr(terminal_module.os, "ttyname", ttyname)

    assert own_tty() == Path("/dev/ttys009")


def test_own_tty_of_a_board_with_no_terminal_at_all(monkeypatch):
    def ttyname(descriptor: int) -> str:
        raise OSError("Inappropriate ioctl for device")

    monkeypatch.setattr(terminal_module.os, "ttyname", ttyname)

    assert own_tty() is None


def test_detect_falls_back_to_generic():
    terminal = detect_terminal({"TERM_PROGRAM": "Apple_Terminal"})

    assert type(terminal) is Terminal
    assert not terminal.supports_tab_color


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("com.googlecode.iterm2", ["/usr/bin/open", "-b", "com.googlecode.iterm2"]),
        (Path("/Applications/iTerm.app"), ["/usr/bin/open", "-a", "/Applications/iTerm.app"]),
    ],
)
def test_raise_application_activates_without_opening_a_window(monkeypatch, target, expected):
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal_module.subprocess, "run", lambda argv, **kwargs: calls.append(argv))

    terminal_module.raise_application(target)

    assert calls == [expected]


def bundle(root: Path, name: str, identifier: str) -> Path:
    app = root / f"{name}.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}))
    return app


def test_terminal_for_recognises_the_owning_emulator(tmp_path):
    terminal = terminal_for(bundle(tmp_path, "iTerm", "com.googlecode.iterm2"))

    assert isinstance(terminal, ITerm2Terminal)
    assert terminal.name == "iterm2"


def test_terminal_for_raises_an_application_it_has_no_escape_codes_for(tmp_path, raised):
    app = bundle(tmp_path, "Ghostty", "com.mitchellh.ghostty")

    terminal = terminal_for(app)
    terminal.foreground(TTY)

    assert terminal.name == "ghostty"
    assert not terminal.supports_attention
    assert terminal.supports_foreground
    assert not terminal.supports_tab_focus
    assert raised == [app]


@pytest.mark.parametrize(
    ("name", "identifier"),
    [
        ("PyCharm", "com.jetbrains.pycharm"),
        ("GoLand", "com.jetbrains.goland"),
        ("Android Studio", "com.google.android.studio"),
    ],
)
def test_terminal_for_selects_tabs_in_an_ide_that_draws_its_own(tmp_path, raised, name, identifier):
    terminal = terminal_for(bundle(tmp_path, name, identifier))

    assert isinstance(terminal, JetBrainsTerminal)
    assert terminal.name == name.lower()
    assert terminal.supports_tab_focus
    assert not terminal.supports_attention


def test_an_ide_is_not_asked_for_tabs_off_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)

    terminal = terminal_for(bundle(tmp_path, "PyCharm", "com.jetbrains.pycharm"))

    assert not terminal.supports_tab_focus


def test_an_ide_marks_the_tab_then_looks_for_that_mark(monkeypatch):
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)
    monkeypatch.setattr(
        terminal_module.jetbrains,
        "select",
        lambda bundle_id, title: asked.append((bundle_id, title)) or Selection(True),
    )

    terminal = RecordingJetBrains("com.jetbrains.pycharm")
    outcome = terminal.select_tab(TTY, "etl-94: input needed")

    assert outcome == Selection(True)
    assert terminal.written == ["\033]0;⚠ etl-94: input needed\a"]
    assert asked == [("com.jetbrains.pycharm", "⚠ etl-94: input needed")]


def test_a_terminal_that_gives_every_tab_a_device_declines_to_select_one():
    assert ITerm2Terminal().select_tab(TTY, "etl-94: input needed") == Selection(False)


def test_attention_title_marks_the_text_it_is_given():
    assert attention_title("etl-94: input needed") == "⚠ etl-94: input needed"


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("com.jetbrains.pycharm", True),
        ("com.jetbrains.intellij.ce", True),
        ("com.google.android.studio", True),
        ("com.googlecode.iterm2", False),
        (None, False),
    ],
)
def test_which_applications_draw_their_own_tabs(identifier, expected):
    assert terminal_module.draws_its_own_tabs(identifier) is expected


def test_terminal_for_falls_back_to_the_local_environment(monkeypatch):
    monkeypatch.setattr(terminal_module, "detect_terminal", lambda env=None: KittyTerminal())

    assert isinstance(terminal_for(None), KittyTerminal)


def test_bundle_id_of_an_unreadable_bundle(tmp_path):
    assert bundle_id_of(tmp_path / "gone.app") is None


def test_copy_to_pasteboard_writes_to_the_system_clipboard(monkeypatch):
    calls: list[tuple[list[str], str]] = []
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)
    monkeypatch.setattr(terminal_module.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs["input"])))

    assert copy_to_pasteboard("hello")
    assert calls == [(["/usr/bin/pbcopy"], "hello")]


def test_copy_to_pasteboard_is_declined_off_macos(monkeypatch):
    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)

    assert copy_to_pasteboard("hello") is False


def test_write_reaches_the_device(tmp_path):
    target = tmp_path / "fake-tty"
    target.touch()

    Terminal().bell(target)

    assert target.read_text() == "\a"


def test_open_url_hands_a_link_to_the_macos_opener(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal_module, "on_macos", lambda: True)
    monkeypatch.setattr(terminal_module.subprocess, "run", lambda argv, **kwargs: calls.append(argv))

    assert terminal_module.open_url("https://github.com/acme/widgets/pull/7")
    assert calls == [["/usr/bin/open", "https://github.com/acme/widgets/pull/7"]]


def test_open_url_asks_xdg_open_everywhere_else(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)
    monkeypatch.setattr(terminal_module.subprocess, "run", lambda argv, **kwargs: calls.append(argv))

    assert terminal_module.open_url("https://example.com")
    assert calls == [["xdg-open", "https://example.com"]]


def test_open_url_reports_a_desktop_with_nothing_to_open_it(monkeypatch):
    def refuse(argv, **kwargs):
        raise FileNotFoundError(argv[0])

    monkeypatch.setattr(terminal_module, "on_macos", lambda: False)
    monkeypatch.setattr(terminal_module.subprocess, "run", refuse)

    assert terminal_module.open_url("https://example.com") is False
