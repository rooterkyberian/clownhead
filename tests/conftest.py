import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clownhead import pulls as pulls_module
from clownhead.discovery import CONFIG_DIR_VAR
from clownhead.pulls import Pull, Status
from clownhead.search import PullRequest

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


@pytest.fixture(autouse=True)
def default_config_dir(monkeypatch) -> None:
    """Answer the suite from the Claude Code default config directory.

    ``CLAUDE_CONFIG_DIR`` is read fresh out of the environment wherever it is asked for,
    and clownhead is written by people whose own shells set it — so a suite that inherited
    it would assert against whichever directory the developer happened to be running under
    and disagree with CI about commands that carry it. Tests that care set it themselves.
    """
    monkeypatch.delenv(CONFIG_DIR_VAR, raising=False)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path) -> Path:
    """Answer the suite from a state directory of its own.

    Settings and the archive of ended sessions live under ``CLOWNHEAD_STATE_DIR``, which
    the suite both reads and writes — so without this a test would archive a session in
    the developer's own board, and read back whatever was already there.
    """
    directory = tmp_path / "state"
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(directory))
    return directory


@pytest.fixture
def socket_dir() -> Iterator[Path]:
    """A directory short enough to hold a unix socket.

    macOS caps an ``AF_UNIX`` path at 104 bytes, which pytest's own temporary directories
    exceed on their own.
    """
    directory = Path(tempfile.mkdtemp(dir="/tmp"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _a_pull(
    number: int = 7,
    repo: str = "widgets",
    title: str = "Add a thing",
    is_draft: bool = False,
    updated: str = "2026-08-10T00:00:00Z",
) -> Pull:
    """One open pull request, with every knob any test has wanted so far."""
    return Pull(
        reference=PullRequest(repo, number, "acme"),
        title=title,
        url=f"https://github.com/acme/{repo}/pull/{number}",
        is_draft=is_draft,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
        updated_at=datetime.fromisoformat(updated),
    )


def _fake_gh(tmp_path: Path, script: str) -> Path:
    """A ``gh`` that does whatever the script says, for ``CLOWNHEAD_GH_BIN`` to point at."""
    path = tmp_path / "gh"
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def a_pull() -> Callable[..., Pull]:
    """The pull request factory, handed over as a fixture so any test module can reach it.

    A fixture rather than an import because ``tests`` is not a package, and a helper
    imported from ``conftest`` resolves under some runners and not others.
    """
    return _a_pull


@pytest.fixture
def fake_gh() -> Callable[[Path, str], Path]:
    """The ``gh`` stub builder, for the same reason."""
    return _fake_gh


@pytest.fixture
def github(monkeypatch) -> list[Pull]:
    """A GitHub answering with one open pull request, approved and green.

    The listing is handed back so a test can put its own pull requests in it. Patched on
    the module rather than on each importer, since they all hold the same module object.
    """
    listing = [_a_pull(42)]
    monkeypatch.setattr(pulls_module, "mine", lambda author, limit: listing)
    monkeypatch.setattr(
        pulls_module,
        "stream_statuses",
        lambda listed: [(pull, Status(ran=True, review="APPROVED")) for pull in listed],
    )
    return listing
