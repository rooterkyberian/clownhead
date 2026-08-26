import json
from datetime import timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

import clownhead
from clownhead import attention, cli, discovery
from clownhead import terminal as terminal_module
from clownhead.models import Session, Status
from clownhead.resume import Launch
from clownhead.terminal import ITerm2Terminal, Terminal
from clownhead.worktrees import Candidate, Worktree

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: True)


def use_terminal(monkeypatch, terminal: Terminal) -> Terminal:
    """Make every session in the fleet resolve to one terminal."""
    monkeypatch.setattr(terminal_module, "detect_terminal", lambda env=None: terminal)
    monkeypatch.setattr(cli, "detect_terminal", lambda: terminal)
    return terminal


def fleet() -> list[Session]:
    return [
        Session(
            session_id="4e020900-df7c",
            cwd=Path("/tmp/payments-api"),
            name="payments-api-7c",
            status=Status.WAITING,
            waiting_for="input needed",
            tty=Path("/dev/ttys004"),
        ),
        Session(
            session_id="cef6830d-aaaa",
            cwd=Path("/tmp/web-platform"),
            name="web-platform-1d",
            status=Status.IDLE,
            tty=Path("/dev/ttys017"),
        ),
    ]


@pytest.fixture
def live_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())


class SilentTerminal(ITerm2Terminal):
    def __init__(self):
        super().__init__()
        self.written: list[str] = []

    def write(self, tty: Path, sequence: str) -> None:
        self.written.append(sequence)


def test_no_arguments_launches_the_tui(live_fleet, monkeypatch):
    launched: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: launched.update(kwargs))

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 0
    assert launched["interval"] is None
    assert launched["include_closed"] is None
    assert launched["loader"](False) == fleet()


def run_and_quit(kwargs, include_closed: bool) -> None:
    """Stand in for a board that loaded the fleet once and was then quit, running nothing."""
    kwargs["loader"](include_closed)
    return None


def test_tui_command_scopes_the_loader(live_fleet, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: run_and_quit(kwargs, False))
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **k: seen.setdefault("cwd", cwd) and [])

    result = runner.invoke(cli.app, ["tui", "--cwd", "/tmp/payments-api", "--interval", "1"])

    assert result.exit_code == 0
    assert seen["cwd"] == Path("/tmp/payments-api")


def test_tui_command_passes_the_closed_flag_through(live_fleet, monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: run_and_quit(kwargs, kwargs["include_closed"]))
    monkeypatch.setattr(
        discovery,
        "list_sessions",
        lambda cwd=None, **kwargs: seen.update(kwargs) or [],
    )

    result = runner.invoke(cli.app, ["tui", "--closed"])

    assert result.exit_code == 0
    assert seen["include_closed"] is True


def test_no_arguments_fails_loudly_when_peer_discovery_is_blocked(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: pytest.fail("tui must not launch"))

    result = runner.invoke(cli.app, [])

    assert result.exit_code == 2


def test_ls_lists_the_fleet(live_fleet):
    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "input needed" in result.stdout


