r"""Issue references, and the names a session started from one is given.

An issue is the other half of :mod:`clownhead.search`: a pull request is where work ended
up, an issue is where it began, and both are written down in transcripts the same way — as
a URL somebody pasted in. The two live apart because an issue answers a question a pull
request cannot, which is where to start work that has not been started yet.

Only URLs are recognised. A bare ``PLAT-4471`` is a tempting shorthand and is refused for
the same reason a bare ``#309`` is: ``[A-Z][A-Z0-9]+-\d+`` also spells ``UTF-8``,
``SHA-256`` and ``ISO-8601``, so accepting it would turn those into a search of every
transcript on the machine that comes back empty. The regex is the obvious thing to add
back later; this paragraph is here to say that it was considered and is not wanted.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

GH_BINARY_VAR = "CLOWNHEAD_GH_BIN"
GH_TIMEOUT = 10.0
SLUG_LIMIT = 48

ISSUE_URL = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/issues/(?P<number>\d+)",
    re.IGNORECASE,
)
JIRA_URL = re.compile(
    r"(?:https?://)?(?P<host>[\w.-]+\.[a-z]{2,})/(?:[\w./-]*?)"
    r"(?:browse/|[?&]selectedIssue=)(?P<key>[A-Z][A-Z\d]+-\d+)",
    re.IGNORECASE,
)


class Tracker(StrEnum):
    """Which issue tracker a reference was written for."""

    GITHUB = "github"
    JIRA = "jira"


@dataclass(frozen=True)
class Issue:
    """An issue, holding as much of one as the URL naming it carried.

    Frozen because the overseer remembers what each reference matched, keyed by the
    reference itself.
    """

    tracker: Tracker
    key: str
    repo: str | None = None
    owner: str | None = None
    host: str | None = None

    def __str__(self) -> str:
        """Render the reference the short way its tracker writes it."""
        if self.tracker is Tracker.JIRA:
            return self.key
        return f"{self.owner}/{self.repo}#{self.key}" if self.owner else f"{self.repo}#{self.key}"

    @property
    def prompt(self) -> str:
        """The URL to hand a session started for this issue, as its first thing to read."""
        if self.tracker is Tracker.JIRA:
            return f"https://{self.host}/browse/{self.key}"
        return f"https://github.com/{self.owner}/{self.repo}/issues/{self.key}"

    @property
    def base_slug(self) -> str:
        """The part of a worktree name that identifies the issue and nothing else."""
        return self.key.lower() if self.tracker is Tracker.JIRA else f"issue-{self.key}"

    @property
    def title_query(self) -> list[str] | None:
        """The ``gh`` arguments that name this issue, or ``None`` where nothing can ask.

        Jira has no equivalent worth the credentials it would need, so a Jira worktree is
        named for its key alone — which is already unique and already readable.
        """
        if self.tracker is Tracker.JIRA or not self.owner or not self.repo:
            return None
        return ["issue", "view", self.key, "--repo", f"{self.owner}/{self.repo}"]

    def mention_pattern(self) -> re.Pattern[bytes]:
        """A pattern matching every way a transcript writes this issue down.

        A GitHub issue carries its repository, in the ``repo/issues/2`` of a URL and the
        ``repo#2`` of a mention — the same anchoring :func:`clownhead.search.mention_pattern`
        explains, and the same overlap GitHub itself has, where ``repo#2`` names an issue
        and a pull request interchangeably.

        A Jira key carries its project instead, and needs no repository to be unambiguous.
        It is matched case-insensitively so that the lowercase form a worktree path is
        named with counts as a mention too, which is safe here only because the key can
        have come from nowhere but a URL.
        """
        if self.tracker is Tracker.JIRA:
            expression = rf"(?<![\w-]){re.escape(self.key)}(?!\d)"
        else:
            expression = rf"(?<![\w.-]){re.escape(self.repo or '')}(?:/issues/|#){self.key}(?!\d)"
        return re.compile(expression.encode(), re.IGNORECASE)


def parse_issue(text: str) -> Issue | None:
    """Read an issue out of a URL, if the text has one in it.

    Searched rather than matched, so a line with a link somewhere in it still resolves —
    which is what pasting one into a filter box looks like.
    """
    stripped = text.strip()
    if not stripped:
        return None
    found = ISSUE_URL.search(stripped)
    if found is not None:
        return Issue(
            tracker=Tracker.GITHUB,
            key=found["number"],
            repo=found["repo"],
            owner=found["owner"],
        )
    found = JIRA_URL.search(stripped)
    if found is None:
        return None
    return Issue(tracker=Tracker.JIRA, key=found["key"].upper(), host=found["host"])


def slug(base: str, title: str | None = None) -> str:
    """A worktree name for an issue, with as much of its title as will fit.

    The name becomes a directory and a branch at once, so it is reduced to the characters
    both accept without argument. The title is cut on a word boundary rather than
    mid-word: a name is read at a glance in a listing, and half a word reads as a typo.
    """
    if not title:
        return base
    words = _slugify(title).split("-")
    room = SLUG_LIMIT - len(base)
    kept: list[str] = []
    for word in words:
        if not word or len("-".join([*kept, word])) + 1 > room:
            break
        kept.append(word)
    return f"{base}-{'-'.join(kept)}" if kept else base


def fetch_title(query: Sequence[str] | None) -> str | None:
    """What GitHub calls the thing a reference names, or ``None`` if it will not say.

    Every way of failing — no ``gh`` installed, no authentication, no network, a reference
    with nothing to look it up by — answers the same way, because the title is a courtesy.
    A worktree named ``issue-2`` is worse than one named for the work, and far better than
    a command that refused to run because GitHub was unreachable.
    """
    if query is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603
            [gh_binary(), *query, "--json", "title", "--jq", ".title"],
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def gh_binary() -> str:
    """Path to the GitHub CLI, overridable for tests via ``CLOWNHEAD_GH_BIN``."""
    return os.environ.get(GH_BINARY_VAR, "gh")


def _slugify(text: str) -> str:
    """Reduce a title to the characters a directory and a branch name both accept.

    Accents are decomposed and dropped rather than deleted whole, so ``Ünïcödé`` becomes
    ``unicode`` instead of ``n-c-d``: a title stripped to its consonants names nothing.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", folded.lower())).strip("-")
