"""Rebuilding the command that brings a session back.

A Claude Code session is a transcript on disk, not a process: killing the terminal loses
nothing, and ``claude --resume <id>`` in the original directory brings the conversation
back. All that is needed is the session id and where it was working.
"""

from __future__ import annotations

from pathlib import Path

from clownhead.models import Session, split_worktree


def resume_plan(session: Session) -> tuple[Path, list[str]]:
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
    argv = ["claude", "--resume", session.session_id]
    repo, worktree = split_worktree(session.cwd)
    if worktree and repo.exists():
        return repo, [*argv, "--worktree", worktree]
    return session.cwd, argv


def resume_argv(session: Session) -> list[str]:
    """Argument vector that resumes one session."""
    return resume_plan(session)[1]


def resume_shell_command(session: Session) -> str:
    """Single shell line that resumes one session where it belongs."""
    directory, argv = resume_plan(session)
    return f"(cd {directory} && {' '.join(argv)})"
