"""Discovery of live Claude Code sessions and the metadata that enriches them.

``claude agents --json`` is the only built-in listing that includes interactive
sessions, so it is the source of truth for what is live. Everything else in this module
layers extra facts on top of that payload: the controlling TTY from ``ps`` and the
registry heartbeat from ``sessions`` under the Claude Code config directory.

Sessions that have ended are found the other way round — from what they left on disk,
under ``projects`` and in the registry — and are only trusted to be closed because the
CLI no longer reports them.

Where that config directory is is a question with an answer that moves: ``CLAUDE_CONFIG_DIR``
relocates the whole of it, and the CLI scopes its listing to whichever one it was invoked
under. clownhead therefore reads the same variable rather than assuming ``~/.claude``.

I/O and parsing are deliberately separate so the parsing half stays trivially testable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import islice
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from clownhead.models import Kind, Session, Status

SOCKET_DIR = Path("/tmp/cc-socks")  # noqa: S108
CONFIG_DIR_VAR = "CLAUDE_CONFIG_DIR"
DEFAULT_CONFIG_DIR = Path.home() / ".claude"
TRANSCRIPT_HEAD_LINES = 40
TRANSCRIPT_TAIL_BYTES = 256 * 1024
TRANSCRIPT_MAX_BYTES = 8 * 1024 * 1024
PREVIEW_MESSAGES = 3
MESSAGE_ROLES = frozenset({"user", "assistant"})
INJECTED_MESSAGE = re.compile(r"^<[a-z][a-z0-9-]*>")
NO_TTY = frozenset({"?", "??", "-", ""})
APP_SUFFIX = ".app"
APP_MARKER = f"{APP_SUFFIX}/Contents/MacOS/"

ATTENTION_RANK = 0
BUSY_RANK = 1
QUIET_RANK = 2
FINISHED_RANK = 3


@dataclass(frozen=True)
class Process:
    """One row of the process table."""

    pid: int
    ppid: int
    tty: Path | None
    command: str


@dataclass(frozen=True)
class Message:
    """One thing said in a session, by the human or by Claude.

    A turn the transcript did not date carries no time rather than a guessed one: the
    file is append-only, so its age would answer for every turn in it at once.
    """

    role: str
    text: str
    at: datetime | None = None


def claude_binary() -> str:
    """Path to the Claude Code CLI, overridable for tests via ``CLOWNHEAD_CLAUDE_BIN``."""
    return os.environ.get("CLOWNHEAD_CLAUDE_BIN", "claude")


def config_dir() -> Path:
    """Where Claude Code keeps its state, which ``CLAUDE_CONFIG_DIR`` may move.

    Read fresh on every call rather than resolved at import, because it is the same
    variable the CLI reads: ``claude agents --json`` lists only the sessions belonging to
    the config directory it is invoked under, so a listing and the records that enrich it
    have to be answered by one directory or the other and never a mixture of both. Pids
    are what the heartbeat records are keyed by, and two config directories on one machine
    number their sessions from the same process table.
    """
    override = os.environ.get(CONFIG_DIR_VAR)
    return Path(override).expanduser() if override else DEFAULT_CONFIG_DIR


def relocated_config_dir() -> Path | None:
    """The directory in use when it is not the one Claude Code would have picked itself.

    A board watching a relocated config directory is watching a different fleet, and an
    empty one is the same shape either way, so callers that have room to say which
    directory they are empty of should say it. ``None`` means there is nothing to say.
    """
    directory = config_dir()
    return None if directory == DEFAULT_CONFIG_DIR else directory


def session_registry() -> Path:
    """Directory of registry records, one per session that has published a heartbeat."""
    return config_dir() / "sessions"


def transcript_root() -> Path:
    """Directory of per-project transcripts, which outlive the sessions that wrote them."""
    return config_dir() / "projects"


def peer_discovery_available() -> bool:
    """Whether the peer socket directory is listable.

    Interactive sessions are discovered through per-process sockets. A sandboxed shell
    can read the CLI but not the socket directory, in which case ``claude agents --json``
    silently degrades to background agents only. Callers should warn rather than report
    an empty fleet.
    """
    try:
        if not SOCKET_DIR.is_dir():
            return True
        list(SOCKET_DIR.iterdir())
    except OSError:
        return False
    return True


def fetch_payload(cwd: Path | None = None, *, include_completed: bool = False) -> list[dict[str, Any]]:
    """Run ``claude agents --json`` and return the decoded payload."""
    args = [claude_binary(), "agents", "--json"]
    if include_completed:
        args.append("--all")
    if cwd is not None:
        args.extend(["--cwd", str(cwd)])
    completed = subprocess.run(args, capture_output=True, text=True, check=True)  # noqa: S603
    decoded: list[dict[str, Any]] = json.loads(completed.stdout)
    return decoded


def parse_sessions(payload: Iterable[dict[str, Any]]) -> list[Session]:
    """Turn a raw ``claude agents --json`` payload into session models."""
    return [Session.model_validate(entry) for entry in payload]


def parse_ps_output(text: str) -> dict[int, Process]:
    """Parse ``ps -axo pid=,ppid=,tty=,command=`` output into a process table."""
    table: dict[int, Process] = {}
    for line in text.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) != 4:
            continue
        pid_text, ppid_text, tty_text, command = fields
        if not pid_text.isdigit() or not ppid_text.isdigit():
            continue
        tty = None if tty_text in NO_TTY else Path("/dev") / tty_text
        table[int(pid_text)] = Process(pid=int(pid_text), ppid=int(ppid_text), tty=tty, command=command)
    return table


def process_table() -> dict[int, Process]:
    """Every process on the machine, by process id."""
    completed = subprocess.run(  # noqa: S603
        ["ps", "-axo", "pid=,ppid=,tty=,command="],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return parse_ps_output(completed.stdout)


def owning_application(pid: int | None, processes: Mapping[int, Process]) -> Path | None:
    """The application bundle whose process tree a session sits in.

    Which application owns a session cannot be read out of clownhead's own environment,
    because a fleet routinely spans several terminals at once — the session being
    signalled is usually not in the one clownhead was started from. Walking up from the
    session process to the first ancestor running out of an application bundle answers it
    per session instead.
    """
    seen: set[int] = set()
    while pid is not None and pid > 1 and pid not in seen:
        seen.add(pid)
        process = processes.get(pid)
        if process is None:
            return None
        bundle = application_bundle(process.command)
        if bundle is not None:
            return bundle
        pid = process.ppid
    return None


def application_bundle(command: str) -> Path | None:
    """The ``.app`` bundle named by a command line, if it names one at all.

    The bundle is as likely to be an argument as the executable — iTerm2 starts its
    shells through ``login``, passing its own path along — so the path is cut back to the
    last argument boundary rather than to the start of the line. Splitting on whitespace
    would be simpler but would lose every application installed under a directory whose
    name contains a space.
    """
    index = command.find(APP_MARKER)
    if index == -1:
        return None
    path = command[: index + len(APP_SUFFIX)]
    boundary = path.rfind(" /")
    return Path(path[boundary + 1 :] if boundary != -1 else path)


def registry_heartbeats(registry: Path | None = None) -> dict[int, datetime]:
    """Last heartbeat per process id, read from the interactive session registry.

    A session that dies without cleaning up leaves its file behind, so entries here are
    only meaningful when joined against sessions the CLI still reports as live.
    """
    heartbeats: dict[int, datetime] = {}
    for entry in registry_entries(registry):
        pid = entry.get("pid")
        updated_at = entry.get("updatedAt")
        if isinstance(pid, int) and isinstance(updated_at, int | float):
            heartbeats[pid] = datetime.fromtimestamp(updated_at / 1000, tz=UTC)
    return heartbeats


def registry_entries(registry: Path | None = None) -> list[dict[str, Any]]:
    """Every readable record in the interactive session registry."""
    directory = registry or session_registry()
    if not directory.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            entry = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def messaging_socket(session_id: str, registry: Path | None = None) -> Path | None:
    """The control socket a session listens on, or ``None`` if it has none.

    Claude Code publishes the path in its registry record. Versions that bind the socket
    without naming it are still reachable at the conventional per-process path, which is
    only offered when something is actually listening there — older ones never bound one
    at all, and deriving a path for them would promise a channel that does not exist.
    """
    entry = _newest_entry(session_id, registry)
    if entry is None:
        return None
    published = entry.get("messagingSocketPath")
    if isinstance(published, str) and published:
        return Path(published)
    pid = entry.get("pid")
    if not isinstance(pid, int):
        return None
    conventional = SOCKET_DIR / f"{pid}.sock"
    return conventional if conventional.is_socket() else None


def closed_sessions(
    live: Iterable[Session],
    cwd: Path | None = None,
    registry: Path | None = None,
    transcripts: Path | None = None,
) -> list[Session]:
    """Sessions that have ended but can still be resumed, marked closed.

    Two records outlive a session. Its transcript is the complete one — that is all
    ``claude --resume`` needs — while the registry keeps the richer metadata but is
    pruned when a session exits cleanly, so it only contributes the ones that crashed.
    Registry facts win where both remember the same session.

    Neither the process id nor the TTY survives: the process is gone and its id may since
    have been recycled by something that would not enjoy being signalled.
    """
    live_ids = {session.session_id for session in live}
    remembered = {session.session_id: session for session in transcript_sessions(transcripts)}
    remembered.update({session.session_id: session for session in registry_sessions(registry)})
    return [
        session.model_copy(update={"status": Status.CLOSED, "tty": None, "pid": None})
        for session_id, session in remembered.items()
        if session_id and session_id not in live_ids and (cwd is None or session.cwd.is_relative_to(cwd))
    ]


def registry_sessions(registry: Path | None = None) -> list[Session]:
    """Every session the interactive registry still has a record of."""
    sessions: list[Session] = []
    for entry in registry_entries(registry):
        try:
            sessions.append(Session.model_validate(entry))
        except ValidationError:
            continue
    return sessions


def recent_messages(session_id: str, limit: int = PREVIEW_MESSAGES, root: Path | None = None) -> list[Message]:
    """The last few turns of a session's conversation, oldest first.

    Only what a human would recognise as the conversation is kept: tool calls, their
    results and thinking are the bulk of a transcript but say nothing about where the
    session got to. Reading starts from the tail, because transcripts run to megabytes
    and only the end is ever shown.

    A run of turns by the same speaker collapses to its last one. Claude narrates between
    tool calls, so the final few entries of a working session are all its own — and a
    preview of one side of a conversation is no preview at all.
    """
    path = transcript_path(session_id, root)
    if path is None:
        return []
    messages = _collapse_runs(_messages_in(_transcript_tail(path, TRANSCRIPT_TAIL_BYTES)))
    if len(messages) < limit:
        messages = _collapse_runs(_messages_in(_transcript_tail(path, TRANSCRIPT_MAX_BYTES)))
    return messages[-limit:]


def transcript_path(session_id: str, root: Path | None = None) -> Path | None:
    """Where a session's transcript lives, whether or not the session still runs."""
    directory = root or transcript_root()
    if not directory.is_dir():
        return None
    return next(iter(sorted(directory.glob(f"*/{session_id}.jsonl"))), None)


