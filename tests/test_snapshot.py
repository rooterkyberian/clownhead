from datetime import UTC, datetime
from pathlib import Path

import pytest

from clownhead import snapshot
from clownhead.models import Session, SnapshotEntry

ENTRY = SnapshotEntry(session_id="4e020900-df7c", cwd=Path("/Users/x/dev/payments-api"), name="payments-api-7c")


def test_state_dir_prefers_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_STATE_DIR", str(tmp_path))

    assert snapshot.state_dir() == tmp_path


def test_state_dir_falls_back_to_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOWNHEAD_STATE_DIR", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert snapshot.state_dir() == tmp_path / "clownhead"


def test_state_dir_defaults_under_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOWNHEAD_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert snapshot.state_dir() == tmp_path / ".local" / "state" / "clownhead"


def test_capture_records_every_session():
    sessions = [
        Session(session_id="a-b", cwd=Path("/tmp/one"), name="one"),
        Session(session_id="c-d", cwd=Path("/tmp/two")),
    ]

    captured = snapshot.capture(sessions, now=datetime(2026, 8, 10, tzinfo=UTC))

    assert captured.saved_at == datetime(2026, 8, 10, tzinfo=UTC)
    assert [entry.session_id for entry in captured.entries] == ["a-b", "c-d"]


def test_save_and_load_round_trip(tmp_path):
    captured = snapshot.capture(
        [Session(session_id="a-b", cwd=Path("/tmp/one"), name="one")],
        now=datetime(2026, 8, 10, tzinfo=UTC),
    )
    target = tmp_path / "nested" / "fleet.json"

    snapshot.save(captured, target)
    restored = snapshot.load(target)

    assert restored.saved_at == captured.saved_at
    assert restored.entries == captured.entries


def test_load_missing_snapshot_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshot.load(tmp_path / "absent.json")


def test_resume_argv_strips_billing_sensitive_vars():
    argv = snapshot.resume_argv(ENTRY)

    assert argv == [
        "env",
        "-u",
        "ANTHROPIC_API_KEY",
        "-u",
        "ANTHROPIC_AUTH_TOKEN",
        "claude",
        "--resume",
        "4e020900-df7c",
    ]


def test_resume_shell_command_returns_to_original_directory():
    command = snapshot.resume_shell_command(ENTRY)

    assert command.startswith("(cd /Users/x/dev/payments-api && env -u ANTHROPIC_API_KEY")
    assert command.endswith("claude --resume 4e020900-df7c)")


def test_tmux_argv_names_the_window_after_the_session():
    argv = snapshot.tmux_argv(ENTRY, "fleet")

    assert argv[:4] == ["tmux", "new-window", "-t", "fleet"]
    assert "-n" in argv
    assert argv[argv.index("-n") + 1] == "payments-api-7c"
    assert argv[-1].startswith("env -u ANTHROPIC_API_KEY")


def test_tmux_window_falls_back_to_directory_name():
    argv = snapshot.tmux_argv(SnapshotEntry(session_id="x", cwd=Path("/Users/x/dev/search-index")))

    assert argv[argv.index("-n") + 1] == "search-index"
