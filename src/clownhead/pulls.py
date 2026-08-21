"""What GitHub says about the pull requests you have open.

Everything else in clownhead answers from the machine it is running on — the process
table, the registry, the transcripts — and answers whether or not anything else is
reachable. This module is the one place that asks somebody else, which makes it the one
place that can be unavailable: no ``gh``, no authentication, no network, or a GitHub
having a bad afternoon. Those are told apart from *you have no open pull requests*,
because a board that showed the same empty table for both would be lying half the time.

Two calls, and deliberately no more. ``gh search prs`` lists what you have open in one
request, whatever repository it lives in — ``gh pr list`` cannot, since it only ever knows
the checkout it was run in. Then one ``gh pr view`` per pull request for the state that
makes the list worth reading: whether the checks passed, whether anybody reviewed it, and
whether it would merge. That is a request per pull request, so it runs concurrently and
folds in as it arrives rather than holding the table back until the slowest of them
answers.
"""

from __future__ import annotations

import concurrent.futures
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from clownhead.issues import Unavailable, run_gh
from clownhead.search import PullRequest, parse_pull_request

MINE = "@me"
DEFAULT_LIMIT = 100
GH_TIMEOUT = 30.0
"""Longer than :data:`clownhead.issues.GH_TIMEOUT`, which is a courtesy lookup that can be
given up on. These calls are the whole content of a view, so they are given time to answer."""
MAX_WORKERS = 6

LIST_FIELDS = "number,repository,title,url,isDraft,createdAt,updatedAt"
STATUS_FIELDS = "reviewDecision,mergeStateStatus,statusCheckRollup"

FAILED_CONCLUSIONS = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"})
RUNNING_STATES = frozenset({"QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"})
BLOCKED_MERGE_STATES = frozenset({"DIRTY", "BEHIND", "BLOCKED"})

CHANGES_REQUESTED = "CHANGES_REQUESTED"
APPROVED = "APPROVED"
NONE = "NONE"

DRAFT_RANK = 10


class Checks(StrEnum):
    """How the checks on a pull request's head commit came out, in one word."""

    PASSING = "passing"
    FAILING = "failing"
    RUNNING = "running"
    NONE = "none"


@dataclass(frozen=True)
class Pull:
    """One open pull request, as ``gh search prs`` lists it.

    Frozen, and keyed on its reference, because the status arriving later is held beside
    it rather than folded into it: a row that has been listed but not yet enriched is a
    real state the board spends its first second in, and one worth being able to name.
    """

    reference: PullRequest
    title: str
    url: str
    is_draft: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class Status:
    """What GitHub says about the state of one pull request.

    The failing and running checks are named rather than counted. A red board says which
    of a dozen jobs went red, and that is the whole difference between knowing to rerun a
    flaky one and having to open the pull request to find out.

    :attr:`checks` is derived from those lists rather than stored beside them, so the two
    cannot disagree. Stored, ``Status(checks=PASSING, failing=("test",))`` is constructible
    and means nothing, and everything building one has to keep the summary in step with the
    lists by hand. The one fact the lists do not carry is whether anything ran at all,
    which is what :attr:`ran` is for: green and never-ran are both a pair of empty tuples,
    and they read very differently on a board.
    """

    failing: tuple[str, ...] = ()
    running: tuple[str, ...] = ()
    ran: bool = False
    review: str = NONE
    merge_state: str = "UNKNOWN"

    @property
    def checks(self) -> Checks:
        """How the checks came out, in one word, as the lists imply."""
        if self.failing:
            return Checks.FAILING
        if self.running:
            return Checks.RUNNING
        return Checks.PASSING if self.ran else Checks.NONE

    @property
    def needs_work(self) -> bool:
        """Whether the ball is with the author: something is red, or a reviewer objected."""
        return self.checks is Checks.FAILING or self.review == CHANGES_REQUESTED

    @property
    def ready(self) -> bool:
        """Whether nothing stands between this pull request and being merged."""
        return (
            self.review == APPROVED and self.checks is Checks.PASSING and self.merge_state not in BLOCKED_MERGE_STATES
        )


def mine(author: str = MINE, limit: int = DEFAULT_LIMIT) -> list[Pull]:
    """Every open pull request the author has, newest first, whatever repository it is in.

    Raises:
        Unavailable: when ``gh`` could not be run, could not authenticate, or answered
            with something that is not the list it was asked for.
    """
    raw = run_gh(
        ["search", "prs", f"--author={author}", "--state=open", f"--limit={limit}", "--json", LIST_FIELDS],
        GH_TIMEOUT,
    )
    try:
        listed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise Unavailable(f"gh answered with something that is not a pull request list: {raw[:120]}") from error
    found = [pull for item in listed if (pull := _pull(item)) is not None]
    return sorted(found, key=lambda pull: pull.created_at or datetime.min.replace(tzinfo=UTC), reverse=True)


