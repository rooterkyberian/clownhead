import json
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clownhead import codex, discovery
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
def no_codex(monkeypatch, tmp_path) -> Path:
    """Answer the suite from a Codex home of its own, which has no app-server in it.

    The developer's own daemon is running while they work, and a suite that found it would
    list their real sessions into every assertion about a fabricated fleet. An empty
    directory has no control socket, so :func:`clownhead.codex.available` is false and the
    Codex half of the fan-out is skipped — which is also the state of every machine without
    Codex installed. The binary is pointed at nothing for the same reason, so a developer
    with Codex on their PATH and a CI runner without it agree about the harness column.
    Tests about Codex point both somewhere of their own.
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv(codex.CONFIG_DIR_VAR, str(home))
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(tmp_path / "no-such-codex"))
    return home


@pytest.fixture(autouse=True)
def no_daemon_wait(monkeypatch) -> None:
    """Give the suite no patience for a daemon that is never going to answer.

    :func:`clownhead.codex.ensure_daemon` waits seconds for the app-server it started, and
    every test that starts one starts it synchronously, so the wait is time the suite would
    only ever spend on a socket that stays absent.
    """
    monkeypatch.setattr(codex, "READY_TIMEOUT", 0.0)


REAL_TRUST_FILE = discovery.trust_file
"""Captured before :func:`unknown_trust` replaces it, for the tests about where it points."""


@pytest.fixture
def real_trust_file() -> Callable[[], Path]:
    """The unpatched :func:`clownhead.discovery.trust_file`."""
    return REAL_TRUST_FILE


@pytest.fixture(autouse=True)
def unknown_trust(monkeypatch, tmp_path) -> Path:
    """Answer the suite from a workspace-trust file of its own, which starts out absent.

    ``~/.claude.json`` is the developer's own, and it decides whether a start command
    carries ``--worktree`` — so a suite reading it would assert on whichever directories
    that developer happens to have accepted a dialog in. Absent reads as *nothing is
    known*, which is what every test that has no opinion about trust wants; the ones that
    do write this file.
    """
    trust = tmp_path / "trust.json"
    monkeypatch.setattr(discovery, "trust_file", lambda: trust)
    return trust


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


class FakeAppServer:
    """A stand-in for the Codex app-server: WebSocket on a Unix socket, JSON-RPC over it.

    Answers are looked up by method name and may be a value or a callable taking the
    params. A method with no answer comes back as a JSON-RPC error, which is how the real
    server refuses one. Every request is recorded in :attr:`asked`.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.answers: dict[str, object] = {}
        self.asked: list[tuple[str, dict]] = []
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener.bind(str(path))
        self._listener.listen(8)
        self._listener.settimeout(0.2)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._thread.join(timeout=3)
        self._listener.close()

    def _serve(self) -> None:
        while self._running:
            try:
                peer, _ = self._listener.accept()
            except (TimeoutError, OSError):
                continue
            try:
                self._converse(peer)
            finally:
                peer.close()

    def _converse(self, peer: socket.socket) -> None:
        peer.settimeout(2.0)
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            chunk = peer.recv(4096)
            if not chunk:
                return
            buffer += chunk
        peer.sendall(b"HTTP/1.1 101 Switching Protocols\r\nupgrade: websocket\r\n\r\n")
        while self._running:
            try:
                frame = _read_masked_frame(peer)
            except (TimeoutError, OSError):
                return
            if frame is None:
                return
            opcode, payload = frame
            if opcode == CLOSE_FRAME:
                return
            if opcode != TEXT_FRAME:
                continue
            peer.sendall(_text_frame(json.dumps(self._answer(json.loads(payload)))))

    def _answer(self, request: dict) -> dict:
        method = request.get("method", "")
        self.asked.append((method, request.get("params") or {}))
        if method == "initialize":
            return {"id": request.get("id"), "result": {"codexHome": str(self.path.parent)}}
        if method not in self.answers:
            return {"id": request.get("id"), "error": {"code": -32601, "message": f"no {method}"}}
        answer = self.answers[method]
        result = answer(request.get("params") or {}) if callable(answer) else answer
        return {"id": request.get("id"), "result": result}


def _text_frame(text: str) -> bytes:
    payload = text.encode()
    length = len(payload)
    if length < 126:
        return struct.pack("!BB", 0x81, length) + payload
    if length < 65536:
        return struct.pack("!BBH", 0x81, 126, length) + payload
    return struct.pack("!BBQ", 0x81, 127, length) + payload


def _read_masked_frame(sock: socket.socket) -> tuple[int, bytes] | None:
    def exactly(count: int) -> bytes | None:
        buffer = b""
        while len(buffer) < count:
            chunk = sock.recv(count - len(buffer))
            if not chunk:
                return None
            buffer += chunk
        return buffer

    header = exactly(2)
    if header is None:
        return None
    opcode = header[0] & 0x0F
    length = header[1] & 0x7F
    if length == 126:
        extended = exactly(2)
        length = struct.unpack("!H", extended)[0] if extended else 0
    elif length == 127:
        extended = exactly(8)
        length = struct.unpack("!Q", extended)[0] if extended else 0
    mask = exactly(4) if header[1] & 0x80 else None
    payload = exactly(length) if length else b""
    if payload is None:
        return None
    if mask is not None:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


TEXT_FRAME = 0x1
CLOSE_FRAME = 0x8


@pytest.fixture
def codex_server(monkeypatch, socket_dir) -> Iterator[FakeAppServer]:
    """A Codex home whose app-server is this fixture, for the tests that need one answering.

    Under ``/tmp`` rather than pytest's own temporary directory, because macOS caps an
    ``AF_UNIX`` path at 104 bytes and the latter spends most of that before the socket is
    named. Overrides :func:`no_codex`, which is what leaves every other test with no Codex.
    """
    home = socket_dir / "codex"
    control = home / "app-server-control"
    control.mkdir(parents=True)
    monkeypatch.setenv(codex.CONFIG_DIR_VAR, str(home))
    server = FakeAppServer(control / "app-server-control.sock")
    yield server
    server.stop()


@pytest.fixture
def codex_server_to_come(monkeypatch, socket_dir) -> Iterator[Callable[[], FakeAppServer]]:
    """A Codex home whose app-server starts only when the test says so.

    For the tests about clownhead starting a daemon itself: the socket is absent until the
    returned callable makes one, which is what the start command does for real.
    """
    home = socket_dir / "codex"
    control = home / "app-server-control"
    control.mkdir(parents=True)
    monkeypatch.setenv(codex.CONFIG_DIR_VAR, str(home))
    started: list[FakeAppServer] = []

    def start() -> FakeAppServer:
        server = FakeAppServer(control / "app-server-control.sock")
        started.append(server)
        return server

    yield start
    for server in started:
        server.stop()


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
