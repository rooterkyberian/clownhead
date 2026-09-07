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
TICKET = Issue(tracker=Tracker.JIRA, key="PLAT-4471", host="kyberian.atlassian.net")


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


def clone(root: Path, name: str) -> Path:
    """A directory that exists, since a checkout is only offered where one is on disk."""
    directory = root / name
    directory.mkdir(parents=True)
    return directory


def remotes_at(monkeypatch, mapping: dict[Path, str]) -> None:
    """The same stub as :func:`remotes`, for tests whose paths are real directories."""
    remotes(monkeypatch, {str(path): url for path, url in mapping.items()})


def test_repos_for_offers_a_clone_no_session_has_ever_run_in(monkeypatch, tmp_path):
    """The normal state of a pull request opened from the web: checked out, never worked in."""
    worked_in = clone(tmp_path, "other")
    never_used = clone(tmp_path, "widgets")
    remotes_at(monkeypatch, {worked_in: "git@github.com:acme/other.git", never_used: "git@github.com:acme/widgets.git"})

    assert repos_for(ISSUE, [session("a", str(worked_in))], set()) == [never_used, worked_in]


def test_repos_for_looks_for_the_clone_beside_every_repository_the_fleet_knows(monkeypatch, tmp_path):
    elsewhere = clone(tmp_path / "personal", "toy")
    found = clone(tmp_path / "work", "widgets")
    remotes_at(monkeypatch, {elsewhere: "git@github.com:acme/toy.git", found: "git@github.com:acme/widgets.git"})
    fleet = [session("a", str(elsewhere)), session("b", str(tmp_path / "work" / "other"))]

    assert repos_for(ISSUE, fleet, set())[0] == found


def test_repos_for_drops_a_directory_that_only_shares_the_name(monkeypatch, tmp_path):
    """A name is the reason to ask git, and ``origin`` is the answer that counts."""
    worked_in = clone(tmp_path, "other")
    impostor = clone(tmp_path, "widgets")
    remotes_at(
        monkeypatch,
        {worked_in: "git@github.com:acme/other.git", impostor: "git@github.com:someone/widgets.git"},
    )

    assert repos_for(ISSUE, [session("a", str(worked_in))], set()) == [worked_in]


def test_repos_for_keeps_a_worked_in_clone_above_one_it_had_to_go_looking_for(monkeypatch, tmp_path):
    mirror = clone(tmp_path / "mirror", "widgets")
    worked_in = clone(tmp_path / "work", "widgets")
    remotes_at(monkeypatch, {mirror: "git@github.com:acme/widgets.git", worked_in: "git@github.com:acme/widgets.git"})
    fleet = [session("a", str(worked_in)), session("b", str(tmp_path / "mirror" / "toy"))]

    assert repos_for(ISSUE, fleet, set())[:2] == [worked_in, mirror]


def test_repos_for_matches_a_remote_however_it_was_spelled(monkeypatch, tmp_path):
    worked_in = clone(tmp_path, "other")
    found = clone(tmp_path, "widgets")
    remotes_at(monkeypatch, {worked_in: "git@github.com:acme/other.git", found: "git@github.com:Acme/Widgets.git"})

    assert repos_for(ISSUE, [session("a", str(worked_in))], set())[0] == found


def test_repos_for_offers_no_clone_where_none_is_checked_out(monkeypatch, tmp_path):
    worked_in = clone(tmp_path, "other")
    remotes_at(monkeypatch, {worked_in: "git@github.com:acme/other.git"})

    assert repos_for(ISSUE, [session("a", str(worked_in))], set()) == [worked_in]