def test_ls_asks_for_closed_sessions(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    result = runner.invoke(cli.app, ["ls", "--closed"])

    assert result.exit_code == 0
    assert seen["include_closed"] is True


def test_ls_leaves_closed_sessions_out_by_default(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    runner.invoke(cli.app, ["ls"])

    assert seen["include_closed"] is False


def test_ls_reports_an_empty_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 0
    assert "no live sessions" in result.stdout


def test_ls_columns_select_what_to_show_and_in_what_order(live_fleet):
    result = runner.invoke(cli.app, ["ls", "--columns", "where,name"])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0].split() == ["WHERE", "NAME"]
    assert "input needed" not in result.stdout


def test_ls_columns_can_ask_for_the_resume_command(live_fleet, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/fleet")
    monkeypatch.setenv("COLUMNS", "120")

    result = runner.invoke(cli.app, ["ls", "--columns", "name,resume"])

    assert result.exit_code == 0
    assert "RESUME" in result.stdout
    assert "CLAUDE_CONFIG_DIR=/tmp/fleet claude --resume 4e020900-df7c" in result.stdout


def test_ls_thins_the_default_columns_on_a_narrow_terminal(live_fleet, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")

    result = runner.invoke(cli.app, ["ls"])

    assert result.stdout.splitlines()[0].split() == ["STATUS", "NAME", "WHERE"]


def test_ls_columns_keep_what_a_narrow_terminal_would_have_dropped(live_fleet, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")

    result = runner.invoke(cli.app, ["ls", "--columns", "name,quiet,age,pid,tty"])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0].split() == ["NAME", "QUIET", "AGE", "PID", "TTY"]


def test_ls_refuses_a_column_that_does_not_exist(live_fleet):
    result = runner.invoke(cli.app, ["ls", "--columns", "name,nope"])

    assert result.exit_code == 2
    assert "unknown column nope" in result.stderr


def test_ls_refuses_a_bad_column_before_it_reads_the_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: pytest.fail("the fleet was read"))

    assert runner.invoke(cli.app, ["ls", "--columns", "nope"]).exit_code == 2


def transcript(tmp_path: Path, session_id: str, said: str, cwd: str = "/tmp/payments-api") -> Path:
    project = tmp_path / "config" / "projects" / cwd.replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text(json.dumps({"sessionId": session_id, "cwd": cwd, "type": "user", "message": {"content": said}}))
    return path


def test_ls_by_pull_request_keeps_the_sessions_that_named_it(live_fleet, tmp_path):
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 needs a rebase")
    transcript(tmp_path, "cef6830d-aaaa", "widgets#420 is somebody else's", cwd="/tmp/web-platform")

    result = runner.invoke(cli.app, ["ls", "--pr", "https://github.com/acme/widgets/pull/42"])

    assert result.exit_code == 0
    assert "acme/widgets#42 · 1 of 2 sessions" in result.stdout
    assert "payments-api-7c" in result.stdout
    assert "web-platform-1d" not in result.stdout


@pytest.mark.parametrize(("flags", "expected"), [([], False), (["--closed"], True)])
def test_ls_by_pull_request_searches_only_what_was_asked_for(monkeypatch, flags, expected):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    runner.invoke(cli.app, ["ls", "--pr", "acme/widgets#42", *flags])

    assert seen["include_closed"] is expected


def test_ls_by_pull_request_says_so_when_nothing_named_it(live_fleet, tmp_path):
    result = runner.invoke(cli.app, ["ls", "--pr", "acme/widgets#42"])

    assert result.exit_code == 0
    assert "acme/widgets#42 · 0 of 2 sessions" in result.stdout
    assert "--closed searches" in result.stdout
    assert "payments-api-7c" not in result.stdout


def test_ls_by_pull_request_does_not_suggest_closed_when_it_already_searched_them(live_fleet, tmp_path):
    result = runner.invoke(cli.app, ["ls", "--pr", "acme/widgets#42", "--closed"])

    assert "--closed searches" not in result.stdout


def test_ls_by_pull_request_does_not_suggest_closed_when_it_found_something(live_fleet, tmp_path):
    transcript(tmp_path, "4e020900-df7c", "acme/widgets#42 needs a rebase")

    result = runner.invoke(cli.app, ["ls", "--pr", "acme/widgets#42"])

    assert "--closed searches" not in result.stdout


def test_ls_refuses_a_reference_that_is_not_a_pull_request(live_fleet):
    result = runner.invoke(cli.app, ["ls", "--pr", "payments-api"])

    assert result.exit_code == 2
    assert "does not name a pull request" in result.stderr


def test_ls_fails_loudly_when_peer_discovery_is_blocked(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    result = runner.invoke(cli.app, ["ls"])

    assert result.exit_code == 2


def test_paint_colours_every_tab(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert len(terminal.written) == 2


def test_paint_reset_clears_tabs_and_exits(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["paint", "--reset"])

    assert result.exit_code == 0
    assert "cleared 2 tabs" in result.stdout
    assert terminal.written == ["\033]6;1;bg;*;default\a"] * 2


def test_paint_warns_on_terminals_without_colour_support(live_fleet, monkeypatch):
    use_terminal(monkeypatch, Terminal())

    result = runner.invoke(cli.app, ["paint"])

    assert result.exit_code == 0
    assert "does not support tab colours" in result.stderr


def test_focus_without_a_name_targets_stalled_sessions(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout
    assert "web-platform-1d" not in result.stdout


def test_focus_brings_the_terminal_to_the_front(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "payments-api-7c"])

    assert result.exit_code == 0
    assert "\033]1337;StealFocus\a" in terminal.written


def test_focus_can_leave_the_terminal_where_it_is(live_fleet, monkeypatch):
    terminal = use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "payments-api-7c", "--no-foreground"])

    assert result.exit_code == 0
    assert "\033]1337;StealFocus\a" not in terminal.written
    assert "\033]1337;RequestAttention=yes\a" in terminal.written


def test_focus_reports_a_calm_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [fleet()[1]])
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus"])

    assert "nothing is waiting on you" in result.stdout


def test_focus_by_name(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "web-platform-1d"])

    assert result.exit_code == 0
    assert "web-platform-1d" in result.stdout