def status_of(reference: PullRequest) -> Status | None:
    """What GitHub says about one pull request, or ``None`` where it would not say.

    A pull request whose status could not be read is still a pull request you have open,
    so this answers softly where :func:`mine` does not: the row stays on the board with
    its status blank, which is honest, rather than taking the board down with it.
    """
    if reference.owner is None:
        return None
    try:
        raw = run_gh(
            [
                "pr",
                "view",
                str(reference.number),
                "--repo",
                f"{reference.owner}/{reference.repo}",
                "--json",
                STATUS_FIELDS,
            ],
            GH_TIMEOUT,
        )
        payload = json.loads(raw)
    except (Unavailable, json.JSONDecodeError):
        return None
    return _status(payload)


def stream_statuses(pulls: Sequence[Pull]) -> Iterator[tuple[Pull, Status]]:
    """Read every pull request's status at once, handing each one over as it arrives.

    One request apiece is the cost of the cheap ``gh pr view`` this asks for, so they go
    out together. The pool is small on purpose: GitHub rate-limits a burst, and a board
    that got itself throttled would answer slower than the one that waited its turn.

    Yielded as they land rather than collected, because fifty of them take the better part
    of ten seconds and a board that showed nothing until the slowest answered would spend
    that whole time looking broken. Pull requests that would not say are dropped: a row
    with no status is the honest rendering of one, and there is nothing to hand over.
    """
    if not pulls:
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        asked = {pool.submit(status_of, pull.reference): pull for pull in pulls}
        for future in concurrent.futures.as_completed(asked):
            status = future.result()
            if status is not None:
                yield asked[future], status


def statuses(pulls: Sequence[Pull]) -> dict[PullRequest, Status]:
    """The same read, waited out and collected, for a caller with nothing to redraw."""
    return {pull.reference: status for pull, status in stream_statuses(pulls)}


def ranked(pulls: Iterable[Pull], found: Mapping[PullRequest, Status]) -> list[Pull]:
    """Order pull requests the way the fleet board orders sessions: what wants you, first.

    Something red or objected to leads, because that is work only you can do. Then what is
    approved and green, which is a merge waiting on somebody to press the button. Then
    everything still out for review, where the ball is with somebody else. Drafts sink
    below all of it — a draft is not yet anyone's problem, including yours — and within
    each band the most recently touched comes first.
    """
    return sorted(pulls, key=lambda pull: (_band(pull, found.get(pull.reference)), _staleness(pull)))


def _band(pull: Pull, status: Status | None) -> int:
    if pull.is_draft:
        return DRAFT_RANK
    if status is None:
        return 3
    if status.needs_work:
        return 0
    if status.ready:
        return 1
    return 2


def _staleness(pull: Pull) -> float:
    """How long ago the pull request was last touched, so that recent sorts first."""
    return -(pull.updated_at or pull.created_at or datetime.min.replace(tzinfo=UTC)).timestamp()


def _pull(item: dict[str, Any]) -> Pull | None:
    """One search result as a pull request, or ``None`` where it does not name one.

    The reference is parsed back out of the URL rather than assembled from the repository
    field, so that everything on the board carries the same reference the transcripts are
    searched with, built by the same parser.
    """
    reference = parse_pull_request(str(item.get("url", "")))
    if reference is None:
        return None
    return Pull(
        reference=reference,
        title=str(item.get("title") or ""),
        url=str(item["url"]),
        is_draft=bool(item.get("isDraft")),
        created_at=_moment(item.get("createdAt")),
        updated_at=_moment(item.get("updatedAt")),
    )


def _status(payload: dict[str, Any]) -> Status:
    rollup = payload.get("statusCheckRollup") or []
    failing, running = _classify(rollup)
    return Status(
        failing=failing,
        running=running,
        ran=bool(rollup),
        review=str(payload.get("reviewDecision") or NONE),
        merge_state=str(payload.get("mergeStateStatus") or "UNKNOWN"),
    )


def _classify(rollup: Iterable[Any]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Sort a rollup into what went red and what is still going.

    ``gh pr view`` flattens check runs and the older commit statuses into one list, which
    name themselves differently and report themselves differently, so both spellings are
    read and neither is assumed.
    """
    failing: set[str] = set()
    running: set[str] = set()
    for node in rollup:
        if not isinstance(node, dict):
            continue
        name = str(node.get("name") or node.get("context") or "?")
        outcome = node.get("conclusion") or node.get("state")
        if outcome in FAILED_CONCLUSIONS:
            failing.add(name)
        elif node.get("status") in RUNNING_STATES or outcome in RUNNING_STATES:
            running.add(name)
    return tuple(sorted(failing)), tuple(sorted(running))


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
