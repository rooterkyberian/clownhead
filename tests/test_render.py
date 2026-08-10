from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console
from rich.markup import render as render_markup

from clownhead.discovery import Message
from clownhead.models import Session, Status
from clownhead.render import build_table, conversation, describe, format_duration, shorten_path

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def visible(markup: str) -> str:
    return render_markup(markup).plain


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (None, "-"),
        (timedelta(seconds=12), "12s"),
        (timedelta(minutes=4), "4m"),
        (timedelta(hours=3), "3h"),
        (timedelta(days=6), "6d"),
        (timedelta(days=12, hours=6), "12d"),
    ],
)
def test_format_duration(delta, expected):
    assert format_duration(delta) == expected


def test_shorten_path_collapses_home():
    assert shorten_path(Path("/Users/x/dev/repo"), home=Path("/Users/x")) == "~/dev/repo"


def test_shorten_path_marks_worktrees():
    path = Path("/Users/x/dev/web-platform/.claude/worktrees/search-index")

    assert shorten_path(path, home=Path("/Users/x")) == "web-platform ⇢ search-index"


def test_shorten_path_leaves_foreign_paths_intact():
    assert shorten_path(Path("/opt/thing"), home=Path("/Users/x")) == "/opt/thing"


def test_build_table_renders_a_row_per_session():
    sessions = [
        Session(
            session_id="a-b",
            cwd=Path("/tmp/repo"),
            name="one",
            pid=77730,
            status=Status.WAITING,
            waiting_for="input needed",
            tty=Path("/dev/ttys004"),
            started_at=NOW - timedelta(hours=2),
            updated_at=NOW - timedelta(minutes=5),
        ),
        Session(session_id="c-d", cwd=Path("/tmp/other"), name="two", status=Status.IDLE),
    ]

    console = Console(width=200, record=True)
    console.print(build_table(sessions, now=NOW, show_pid=True, show_tty=True))
    output = console.export_text()

    assert "input needed" in output
    assert "one" in output
    assert "77730" in output
    assert "ttys004" in output
    assert "2h" in output
    assert "5m" in output


def test_build_table_shows_the_owning_process_when_asked():
    sessions = [
        Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one", pid=77730, tty=Path("/dev/ttys004")),
        Session(session_id="c-d", cwd=Path("/tmp/other"), name="two", status=Status.CLOSED),
    ]

    console = Console(width=200, record=True)
    console.print(build_table(sessions, now=NOW, show_pid=True, show_tty=True))
    lines = console.export_text().splitlines()

    assert "PID" in lines[0]
    assert "TTY" in lines[0]
    assert "77730" in lines[1]
    assert "closed" in lines[2]


