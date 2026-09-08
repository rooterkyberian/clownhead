"""Discovery and control of Codex sessions through the app-server.

Codex has no ``claude agents --json``. What it has is an app-server daemon holding every
session loaded on the machine, reachable on a Unix socket under the Codex home directory,
and answering JSON-RPC once a WebSocket handshake has been done over it. See
:mod:`clownhead.websocket` for the framing.

Discovery takes two questions rather than one, because no single call answers both halves.
``thread/list`` reads an index that a running session has not been written into yet, so a
listing of the newest threads can start below a session opened minutes ago. Live sessions
therefore come from ``thread/loaded/list``, one ``thread/read`` each, and everything that
has ended comes from ``thread/list``.

The daemon has to be running for any of this. ``codex app-server daemon start`` starts it.
A missing socket is reported rather than being allowed to look like an empty fleet, which
is the same call :func:`clownhead.discovery.peer_discovery_available` makes for Claude Code.

I/O and parsing are kept apart, as they are in :mod:`clownhead.discovery`, so the parsing
half is testable without a daemon to answer.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from itertools import count
from pathlib import Path
from typing import Any

from clownhead.models import Harness, Kind, Message, Process, Session, Status, epoch_seconds_to_datetime
from clownhead.websocket import Connection, ProtocolError, connect

CONFIG_DIR_VAR = "CODEX_HOME"
DEFAULT_CONFIG_DIR = Path.home() / ".codex"
SOCKET_DIR_NAME = "app-server-control"
SOCKET_NAME = "app-server-control.sock"
CODEX_COMMAND = "codex"
CLIENT_NAME = "clownhead"
CLOSED_PAGE = 200
PREVIEW_MESSAGES = 3
START_DAEMON = "codex app-server daemon start"

APPROVAL_FLAG = "waitingOnApproval"
INPUT_FLAG = "waitingOnUserInput"
USER_ITEM = "userMessage"
AGENT_ITEM = "agentMessage"

STATUS_BY_TYPE = {
    "idle": Status.IDLE,
    "notLoaded": Status.CLOSED,
    "systemError": Status.FAILED,
}
WAITING_BY_FLAG = {
    APPROVAL_FLAG: (Status.BLOCKED, "approval"),
    INPUT_FLAG: (Status.WAITING, "input"),
}


class Unavailable(RuntimeError):
    """The Codex app-server could not be reached.

    Raised rather than returning nothing, because a board with no Codex row and a board
    whose Codex half failed to answer look identical and mean opposite things.
    """


def list_sessions(cwd: Path | None = None, *, include_closed: bool = False) -> list[Session]:
    """Every Codex session the daemon knows about, live ones first.

    ``cwd`` filters to one directory. Only ``thread/list`` takes that filter itself, so
    the live half is filtered here instead, which costs nothing at fleet sizes a person
    can read.
    """
    with session() as client:
        live = [_read_thread(client, thread_id) for thread_id in _loaded_ids(client)]
        threads = [thread for thread in live if thread is not None]
        seen = {thread.get("id") for thread in threads}
        if include_closed:
            threads.extend(thread for thread in _listed(client, cwd) if thread.get("id") not in seen)
    sessions = [parse_thread(thread) for thread in threads]
    return [found for found in sessions if cwd is None or found.cwd == cwd]


def attach_processes(sessions: Iterable[Session], processes: Mapping[int, Process]) -> list[Session]:
    """Give each Codex session the terminal process running it, where that is unambiguous.

    The app-server names no process, so the only join available is the working directory:
    ``ps`` says which processes are Codex and ``lsof`` says where each one is.

    A pairing is taken only where it is unambiguous in both directions, one session in a
    directory and one process sitting in it. Three sessions and one terminal in the same
    checkout is the ordinary case once an editor is also running Codex there, and the
    directory says nothing about which of the three the terminal holds. A pid is what
    terminating and signalling act on, so the wrong one costs somebody else's session.
    """
    found = list(sessions)
    candidates = {pid: process for pid, process in processes.items() if is_codex(process.command)}
    if not candidates:
        return found
    by_directory: dict[Path, list[int]] = {}
    for pid, directory in process_directories(candidates).items():
        by_directory.setdefault(directory, []).append(pid)
    crowded = {found_session.cwd for found_session in found if _shares_directory(found_session, found)}
    attached = []
    for found_session in found:
        pids = by_directory.get(found_session.cwd, [])
        if len(pids) != 1 or found_session.cwd in crowded:
            attached.append(found_session)
            continue
        process = candidates[pids[0]]
        attached.append(found_session.model_copy(update={"pid": pids[0], "tty": process.tty}))
    return attached


def _shares_directory(one: Session, among: Sequence[Session]) -> bool:
    return sum(1 for other in among if other.cwd == one.cwd) > 1


def process_directories(pids: Iterable[int]) -> dict[int, Path]:
    """The working directory of each process id, asked of ``lsof``.

    One call for the whole set rather than one per process: ``lsof`` is slow to start and
    the board asks this on every refresh.
    """
    listed = list(pids)
    if not listed:
        return {}
    try:
        completed = subprocess.run(  # noqa: S603
            ["lsof", "-a", "-d", "cwd", "-F", "pn", "-p", ",".join(str(pid) for pid in listed)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return {}
    return parse_lsof(completed.stdout)


def parse_lsof(text: str) -> dict[int, Path]:
    """Read ``lsof -F pn`` output, which names a process and then the paths under it."""
    directories: dict[int, Path] = {}
    pid: int | None = None
    for line in text.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            directories[pid] = Path(line[1:])
            pid = None
    return directories


def recent_messages(session_id: str, limit: int = PREVIEW_MESSAGES) -> list[Message]:
    """The last few things said in a session, oldest of them first.

    The app-server pages items newest-first, so the page is taken from that end and turned
    back around. Items carry no timestamp of their own, which is why every message here is
    undated.
    """
    with session() as client:
        payload = client.request(
            "thread/items/list",
            {"threadId": session_id, "limit": max(limit * 4, limit), "sortDirection": "desc"},
        )
    return parse_items(payload.get("data", []) if isinstance(payload, dict) else [], limit)


def trusted_dirs() -> set[Path] | None:
    """Every directory trusted in ``config.toml``, or ``None`` when the file cannot answer.

    ``None`` is *assume trusted*, matching :func:`clownhead.discovery.trusted_dirs`: being
    wrong that way costs one dialog, and being wrong the other way refuses everywhere.
    """
    try:
        payload = tomllib.loads((config_dir() / "config.toml").read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return parse_trust(payload)


def send_message(session_id: str, text: str) -> None:
    """Queue a message for a session, whether or not a turn is running.

    Through the CLI rather than the socket: ``codex queue`` is the supported spelling of
    this, and the socket's own ``turn/steer`` needs the id of a turn already in flight.
    """
    _run(["queue", "--thread", session_id, "--message", text])


def rename(session_id: str, name: str) -> None:
    """Give a session a name of your choosing, replacing the one the model wrote."""
    with session() as client:
        client.request("thread/name/set", {"threadId": session_id, "name": name})


def codex_binary() -> str:
    """Path to the Codex CLI, overridable for tests via ``CLOWNHEAD_CODEX_BIN``."""
    return os.environ.get("CLOWNHEAD_CODEX_BIN", CODEX_COMMAND)


def config_dir() -> Path:
    """Where Codex keeps its state, which ``CODEX_HOME`` may move.

    Read fresh on every call for the same reason :func:`clownhead.discovery.config_dir` is:
    it is the variable the CLI itself reads, and a listing has to be answered by one
    directory throughout.
    """
    override = os.environ.get(CONFIG_DIR_VAR)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_DIR


def relocated_config_dir() -> Path | None:
    """The Codex home in use when it is not the one Codex would have picked itself."""
    directory = config_dir()
    return None if directory == DEFAULT_CONFIG_DIR else directory


def control_socket() -> Path:
    """The app-server control socket, inside the Codex home directory."""
    return config_dir() / SOCKET_DIR_NAME / SOCKET_NAME


def available() -> bool:
    """Whether the daemon socket is there to be dialled.

    A cheap check callers make before offering Codex at all, so a machine with no daemon
    gets one clear sentence instead of an error per refresh.
    """
    try:
        return control_socket().is_socket()
    except OSError:
        return False


def installed() -> bool:
    """Whether the Codex CLI is on ``PATH`` at all."""
    from shutil import which

    return which(codex_binary()) is not None


def is_codex(command: str) -> bool:
    """Whether a process command line is a Codex CLI, by its argv0."""
    argv0 = command.split(maxsplit=1)[0] if command else ""
    return Path(argv0).name.startswith(CODEX_COMMAND)


class Client:
    """One JSON-RPC conversation with the app-server.

    Requests are numbered and matched by id, and anything else arriving in between is
    dropped: the daemon narrates its own state on the same connection, and none of it is
    an answer to a question asked here.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._replies = connection.messages()
        self._ids = count(1)

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Send one request and return its result, raising :class:`Unavailable` on failure."""
        identifier = next(self._ids)
        body = {"jsonrpc": "2.0", "id": identifier, "method": method, "params": dict(params or {})}
        try:
            self._connection.send(json.dumps(body))
        except OSError as error:
            raise Unavailable(f"{method} could not be sent: {error}") from error
        for reply in self._replies:
            decoded = _decode(reply)
            if decoded is None or decoded.get("id") != identifier:
                continue
            if "error" in decoded:
                raise Unavailable(f"{method} failed: {decoded['error']}")
            result = decoded.get("result")
            return result if isinstance(result, dict) else {}
        raise Unavailable(f"{method} went unanswered")


@contextmanager
def session() -> Iterator[Client]:
    """Open a conversation with the daemon, introduce clownhead, and close it afterwards."""
    path = control_socket()
    try:
        connection = connect(str(path))
    except (OSError, ProtocolError) as error:
        raise Unavailable(f"no Codex app-server at {path}; start one with `{START_DAEMON}`") from error
    try:
        client = Client(connection)
        client.request("initialize", {"clientInfo": {"name": CLIENT_NAME, "version": _version()}})
        yield client
    finally:
        connection.close()


def parse_thread(payload: Mapping[str, Any]) -> Session:
    """Turn one app-server ``Thread`` into a session model."""
    status, waiting_for = parse_status(payload.get("status"))
    return Session(
        session_id=str(payload.get("id") or payload.get("sessionId") or ""),
        cwd=Path(str(payload.get("cwd") or ".")),
        harness=Harness.CODEX,
        kind=Kind.INTERACTIVE,
        name=payload.get("name") or None,
        status=status,
        waiting_for=waiting_for,
        started_at=epoch_seconds_to_datetime(payload.get("createdAt")),
        updated_at=epoch_seconds_to_datetime(payload.get("updatedAt")),
        transcript=Path(str(payload["path"])) if payload.get("path") else None,
    )


def parse_status(payload: Any) -> tuple[Status, str | None]:
    """A ``ThreadStatus`` as one of clownhead's statuses, and why it is waiting.

    ``active`` covers three of the board's states at once: a turn in flight, a turn
    stopped on an approval, and a turn stopped on a question. The flags tell them apart,
    and an active thread carrying neither flag is working.
    """
    if not isinstance(payload, Mapping):
        return Status.UNKNOWN, None
    kind = payload.get("type")
    if kind in STATUS_BY_TYPE:
        return STATUS_BY_TYPE[str(kind)], None
    if kind != "active":
        return Status.UNKNOWN, None
    flags = payload.get("activeFlags") or ()
    for flag, (status, reason) in WAITING_BY_FLAG.items():
        if flag in flags:
            return status, reason
    return Status.BUSY, None


def parse_items(payload: Iterable[Any], limit: int) -> list[Message]:
    """The newest-first item page as messages, oldest first and capped at ``limit``.

    Only what was said survives: an item page is mostly commands run, files read and
    searches made, and the conversation pane is for the conversation.
    """
    messages: list[Message] = []
    for entry in payload:
        if len(messages) >= limit:
            break
        item = entry.get("item") if isinstance(entry, Mapping) else None
        spoken = _spoken(item) if isinstance(item, Mapping) else None
        if spoken is not None:
            messages.append(spoken)
    return list(reversed(messages))


def parse_trust(payload: Mapping[str, Any]) -> set[Path]:
    """Directories marked trusted in a decoded ``config.toml``."""
    projects = payload.get("projects")
    if not isinstance(projects, Mapping):
        return set()
    return {
        Path(path)
        for path, record in projects.items()
        if isinstance(record, Mapping) and record.get("trust_level") == "trusted"
    }


def _loaded_ids(client: Client) -> list[str]:
    payload = client.request("thread/loaded/list")
    data = payload.get("data")
    return [str(entry) for entry in data] if isinstance(data, list) else []


def _read_thread(client: Client, thread_id: str, *, include_turns: bool = False) -> dict[str, Any] | None:
    params: dict[str, Any] = {"threadId": thread_id}
    if include_turns:
        params["includeTurns"] = True
    try:
        payload = client.request("thread/read", params)
    except Unavailable:
        return None
    thread = payload.get("thread")
    return thread if isinstance(thread, dict) else None


def _listed(client: Client, cwd: Path | None) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"limit": CLOSED_PAGE}
    if cwd is not None:
        params["cwd"] = str(cwd)
    payload = client.request("thread/list", params)
    data = payload.get("data")
    return [entry for entry in data if isinstance(entry, dict)] if isinstance(data, list) else []


def _spoken(item: Mapping[str, Any]) -> Message | None:
    kind = item.get("type")
    if kind == USER_ITEM:
        text = _content_text(item.get("content"))
        return Message(role="user", text=text) if text else None
    if kind == AGENT_ITEM:
        text = str(item.get("text") or "").strip()
        return Message(role="assistant", text=text) if text else None
    return None


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        str(block.get("text") or "") for block in content if isinstance(block, Mapping) and block.get("type") == "text"
    ]
    return "\n".join(part for part in parts if part).strip()


def _decode(line: str) -> dict[str, Any] | None:
    try:
        decoded = json.loads(line)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _run(arguments: list[str]) -> None:
    try:
        subprocess.run([codex_binary(), *arguments], capture_output=True, text=True, check=True)  # noqa: S603
    except (OSError, subprocess.CalledProcessError) as error:
        raise Unavailable(f"codex {arguments[0]} failed: {error}") from error


def _version() -> str:
    from clownhead import __version__

    return __version__
