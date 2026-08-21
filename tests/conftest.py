import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

DESKTOP_BINARIES = ("/usr/bin/open", "xdg-open", "osascript", "/usr/bin/osascript")


@pytest.fixture(autouse=True)
def unreachable_desktop(monkeypatch) -> list[list[str]]:
    """Put the desktop out of the suite's reach, and remember what it tried.

    clownhead exists to raise windows and select tabs, so most of what it does ends in
    ``open -b <bundle>`` or an AppleScript. A test double that forgets to override one of
    those reaches the real thing: an IDE jumps to the front of whoever is running the
    suite, on every run, from a test that passes either way. That happened — four tests in
    ``test_attention.py`` stub ``select_tab`` and inherit a live :meth:`Terminal.foreground`
    — and patching those four would leave the fifth for whoever writes it next.

    So the guard is here rather than there. Only the binaries that act on the desktop are
    intercepted; ``gh``, ``git`` and the fake scripts tests write for themselves run
    normally, because those are what the suite is entitled to run. A test that wants to
    assert on the argv patches ``subprocess.run`` itself and shadows this.
    """
    attempted: list[list[str]] = []
    real = subprocess.run

    def guarded(argv, *arguments, **keywords):  # type: ignore[no-untyped-def]
        first = str(argv[0]) if isinstance(argv, list | tuple) and argv else str(argv)
        if any(binary in first for binary in DESKTOP_BINARIES):
            attempted.append([str(part) for part in argv])
            return subprocess.CompletedProcess(argv, 0, "", "")
        return real(argv, *arguments, **keywords)

    monkeypatch.setattr(subprocess, "run", guarded)
    return attempted


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory short enough to hold a unix socket.

    macOS caps an ``AF_UNIX`` path at 104 bytes, which pytest's own temporary directories
    exceed on their own.
    """
    directory = Path(tempfile.mkdtemp(dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)
