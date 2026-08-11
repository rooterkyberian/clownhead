import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clownhead import worktrees
from clownhead.discovery import Process
from clownhead.models import Session, Status
from clownhead.worktrees import Candidate, Worktree

NOW = datetime(2026, 8, 10, tzinfo=UTC)

PORCELAIN = """worktree /repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /repo/.claude/worktrees/judge
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature/judge
locked claude session judge (pid 72883 start Tue Aug 11 09:20:08 2026)

worktree /repo/.claude/worktrees/detached
HEAD 3333333333333333333333333333333333333333
detached

worktree /repo/.claude/worktrees/quiet
HEAD 4444444444444444444444444444444444444444
branch refs/heads/worktree-quiet
locked

worktree /repo/../elsewhere/by-hand
HEAD 5555555555555555555555555555555555555555
branch refs/heads/by-hand
"""


def worktree(path: Path, name: str = "judge", **overrides: object) -> Worktree:
    fields: dict[str, object] = {
        "path": path / ".claude" / "worktrees" / name,
        "repo": path,
        "name": name,
        "branch": f"feature/{name}",
        "head": "2" * 40,
        "lock": None,
    }
    return Worktree(**{**fields, **overrides})  # type: ignore[arg-type]


def session(cwd: Path, status: Status = Status.CLOSED, updated: datetime | None = None) -> Session:
    return Session(session_id="a-b", cwd=cwd, name="one", status=status, updated_at=updated)


def claude(pid: int) -> dict[int, Process]:
    return {pid: Process(pid=pid, ppid=1, tty=None, command="claude --resume")}


def git(directory: Path, *args: str) -> str:
    """Run git in a test repository, insulated from whoever is running the suite."""
    environment = {
        **os.environ,
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_AUTHOR_NAME": "clownhead",
        "GIT_AUTHOR_EMAIL": "clownhead@example.com",
        "GIT_COMMITTER_NAME": "clownhead",
        "GIT_COMMITTER_EMAIL": "clownhead@example.com",
    }
    completed = subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=directory,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout


def commit(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text)
    git(directory, "add", name)
    git(directory, "commit", "-m", f"add {name}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository with one worktree branched off it, both with a commit of their own."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    commit(root, "README", "start\n")
    git(root, "worktree", "add", "-b", "feature", ".claude/worktrees/feature")
    commit(root / ".claude" / "worktrees" / "feature", "feature.txt", "work\n")
    return root


def test_parse_worktrees_keeps_only_the_ones_claude_code_made():
    found = worktrees.parse_worktrees(PORCELAIN, Path("/repo"))

    assert [entry.name for entry in found] == ["judge", "detached", "quiet"]


def test_parse_worktrees_reads_branch_head_and_lock():
    judge = worktrees.parse_worktrees(PORCELAIN, Path("/repo"))[0]

    assert judge.path == Path("/repo/.claude/worktrees/judge")
    assert judge.branch == "feature/judge"
    assert judge.head == "2" * 40
    assert judge.lock == "claude session judge (pid 72883 start Tue Aug 11 09:20:08 2026)"


def test_parse_worktrees_marks_a_detached_worktree_as_having_no_branch():
    detached = worktrees.parse_worktrees(PORCELAIN, Path("/repo"))[1]

    assert detached.branch is None


def test_parse_worktrees_tells_a_lock_without_a_reason_from_no_lock_at_all():
    detached, quiet = worktrees.parse_worktrees(PORCELAIN, Path("/repo"))[1:]

    assert detached.lock is None
    assert quiet.lock == ""


def test_parse_worktrees_answers_nothing_for_a_repository_with_no_worktrees():
    assert worktrees.parse_worktrees("worktree /repo\nHEAD 1111\nbranch refs/heads/main\n", Path("/repo")) == []


@pytest.mark.parametrize(
    ("lock", "expected"),
    [
        ("claude session judge (pid 72883 start Tue Aug 11 09:20:08 2026)", 72883),
        ("claude session x (pid 1)", 1),
        ("somebody locked this by hand", None),
        ("", None),
        (None, None),
    ],
)
def test_lock_holder_reads_the_process_a_lock_names(lock, expected):
    assert worktrees.lock_holder(lock) == expected


def test_repos_of_answers_the_repository_behind_a_worktree_session():
    sessions = [session(Path("/dev/repo/.claude/worktrees/one")), session(Path("/dev/other"))]

    assert worktrees.repos_of(sessions) == {Path("/dev/repo"), Path("/dev/other")}


def test_a_live_session_holds_its_worktree(tmp_path):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)

    kept = worktrees.guard_for(entry, [session(entry.path, Status.BUSY)], NOW, timedelta(0), NOW, {})

    assert kept == "a live session is in it"