def test_build_table_hides_the_process_columns_by_default():
    sessions = [Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one", pid=77730, tty=Path("/dev/ttys004"))]

    console = Console(width=200, record=True)
    console.print(build_table(sessions, now=NOW))
    output = console.export_text()

    assert "PID" not in output
    assert "TTY" not in output
    assert "77730" not in output
    assert "ttys004" not in output
    assert "one" in output


def test_build_table_keeps_one_line_per_session_when_narrow():
    sessions = [
        Session(
            session_id=f"id-{index}",
            cwd=Path(f"/Users/x/dev/web-platform/.claude/worktrees/very-long-worktree-name-{index}"),
            name=f"a-rather-long-session-name-{index}",
            status=Status.IDLE,
            tty=Path("/dev/ttys004"),
        )
        for index in range(3)
    ]

    console = Console(width=60, record=True)
    console.print(build_table(sessions, now=NOW, width=60))
    lines = [line for line in console.export_text().splitlines() if line.strip()]

    assert len(lines) == len(sessions) + 1
    assert all(line.startswith("idle") for line in lines[1:])


@pytest.mark.parametrize("width", [50, 60, 80, 99])
def test_status_survives_narrow_terminals(width):
    session = Session(
        session_id="a-b",
        cwd=Path("/Users/x/dev/web-platform/.claude/worktrees/enormous-worktree-name-here"),
        name="a-rather-long-session-name",
        status=Status.WAITING,
        waiting_for="input needed",
        tty=Path("/dev/ttys004"),
    )

    console = Console(width=width, record=True)
    console.print(build_table([session], now=NOW, width=width))

    assert "input needed" in console.export_text()


@pytest.mark.parametrize("width", [40, 50, 60, 80, 99, 100, 120, 200])
def test_table_never_overflows_the_requested_width(width):
    sessions = [
        Session(
            session_id=f"id-{index}",
            cwd=Path(f"/Users/x/dev/web-platform/.claude/worktrees/an-extremely-long-worktree-name-{index}"),
            name=f"an-extremely-long-session-name-number-{index}",
            status=Status.WAITING,
            waiting_for="input needed",
            tty=Path("/dev/ttys004"),
            started_at=NOW - timedelta(days=12),
            updated_at=NOW - timedelta(days=7),
        )
        for index in range(4)
    ]

    console = Console(width=width, record=True)
    console.print(build_table(sessions, now=NOW, width=width))
    lines = console.export_text().splitlines()

    assert lines
    assert all(len(line.rstrip()) <= width for line in lines)


def test_narrow_tables_drop_timing_columns():
    console = Console(width=60, record=True)
    console.print(build_table([], now=NOW, width=60))
    header = console.export_text()

    assert "QUIET" not in header
    assert "PID" not in header


def test_wide_tables_keep_timing_columns():
    console = Console(width=160, record=True)
    console.print(build_table([], now=NOW, width=160))
    header = console.export_text()

    assert "QUIET" in header
    assert "AGE" in header


@pytest.mark.parametrize("width", [40, 60, 99, 120, 200])
def test_table_never_overflows_with_every_column_on(width):
    sessions = [
        Session(
            session_id=f"id-{index}",
            cwd=Path(f"/Users/x/dev/web-platform/.claude/worktrees/an-extremely-long-worktree-name-{index}"),
            name=f"an-extremely-long-session-name-number-{index}",
            pid=77730,
            status=Status.WAITING,
            waiting_for="input needed",
            tty=Path("/dev/ttys004"),
        )
        for index in range(4)
    ]

    console = Console(width=width, record=True)
    console.print(build_table(sessions, now=NOW, width=width, show_pid=True, show_tty=True))

    assert all(len(line.rstrip()) <= width for line in console.export_text().splitlines())


def test_describe_covers_what_the_table_leaves_out():
    session = Session(
        session_id="4e020900-df7c-4665-a804-d973b14a1926",
        cwd=Path("/Users/x/dev/payments-api"),
        name="payments-api-7c",
        pid=77730,
        status=Status.WAITING,
        waiting_for="input needed",
        tty=Path("/dev/ttys004"),
        started_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(minutes=5),
    )

    detail = describe(session, now=NOW, terminal="iterm2")

    assert "payments-api-7c" in detail
    assert "input needed" in detail
    assert "4e020900-df7c-4665-a804-d973b14a1926" in detail
    assert "/Users/x/dev/payments-api" in detail
    assert "pid 77730 · ttys004 · iterm2" in detail
    assert "started 2d ago · quiet 5m" in detail
    assert "(cd /Users/x/dev/payments-api && claude --resume 4e020900-df7c-4665-a804-d973b14a1926)" in detail


def test_conversation_names_both_speakers():
    messages = [
        Message(role="user", text="show history in detail view"),
        Message(role="assistant", text="Enter opens the conversation beside the fleet."),
    ]

    turns = visible(conversation(messages))

    assert "you\nshow history in detail view" in turns
    assert "claude\nEnter opens the conversation beside the fleet." in turns


def test_conversation_escapes_markup():
    assert r"\[bold]red\[/]" in conversation([Message(role="user", text="use [bold]red[/] here")])


def test_conversation_with_nothing_said():
    assert "nothing said yet" in conversation([])


def test_describe_a_closed_session_has_no_process_left():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), status=Status.CLOSED)

    detail = describe(session, now=NOW)

    assert "gone" in detail
    assert "claude --resume a-b" in detail


def test_describe_escapes_markup_in_paths():
    session = Session(session_id="a-b", cwd=Path("/tmp/[weird]/repo"), name="one")

    detail = describe(session, now=NOW)

    assert r"/tmp/\[weird]/repo" in detail


def test_build_table_handles_missing_tty_and_timestamps():
    console = Console(width=200, record=True)
    console.print(build_table([Session(session_id="a-b", cwd=Path("/tmp/x"))], now=NOW))

    assert "-" in console.export_text()
