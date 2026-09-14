"""The harnesses the board oversees, and what each of them can answer.

clownhead began as a board over Claude Code and now covers Codex too. The two agree on
almost nothing underneath: one answers a CLI subcommand, the other a daemon over a socket;
one keeps a registry of heartbeats, the other an index that lags the sessions in it. What
they do agree on is the shape of the answer, which is why everything above this module
sees one list of :class:`~clownhead.models.Session` and never asks who produced it.

The pattern is the one :mod:`clownhead.terminal` already uses for terminal emulators: a
base class carrying capability flags, a registry keyed by what identifies each one, and
operations that report they cannot rather than raising. A harness that is not installed is
left out of the fan-out entirely, so a machine with one agent on it sees no trace of the
other.
"""

from __future__ import annotations

from pathlib import Path

from clownhead import codex, discovery, models
from clownhead.models import Message, Session


class Harness:
    """One coding agent, and what clownhead can get out of it.

    The flags are read by callers that have to explain a difference rather than hide it:
    :func:`clownhead.cli.doctor` lists them, and the board greys an action a harness has
    no answer for instead of offering it and failing.
    """

    kind: models.Harness = models.Harness.CLAUDE
    label: str = "claude"
    supports_send_message: bool = False
    supports_rename: bool = False
    supports_terminate: bool = False
    supports_worktrees: bool = False
    supports_named_start: bool = False

    def installed(self) -> bool:
        """Whether this agent is on the machine at all."""
        raise NotImplementedError

    def available(self) -> bool:
        """Whether it can be asked for a listing right now."""
        raise NotImplementedError

    def unavailable_reason(self) -> str | None:
        """Why a listing cannot be had, or ``None`` when one can."""
        raise NotImplementedError

    def binary(self) -> str:
        """The command clownhead runs to reach it."""
        raise NotImplementedError

    def config_dir(self) -> Path:
        """Where it keeps its state."""
        raise NotImplementedError

    def relocated_config_dir(self) -> Path | None:
        """That directory when it is not the one this agent would have picked itself."""
        raise NotImplementedError

    def worktree_blocker(self) -> str | None:
        """Why it cannot make its own worktree at launch, or ``None`` when it can.

        Both agents check a job out themselves, given a new enough CLI, so the answer is
        ``None`` for anything that has not said otherwise.
        """
        return None

    def list_sessions(self, cwd: Path | None, *, include_closed: bool) -> list[Session]:
        """Every session it knows about."""
        raise NotImplementedError

    def recent_messages(self, session_id: str, *, limit: int) -> list[Message]:
        """The tail of one session's conversation, oldest first."""
        raise NotImplementedError

    def trusted_dirs(self) -> set[Path] | None:
        """Directories it has been trusted in, or ``None`` where the question has no answer."""
        raise NotImplementedError

    def transcript_paths(self, session: Session) -> list[Path]:
        """Every file holding what a session said, for scanning."""
        raise NotImplementedError

    def owns_process(self, command: str) -> bool:
        """Whether a process command line belongs to this agent."""
        raise NotImplementedError


class Claude(Harness):
    """Claude Code, reached through ``claude agents --json`` and the config directory."""

    kind = models.Harness.CLAUDE
    label = "claude"
    supports_send_message = True
    supports_rename = True
    supports_terminate = True
    supports_worktrees = True
    supports_named_start = True

    def installed(self) -> bool:
        """Claude Code is assumed present, since it is what the board was built on."""
        return True

    def available(self) -> bool:
        """Whether the peer socket directory can be listed, which the CLI needs."""
        return discovery.peer_discovery_available()

    def unavailable_reason(self) -> str | None:
        """The sandbox note, which is the only way this listing goes quiet."""
        if self.available():
            return None
        return f"cannot list {discovery.SOCKET_DIR}, so interactive sessions are invisible"

    def binary(self) -> str:
        """The Claude Code CLI."""
        return discovery.claude_binary()

    def config_dir(self) -> Path:
        """The Claude Code config directory, which ``CLAUDE_CONFIG_DIR`` may move."""
        return discovery.config_dir()

    def relocated_config_dir(self) -> Path | None:
        """The config directory when ``CLAUDE_CONFIG_DIR`` moved it."""
        return discovery.relocated_config_dir()

    def list_sessions(self, cwd: Path | None, *, include_closed: bool) -> list[Session]:
        """Live sessions from the CLI, ended ones from the registry and the transcripts."""
        return discovery.list_sessions(cwd, include_closed=include_closed)

    def recent_messages(self, session_id: str, *, limit: int) -> list[Message]:
        """The tail of the transcript on disk."""
        return discovery.recent_messages(session_id, limit)

    def trusted_dirs(self) -> set[Path] | None:
        """Directories whose trust dialog has been accepted."""
        return discovery.trusted_dirs()

    def transcript_paths(self, session: Session) -> list[Path]:
        """The session's own transcript and one per subagent it delegated to."""
        return discovery.transcript_paths(session.session_id)

    def owns_process(self, command: str) -> bool:
        """Whether argv0 is the Claude Code CLI."""
        return discovery.is_claude(command)


