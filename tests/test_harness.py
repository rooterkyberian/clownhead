from pathlib import Path

import pytest

from clownhead import codex, discovery, harness, models
from clownhead.models import Harness, Kind, Message, Session, Status

CLAUDE = harness.HARNESSES[models.Harness.CLAUDE]
CODEX = harness.HARNESSES[models.Harness.CODEX]


def a_session(session_id: str, agent: Harness = Harness.CLAUDE, **overrides) -> Session:
    fields = {"session_id": session_id, "cwd": Path("/tmp/repo"), "harness": agent, "status": Status.IDLE}
    fields.update(overrides)
    return Session(**fields)


@pytest.fixture
def only_claude(monkeypatch) -> None:
    """A machine with Claude Code on it and no Codex, which is what most of these assume."""
    monkeypatch.setattr(CODEX, "installed", lambda: False)


@pytest.fixture
def both(monkeypatch) -> None:
    """A machine with both agents installed and both able to answer."""
    monkeypatch.setattr(CODEX, "installed", lambda: True)
    monkeypatch.setattr(CODEX, "available", lambda: True)


def test_every_harness_is_registered_under_its_own_kind():
    assert [found.kind for found in harness.HARNESSES.values()] == [models.Harness.CLAUDE, models.Harness.CODEX]
    assert all(kind is found.kind for kind, found in harness.HARNESSES.items())


def test_a_session_is_matched_to_the_harness_that_answered_for_it():
    assert harness.for_session(a_session("a")) is CLAUDE
    assert harness.for_session(a_session("b", Harness.CODEX)) is CODEX


def test_a_kind_names_its_harness():
    assert harness.for_kind(models.Harness.CODEX) is CODEX


def test_installed_leaves_out_what_the_machine_does_not_have(only_claude):
    assert [found.label for found in harness.installed()] == ["claude"]


def test_installed_lists_both_when_both_are_there(both):
    assert [found.label for found in harness.installed()] == ["claude", "codex"]


def test_the_fan_out_asks_every_harness_that_can_answer(monkeypatch, both):
    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [a_session("one")])
    monkeypatch.setattr(CODEX, "list_sessions", lambda cwd, *, include_closed: [a_session("two", Harness.CODEX)])

    found = harness.list_sessions()

    assert {session.session_id for session in found} == {"one", "two"}


def test_the_fan_out_skips_a_harness_that_is_not_installed(monkeypatch, only_claude):
    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [a_session("one")])
    monkeypatch.setattr(CODEX, "list_sessions", lambda cwd, *, include_closed: pytest.fail("asked an absent harness"))

    assert [session.session_id for session in harness.list_sessions()] == ["one"]


def test_the_fan_out_skips_a_harness_with_nothing_running_to_ask(monkeypatch):
    """An installed Codex with no daemon is a normal state, so the board carries on without it."""
    monkeypatch.setattr(CODEX, "installed", lambda: True)
    monkeypatch.setattr(CODEX, "available", lambda: False)
    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [a_session("one")])
    monkeypatch.setattr(CODEX, "list_sessions", lambda cwd, *, include_closed: pytest.fail("asked a silent daemon"))

    assert [session.session_id for session in harness.list_sessions()] == ["one"]


def test_the_fan_out_lets_a_harness_that_said_it_could_answer_report_its_failure(monkeypatch, both):
    def refuse(cwd, *, include_closed):
        raise codex.Unavailable("the daemon went away mid-listing")

    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [])
    monkeypatch.setattr(CODEX, "list_sessions", refuse)

    with pytest.raises(codex.Unavailable):
        harness.list_sessions()


def test_the_fan_out_sorts_one_board_rather_than_two(monkeypatch, both):
    waiting = a_session("codex-waiting", Harness.CODEX, status=Status.WAITING)
    idle = a_session("claude-idle")
    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [idle])
    monkeypatch.setattr(CODEX, "list_sessions", lambda cwd, *, include_closed: [waiting])

    found = harness.list_sessions()

    assert [session.session_id for session in found] == ["codex-waiting", "claude-idle"]


def test_the_fan_out_can_drop_the_background_agents(monkeypatch, both):
    background = a_session("agent", kind=Kind.BACKGROUND)
    monkeypatch.setattr(CLAUDE, "list_sessions", lambda cwd, *, include_closed: [a_session("one"), background])
    monkeypatch.setattr(CODEX, "list_sessions", lambda cwd, *, include_closed: [])

    assert [session.session_id for session in harness.list_sessions(interactive_only=True)] == ["one"]


def test_the_fan_out_passes_the_directory_and_the_closed_flag_down(monkeypatch, both):
    asked: list[tuple] = []
    for found in (CLAUDE, CODEX):
        monkeypatch.setattr(
            found,
            "list_sessions",
            lambda cwd, *, include_closed: asked.append((cwd, include_closed)) or [],
        )

    harness.list_sessions(Path("/tmp/repo"), include_closed=True)

    assert asked == [(Path("/tmp/repo"), True), (Path("/tmp/repo"), True)]


