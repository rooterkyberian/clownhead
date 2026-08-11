"""Which checkout on this machine a reference belongs to.

A pull request URL names its repository outright; a Jira key names nothing at all. Neither
says where that repository is on disk, and the answer has to come from what the machine
already knows — which is the fleet. Every session is standing in a checkout, so the
repositories worth offering are exactly the ones sessions have been run in.

Nothing here decides on the user's behalf. The repositories come back best-guess first and
the choice stays with whoever is looking at them, because the two signals available — a
remote that matches, and a session that said the words — are each strong enough to lead
and neither is ever certain. A repository mirrored twice satisfies the first; a session
that merely read a linked ticket satisfies the second.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from clownhead import worktrees
from clownhead.models import Session, split_worktree
from clownhead.search import Reference


def repos_for(reference: Reference, sessions: Iterable[Session], named: set[str]) -> list[Path]:
    """Every repository a session for this reference could be started in, best first.

    A repository whose ``origin`` is the reference's own is as close to certain as this
    gets, so it leads. Behind it come the repositories holding a session that named the
    reference, which is the only signal a Jira key ever has. The rest follow in full
    rather than being dropped: a ticket may well be the first work of its kind in a
    checkout nothing has said anything about yet.

    One ``git`` call per repository, so this belongs on a worker thread rather than in
    front of a redraw.
    """
    repos = sorted(worktrees.repos_of(sessions))
    wanted = _owner_and_repo(reference)
    remotes = {repo: worktrees.remote_of(repo) for repo in repos} if wanted else {}
    holding = {split_worktree(session.cwd)[0] for session in sessions if session.session_id in named}
    return sorted(repos, key=lambda repo: (remotes.get(repo) != wanted, repo not in holding, repo))


def _owner_and_repo(reference: Reference) -> tuple[str, str] | None:
    """What the reference calls its repository on GitHub, where it names one at all.

    A Jira key has no owner, and neither has a bare ``repo#309`` — which does name a
    repository, but not the one of several forks of it that is checked out here.
    """
    if reference.owner is None or reference.repo is None:
        return None
    return reference.owner, reference.repo
