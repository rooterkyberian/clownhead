from pathlib import Path

from clownhead.discovery import CONFIG_DIR_VAR
from clownhead.models import Session
from clownhead.resume import Launch, resume_argv, resume_plan, resume_shell_command, start_plan


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
    plan = start_plan(tmp_path, name="plat-4471", prompt="https://craft.atlassian.net/browse/PLAT-4471")

    assert plan.shell_command == (
        f"(cd {tmp_path} && claude --permission-mode plan --worktree plat-4471 "
        "--name plat-4471 https://craft.atlassian.net/browse/PLAT-4471)"
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

    plan = start_plan(tmp_path, name="plat-4471", prompt="https://craft.atlassian.net/browse/PLAT-4471")

    assert plan.shell_command.startswith(f"(cd {tmp_path} && CLAUDE_CONFIG_DIR=/Users/you/.claude-personal claude ")


def test_a_launch_quotes_a_carried_value_a_shell_would_split(tmp_path):
    plan = Launch(tmp_path, ("claude",), ((CONFIG_DIR_VAR, "/Users/you/Application Support/.claude"),))

    assert plan.shell_command == f"(cd {tmp_path} && CLAUDE_CONFIG_DIR='/Users/you/Application Support/.claude' claude)"


def test_resume_plan_forks_the_conversation_when_asked(tmp_path):
    plan = resume_plan(session(tmp_path), fork=True)

    assert plan.argv == ("claude", "--resume", "a-b", "--fork-session")


def test_resume_plan_keeps_the_session_id_by_default(tmp_path):
    assert "--fork-session" not in resume_plan(session(tmp_path)).argv
