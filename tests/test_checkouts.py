from pathlib import Path

import pytest

from clownhead import checkouts, worktrees
from clownhead.checkouts import repos_for
from clownhead.issues import Issue, Tracker
from clownhead.models import Session
from clownhead.search import PullRequest


def session(session_id: str, cwd: str) -> Session:
    return Session(session_id=session_id, cwd=Path(cwd))


def remotes(monkeypatch, mapping: dict[str, str]):
    known = {Path(path): worktrees.parse_remote(url) for path, url in mapping.items()}
    monkeypatch.setattr(checkouts.worktrees, "remote_of", lambda repo: known.get(repo))


ISSUE = Issue(tracker=Tracker.GITHUB, key="2", repo="widgets", owner="acme")
TICKET = Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="craft.atlassian.net")


def test_repos_for_puts_the_matching_remote_first(monkeypatch):
    remotes(
        monkeypatch,
        {
            "/dev/aaa": "git@github.com:acme/other.git",
            "/dev/widgets": "git@github.com:acme/widgets.git",
        },
    )
    fleet = [session("a", "/dev/aaa"), session("b", "/dev/widgets")]

    assert repos_for(ISSUE, fleet, set()) == [Path("/dev/widgets"), Path("/dev/aaa")]


def test_repos_for_falls_back_to_the_repository_that_named_it(monkeypatch):
    remotes(monkeypatch, {})
    fleet = [session("a", "/dev/aaa"), session("b", "/dev/zzz")]

    assert repos_for(TICKET, fleet, {"b"}) == [Path("/dev/zzz"), Path("/dev/aaa")]


def test_repos_for_reads_a_worktree_session_back_to_its_repository(monkeypatch):
    remotes(monkeypatch, {})
    fleet = [session("a", "/dev/aaa"), session("b", "/dev/zzz/.claude/worktrees/plat-4471")]

    assert repos_for(TICKET, fleet, {"b"}) == [Path("/dev/zzz"), Path("/dev/aaa")]


def test_repos_for_offers_every_checkout_even_when_nothing_matches(monkeypatch):
    remotes(monkeypatch, {})
    fleet = [session("a", "/dev/bbb"), session("b", "/dev/aaa")]

    assert repos_for(TICKET, fleet, set()) == [Path("/dev/aaa"), Path("/dev/bbb")]


def test_repos_for_prefers_the_remote_over_a_session_that_named_it(monkeypatch):
    remotes(monkeypatch, {"/dev/widgets": "https://github.com/acme/widgets"})
    fleet = [session("a", "/dev/aaa"), session("b", "/dev/widgets")]

    assert repos_for(ISSUE, fleet, {"a"}) == [Path("/dev/widgets"), Path("/dev/aaa")]


def test_repos_for_asks_git_nothing_when_the_reference_names_no_repository(monkeypatch):
    monkeypatch.setattr(
        checkouts.worktrees,
        "remote_of",
        lambda repo: pytest.fail("git was asked about a reference with no repository"),
    )

    assert repos_for(TICKET, [session("a", "/dev/aaa")], set()) == [Path("/dev/aaa")]


def test_repos_for_treats_a_shorthand_pull_request_as_naming_no_repository(monkeypatch):
    monkeypatch.setattr(
        checkouts.worktrees,
        "remote_of",
        lambda repo: pytest.fail("git was asked about a reference with no owner"),
    )

    assert repos_for(PullRequest("widgets", 309), [session("a", "/dev/aaa")], set()) == [Path("/dev/aaa")]


def test_repos_for_answers_nothing_for_an_empty_fleet(monkeypatch):
    remotes(monkeypatch, {})

    assert repos_for(ISSUE, [], set()) == []