def transcript_paths(session_id: str, root: Path | None = None) -> list[Path]:
    """Everything a session wrote down: its own transcript, and its subagents' beside it.

    A subagent's conversation is the session's work too. A pull request a subagent read
    and reported on, which the main thread only ever discussed in the abstract, was still
    worked on here — so a question asked of a session has to be asked of the directory
    named after it as well.
    """
    path = transcript_path(session_id, root)
    if path is None:
        return []
    return [path, *sorted(path.with_suffix("").glob("*.jsonl"))]


def transcript_sessions(root: Path | None = None) -> list[Session]:
    """Every session with a transcript on disk, live or long gone.

    Only the top level of each project directory is a resumable session; the nested ones
    are the subagent transcripts belonging to it. The working directory is read out of
    the transcript rather than decoded from the project directory name, which flattens
    path separators into the same dash it allows inside a directory name.
    """
    directory = root or transcript_root()
    if not directory.is_dir():
        return []
    sessions: list[Session] = []
    for path in sorted(directory.glob("*/*.jsonl")):
        entry = _transcript_head(path)
        if entry is None:
            continue
        sessions.append(
            Session(
                session_id=path.stem,
                cwd=Path(entry["cwd"]),
                started_at=_parse_timestamp(entry.get("timestamp")),
                updated_at=_modified_at(path),
            )
        )
    return sessions


