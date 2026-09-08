"""Rebuilding the commands that get you into a session.

A session outlives the terminal it was typed in. It is a transcript on disk that killing
the terminal loses nothing of, and ``claude --resume <id>`` or ``codex resume <id>`` in the
original directory brings the conversation back. All that is needed is the session id,
where it was working, and which agent it belongs to.

A session that does not exist yet is the same shape of answer — a directory, and a command
to run in it — so starting one lives here too. Both are built as an argument vector rather
than a string, because they are run as often as they are copied, and quoting a line only
to take it apart again is how the two spellings drift.

A command copied out of the board is run in some other shell, which is why the environment
that decides which fleet it lands in travels with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shlex import quote

from clownhead import codex
from clownhead.discovery import CONFIG_DIR_VAR, claude_binary, relocated_config_dir
from clownhead.models import WORKTREE_MARKER, Harness, Session, split_worktree


@dataclass(frozen=True)
class Launch:
    """A directory and the command to run in it, ready to be copied or handed the terminal.

    ``env`` is what the command needs said out loud rather than inherited. clownhead runs
    the command it hands the terminal with its own environment already under it, so the
    assignments are there for the copied line, which is pasted into a shell that was
    started some other way.
    """

    directory: Path
    argv: tuple[str, ...]
    env: tuple[tuple[str, str], ...] = ()

    @property
    def shell_command(self) -> str:
        """Single shell line that runs it where it belongs."""
        assignments = (f"{name}={quote(value)}" for name, value in self.env)
        words = " ".join((*assignments, *(quote(argument) for argument in self.argv)))
        return f"(cd {self.directory} && {words})"


def resume_plan(session: Session, fork: bool = False) -> Launch:
    """Where to resume a session from, and the command that does it.

    A worktree session records the worktree itself as its directory, but ``--worktree``
    from the owning repository is how Claude Code enters one: it attaches to the worktree
    that still stands and rebuilds the one that has been pruned. Worktree sessions
    therefore always resume from the repository, and a worktree disappearing between
    sessions changes nothing about the command.

    Any other missing directory keeps its ``cd`` and the failure that comes with it.
    Resuming a session somewhere other than where it belongs would hand it a working
    directory full of the wrong project, which is worse than a command that stops.

    ``fork`` takes the conversation and leaves the session id behind, which is what makes
    a session that is still running safe to open a second time: the transcript the live
    process is writing stays its own, and the copy carries on under an id of its own from
    everything said up to now.

    Codex resumes the same way and cannot do the rest of it. ``codex resume`` takes an id
    and a directory and has no ``--worktree``: it attaches to a worktree that stands and
    fails on one that has been pruned, where Claude Code rebuilds it. So a Codex session in
    a worktree resumes from the worktree itself, and a pruned one is a command that stops
    rather than one that quietly works somewhere else.
    """
    if session.harness is Harness.CODEX:
        return _codex_resume(session, fork)
    argv = (claude_binary(), "--resume", session.session_id, *(("--fork-session",) if fork else ()))
    env = carried_env()
    repo, worktree = split_worktree(session.cwd)
    if worktree and repo.exists():
        return Launch(repo, (*argv, "--worktree", worktree), env)
    return Launch(session.cwd, argv, env)


def _codex_resume(session: Session, fork: bool) -> Launch:
    verb = "fork" if fork else "resume"
    return Launch(session.cwd, (codex.codex_binary(), verb, session.session_id), codex_env())


def start_plan(
    repo: Path,
    *,
    name: str,
    prompt: str,
    worktree: bool = True,
    harness: Harness = Harness.CLAUDE,
) -> Launch:
    """Where to start a session for a reference, and the command that does it.

    Claude Code makes the worktree itself, which is the same ``--worktree`` that rebuilds
    a pruned one on resume, so nothing here asks git for anything and a name that has been
    used before is attached to rather than refused. Codex has no such flag, so a Codex
    session is started in a directory something else has already made. See
    :func:`clownhead.worktrees.create`.

    ``worktree`` is what a caller sets false for a repository the agent has never been run
    in. Claude Code refuses to make a worktree in a directory whose trust dialog has not
    been accepted, and the dialog only comes up once a session is running there, so the
    first session in a checkout works in the checkout and every one after it gets a
    worktree. See :func:`clownhead.discovery.trusted_dirs` for how that is known in advance.

    The name is spent twice on purpose. As a worktree it is the directory the work happens
    in; as ``--name`` it is what the session calls itself in the prompt box, the terminal
    title and every listing, which is what makes a board a dozen sessions deep readable at
    all. Codex takes the first of those and writes its own name from the conversation,
    which :func:`clownhead.codex.rename` can overwrite once the session exists.

    It starts read-only, because the prompt is a URL and nothing else. A session handed a
    ticket has to go and read it before there is anything to agree to, and the first thing
    it learns is what somebody wrote down about work nobody has scoped yet. Planning it
    back is the answer worth having; a session that started editing on the strength of an
    issue title is the one you would have to unpick. Claude Code spells that
    ``--permission-mode plan``, Codex spells it ``--sandbox read-only``.
    """
    if harness is Harness.CODEX:
        directory = worktree_path(repo, name) if worktree else repo
        return Launch(directory, (codex.codex_binary(), "--sandbox", "read-only", prompt), codex_env())
    tree = ("--worktree", name) if worktree else ()
    argv = (claude_binary(), "--permission-mode", "plan", *tree, "--name", name, prompt)
    return Launch(repo, argv, carried_env())


def worktree_path(repo: Path, name: str) -> Path:
    """Where clownhead puts a worktree of ``repo`` called ``name``.

    One layout whichever agent works in it, because the board reads the worktree name out
    of the path and a second layout would be a second thing to teach it. The directory is
    Claude Code's by origin and clownhead's by use.
    """
    return repo / WORKTREE_MARKER.strip("/") / name


def resume_argv(session: Session) -> list[str]:
    """Argument vector that resumes one session."""
    return list(resume_plan(session).argv)


def resume_shell_command(session: Session) -> str:
    """Single shell line that resumes one session where it belongs."""
    return resume_plan(session).shell_command


def carried_env() -> tuple[tuple[str, str], ...]:
    """Environment that has to travel with a copied command for it to reach the same fleet.

    ``CLAUDE_CONFIG_DIR`` scopes Claude Code to one config directory: a board opened under
    a relocated one lists sessions whose transcripts a default-config shell cannot see, so
    a bare ``claude --resume <id>`` pasted into that shell fails to find the conversation
    the board was showing. The variable is carried at the value clownhead itself was
    spawned with, which is the directory the listing came from.

    The default directory is left off. It is what a shell without the variable picks
    anyway, and it is the case nearly every command is copied in.
    """
    directory = relocated_config_dir()
    return () if directory is None else ((CONFIG_DIR_VAR, str(directory)),)


def codex_env() -> tuple[tuple[str, str], ...]:
    """The same for Codex, whose ``CODEX_HOME`` scopes it the way ``CLAUDE_CONFIG_DIR`` does."""
    directory = codex.relocated_config_dir()
    return () if directory is None else ((codex.CONFIG_DIR_VAR, str(directory)),)
