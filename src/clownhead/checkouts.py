"""Which checkout on this machine a reference belongs to.

A pull request URL names its repository outright; a Jira key names nothing at all. Neither
says where that repository is on disk, and the answer has to come from what the machine
already knows — which is the fleet. Every session is standing in a checkout, so the
repositories the fleet has been run in are where the search starts.

It does not end there. Checkouts sit beside each other: one clone of a GitHub repository
lands in ``~/dev/acme/widgets`` and the next in ``~/dev/acme/gadgets``, because that is
what ``git clone`` names a directory. So the fleet's own repositories say where clones are
kept, and a repository the reference names is looked for by that name in each of those
places. It is one ``git`` call for a checkout the fleet has never had a session in, which
is the normal state of a pull request opened from the web.

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
    gets, so it leads, whether or not the fleet has ever had a session in it. Behind it
    come the repositories holding a session that named the reference, which is the only
    signal a Jira key ever has, and then the ones the fleet has been in at all. The rest
    follow in full rather than being dropped: a ticket may well be the first work of its
    kind in a checkout nothing has said anything about yet.

    One ``git`` call per repository, so this belongs on a worker thread rather than in
    front of a redraw.
    """
    wanted = _owner_and_repo(reference)
    known = worktrees.repos_of(sessions)
    repos = sorted(known | _clones_named(wanted, known))
    remotes = {repo: worktrees.remote_of(repo) for repo in repos} if wanted else {}
    holding = {split_worktree(session.cwd)[0].resolve() for session in sessions if session.session_id in named}
    return sorted(
        repos,
        key=lambda repo: (
            not _is_wanted(remotes.get(repo), wanted),
            repo not in holding,
            repo not in known,
            repo,
        ),
    )


def _clones_named(wanted: tuple[str, str] | None, known: set[Path]) -> set[Path]:
    """Checkouts of the wanted repository sitting beside one the fleet has been run in.

    Looked for by name, because ``git clone`` names a directory after the repository and a
    clone renamed by hand is the rare case. The name is only the reason to ask: what makes
    one of these an answer is ``origin`` saying it is the repository the reference names,
    so a directory that happens to share the name is dropped rather than offered.
    """
    if wanted is None:
        return set()
    candidates = {repo.parent / wanted[1] for repo in known} - known
    return {repo for repo in candidates if repo.is_dir() and _is_wanted(worktrees.remote_of(repo), wanted)}


def _is_wanted(remote: tuple[str, str] | None, wanted: tuple[str, str] | None) -> bool:
    """Whether a checkout's ``origin`` is the repository the reference names.

    Folded to lower case on both sides, for the reason :class:`clownhead.search.PullRequest`
    folds its own: GitHub resolves ``Acme/Widgets`` and ``acme/widgets`` to one repository,
    and a remote URL holds whatever spelling somebody cloned with.
    """
    if remote is None or wanted is None:
        return False
    return (remote[0].lower(), remote[1].lower()) == (wanted[0].lower(), wanted[1].lower())


def _owner_and_repo(reference: Reference) -> tuple[str, str] | None:
    """What the reference calls its repository on GitHub, where it names one at all.

    A Jira key has no owner, and neither has a bare ``repo#309`` — which does name a
    repository, but not the one of several forks of it that is checked out here.
    """
    if reference.owner is None or reference.repo is None:
        return None
    return reference.owner, reference.repo