def test_focus_by_short_id(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "4e020900"])

    assert result.exit_code == 0
    assert "payments-api-7c" in result.stdout


def test_focus_unknown_name_fails(live_fleet, monkeypatch):
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["focus", "nope"])

    assert result.exit_code == 1


def test_doctor_names_the_configured_claude_directory(live_fleet, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/tmp/elsewhere")
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "/tmp/elsewhere" in result.stdout
    assert "CLAUDE_CONFIG_DIR" in result.stdout
    assert "missing" in result.stdout


def test_doctor_falls_back_to_the_default_claude_directory(live_fleet, monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    use_terminal(monkeypatch, SilentTerminal())

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert str(Path.home() / ".claude") in result.stdout
    assert "CLAUDE_CONFIG_DIR" not in result.stdout


def test_doctor_reports_blocked_peer_discovery(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    result = runner.invoke(cli.app, ["doctor"])

    assert result.exit_code == 0
    assert "blocked" in result.stdout


def test_version_prints_the_version_without_launching_the_tui(monkeypatch):
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: pytest.fail("tui must not launch"))

    result = runner.invoke(cli.app, ["--version"])

    assert result.exit_code == 0
    assert clownhead.__version__ in result.stdout


def test_attention_module_is_reachable_from_cli():
    assert cli.attention is attention


def worktree_candidate(tmp_path, name="httpx2", **overrides):
    entry = Worktree(
        path=tmp_path / "repo" / ".claude" / "worktrees" / name,
        repo=tmp_path / "repo",
        name=name,
        branch=f"chore/{name}",
        head="a" * 40,
    )
    fields = {"worktree": entry, "last_used": None, "merged": False, "kept_for": None}
    return Candidate(**{**fields, **overrides})


@pytest.fixture
def swept(monkeypatch, tmp_path):
    """A survey of one removable worktree and one being kept, with removal recorded."""
    removed: list[tuple[str, bool]] = []
    candidates = [
        worktree_candidate(tmp_path, "httpx2"),
        worktree_candidate(tmp_path, "judge", merged=True, kept_for="uncommitted changes"),
    ]
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: candidates)
    monkeypatch.setattr(cli.worktrees, "remove", lambda entry, branch=False: removed.append((entry.name, branch)))
    return removed


def test_worktrees_cleanup_dry_run_lists_without_removing_anything(swept):
    result = runner.invoke(cli.app, ["worktrees-cleanup", "--dry-run"])

    assert result.exit_code == 0
    assert "2 worktrees · 1 to remove" in result.stdout
    assert "httpx2" in result.stdout
    assert "uncommitted changes" in result.stdout
    assert swept == []


def test_worktrees_cleanup_removes_nothing_when_the_question_is_declined(swept):
    result = runner.invoke(cli.app, ["worktrees-cleanup"], input="n\n")

    assert result.exit_code == 0
    assert "nothing removed" in result.stdout
    assert swept == []


def test_worktrees_cleanup_removes_what_it_offered_once_confirmed(swept):
    result = runner.invoke(cli.app, ["worktrees-cleanup"], input="y\n")

    assert result.exit_code == 0
    assert swept == [("httpx2", False)]
    assert "removed 1 of 1 worktrees" in result.stdout


def test_worktrees_cleanup_does_not_ask_when_yes_was_given(swept):
    result = runner.invoke(cli.app, ["worktrees-cleanup", "--yes"])

    assert result.exit_code == 0
    assert swept == [("httpx2", False)]


def test_worktrees_cleanup_keeps_going_when_one_removal_fails(monkeypatch, tmp_path):
    candidates = [worktree_candidate(tmp_path, "one"), worktree_candidate(tmp_path, "two")]
    removed: list[str] = []

    def remove(entry, branch=False):
        if entry.name == "one":
            raise LookupError("git refused")
        removed.append(entry.name)

    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: candidates)
    monkeypatch.setattr(cli.worktrees, "remove", remove)

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--yes"])

    assert result.exit_code == 0
    assert removed == ["two"]
    assert "git refused" in result.stderr
    assert "removed 1 of 2 worktrees" in result.stdout


