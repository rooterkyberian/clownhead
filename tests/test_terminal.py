from pathlib import Path

from clownhead.terminal import ITerm2Terminal, KittyTerminal, Rgb, Terminal, detect_terminal


class RecordingTerminal(Terminal):
    def __init__(self):
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


class RecordingITerm2(ITerm2Terminal):
    def __init__(self):
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


TTY = Path("/dev/ttys004")


def test_generic_terminal_rings_the_bell():
    terminal = RecordingTerminal()

    terminal.request_attention(TTY)

    assert terminal.written == ["\a"]


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


def test_write_reaches_the_device(tmp_path):
    target = tmp_path / "fake-tty"
    target.touch()

    Terminal().bell(target)

    assert target.read_text() == "\a"
