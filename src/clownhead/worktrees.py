"""The worktrees Claude Code leaves behind, and retiring the ones nothing needs.

Claude Code checks a job out into ``<repo>/.claude/worktrees/<name>``, locks it while the
session is in it, and unlocks it on the way out — but never removes the directory. They
accumulate, and they are not free: the sandbox and permission rules a session is given
enumerate paths per worktree, so a repository carrying a dozen finished ones pushes that
rule set past what is workable.

This is the first module that asks git anything. It follows :mod:`clownhead.discovery` in
keeping the commands thin and the parsing pure, so what git says can be tested without a
repository to say it.

Retiring a worktree is not discarding work. ``git worktree remove`` deletes the checkout
and leaves the branch exactly where it was, so every commit on it survives. The guards
here are for what does not survive: files never committed, and commits on no remote.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from clownhead.discovery import Process, is_claude, process_table
from clownhead.models import Session, split_worktree

GIT_BINARY_VAR = "CLOWNHEAD_GIT_BIN"
GIT_TIMEOUT = 20.0

LOCK_HOLDER = re.compile(r"\bpid\s+(\d+)\b")
"""The process id inside a Claude Code lock reason.

It writes ``claude session judge (pid 72883 start Tue Aug 11 09:20:08 2026)``, and the pid
is the part that makes a lock checkable rather than merely present.
"""

DEFAULT_BRANCHES = ("origin/HEAD", "origin/main", "origin/master", "main", "master")
"""Where finished work would have landed, best answer first.