def test_a_lock_held_by_a_running_session_holds_its_worktree(tmp_path):
    entry = worktree(tmp_path, lock="claude session judge (pid 4242 start x)")
    entry.path.mkdir(parents=True)

    kept = worktrees.guard_for(entry, [], NOW, timedelta(0), NOW, claude(4242))

    assert kept == "locked by a live session"


def test_a_lock_left_by_a_session_that_crashed_holds_nothing(tmp_path, monkeypatch):
    entry = worktree(tmp_path, lock="claude session judge (pid 4242 start x)")
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: 0)

    assert worktrees.guard_for(entry, [], NOW, timedelta(0), NOW, {}) is None


def test_a_lock_nobody_explained_holds_its_worktree(tmp_path):
    entry = worktree(tmp_path, lock="")
    entry.path.mkdir(parents=True)

    kept = worktrees.guard_for(entry, [], NOW, timedelta(0), NOW, {})

    assert kept == "locked by a live session"


def test_a_worktree_used_more_recently_than_the_filter_is_held(tmp_path):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)

    kept = worktrees.guard_for(entry, [], NOW - timedelta(days=1), timedelta(days=7), NOW, {})

    assert kept == "newer than the age filter"


def test_uncommitted_changes_hold_a_worktree(tmp_path, monkeypatch):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: True)

    assert worktrees.guard_for(entry, [], None, timedelta(0), NOW, {}) == "uncommitted changes"


@pytest.mark.parametrize(("ahead", "expected"), [(1, "1 commit on no remote"), (4, "4 commits on no remote")])
def test_commits_on_no_remote_hold_a_worktree(tmp_path, monkeypatch, ahead, expected):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: ahead)

    assert worktrees.guard_for(entry, [], None, timedelta(0), NOW, {}) == expected


def test_a_worktree_git_cannot_answer_for_is_held(tmp_path, monkeypatch):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)

    def refuse(_: Worktree) -> bool:
        raise LookupError("not a git repository")

    monkeypatch.setattr(worktrees, "is_dirty", refuse)

    assert worktrees.guard_for(entry, [], None, timedelta(0), NOW, {}) == "git could not say: not a git repository"


def test_a_worktree_already_gone_from_disk_is_held_by_nothing(tmp_path):
    entry = worktree(tmp_path)

    assert worktrees.guard_for(entry, [], None, timedelta(0), NOW, {}) is None


def test_a_closed_session_does_not_hold_its_worktree(tmp_path, monkeypatch):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: 0)

    assert worktrees.guard_for(entry, [session(entry.path, Status.CLOSED)], None, timedelta(0), NOW, {}) is None


def test_last_used_prefers_the_newest_session_that_worked_in_it(tmp_path):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    recent = datetime(2030, 1, 1, tzinfo=UTC)

    used = worktrees.last_used(entry, [session(entry.path, updated=NOW), session(entry.path, updated=recent)])

    assert used == recent


def test_last_used_falls_back_to_what_git_wrote_when_no_session_remembers(tmp_path):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)

    assert worktrees.last_used(entry, []) is not None


def test_reading_a_worktrees_state_does_not_make_it_look_freshly_used(repo):
    entry = worktrees.worktrees_of(repo)[0]
    before = worktrees.last_used(entry, [])

    assert worktrees.is_dirty(entry) is False
    assert worktrees.last_used(entry, []) == before


def test_worktrees_of_reads_a_real_repository(repo):
    found = worktrees.worktrees_of(repo)

    assert [entry.name for entry in found] == ["feature"]
    assert found[0].branch == "feature"
    assert found[0].path == repo / ".claude" / "worktrees" / "feature"