def test_worktrees_cleanup_merged_narrows_to_the_merged_ones(monkeypatch, tmp_path):
    candidates = [worktree_candidate(tmp_path, "plain"), worktree_candidate(tmp_path, "done", merged=True)]
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: candidates)
    monkeypatch.setattr(cli.worktrees, "remove", lambda entry, branch=False: None)

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--merged", "--dry-run"])

    assert result.exit_code == 0
    assert "merged only" in result.stdout
    assert "done" in result.stdout
    assert "plain" not in result.stdout


def test_worktrees_cleanup_branches_takes_them_alongside_the_worktrees(monkeypatch, tmp_path):
    removed: list[tuple[str, bool]] = []
    candidates = [worktree_candidate(tmp_path, "done", merged=True)]
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: candidates)
    monkeypatch.setattr(cli.worktrees, "remove", lambda entry, branch=False: removed.append((entry.name, branch)))

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--branches", "--yes"])

    assert result.exit_code == 0
    assert removed == [("done", True)]


def test_worktrees_cleanup_branches_only_offers_the_merged_ones(monkeypatch, tmp_path):
    """A branch is only ever deleted where its work is upstream, so --branches implies --merged."""
    candidates = [worktree_candidate(tmp_path, "plain"), worktree_candidate(tmp_path, "done", merged=True)]
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: candidates)
    monkeypatch.setattr(cli.worktrees, "remove", lambda entry, branch=False: None)

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--branches", "--dry-run"])

    assert result.exit_code == 0
    assert "merged only" in result.stdout
    assert "plain" not in result.stdout


def test_worktrees_cleanup_says_so_when_there_are_no_worktrees(monkeypatch, live_fleet):
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["worktrees-cleanup"])

    assert result.exit_code == 0
    assert "no worktrees" in result.stdout


def test_worktrees_cleanup_refuses_a_bad_age_before_it_reads_the_fleet(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: pytest.fail("fleet must not be read"))

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--older-than", "soon"])

    assert result.exit_code == 2
    assert "not a duration" in result.stderr


def test_worktrees_cleanup_passes_the_age_it_was_given(monkeypatch, live_fleet):
    seen = {}
    monkeypatch.setattr(cli.worktrees, "survey", lambda sessions, **kwargs: seen.update(kwargs) or [])

    result = runner.invoke(cli.app, ["worktrees-cleanup", "--older-than", "12h"])

    assert result.exit_code == 0
    assert seen["older_than"] == timedelta(hours=12)


def test_worktrees_cleanup_reads_the_closed_sessions_too(monkeypatch, live_fleet):
    seen = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())
    monkeypatch.setattr(cli.worktrees, "survey", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["worktrees-cleanup"])

    assert result.exit_code == 0
    assert seen["include_closed"] is True


