import plistlib
from pathlib import Path

import pytest

from clownhead import terminal as terminal_module
from clownhead.terminal import (
    ITerm2Terminal,
    KittyTerminal,
    Rgb,
    Terminal,
    bundle_id_of,
    copy_to_pasteboard,
    detect_terminal,
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
    app = bundle(tmp_path, "PyCharm", "com.jetbrains.pycharm")

    terminal = terminal_for(app)
    terminal.foreground(TTY)

    assert terminal.name == "pycharm"
    assert not terminal.supports_attention
    assert terminal.supports_foreground
    assert raised == [app]


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
