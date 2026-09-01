"""Rebuilding the commands that get you into a session.

A Claude Code session is a transcript on disk, not a process: killing the terminal loses
nothing, and ``claude --resume <id>`` in the original directory brings the conversation
back. All that is needed is the session id and where it was working.

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

from clownhead.discovery import CONFIG_DIR_VAR, relocated_config_dir
from clownhead.models import Session, split_worktree


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
    """
    argv = ("claude", "--resume", session.session_id, *(("--fork-session",) if fork else ()))
    env = carried_env()
    repo, worktree = split_worktree(session.cwd)
    if worktree and repo.exists():
        return Launch(repo, (*argv, "--worktree", worktree), env)
    return Launch(session.cwd, argv, env)


def start_plan(repo: Path, *, name: str, prompt: str) -> Launch:
    """Where to start a session for a reference, and the command that does it.

    Claude Code makes the worktree itself, which is the same ``--worktree`` that rebuilds
    a pruned one on resume — so nothing here asks git for anything, and a name that has
    been used before is attached to rather than refused.

    The name is spent twice on purpose. As a worktree it is the directory the work happens
    in; as ``--name`` it is what the session calls itself in the prompt box, the terminal
    title and every listing, which is what makes a board a dozen sessions deep readable at
    all. The prompt is the reference itself: the first thing the session should read is
    what it was started to work on.

    It starts in plan mode, because the prompt is a URL and nothing else. A session handed
    a ticket has to go and read it before there is anything to agree to, and the first
    thing it learns is what somebody wrote down about work nobody has scoped yet. Planning
    it back is the answer worth having; a session that started editing on the strength of
    an issue title is the one you would have to unpick.
    """
    argv = ("claude", "--permission-mode", "plan", "--worktree", name, "--name", name, prompt)
    return Launch(repo, argv, carried_env())


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