def test_worktrees_of_answers_nothing_for_somewhere_that_is_not_a_repository(tmp_path):
    assert worktrees.worktrees_of(tmp_path) == []


def test_a_branch_nobody_merged_is_not_merged(repo):
    assert worktrees.is_merged(worktrees.worktrees_of(repo)[0]) is False


def test_a_branch_merged_whole_is_merged(repo):
    git(repo, "merge", "--no-ff", "-m", "merge feature", "feature")

    assert worktrees.is_merged(worktrees.worktrees_of(repo)[0]) is True


def test_a_branch_squashed_into_the_default_branch_is_merged(repo):
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature")

    assert worktrees.is_merged(worktrees.worktrees_of(repo)[0]) is True


def test_a_squash_is_seen_where_git_has_no_identity_to_write_with(repo, monkeypatch):
    """The squash check writes a commit object, which a machine with no `user.email` cannot."""
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(name, "")

    assert worktrees.is_merged(worktrees.worktrees_of(repo)[0]) is True


def test_a_worktree_with_uncommitted_work_is_dirty(repo):
    entry = worktrees.worktrees_of(repo)[0]
    (entry.path / "scratch.txt").write_text("unsaved\n")

    assert worktrees.is_dirty(entry) is True


def test_a_repository_with_no_remote_counts_nothing_as_unpushed(repo):
    assert worktrees.unpushed(worktrees.worktrees_of(repo)[0]) == 0


def test_removing_a_worktree_takes_the_checkout_and_leaves_the_branch(repo):
    entry = worktrees.worktrees_of(repo)[0]

    worktrees.remove(entry)

    assert not entry.path.exists()
    assert worktrees.worktrees_of(repo) == []
    assert "feature" in git(repo, "branch", "--list", "feature")


def test_removing_a_worktree_leaves_its_branch_unless_asked(repo):
    worktrees.remove(worktrees.worktrees_of(repo)[0], branch=False)

    assert "feature" in git(repo, "branch", "--list", "feature")


def test_removing_a_worktree_takes_a_merged_branch_when_asked(repo):
    git(repo, "merge", "--no-ff", "-m", "merge feature", "feature")

    worktrees.remove(worktrees.worktrees_of(repo)[0], branch=True)

    assert git(repo, "branch", "--list", "feature").strip() == ""


def test_removing_a_worktree_takes_a_squash_merged_branch_git_would_refuse(repo):
    """`git branch -d` refuses a squash merge; the module's own check knows better."""
    entry = worktrees.worktrees_of(repo)[0]
    git(repo, "merge", "--squash", "feature")
    git(repo, "commit", "-m", "squashed feature")

    assert not worktrees._git_ok(repo, "branch", "-d", "feature")

    worktrees.remove(entry, branch=True)

    assert git(repo, "branch", "--list", "feature").strip() == ""


def test_removing_a_worktree_keeps_a_branch_whose_work_is_nowhere_else(repo):
    entry = worktrees.worktrees_of(repo)[0]

    with pytest.raises(LookupError, match="not merged"):
        worktrees.remove(entry, branch=True)

    assert not entry.path.exists()
    assert "feature" in git(repo, "branch", "--list", "feature")


def test_removing_a_detached_worktrees_branch_says_there_is_none(tmp_path):
    with pytest.raises(LookupError, match="detached HEAD"):
        worktrees.remove_branch(worktree(tmp_path, branch=None))


def test_removing_a_worktree_a_running_session_locked_is_refused(tmp_path):
    entry = worktree(tmp_path, lock="claude session judge (pid 4242 start x)")

    with pytest.raises(LookupError, match="locked by a live session"):
        worktrees.remove(entry, claude(4242))


def test_removing_a_worktree_unlocks_what_a_crashed_session_left(repo):
    entry = worktrees.worktrees_of(repo)[0]
    git(repo, "worktree", "lock", "--reason", "claude session feature (pid 999999 start x)", str(entry.path))

    worktrees.remove(worktrees.worktrees_of(repo)[0], {})

    assert not entry.path.exists()


