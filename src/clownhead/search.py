"""Finding the sessions that worked on a pull request or an issue.

Nothing a session publishes about itself says which pull request or ticket it belongs to.
That fact only ever exists in what was said — a URL pasted in, a ``gh pr view`` that
scrolled past — so the only place to look is the transcripts, which run to hundreds of
megabytes across a fleet. Reading them is cheap once and ruinous on every keystroke, so
callers are expected to hold on to what comes back rather than ask the same question twice.

A reference is only recognised with something unambiguous attached: a pull request carries
its repository, because that is how one is written down — as a URL, or as ``repo#309``. A
bare ``#309`` names a different pull request in every checkout on the machine, and matching
it would answer with all of them at once. :mod:`clownhead.issues` refuses a bare Jira key
for the same reason, and says so at greater length.

The question is asked both ways round. Given a reference, :func:`sessions_mentioning` says
which sessions worked on it; given a session, :func:`pulls_mentioned` says which pull
requests it worked on. The second is what a board of pull requests is built from, because
one pass over the transcripts answers for every pull request at once where the first would
read the whole corpus again for each one asked about.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import re2

from clownhead.discovery import transcript_paths
from clownhead.issues import Issue, parse_issue
from clownhead.models import Session
from clownhead.scan import NAME_BYTES, Mention, mapped, mention

PULL_REQUEST_PATTERN = r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)"
"""How a pull request URL is spelled, in the one place both readers of one take it from.

Written once because the two readings must not disagree: the whole premise of the reverse
scan is that it agrees with the forward search, and a repository name or a path shape
taught to one and not the other shows up as a session the board says worked on a pull
request that the filter then cannot find.

It starts at the host rather than at the scheme, and the optional ``https://`` is added
back below for the reader that wants it. Everything either reader takes out of a match
lives to the right of the host, so the two spellings capture identically and the shorter
one is what both can share.
"""

PULL_REQUEST_URL = re.compile(rf"(?:https?://)?(?:www\.)?{PULL_REQUEST_PATTERN}", re.IGNORECASE)
PULL_REQUEST_SHORTHAND = re.compile(r"(?:(?P<owner>[\w.-]+)/)?(?P<repo>[\w.-]+)#(?P<number>\d+)")

ANY_PULL_REQUEST = re2.compile(PULL_REQUEST_PATTERN.encode())
r"""Every pull request a transcript names, rather than one that was asked about.

URLs alone, where :meth:`PullRequest.mention_pattern` also takes the ``repo#309`` shorthand.
Asked of a known reference, the shorthand is anchored on a repository somebody named and
cannot mean anything else; asked of the whole transcript, it is a pattern loose enough to
read ``PLAT-4471#3`` and half the diffs that ever scrolled past as pull requests, and every
one of those becomes a row on a board claiming the session worked on it. A URL is the
spelling that carries its own proof.

Issues and Jira keys are left to :func:`sessions_mentioning`, which is asked about one
reference at a time. This runs over every transcript on the machine to build a board of
pull requests, and there is no board of issues for the other halves to fill.