class Codex(Harness):
    """Codex, reached through the app-server daemon over its control socket."""

    kind = models.Harness.CODEX
    label = "codex"
    supports_send_message = True
    supports_rename = True

    def installed(self) -> bool:
        """Whether the Codex CLI is on ``PATH``."""
        return codex.installed()

    def available(self) -> bool:
        """Whether the app-server answers, starting a daemon when none is running."""
        return codex.ensure_daemon()

    def unavailable_reason(self) -> str | None:
        """Whether Codex is missing entirely or merely has no daemon running."""
        if not self.installed():
            return "not installed"
        if not self.available():
            return codex.daemon_failure() or f"no app-server running; start one with `{codex.START_DAEMON}`"
        return None

    def binary(self) -> str:
        """The Codex CLI."""
        return codex.codex_binary()

    def config_dir(self) -> Path:
        """The Codex home, which ``CODEX_HOME`` may move."""
        return codex.config_dir()

    def relocated_config_dir(self) -> Path | None:
        """The Codex home when ``CODEX_HOME`` moved it."""
        return codex.relocated_config_dir()

    def worktree_blocker(self) -> str | None:
        """Whether the installed Codex is new enough to check a job out for itself.

        Asked before the command is built, because the flags reach a terminal clownhead has
        already handed over and an older CLI exits there with a message nobody is watching
        for.
        """
        if codex.supports_worktrees():
            return None
        wanted = ".".join(str(part) for part in codex.WORKTREE_VERSION)
        found = codex.cli_version()
        spelled = ".".join(str(part) for part in found) if found else "this one"
        return f"codex {wanted} or newer makes its own worktree, and {spelled} does not"

    def list_sessions(self, cwd: Path | None, *, include_closed: bool) -> list[Session]:
        """Live threads from the daemon, and the ended ones it and the index remember.

        The process table is joined on afterwards, since the app-server names no process
        and everything that signals a session needs one.
        """
        found = codex.list_sessions(cwd, include_closed=include_closed)
        live = [session for session in found if not session.is_finished]
        if not live:
            return found
        attached = {session.session_id: session for session in codex.attach_processes(live, discovery.process_table())}
        return [attached.get(session.session_id, session) for session in found]

    def recent_messages(self, session_id: str, *, limit: int) -> list[Message]:
        """The tail of the thread's item list."""
        return codex.recent_messages(session_id, limit)

    def trusted_dirs(self) -> set[Path] | None:
        """Directories marked ``trust_level = "trusted"`` in ``config.toml``."""
        return codex.trusted_dirs()

    def transcript_paths(self, session: Session) -> list[Path]:
        """The rollout file the daemon named, which is the whole of what a thread said.

        Codex writes one file per thread and puts its path in the thread itself, so there
        is nothing to derive and no sibling directory of subagent transcripts to gather.
        """
        return [session.transcript] if session.transcript else []

    def owns_process(self, command: str) -> bool:
        """Whether argv0 is the Codex CLI."""
        return codex.is_codex(command)


HARNESSES: dict[models.Harness, Harness] = {
    models.Harness.CLAUDE: Claude(),
    models.Harness.CODEX: Codex(),
}
"""Every harness clownhead knows, whether or not this machine has it."""


def for_session(session: Session) -> Harness:
    """The harness a session belongs to."""
    return HARNESSES[session.harness]


def for_kind(kind: models.Harness) -> Harness:
    """The harness one enum value names."""
    return HARNESSES[kind]


def installed() -> list[Harness]:
    """Every harness present on this machine, in the order the board lists them."""
    return [harness for harness in HARNESSES.values() if harness.installed()]


def list_sessions(
    cwd: Path | None = None,
    *,
    interactive_only: bool = False,
    include_closed: bool = False,
) -> list[Session]:
    """One fleet made of every harness that can answer, sorted as a single board.

    A harness that is installed but cannot be reached is skipped rather than raising: a
    daemon that is not running is a normal state of the machine, and the reason for the
    absence is available from :meth:`Harness.unavailable_reason` for whoever is in a
    position to say it. A harness that answers badly after saying it was available is a
    different matter, and that error travels.
    """
    found: list[Session] = []
    for harness in HARNESSES.values():
        if not harness.installed() or not harness.available():
            continue
        found.extend(harness.list_sessions(cwd, include_closed=include_closed))
    kept = [session for session in found if session.kind is models.Kind.INTERACTIVE] if interactive_only else found
    return sorted(kept, key=discovery.sort_key)


def recent_messages(session: Session, *, limit: int) -> list[Message]:
    """The tail of a session's conversation, asked of whichever harness holds it."""
    return for_session(session).recent_messages(session.session_id, limit=limit)


def transcript_paths(session: Session) -> list[Path]:
    """Every file holding what a session said, asked of whichever harness wrote them."""
    return for_session(session).transcript_paths(session)


def trusted_dirs(kind: models.Harness = models.Harness.CLAUDE) -> set[Path] | None:
    """Directories one harness has been trusted in."""
    return for_kind(kind).trusted_dirs()


def owning_harness(command: str) -> Harness | None:
    """The harness a process command line belongs to, or ``None`` when it is neither."""
    for harness in HARNESSES.values():
        if harness.owns_process(command):
            return harness
    return None
