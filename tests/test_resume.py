from pathlib import Path

import pytest

from clownhead.discovery import CONFIG_DIR_VAR
from clownhead.models import Harness, Session
from clownhead.resume import (
    Launch,
    resume_argv,
    resume_plan,
    resume_shell_command,
    start_plan,
    worktree_path,
)


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

    plan = resume_plan(session(cwd))

    assert plan.directory == tmp_path
    assert plan.argv == ("claude", "--resume", "a-b", "--worktree", "dbx-spot")


def test_resume_rebuilds_a_worktree_only_while_its_repository_stands(tmp_path):
    cwd = worktree(tmp_path / "deleted-repo", exists=False)

    assert resume_shell_command(session(cwd)) == f"(cd {cwd} && claude --resume a-b)"


def test_resume_keeps_a_cd_that_will_fail_rather_than_land_in_the_wrong_project(tmp_path):
    gone = tmp_path / "long-gone"

    assert resume_shell_command(session(gone)) == f"(cd {gone} && claude --resume a-b)"


def test_start_plan_makes_the_worktree_and_names_the_session_after_it(tmp_path):
    plan = start_plan(tmp_path, name="issue-2-open-a-session", prompt="https://github.com/acme/widgets/issues/2")

    assert plan.directory == tmp_path
    assert plan.argv == (
        "claude",
        "--permission-mode",
        "plan",
        "--worktree",
        "issue-2-open-a-session",
        "--name",
        "issue-2-open-a-session",
        "https://github.com/acme/widgets/issues/2",
    )


def test_start_shell_command_runs_from_the_repository(tmp_path):
    plan = start_plan(tmp_path, name="plat-4471", prompt="https://kyberian.atlassian.net/browse/PLAT-4471")

    assert plan.shell_command == (
        f"(cd {tmp_path} && claude --permission-mode plan --worktree plat-4471 "
        "--name plat-4471 https://kyberian.atlassian.net/browse/PLAT-4471)"
    )


def test_start_plan_starts_in_plan_mode(tmp_path):
    plan = start_plan(tmp_path, name="issue-2", prompt="https://github.com/acme/widgets/issues/2")

    assert "--permission-mode" in plan.argv
    assert plan.argv[plan.argv.index("--permission-mode") + 1] == "plan"


def test_resume_does_not_impose_a_permission_mode(tmp_path):
    assert "--permission-mode" not in resume_argv(session(tmp_path))


def test_a_launch_quotes_what_a_shell_would_otherwise_read_as_its_own(tmp_path):
    plan = Launch(tmp_path, ("claude", "--name", "issue 2", "widgets#2 & more"))

    assert plan.shell_command == f"(cd {tmp_path} && claude --name 'issue 2' 'widgets#2 & more')"


def test_resume_carries_the_config_directory_clownhead_was_spawned_with(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_DIR_VAR, "/Users/you/.claude-personal")

    command = resume_shell_command(session(tmp_path))

    assert command == f"(cd {tmp_path} && CLAUDE_CONFIG_DIR=/Users/you/.claude-personal claude --resume a-b)"


def test_resume_leaves_the_default_config_directory_unsaid(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_DIR_VAR, str(Path.home() / ".claude"))

    assert CONFIG_DIR_VAR not in resume_shell_command(session(tmp_path))


def test_the_carried_config_directory_is_environment_and_not_an_argument(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_DIR_VAR, "/Users/you/.claude-personal")

    assert resume_argv(session(tmp_path)) == ["claude", "--resume", "a-b"]


def test_starting_a_session_carries_the_config_directory_too(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_DIR_VAR, "/Users/you/.claude-personal")

    plan = start_plan(tmp_path, name="plat-4471", prompt="https://kyberian.atlassian.net/browse/PLAT-4471")

    assert plan.shell_command.startswith(f"(cd {tmp_path} && CLAUDE_CONFIG_DIR=/Users/you/.claude-personal claude ")


def test_a_launch_quotes_a_carried_value_a_shell_would_split(tmp_path):
    plan = Launch(tmp_path, ("claude",), ((CONFIG_DIR_VAR, "/Users/you/Application Support/.claude"),))

    assert plan.shell_command == f"(cd {tmp_path} && CLAUDE_CONFIG_DIR='/Users/you/Application Support/.claude' claude)"


def test_resume_plan_forks_the_conversation_when_asked(tmp_path):
    plan = resume_plan(session(tmp_path), fork=True)

    assert plan.argv == ("claude", "--resume", "a-b", "--fork-session")


def test_resume_plan_keeps_the_session_id_by_default(tmp_path):
    assert "--fork-session" not in resume_plan(session(tmp_path)).argv


def test_start_plan_leaves_the_worktree_out_where_claude_has_not_been_run(tmp_path):
    """Claude Code refuses a worktree in a directory whose trust dialog is unanswered."""
    plan = start_plan(tmp_path, name="issue-2", prompt="https://example.invalid/2", worktree=False)

    assert "--worktree" not in plan.argv
    assert plan.argv == ("claude", "--permission-mode", "plan", "--name", "issue-2", "https://example.invalid/2")


@pytest.fixture(autouse=True)
def codex_on_path(monkeypatch) -> None:
    """Spell the Codex binary plainly, which the suite otherwise points at nothing."""
    monkeypatch.delenv("CLOWNHEAD_CODEX_BIN", raising=False)


def codex_session(cwd: Path, session_id: str = "01a080d8") -> Session:
    return Session(session_id=session_id, cwd=cwd, harness=Harness.CODEX)


def test_a_codex_session_resumes_through_its_own_cli(tmp_path):
    assert resume_argv(codex_session(tmp_path)) == ["codex", "resume", "01a080d8"]


def test_a_codex_session_forks_through_its_own_cli(tmp_path):
    plan = resume_plan(codex_session(tmp_path), fork=True)

    assert plan.argv == ("codex", "fork", "01a080d8")


def test_a_codex_worktree_session_resumes_in_the_worktree_itself(tmp_path):
    """`codex resume` has no `--worktree`, so there is no rebuilding one from the repository."""
    cwd = worktree(tmp_path)

    plan = resume_plan(codex_session(cwd))

    assert plan.directory == cwd
    assert "--worktree" not in plan.argv


def test_a_codex_command_carries_the_codex_home_it_was_listed_from(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "elsewhere"))

    command = resume_shell_command(codex_session(tmp_path))

    assert f"CODEX_HOME={tmp_path / 'elsewhere'}" in command


def test_a_codex_command_leaves_the_default_home_unsaid(tmp_path, monkeypatch):
    monkeypatch.delenv("CODEX_HOME", raising=False)

    assert "CODEX_HOME" not in resume_shell_command(codex_session(tmp_path))


def test_starting_under_codex_works_in_the_worktree_and_asks_for_no_edits(tmp_path):
    plan = start_plan(tmp_path, name="issue-2", prompt="https://example/2", harness=Harness.CODEX)

    assert plan.directory == tmp_path / ".claude" / "worktrees" / "issue-2"
    assert plan.argv == ("codex", "--sandbox", "read-only", "https://example/2")


def test_starting_under_codex_without_a_worktree_works_in_the_checkout(tmp_path):
    plan = start_plan(tmp_path, name="issue-2", prompt="https://example/2", worktree=False, harness=Harness.CODEX)

    assert plan.directory == tmp_path


def test_worktree_path_is_the_one_layout_both_agents_share(tmp_path):
    assert worktree_path(tmp_path, "issue-2") == tmp_path / ".claude" / "worktrees" / "issue-2"