def test_ls_can_show_the_worktree_column(monkeypatch):
    session = Session(session_id="a-b", cwd=Path("/tmp/repo/.claude/worktrees/search-index"), name="one")
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [session])

    result = runner.invoke(cli.app, ["ls", "--columns", "name,worktree"])

    assert result.exit_code == 0
    assert result.stdout.splitlines()[0].split() == ["NAME", "WORKTREE"]
    assert "search-index" in result.stdout


ISSUE_URL = "https://github.com/acme/widgets/issues/2"


@pytest.fixture
def one_checkout(monkeypatch):
    """A fleet standing in one repository, which git says is the reference's own."""
    monkeypatch.setattr(cli.checkouts.worktrees, "remote_of", lambda repo: ("acme", "widgets"))
    monkeypatch.setattr(cli.issues, "fetch_title", lambda query: "Open a session")


def test_open_lists_the_sessions_that_named_the_issue(live_fleet, tmp_path, one_checkout):
    transcript(tmp_path, "4e020900-df7c", f"picking up {ISSUE_URL}")

    result = runner.invoke(cli.app, ["open", ISSUE_URL, "--print"])

    assert result.exit_code == 0
    assert "acme/widgets#2 · 1 of 2 sessions" in result.stdout
    assert "payments-api-7c" in result.stdout
    assert "web-platform-1d" not in result.stdout


def test_open_prints_the_command_that_starts_a_session_for_it(live_fleet, tmp_path, one_checkout):
    transcript(tmp_path, "4e020900-df7c", f"picking up {ISSUE_URL}")

    result = runner.invoke(cli.app, ["open", ISSUE_URL, "--print"])

    assert "--worktree issue-2-open-a-session" in result.stdout
    assert "--name issue-2-open-a-session" in result.stdout
    assert ISSUE_URL in result.stdout


def test_open_names_the_worktree_for_the_issue_alone_when_gh_says_nothing(live_fleet, monkeypatch):
    monkeypatch.setattr(cli.checkouts.worktrees, "remote_of", lambda repo: None)
    monkeypatch.setenv("CLOWNHEAD_GH_BIN", "/nonexistent/gh")

    result = runner.invoke(cli.app, ["open", ISSUE_URL, "--print"])

    assert "--worktree issue-2 " in result.stdout


def test_open_searches_the_sessions_that_have_ended_too(monkeypatch):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    runner.invoke(cli.app, ["open", ISSUE_URL, "--print"])

    assert seen["include_closed"] is True


def test_open_says_so_when_the_fleet_names_no_repository(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["open", ISSUE_URL, "--print"])

    assert result.exit_code == 0
    assert "no repository to start one in" in result.stdout


@pytest.mark.parametrize(
    "reference",
    [ISSUE_URL, "https://github.com/acme/widgets/pull/42", "https://craft.atlassian.net/browse/PLAT-4471"],
)
def test_a_bare_reference_reaches_open_without_naming_it(live_fleet, monkeypatch, reference, one_checkout):
    result = runner.invoke(cli.app, [reference, "--print"])

    assert result.exit_code == 0
    assert "of 2 sessions" in result.stdout


def test_a_mistyped_command_is_still_a_mistyped_command(live_fleet):
    result = runner.invoke(cli.app, ["lss"])

    assert result.exit_code == 2
    assert "Did you mean 'ls'?" in result.stdout + result.stderr


def test_open_refuses_a_reference_that_names_nothing(monkeypatch):
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: pytest.fail("the fleet was read"))

    result = runner.invoke(cli.app, ["open", "payments-api"])

    assert result.exit_code == 2
    assert "does not name a pull request or an issue" in result.stderr


def test_open_becomes_the_session_the_board_was_left_for(monkeypatch, live_fleet):
    plan = Launch(Path("/tmp/payments-api"), ("claude", "--resume", "4e020900-df7c"))
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: plan)
    monkeypatch.setenv("CLOWNHEAD_CLAUDE_BIN", "/usr/local/bin/claude")
    became: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(cli.os, "chdir", lambda directory: became.append(("cd", [str(directory)])))
    monkeypatch.setattr(cli.os, "execvp", lambda binary, argv: became.append((binary, argv)))

    result = runner.invoke(cli.app, ["open", ISSUE_URL])

    assert result.exit_code == 0
    assert became == [
        ("cd", ["/tmp/payments-api"]),
        ("/usr/local/bin/claude", ["claude", "--resume", "4e020900-df7c"]),
    ]


