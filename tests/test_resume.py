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
    """Resume preserves the recorded directory rather than requesting a new worktree."""
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

    assert plan.directory == tmp_path
    assert plan.argv == ("codex", "--enable", "worktrees", "--worktree", "--sandbox", "read-only", "https://example/2")


def test_starting_under_codex_without_a_worktree_works_in_the_checkout(tmp_path):
    plan = start_plan(tmp_path, name="issue-2", prompt="https://example/2", worktree=False, harness=Harness.CODEX)

    assert plan.directory == tmp_path


def test_worktree_path_preserves_the_legacy_layout(tmp_path):
    assert worktree_path(tmp_path, "issue-2") == tmp_path / ".claude" / "worktrees" / "issue-2"


def test_codex_resume_quotes_external_worktree_paths_with_spaces(tmp_path):
    cwd = tmp_path / "Codex Home/worktrees/hash/feature"
    assert resume_shell_command(codex_session(cwd)).startswith(f"(cd '{cwd}' && ")


def test_claude_in_a_codex_checkout_resumes_without_creating_a_different_worktree(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cwd = tmp_path / "worktrees/hash/feature"
    cwd.mkdir(parents=True)
    plan = resume_plan(Session(session_id="a-b", cwd=cwd))

    assert plan.directory == cwd
    assert "--worktree" not in plan.argv


@pytest.mark.parametrize("target", [Harness.CLAUDE, Harness.CODEX])
def test_handoff_starts_target_in_original_checkout_with_context(tmp_path, monkeypatch, target):
    from clownhead.models import Message
    from clownhead.resume import handoff_plan

    source = Harness.CODEX if target is Harness.CLAUDE else Harness.CLAUDE
    original = Session(session_id="source-id", cwd=tmp_path, harness=source)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    plan = handoff_plan(original, target, [Message(role="user", text="Fix the retry loop")], [tmp_path / "log.jsonl"])

    assert plan.directory == tmp_path
    assert plan.argv[0] == target.value
    assert "resume" not in plan.argv
    assert "--resume" not in plan.argv
    assert "--worktree" not in plan.argv
    assert "Fix the retry loop" in plan.argv[-1]
    assert str(tmp_path / "log.jsonl") in plan.argv[-1]
    assert "source-id" in plan.argv[-1]
    assert dict(plan.env) == {
        "CODEX_HOME" if target is Harness.CODEX else "CLAUDE_CONFIG_DIR": str(tmp_path / f"{target.value}-home")
    }


def test_handoff_bounds_the_transcripts_it_names(tmp_path):
    from clownhead.resume import TRANSCRIPT_LIMIT, handoff_plan

    original = Session(session_id="source-id", cwd=tmp_path, harness=Harness.CODEX)
    transcripts = [tmp_path / f"subagent-{index}.jsonl" for index in range(TRANSCRIPT_LIMIT + 4)]

    prompt = handoff_plan(original, Harness.CLAUDE, [], transcripts).argv[-1]

    assert str(transcripts[0]) in prompt
    assert str(transcripts[TRANSCRIPT_LIMIT]) not in prompt


def test_handoff_into_claude_carries_the_name_the_board_showed(tmp_path):
    from clownhead.resume import handoff_plan

    original = Session(session_id="source-id", cwd=tmp_path, harness=Harness.CODEX, name="fix-retry-loop")

    argv = handoff_plan(original, Harness.CLAUDE, [], []).argv

    assert "--name" in argv
    assert argv[argv.index("--name") + 1] == "fix-retry-loop"


def test_handoff_into_claude_asks_for_no_name_when_the_session_has_none(tmp_path):
    from clownhead.resume import handoff_plan

    original = Session(session_id="source-id", cwd=tmp_path, harness=Harness.CODEX)

    assert "--name" not in handoff_plan(original, Harness.CLAUDE, [], []).argv


def test_handoff_bounds_context_and_keeps_latest_messages(tmp_path):
    from clownhead.models import Message
    from clownhead.resume import handoff_plan

    messages = [Message(role="user", text="old" * 10000), Message(role="assistant", text="latest decision")]
    plan = handoff_plan(session(tmp_path), Harness.CODEX, messages, [])
    assert len(plan.argv[-1]) < 13000
    assert plan.argv[-1].endswith("latest decision")


def test_handoff_refuses_a_missing_checkout(tmp_path):
    from clownhead.resume import handoff_plan

    with pytest.raises(LookupError, match="working directory no longer exists"):
        handoff_plan(session(tmp_path / "gone"), Harness.CODEX, [], [])