``origin/HEAD`` is the remote's own account of its default branch, and is right even where
that is neither ``main`` nor ``master``; it is only set when the clone asked for it, so the
usual names follow. Remote before local throughout, because a local ``main`` that has not
been pulled in a month would call merged work unmerged.
"""


@dataclass(frozen=True)
class Worktree:
    """One worktree of a repository, as ``git worktree list --porcelain`` describes it.

    :attr:`lock` is the reason a worktree was locked, empty where it was locked without
    one, and ``None`` where it was not locked at all — a distinction worth keeping, since
    a lock nobody explained is still a lock.
    """

    path: Path
    repo: Path
    name: str
    branch: str | None = None
    head: str | None = None
    lock: str | None = None


@dataclass(frozen=True)
class Candidate:
    """A worktree weighed up for removal, with the reason it is being kept if it is."""

    worktree: Worktree
    last_used: datetime | None = None
    merged: bool = False
    kept_for: str | None = None

    @property
    def removable(self) -> bool:
        """Whether nothing is holding this worktree back."""
        return self.kept_for is None


def survey(
    sessions: Iterable[Session],
    older_than: timedelta = timedelta(0),
    now: datetime | None = None,
    processes: Mapping[int, Process] | None = None,
    only: Path | None = None,
) -> list[Candidate]:
    """Every Claude Code worktree the herd's repositories hold, and what protects each.

    The worktrees are asked of git rather than derived from the sessions, because the ones
    worth finding are exactly the ones no session remembers any more: a transcript ages out
    of the config directory long before the checkout it was written in goes anywhere.

    Sessions are matched to worktrees by path containment rather than by name, since a
    session that worked in a subdirectory of one records the subdirectory.

    ``only`` narrows the answer to the worktree holding one path, and narrows it before any
    of the expensive questions are asked. Weighing up a repository's every worktree costs
    several subprocesses apiece, which is worth it for a sweep and pure waste for a caller
    that already knows which one it is asking about.
    """
    moment = now or datetime.now(tz=UTC)
    herd = list(sessions)
    table = processes if processes is not None else process_table()

    found: dict[Path, Worktree] = {}
    for repo in sorted(repos_of(herd)):
        for worktree in worktrees_of(repo):
            if only is None or only.is_relative_to(worktree.path):
                found.setdefault(worktree.path, worktree)

    candidates = []
    for path, worktree in sorted(found.items()):
        inside = [session for session in herd if session.cwd.is_relative_to(path)]
        used = last_used(worktree, inside)
        candidates.append(
            Candidate(
                worktree=worktree,
                last_used=used,
                merged=is_merged(worktree),
                kept_for=guard_for(worktree, inside, used, older_than, moment, table),
            )
        )
    return candidates


def remove(worktree: Worktree, processes: Mapping[int, Process] | None = None) -> None:
    """Retire a worktree, leaving its branch where it is.

    Never ``--force``. Git refuses a worktree with changes in it, and that refusal is a
    last guard rather than an obstacle — everything this module knows about a worktree was
    read a moment ago, and the moment is long enough for somebody to have started typing
    in it.

    A lock is cleared only when the process that took it has gone. Claude Code holds one
    for as long as a session is in the worktree, so a lock outliving its process is what a
    crash leaves behind, and clearing that is the whole point of being able to.

    Raises:
        LookupError: the worktree is in use, or git refused to remove it.
    """
    if worktree.lock is not None:
        holder = lock_holder(worktree.lock)
        table = processes if processes is not None else process_table()
        if holder is None or _is_live_session(holder, table):
            raise LookupError(f"{worktree.name} is locked by a live session")
        _git(worktree.repo, "worktree", "unlock", str(worktree.path))
    _git(worktree.repo, "worktree", "remove", str(worktree.path))


def repos_of(sessions: Iterable[Session]) -> set[Path]:
    """The repositories the herd is checked out in, as far as its paths say.

    A session in a worktree names its repository in its own path; one that is not in a
    worktree is somewhere in the repository already. Both are answered, because a
    repository whose worktrees have all been abandoned still has ordinary sessions in it,
    and those are what lead back to the abandoned ones.
    """
    return {split_worktree(session.cwd)[0] for session in sessions}


def worktrees_of(repo: Path) -> list[Worktree]:
    """Every Claude Code worktree of a repository.

    Only the ones under ``.claude/worktrees``. A repository may well have worktrees
    somebody made by hand, and those are nobody's to tidy but theirs.
    """
    try:
        listing = _git(repo, "worktree", "list", "--porcelain")
    except LookupError:
        return []
    return parse_worktrees(listing, repo)


def parse_worktrees(text: str, repo: Path) -> list[Worktree]:
    """Read ``git worktree list --porcelain`` output.

    One stanza per worktree, separated by blank lines, each a run of ``key value`` lines —
    ``worktree``, ``HEAD``, ``branch``, and the bare ``detached``, ``locked`` and
    ``prunable`` markers, which may carry a reason or nothing at all.
    """
    worktrees: list[Worktree] = []
    fields: dict[str, str] = {}
    for line in [*text.splitlines(), ""]:
        if line.strip():
            key, _, value = line.partition(" ")
            fields[key] = value.strip()
            continue
        found = _worktree_from(fields, repo)
        if found is not None:
            worktrees.append(found)
        fields = {}
    return worktrees


def lock_holder(lock: str | None) -> int | None:
    """The process id a worktree lock names, when its reason names one.

    ``None`` for a lock somebody else took, which is not clownhead's to judge and is
    treated as protection wherever it is read.
    """
    if not lock:
        return None
    found = LOCK_HOLDER.search(lock)
    return int(found[1]) if found else None


def guard_for(
    worktree: Worktree,
    inside: Sequence[Session],
    used: datetime | None,
    older_than: timedelta,
    now: datetime,
    processes: Mapping[int, Process],
) -> str | None:
    """Why a worktree is being kept, or ``None`` when nothing is keeping it.

    Ordered by how definite each answer is, so what a worktree is held back by is the
    plainest true thing about it rather than whichever check ran first. The cheap
    questions come before the ones that cost a subprocess, which is the same order.
    """
    if any(not session.is_finished for session in inside):
        return "a live session is in it"
    if worktree.lock is not None:
        holder = lock_holder(worktree.lock)
        if holder is None or _is_live_session(holder, processes):
            return "locked by a live session"
    if used is not None and now - used < older_than:
        return "newer than the age filter"
    if not worktree.path.exists():
        return None
    try:
        if is_dirty(worktree):
            return "uncommitted changes"
        ahead = unpushed(worktree)
    except LookupError as error:
        return f"git could not say: {error}"
    if ahead:
        return f"{ahead} commit{'' if ahead == 1 else 's'} on no remote"
    return None


def is_dirty(worktree: Worktree) -> bool:
    """Whether the worktree holds changes that removing it would take with it.

    Untracked files count. A worktree is where a session worked, and what a session leaves
    unsaved is as likely to be a file it never got as far as adding as one it edited.

    ``--no-optional-locks`` keeps the question from answering itself: an ordinary
    ``git status`` rewrites the index it refreshed, which is one of the timestamps
    :func:`last_used` reads.

    Raises:
        LookupError: git could not be run in the worktree.
    """
    return bool(_git(worktree.path, "--no-optional-locks", "status", "--porcelain").strip())


def unpushed(worktree: Worktree) -> int:
    """How many of the worktree's commits are on no remote.

    Committed work survives a worktree being retired, because the branch does — unless the
    commits live only in this clone, in which case the branch they survive on is one
    nobody else has a copy of.

    A repository with no remote configured is not asked. There is nowhere to have pushed
    to, and counting every commit would hold every worktree back for ever.

    Raises:
        LookupError: git could not be run in the worktree.
    """
    if not _git(worktree.repo, "remote").strip():
        return 0
    counted = _git(worktree.path, "rev-list", "--count", "HEAD", "--not", "--remotes").strip()
    return int(counted) if counted.isdigit() else 0


def is_merged(worktree: Worktree, default: str | None = None) -> bool:
    """Whether the worktree's work is already in the default branch.

    Two questions, because GitHub's default merge is a squash and the cheap question misses
    it. First, whether the branch tip is an ancestor of the default branch, which is true
    of a merge and of a rebase. Otherwise the branch is collapsed to a single commit
    against the point it left from, and ``git cherry`` asked whether that patch is upstream
    already — which is what a squash leaves behind and what nothing else can see.

    Collapsing writes a commit object nothing refers to, which is what ``git gc`` exists to
    sweep up. Both answers are heuristics: they decide what cleanup *offers*, never what it
    removes without being asked.
    """
    target = default if default is not None else default_branch(worktree.repo)
    if target is None or worktree.head is None:
        return False
    if _git_ok(worktree.repo, "merge-base", "--is-ancestor", worktree.head, target):
        return True
    return _squashed_into(worktree, target)


def default_branch(repo: Path) -> str | None:
    """The ref finished work would have been merged into, or ``None`` if there is no telling."""
    for candidate in DEFAULT_BRANCHES:
        if _git_ok(repo, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"):
            return candidate
    return None


def last_used(worktree: Worktree, inside: Iterable[Session]) -> datetime | None:
    """When the worktree was last worked in.

    The newest of what the sessions in it say and when git last wrote to its own
    administrative directory. Sessions are the better answer where there are any, and often
    there are none — a worktree outlives the record of the session that made it. Git's
    directory is the fallback rather than the checkout's because it is touched on every
    index write and every move of HEAD, where the checkout's timestamp only follows changes
    to its top level.
    """
    moments = [session.updated_at for session in inside if session.updated_at is not None]
    touched = _admin_mtime(worktree)
    if touched is not None:
        moments.append(touched)
    return max(moments, default=None)


def git_binary() -> str:
    """Path to git, overridable for tests via ``CLOWNHEAD_GIT_BIN``."""
    return os.environ.get(GIT_BINARY_VAR, "git")


def _worktree_from(fields: Mapping[str, str], repo: Path) -> Worktree | None:
    path = fields.get("worktree")
    if not path:
        return None
    _, name = split_worktree(Path(path))
    if name is None:
        return None
    branch = fields.get("branch")
    return Worktree(
        path=Path(path),
        repo=repo,
        name=name,
        branch=branch.removeprefix("refs/heads/") if branch else None,
        head=fields.get("HEAD") or None,
        lock=fields.get("locked"),
    )


def _squashed_into(worktree: Worktree, target: str) -> bool:
    head = worktree.head or "HEAD"
    try:
        base = _git(worktree.repo, "merge-base", target, head).strip()
        tree = _git(worktree.repo, "rev-parse", f"{head}^{{tree}}").strip()
        collapsed = _git(worktree.repo, "commit-tree", tree, "-p", base, "-m", "merged check").strip()
        answer = _git(worktree.repo, "cherry", target, collapsed).strip()
    except LookupError:
        return False
    return answer.startswith("-")


def _is_live_session(pid: int, processes: Mapping[int, Process]) -> bool:
    process = processes.get(pid)
    return process is not None and is_claude(process.command)


def _admin_mtime(worktree: Worktree) -> datetime | None:
    """When git last recorded work in the worktree, by its own bookkeeping.

    The ``index`` and ``HEAD`` files rather than the directory holding them. A directory's
    timestamp follows every file created or removed inside it, and git makes and unmakes
    temporary files there merely to answer a question — so reading a worktree's state would
    reset the very age this is asked for, and a sweep would find nothing old the second
    time it looked. The two files move only when something is staged, committed or checked
    out.
    """
    admin = _admin_dir(worktree)
    stamps = []
    for path in (admin / "index", admin / "HEAD", worktree.path):
        try:
            stamps.append(datetime.fromtimestamp(path.stat().st_mtime, tz=UTC))
        except OSError:
            continue
    return max(stamps, default=None)


def _admin_dir(worktree: Worktree) -> Path:
    pointer = worktree.path / ".git"
    try:
        text = pointer.read_text(encoding="utf-8")
    except OSError:
        return pointer
    _, _, target = text.partition("gitdir:")
    return Path(target.strip()) if target.strip() else pointer


def _git(directory: Path, *args: str) -> str:
    """Run one git command and return what it printed.

    Raises:
        LookupError: git could not be run, or refused.
    """
    completed = _run(directory, *args)
    if completed.returncode != 0:
        raise LookupError(completed.stderr.strip().splitlines()[-1] if completed.stderr.strip() else "git refused")
    return completed.stdout


def _git_ok(directory: Path, *args: str) -> bool:
    """Whether git answered yes, for the commands that answer with an exit status.

    A git that could not be run at all answers no, because every caller is asking whether
    something is definitely true and an unanswered question is not.
    """
    try:
        return _run(directory, *args).returncode == 0
    except LookupError:
        return False


def _run(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            [git_binary(), *args],
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LookupError(f"git could not be run in {directory}: {error}") from error