def test_the_board_runs_nothing_when_it_was_simply_quit(monkeypatch, live_fleet):
    monkeypatch.setattr(cli.tui, "run", lambda **kwargs: None)
    monkeypatch.setattr(cli.os, "execvp", lambda binary, argv: pytest.fail("a session was started"))

    result = runner.invoke(cli.app, ["tui"])

    assert result.exit_code == 0


PULL_URL = "https://github.com/acme/widgets/pull/42"


def test_prs_lists_what_github_says_is_open(live_fleet, github):
    result = runner.invoke(cli.app, ["prs"])

    assert result.exit_code == 0
    assert "1 open · @me" in result.stdout
    assert "acme/widgets#42" in result.stdout
    assert "approved" in result.stdout


def test_prs_counts_the_sessions_that_worked_on_each(live_fleet, tmp_path, github):
    transcript(tmp_path, "4e020900-df7c", f"{PULL_URL} is ready")

    result = runner.invoke(cli.app, ["prs"])

    assert "acme/widgets#42" in result.stdout
    assert result.stdout.count("1") >= 1


def test_prs_reads_the_sessions_that_have_ended_too(monkeypatch, github):
    seen: dict[str, object] = {}
    monkeypatch.setattr(discovery, "list_sessions", lambda cwd=None, **kwargs: seen.update(kwargs) or fleet())

    runner.invoke(cli.app, ["prs"])

    assert seen["include_closed"] is True


def test_prs_skips_the_transcripts_when_told_to(monkeypatch, github):
    asked: list[str] = []
    monkeypatch.setattr(discovery, "list_sessions", lambda *a, **k: asked.append("read") or fleet())

    result = runner.invoke(cli.app, ["prs", "--no-sessions"])

    assert result.exit_code == 0
    assert asked == []


def test_prs_says_github_could_not_be_asked_rather_than_printing_nothing(monkeypatch, live_fleet):
    def refuse(author, limit):
        raise cli.Unavailable("gh: not logged in")

    monkeypatch.setattr(cli.pulls, "mine", refuse)

    result = runner.invoke(cli.app, ["prs"])

    assert result.exit_code == 1
    assert "github could not be asked" in result.stderr
    assert "not logged in" in result.stderr


def test_prs_tells_an_empty_list_from_a_failure(monkeypatch, live_fleet):
    monkeypatch.setattr(cli.pulls, "mine", lambda author, limit: [])

    result = runner.invoke(cli.app, ["prs"])

    assert result.exit_code == 0
    assert "no open pull requests for @me" in result.stdout


def test_prs_passes_the_author_and_the_limit_through(monkeypatch, live_fleet):
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(cli.pulls, "mine", lambda author, limit: seen.append((author, limit)) or [])

    runner.invoke(cli.app, ["prs", "--author", "someone", "--limit", "5"])

    assert seen == [("someone", 5)]


def test_ls_prs_column_names_what_each_session_worked_on(live_fleet, tmp_path):
    transcript(tmp_path, "4e020900-df7c", "https://github.com/acme/widgets/pull/42 is ready")

    result = runner.invoke(cli.app, ["ls", "--columns", "name,prs"])

    assert result.exit_code == 0
    assert "PRS" in result.stdout
    assert "widgets#42" in result.stdout


def test_ls_reads_no_transcripts_unless_the_prs_column_was_asked_for(live_fleet, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(cli.search, "pulls_by_session", lambda sessions: asked.append("read") or {})

    runner.invoke(cli.app, ["ls", "--columns", "name,where"])
    assert asked == []

    runner.invoke(cli.app, ["ls", "--columns", "name,prs"])
    assert asked == ["read"]
