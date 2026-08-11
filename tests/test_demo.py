import os
from datetime import UTC, datetime

import pytest

from clownhead import attention
from clownhead import demo as demo_module
from clownhead import discovery as discovery_module
from clownhead.discovery import CONFIG_DIR_VAR
from clownhead.models import Status
from clownhead.render import build_rows, describe, worktree_cell
from clownhead.resume import resume_shell_command


@pytest.fixture
def demo_home(monkeypatch, tmp_path):
    home = tmp_path / "clownhead-demo"
    monkeypatch.setattr(demo_module, "DEMO_HOME", home)
    monkeypatch.setenv("HOME", str(tmp_path / "elsewhere"))
    return home


@pytest.fixture
def loader(demo_home):
    return demo_module.fabricated_fleet()


def test_the_demo_fleet_covers_the_statuses_worth_showing(loader):
    statuses = {session.status for session in loader(True)}
    assert statuses == {Status.WAITING, Status.BUSY, Status.IDLE, Status.CLOSED}


def test_closed_sessions_stay_out_until_they_are_asked_for(loader):
    assert all(session.status is not Status.CLOSED for session in loader(False))
    assert any(session.status is Status.CLOSED for session in loader(True))


def test_the_fleet_is_ordered_attention_first(loader):
    assert loader(True)[0].status is Status.WAITING


def test_every_working_directory_is_really_there(loader):
    """A board renders a missing directory as ``(gone)``, which no demo should show."""
    for session in loader(True):
        assert session.cwd.is_dir()
        assert "(gone)" not in describe(session)


def test_worktree_sessions_resume_from_a_repository_that_exists(loader):
    worktrees = [session for session in loader(True) if "worktrees" in str(session.cwd)]
    assert worktrees
    for session in worktrees:
        assert "--worktree" in resume_shell_command(session)


def test_a_worktree_outlives_the_session_that_made_it(loader):
    """The row `w` is for: a session that has ended whose checkout is still on the disk."""
    stale = next(session for session in loader(True) if session.session_id == demo_module.LEGACY_IMPORTS_SESSION)

    assert stale.status is Status.CLOSED
    assert stale.cwd.is_dir()
    assert worktree_cell(stale) == "legacy-imports"


def test_paths_shorten_against_the_demo_home(loader, demo_home, monkeypatch):
    monkeypatch.setenv("HOME", str(demo_home))
    wheres = {row.where for row in build_rows(loader(True), datetime.now(tz=UTC))}
    assert "~/dev/payments-api" in wheres
    assert "web-platform ⇢ search-index" in wheres


def test_signals_land_in_the_demos_own_directory(loader, demo_home):
    """The TTYs are files, so a signal aimed at one never reaches somebody's terminal."""
    for session in loader(False):
        assert session.tty is not None
        assert session.tty.is_file()
        assert demo_home in session.tty.parents


def test_no_session_carries_a_process_to_signal(loader):
    assert all(session.pid is None for session in loader(True))


def test_the_terminal_is_the_demos_own_bundle(loader):
    live = loader(False)
    assert all(attention.terminal_of(session).name == "iterm2" for session in live)


def test_a_relocated_config_directory_is_left_with_the_shell_that_set_it(demo_home, monkeypatch):
    monkeypatch.setenv(CONFIG_DIR_VAR, "/somewhere/private/.claude")
    demo_module.fabricated_fleet()
    assert CONFIG_DIR_VAR not in os.environ


def test_the_demo_never_paints_a_tab_it_was_not_given(loader):
    assert demo_module.DEMO_SETTINGS.paint_tabs is False


def test_the_stalled_session_stopped_on_a_question(loader):
    """What `→` is for: the turn that explains why the board says it is waiting on you."""
    turns = demo_module.fabricated_conversation(demo_module.PAYMENTS_SESSION, limit=20)

    assert [turn.role for turn in turns] == ["user", "assistant", "user", "assistant"]
    assert turns[-1].text.endswith("I would rather ask than pick.")
    assert all(turn.at is not None for turn in turns)


def test_a_conversation_is_cut_to_the_turns_asked_for(loader):
    turns = demo_module.fabricated_conversation(demo_module.PAYMENTS_SESSION, limit=2)

    assert len(turns) == 2
    assert turns[-1].role == "assistant"


def test_a_session_with_nothing_to_show_says_nothing(loader):
    assert demo_module.fabricated_conversation(demo_module.NOTIFICATIONS_SESSION, limit=20) == []


def test_every_conversation_belongs_to_a_session_on_the_board(loader):
    fleet = {session.session_id for session in loader(True)}

    assert set(demo_module.CONVERSATIONS) <= fleet


def test_the_script_opens_the_board_on_the_fabricated_world(demo_home, monkeypatch):
    launched: dict[str, object] = {}
    monkeypatch.setattr(demo_module.tui, "run", lambda **kwargs: launched.update(kwargs))

    demo_module.board()

    assert launched["settings"] is demo_module.DEMO_SETTINGS
    assert launched["reader"] is demo_module.fabricated_conversation
    assert [session.name for session in launched["loader"](False)] == [
        "payments-api-7c",
        "index-rebuild-stage-3",
        "backfill-rerun",
        "web-platform-1d",
        "notifications-svc",
        "invoice-parser",
    ]


def test_the_script_needs_nothing_of_the_machine_it_runs_on(demo_home, monkeypatch):
    """A sandboxed shell blocks discovery, and the demo never asks it anything."""
    monkeypatch.setattr(discovery_module, "peer_discovery_available", lambda: False)
    monkeypatch.setattr(demo_module.tui, "run", lambda **kwargs: kwargs["loader"](True))

    demo_module.board()