def enrich(
    sessions: Iterable[Session],
    *,
    processes: Mapping[int, Process] | None = None,
    heartbeats: Mapping[int, datetime] | None = None,
) -> list[Session]:
    """Attach TTY, owning application and heartbeat facts to sessions with a process id."""
    table = processes or {}
    enriched: list[Session] = []
    for session in sessions:
        if session.pid is None:
            enriched.append(session)
            continue
        process = table.get(session.pid)
        enriched.append(
            session.model_copy(
                update={
                    "tty": (process.tty if process else None) or session.tty,
                    "app": owning_application(session.pid, table) or session.app,
                    "updated_at": (heartbeats or {}).get(session.pid, session.updated_at),
                }
            )
        )
    return enriched


def sort_key(session: Session) -> tuple[int, float]:
    """Order sessions attention-first and finished last.

    Live sessions sort oldest-first, because a session that has been idle for a week is
    the one worth noticing. Finished ones sort newest-first, because the session you just
    closed is the one you are most likely to want back.
    """
    if session.needs_attention:
        rank = ATTENTION_RANK
    elif session.status is Status.BUSY:
        rank = BUSY_RANK
    elif session.is_finished:
        rank = FINISHED_RANK
    else:
        rank = QUIET_RANK
    started = session.started_at.timestamp() if session.started_at else 0.0
    return rank, -started if rank == FINISHED_RANK else started


