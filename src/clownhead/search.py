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
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clownhead.discovery import transcript_paths
from clownhead.issues import Issue, parse_issue
from clownhead.models import Session

CHUNK_BYTES = 1 << 20
OVERLAP_BYTES = 256

PULL_REQUEST_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)",
    re.IGNORECASE,
)
PULL_REQUEST_SHORTHAND = re.compile(r"(?:(?P<owner>[\w.-]+)/)?(?P<repo>[\w.-]+)#(?P<number>\d+)")


@dataclass(frozen=True)
class PullRequest:
    """A GitHub pull request, holding as much of one as the reference to it named."""

    repo: str
    number: int
    owner: str | None = None

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
        if self.owner is None:
            return str(self)
        return f"https://github.com/{self.owner}/{self.repo}/pull/{self.number}"

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

    def mention_pattern(self) -> re.Pattern[bytes]:
        """A pattern matching every way a transcript writes one pull request down.

        Both spellings carry the repository — the ``repo/pull/309`` of a URL and the
        ``repo#309`` of a mention — which is what keeps one repository's 309 apart from
        another's. The number is anchored on its right because ``pull/309`` is otherwise a
        substring of ``pull/3090``, and the repository on its left because ``data-platform``
        is one of ``my-data-platform``.
        """
        expression = rf"(?<![\w.-]){re.escape(self.repo)}(?:/pull/|#){self.number}(?!\d)"
        return re.compile(expression.encode(), re.IGNORECASE)


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
    pattern = reference.mention_pattern()
    return {
        session.session_id
        for session in sessions
        if any(mentions(path, pattern) for path in transcript_paths(session.session_id, root))
    }


def mentions(path: Path, pattern: re.Pattern[bytes]) -> bool:
    """Whether a transcript names the pull request anywhere in it.

    Read as bytes in chunks rather than parsed as the JSON it is: the question is only
    whether a string appears somewhere, and decoding megabytes of tool output to answer it
    costs far more than the answer is worth. Consecutive chunks overlap by more than the
    longest mention can be — a GitHub repository name stops at 100 characters — so one
    split across the boundary between them is still whole in one of the searches.
    """
    carry = b""
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK_BYTES):
                if pattern.search(carry + chunk):
                    return True
                carry = chunk[-OVERLAP_BYTES:]
    except OSError:
        return False
    return False
