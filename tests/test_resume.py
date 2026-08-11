from pathlib import Path

from clownhead.models import Session
from clownhead.resume import resume_argv, resume_plan, resume_shell_command


def session(cwd: Path, session_id: str = "a-b") -> Session:
    return Session(session_id=session_id, cwd=cwd, name="one")


def worktree(repo: Path, name: str = "dbx-spot", exists: bool = True) -> Path:
    cwd = repo / ".claude" / "worktrees" / name
    if exists:
        cwd.mkdir(parents=True)
    return cwd


def test_resume_argv_resumes_by_session_id(tmp_path):
    assert resume_argv(session(tmp_path, "4e020900-df7c")) == ["claude", "--resume", "4e020900-df7c"]


def test_resume_shell_command_returns_to_the_original_directory(tmp_path):
    assert resume_shell_command(session(tmp_path)) == f"(cd {tmp_path} && claude --resume a-b)"


def test_resume_re_enters_a_live_worktree_from_its_repository(tmp_path):
    cwd = worktree(tmp_path)

    command = resume_shell_command(session(cwd))

    assert command == f"(cd {tmp_path} && claude --resume a-b --worktree dbx-spot)"


def test_resume_rebuilds_a_worktree_that_has_been_pruned(tmp_path):
    cwd = worktree(tmp_path, exists=False)

    directory, argv = resume_plan(session(cwd))

    assert directory == tmp_path
    assert argv == ["claude", "--resume", "a-b", "--worktree", "dbx-spot"]


def test_resume_rebuilds_a_worktree_only_while_its_repository_stands(tmp_path):
    cwd = worktree(tmp_path / "deleted-repo", exists=False)

    assert resume_shell_command(session(cwd)) == f"(cd {cwd} && claude --resume a-b)"


def test_resume_keeps_a_cd_that_will_fail_rather_than_land_in_the_wrong_project(tmp_path):
    gone = tmp_path / "long-gone"

    assert resume_shell_command(session(gone)) == f"(cd {gone} && claude --resume a-b)"