def list_sessions(
    cwd: Path | None = None,
    *,
    interactive_only: bool = False,
    include_closed: bool = False,
) -> list[Session]:
    """Discover live sessions and enrich them with TTY and heartbeat metadata.

    With ``include_closed`` the listing also covers sessions that have ended: background
    agents the CLI reports as completed, and interactive sessions that survive only as a
    transcript on disk.
    """
    live = parse_sessions(fetch_payload(cwd, include_completed=include_closed))
    kept = [session for session in live if session.kind is Kind.INTERACTIVE] if interactive_only else list(live)
    sessions = enrich(kept, processes=process_table(), heartbeats=registry_heartbeats())
    if include_closed:
        sessions.extend(closed_sessions(live, cwd))
    return sorted(sessions, key=sort_key)


def _newest_entry(session_id: str, registry: Path | None) -> dict[str, Any] | None:
    """The liveliest registry record for a session id.

    The registry is keyed by process id, and a session that has been resumed answers to a
    new one while its abandoned record survives until the registry is next pruned. Both
    name the same session, so the one that last wrote a heartbeat is the running one.
    """
    matching = [entry for entry in registry_entries(registry) if entry.get("sessionId") == session_id]
    return max(matching, key=_heartbeat_of) if matching else None


def _heartbeat_of(entry: Mapping[str, Any]) -> float:
    updated_at = entry.get("updatedAt")
    return float(updated_at) if isinstance(updated_at, int | float) else 0.0


def _transcript_tail(path: Path, size: int) -> list[str]:
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            start = max(0, handle.tell() - size)
            handle.seek(start)
            chunk = handle.read()
    except OSError:
        return []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    return lines[1:] if start else lines


def _messages_in(lines: Iterable[str]) -> list[Message]:
    messages: list[Message] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain") or entry.get("isMeta"):
            continue
        role = entry.get("type")
        if role not in MESSAGE_ROLES:
            continue
        text = _spoken_text((entry.get("message") or {}).get("content"))
        if text and not INJECTED_MESSAGE.match(text):
            messages.append(Message(role=role, text=text, at=_parse_timestamp(entry.get("timestamp"))))
    return messages


def _collapse_runs(messages: Sequence[Message]) -> list[Message]:
    return [
        message
        for index, message in enumerate(messages)
        if index + 1 == len(messages) or messages[index + 1].role != message.role
    ]


def _spoken_text(content: Any) -> str:
    if isinstance(content, str):
        return " ".join(content.split())
    if not isinstance(content, list):
        return ""
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    return " ".join(" ".join(parts).split())


def _transcript_head(path: Path) -> dict[str, Any] | None:
    try:
        with path.open() as handle:
            for line in islice(handle, TRANSCRIPT_HEAD_LINES):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("cwd"):
                    return entry
    except OSError:
        return None
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _modified_at(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None
