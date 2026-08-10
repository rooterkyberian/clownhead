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

    A worktree is often gone by the time you come back to it — Claude Code prunes them —
    and a ``cd`` into a directory that no longer exists fails before ``claude`` is ever
    reached. Such a session is resumed from the repository that owned the worktree, with
    ``--worktree`` to put the worktree back.

    Any other missing directory keeps its ``cd`` and the failure that comes with it.
    Resuming a session somewhere other than where it belongs would hand it a working
    directory full of the wrong project, which is worse than a command that stops.
    """
    argv = ["claude", "--resume", session.session_id]
    if session.cwd.exists():
        return session.cwd, argv
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