def test_a_conversation_is_read_by_the_harness_that_holds_it(monkeypatch):
    read: list[tuple[str, int]] = []
    monkeypatch.setattr(
        CODEX,
        "recent_messages",
        lambda session_id, *, limit: read.append((session_id, limit)) or [Message(role="user", text="hi")],
    )
    monkeypatch.setattr(CLAUDE, "recent_messages", lambda session_id, *, limit: pytest.fail("asked the wrong harness"))

    messages = harness.recent_messages(a_session("thread", Harness.CODEX), limit=4)

    assert read == [("thread", 4)]
    assert [message.text for message in messages] == ["hi"]


def test_trust_is_asked_of_claude_unless_another_harness_is_named(monkeypatch):
    monkeypatch.setattr(CLAUDE, "trusted_dirs", lambda: {Path("/tmp/claude")})
    monkeypatch.setattr(CODEX, "trusted_dirs", lambda: {Path("/tmp/codex")})

    assert harness.trusted_dirs() == {Path("/tmp/claude")}
    assert harness.trusted_dirs(models.Harness.CODEX) == {Path("/tmp/codex")}


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("claude --resume abc", "claude"),
        ("codex resume abc", "codex"),
        ("/usr/local/bin/codex", "codex"),
        ("zsh", None),
        ("vim claude.py", None),
    ],
)
def test_a_process_is_traced_to_the_agent_that_owns_it(command, expected):
    found = harness.owning_harness(command)

    assert (found.label if found else None) == expected


def test_claude_reports_where_it_is_and_what_it_reads():
    assert CLAUDE.binary() == discovery.claude_binary()
    assert CLAUDE.config_dir() == discovery.config_dir()
    assert CLAUDE.installed() is True


def test_codex_reports_where_it_is_and_what_it_reads(no_codex):
    assert CODEX.binary() == codex.codex_binary()
    assert CODEX.config_dir() == no_codex


def test_claude_says_why_a_sandboxed_shell_sees_nothing(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: False)

    assert "cannot list" in (CLAUDE.unavailable_reason() or "")


def test_claude_has_nothing_to_explain_when_it_can_answer(monkeypatch):
    monkeypatch.setattr(discovery, "peer_discovery_available", lambda: True)

    assert CLAUDE.unavailable_reason() is None


def test_codex_says_when_it_is_not_installed(no_codex):
    assert CODEX.unavailable_reason() == "not installed"


def test_codex_says_how_to_start_a_daemon_it_is_missing(monkeypatch, no_codex):
    monkeypatch.setattr(CODEX, "installed", lambda: True)

    assert "app-server daemon start" in (CODEX.unavailable_reason() or "")


def test_codex_has_nothing_to_explain_once_a_daemon_answers(monkeypatch, codex_server):
    monkeypatch.setattr(CODEX, "installed", lambda: True)

    assert CODEX.unavailable_reason() is None


def test_codex_reads_its_own_trust_file(no_codex):
    (no_codex / "config.toml").write_text('[projects."/tmp/one"]\ntrust_level = "trusted"\n')

    assert CODEX.trusted_dirs() == {Path("/tmp/one")}


def test_codex_joins_the_process_table_onto_the_live_sessions(monkeypatch, codex_server):
    live = a_session("thread", Harness.CODEX)
    monkeypatch.setattr(codex, "list_sessions", lambda cwd, *, include_closed: [live])
    monkeypatch.setattr(codex, "attach_processes", lambda sessions, processes: [live.model_copy(update={"pid": 42})])

    assert CODEX.list_sessions(None, include_closed=False)[0].pid == 42


def test_codex_asks_nothing_of_the_process_table_when_every_session_has_ended(monkeypatch, codex_server):
    ended = a_session("thread", Harness.CODEX, status=Status.CLOSED)
    monkeypatch.setattr(codex, "list_sessions", lambda cwd, *, include_closed: [ended])
    monkeypatch.setattr(codex, "attach_processes", lambda sessions, processes: pytest.fail("joined a dead fleet"))

    assert CODEX.list_sessions(None, include_closed=True) == [ended]


def test_the_base_harness_answers_nothing_on_its_own():
    """Every operation is a subclass's to provide, so the base refuses rather than guessing."""
    base = harness.Harness()

    for call in (
        base.installed,
        base.available,
        base.unavailable_reason,
        base.binary,
        base.config_dir,
        base.trusted_dirs,
    ):
        with pytest.raises(NotImplementedError):
            call()
    with pytest.raises(NotImplementedError):
        base.list_sessions(None, include_closed=False)
    with pytest.raises(NotImplementedError):
        base.recent_messages("a", limit=1)
    with pytest.raises(NotImplementedError):
        base.owns_process("claude")