Case-sensitive, alone among the patterns here. ``\w`` matches both cases in a bytes pattern
already, so the flag would reach only ``github.com`` and ``pull``, which arrive in a
transcript as something a browser or ``gh`` printed, in lower case.
"""


@dataclass(frozen=True, eq=False)
class PullRequest:
    """A GitHub pull request, holding as much of one as the reference to it named.

    Compared and hashed without regard to the case its repository was written in, because
    GitHub resolves ``Acme/Widgets#7`` and ``acme/widgets#7`` to one pull request and a
    transcript holds whatever somebody pasted. Two spellings would otherwise be two rows on
    the board, each with half the sessions — and two separate transcript searches for the
    one answer.

    The number is compared as itself: it is a number, and nothing about it varies by
    spelling.
    """

    repo: str
    number: int
    owner: str | None = None

    def __eq__(self, other: object) -> bool:
        """Whether both references name the pull request GitHub would resolve them to."""
        if not isinstance(other, PullRequest):
            return NotImplemented
        return self._identity == other._identity

    def __hash__(self) -> int:
        """Hash on the same folded identity that :meth:`__eq__` compares."""
        return hash(self._identity)

    @property
    def _identity(self) -> tuple[str | None, str, int]:
        return (self.owner.lower() if self.owner else None, self.repo.lower(), self.number)

    def __str__(self) -> str:
        """Render the reference back, owner included where one was given."""
        return f"{self.owner}/{self.repo}#{self.number}" if self.owner else f"{self.repo}#{self.number}"

    @property
    def prompt(self) -> str:
        """The reference to hand a session started for it, as its first thing to read.

        A URL where there is an owner to build one from, and the shorthand as written
        otherwise — which is still enough for whoever reads it to find the pull request,
        standing as they are in the repository it belongs to.
        """
        return str(self) if self.owner is None else self.url

    @property
    def url(self) -> str:
        """Where this pull request lives, or ``None``-shaped emptiness where it cannot be said.

        Separate from :attr:`prompt` because they part company exactly when it matters: a
        reference with no owner has no URL to build, and ``prompt`` answers that with the
        ``widgets#309`` shorthand — which is the right thing to hand a session standing in
        the repository, and the wrong thing to hand a browser.
        """
        return "" if self.owner is None else f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

    @property
    def base_slug(self) -> str:
        """The part of a worktree name that identifies the pull request and nothing else."""
        return f"pr-{self.number}"

    @property
    def title_query(self) -> list[str] | None:
        """The ``gh`` arguments that name this pull request, or ``None`` without an owner."""
        if self.owner is None:
            return None
        return ["pr", "view", str(self.number), "--repo", f"{self.owner}/{self.repo}"]

    def mention_pattern(self) -> Mention:
        """A test matching every way a transcript writes one pull request down.

        Both spellings carry the repository — the ``repo/pull/309`` of a URL and the
        ``repo#309`` of a mention — which is what keeps one repository's 309 apart from
        another's. The number is bounded on its right because ``pull/309`` is otherwise a
        substring of ``pull/3090``, and the repository on its left because ``data-platform``
        is one of ``my-data-platform``. Both bounds are checked by :class:`Mention` on the
        bytes around a match, since the engine underneath has no lookaround to carry them.
        """
        return mention(rf"{re.escape(self.repo)}(?:/pull/|#){self.number}", NAME_BYTES)


type Reference = PullRequest | Issue
"""Something a transcript can be searched for: where work ended up, or where it began."""


def parse_pull_request(text: str) -> PullRequest | None:
    """Read a pull request out of a URL or an ``owner/repo#123`` reference, if it is one.

    A URL is searched for rather than matched, so a line with a link somewhere in it still
    resolves — that is what pasting one into a filter box looks like. The shorthand has to
    be the whole of the string, or every path with a fragment on the end would look like a
    pull request.
    """
    stripped = text.strip()
    if not stripped:
        return None
    found = PULL_REQUEST_URL.search(stripped) or PULL_REQUEST_SHORTHAND.fullmatch(stripped)
    if found is None:
        return None
    return PullRequest(repo=found["repo"], number=int(found["number"]), owner=found["owner"])


def parse_reference(text: str) -> Reference | None:
    """Read whatever kind of reference the text names, if it names one.

    Pull requests are tried first, which settles the one spelling both could claim:
    ``repo#309`` stays a pull request, as it has always been here. GitHub gives issues and
    pull requests one number space and writes both that way, so nothing can be read out of
    the text alone — and a pull request is the half that already had a meaning.
    """
    return parse_pull_request(text) or parse_issue(text)


def sessions_mentioning(
    reference: Reference,
    sessions: Iterable[Session],
    root: Path | None = None,
) -> set[str]:
    """The ids of the sessions whose transcripts name the reference.

    The sessions to look in are passed rather than discovered, so the answer covers
    exactly the fleet the caller is already showing and inherits whatever scoped it.
    """
    wanted = reference.mention_pattern()
    return {
        session.session_id
        for session in sessions
        if any(mentions(path, wanted) for path in transcript_paths(session.session_id, root))
    }


def mentions(path: Path, wanted: Mention) -> bool:
    """Whether a transcript names the pull request anywhere in it."""
    return any(wanted.found_in(buffer) for buffer in mapped(path))


def pulls_mentioned(session_id: str, root: Path | None = None) -> list[PullRequest]:
    """The pull requests one session named, the most recently named first.

    The inverse of :func:`sessions_mentioning`, and the cheaper half of it: one transcript
    rather than a fleet of them, which is what makes it affordable to ask of whichever
    session is under the cursor.

    Ordered by where each was last named rather than first, because a session that opened
    on one pull request and spent the afternoon on its follow-up belongs to the follow-up.
    A session with several transcripts — one per subagent — is ordered across all of them
    together, since an offset is only comparable within a file: the transcripts are read
    newest last, so the main thread's own mentions come after a subagent's.
    """
    latest: dict[PullRequest, tuple[int, int]] = {}
    for order, path in enumerate(sorted(transcript_paths(session_id, root), key=_read_order)):
        for reference, offset in _pulls_in(path).items():
            ranking = (order, offset)
            latest[reference] = max(ranking, latest.get(reference, ranking))
    return sorted(latest, key=lambda reference: latest[reference], reverse=True)


def pulls_by_session(sessions: Iterable[Session], root: Path | None = None) -> dict[str, list[PullRequest]]:
    """What each session named, keyed by session id, for a whole fleet in one pass.

    One pass over the transcripts answers for every session there is, where asking one at a
    time costs a worker and a wake-up apiece. That is what a column of pull requests needs:
    every visible row at once, rather than whichever row the cursor happens to rest on.

    Sessions naming nothing are kept, mapped to an empty list. The distinction matters to
    everything downstream — a session read and found to name nothing is not the same as one
    nobody has read, and only the caller holding this map can still tell them apart.
    """
    return {session.session_id: pulls_mentioned(session.session_id, root) for session in sessions}


def sessions_by_pull(sessions: Iterable[Session], root: Path | None = None) -> dict[PullRequest, list[str]]:
    """The same pass read the other way round: which sessions named each pull request.

    One pass over the transcripts answers for every pull request there is, where
    :func:`sessions_mentioning` reads the same bytes again for each one asked about. That
    is what makes a board of pull requests possible at all: a fleet holds far more pull
    requests than sessions, so asking per pull request would cost a full corpus read apiece.

    The references keying this were spelled by whoever pasted the URL and the caller
    matching against them holds what GitHub spelled, which is why :class:`PullRequest`
    compares without regard to case.
    """
    holders: dict[PullRequest, list[str]] = {}
    for session_id, named in pulls_by_session(sessions, root).items():
        for reference in named:
            holders.setdefault(reference, []).append(session_id)
    return holders


def _read_order(path: Path) -> tuple[float, str]:
    """Transcripts oldest first, so the newest mention of a pull request is the last one.

    A transcript with no modification time to read sorts first: it is either gone or
    unreadable, and either way its mentions are the ones to be overruled.
    """
    try:
        return (path.stat().st_mtime, path.name)
    except OSError:
        return (0.0, path.name)


def _pulls_in(path: Path) -> dict[PullRequest, int]:
    """Every pull request named in one transcript, and how far in it was last named.

    Counted by the bytes that spelled it before anything is turned into a reference. One
    URL is repeated thousands of times across a long transcript, and a session that talked
    about its pull request all afternoon would otherwise build thousands of identical
    references and hash each one — which is not free, since :class:`PullRequest` folds case
    to compare. Deduplicating on the raw span first makes that per distinct spelling rather
    than per mention, and it measured as most of the cost of a full-fleet scan.

    The surviving spellings go through :func:`parse_pull_request`, so every reference on
    the board is built by the one parser, out of the one grammar.
    """
    spellings: dict[bytes, int] = {}
    for buffer in mapped(path):
        for match in ANY_PULL_REQUEST.finditer(buffer):
            offset = match.start()
            if offset > spellings.get(match.group(), -1):
                spellings[match.group()] = offset

    found: dict[PullRequest, int] = {}
    for spelling, offset in spellings.items():
        reference = parse_pull_request(spelling.decode())
        if reference is not None:
            found[reference] = max(found.get(reference, -1), offset)
    return found