def test_removing_a_worktree_with_uncommitted_work_is_refused_by_git(repo):
    entry = worktrees.worktrees_of(repo)[0]
    (entry.path / "scratch.txt").write_text("unsaved\n")

    with pytest.raises(LookupError):
        worktrees.remove(entry)

    assert entry.path.exists()


def test_survey_finds_a_worktree_no_session_remembers(tmp_path, monkeypatch):
    orphan = worktree(tmp_path, "orphan")
    orphan.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "worktrees_of", lambda _: [orphan])
    monkeypatch.setattr(worktrees, "is_merged", lambda _: False)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: 0)

    surveyed = worktrees.survey([session(tmp_path / "src")], processes={})

    assert [candidate.worktree.name for candidate in surveyed] == ["orphan"]
    assert surveyed[0].removable


def test_survey_matches_a_session_working_below_a_worktree_to_it(tmp_path, monkeypatch):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "worktrees_of", lambda _: [entry])
    monkeypatch.setattr(worktrees, "is_merged", lambda _: False)

    surveyed = worktrees.survey([session(entry.path / "src" / "deep", Status.BUSY)], processes={})

    assert surveyed[0].kept_for == "a live session is in it"


def test_survey_lists_each_worktree_once_however_many_sessions_lead_to_it(tmp_path, monkeypatch):
    entry = worktree(tmp_path)
    entry.path.mkdir(parents=True)
    monkeypatch.setattr(worktrees, "worktrees_of", lambda _: [entry])
    monkeypatch.setattr(worktrees, "is_merged", lambda _: False)
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: 0)

    surveyed = worktrees.survey([session(tmp_path / "a"), session(tmp_path / "b"), session(entry.path)], processes={})

    assert len(surveyed) == 1


def test_survey_narrowed_to_one_path_weighs_up_nothing_else(tmp_path, monkeypatch):
    wanted = worktree(tmp_path, "wanted")
    other = worktree(tmp_path, "other")
    for entry in (wanted, other):
        entry.path.mkdir(parents=True)
    weighed: list[str] = []
    monkeypatch.setattr(worktrees, "worktrees_of", lambda _: [wanted, other])
    monkeypatch.setattr(worktrees, "is_merged", lambda entry: bool(weighed.append(entry.name)))
    monkeypatch.setattr(worktrees, "is_dirty", lambda _: False)
    monkeypatch.setattr(worktrees, "unpushed", lambda _: 0)

    surveyed = worktrees.survey([session(tmp_path)], processes={}, only=wanted.path / "src")

    assert [candidate.worktree.name for candidate in surveyed] == ["wanted"]
    assert weighed == ["wanted"]


def test_a_candidate_being_kept_is_not_removable():
    entry = worktree(Path("/repo"))

    assert Candidate(worktree=entry).removable
    assert not Candidate(worktree=entry, kept_for="uncommitted changes").removable


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:acme/widgets.git", ("acme", "widgets")),
        ("git@github.com:acme/widgets", ("acme", "widgets")),
        ("https://github.com/acme/widgets.git", ("acme", "widgets")),
        ("https://github.com/acme/widgets", ("acme", "widgets")),
        ("https://github.com/acme/widgets/", ("acme", "widgets")),
        ("ssh://git@github.com/acme/widgets.git", ("acme", "widgets")),
        ("ssh://git@github.com:22/acme/widgets.git", ("acme", "widgets")),
        ("https://user@dev.azure.com/acme/data-platform", ("acme", "data-platform")),
        ("  git@github.com:acme/widgets.git  ", ("acme", "widgets")),
        ("/srv/mirrors/acme/widgets.git", ("acme", "widgets")),
        ("", None),
        ("not a url", None),
        ("git@github.com:widgets.git", None),
    ],
)
def test_parse_remote_reads_the_owner_and_repository_out_of_any_spelling(url, expected):
    assert worktrees.parse_remote(url) == expected


def test_remote_of_reads_what_origin_points_at(repo):
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")

    assert worktrees.remote_of(repo) == ("acme", "widgets")


def test_remote_of_says_nothing_for_a_repository_without_one(repo):
    assert worktrees.remote_of(repo) is None


def test_remote_of_says_nothing_where_there_is_no_repository(tmp_path):
    assert worktrees.remote_of(tmp_path / "nowhere") is None
