"""Rebuilding the commands that get you into a session.

A Claude Code session is a transcript on disk, not a process: killing the terminal loses
nothing, and ``claude --resume <id>`` in the original directory brings the conversation
back. All that is needed is the session id and where it was working.

A session that does not exist yet is the same shape of answer — a directory, and a command
to run in it — so starting one lives here too. Both are built as an argument vector rather
than a string, because they are run as often as they are copied, and quoting a line only
to take it apart again is how the two spellings drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from shlex import quote

from clownhead.models import Session, split_worktree


@dataclass(frozen=True)
class Launch:
    """A directory and the command to run in it, ready to be copied or handed the terminal."""

    directory: Path
    argv: tuple[str, ...]

    @property
    def shell_command(self) -> str:
        """Single shell line that runs it where it belongs."""
        return f"(cd {self.directory} && {' '.join(quote(argument) for argument in self.argv)})"


def resume_plan(session: Session) -> Launch:
    """Where to resume a session from, and the command that does it.

    A worktree session records the worktree itself as its directory, but ``--worktree``
    from the owning repository is how Claude Code enters one: it attaches to the worktree
    that still stands and rebuilds the one that has been pruned. Worktree sessions
    therefore always resume from the repository, and a worktree disappearing between
    sessions changes nothing about the command.

    Any other missing directory keeps its ``cd`` and the failure that comes with it.
    Resuming a session somewhere other than where it belongs would hand it a working
    directory full of the wrong project, which is worse than a command that stops.
    """
    argv = ("claude", "--resume", session.session_id)
    repo, worktree = split_worktree(session.cwd)
    if worktree and repo.exists():
        return Launch(repo, (*argv, "--worktree", worktree))
    return Launch(session.cwd, argv)


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
    return Launch(repo, ("claude", "--permission-mode", "plan", "--worktree", name, "--name", name, prompt))


def resume_argv(session: Session) -> list[str]:
    """Argument vector that resumes one session."""
    return list(resume_plan(session).argv)


def resume_shell_command(session: Session) -> str:
    """Single shell line that resumes one session where it belongs."""
    return resume_plan(session).shell_command
