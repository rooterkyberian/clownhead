from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rich.console import Console, Group, RenderableType
from rich.markup import render as render_markup
from rich.text import Text

from clownhead.discovery import Message
from clownhead.models import Session, Status
from clownhead.pulls import Status as PullStatus
from clownhead.render import (
    PRS_CAP,
    YOUR_TURN_BACKGROUND,
    Column,
    build_pull_rows,
    build_pull_table,
    build_rows,
    build_table,
    conversation,
    default_columns,
    describe,
    describe_pull,
    format_duration,
    parse_columns,
    parse_duration,
    shorten_path,
    shorten_reference,
    worktree_cell,
)
from clownhead.search import PullRequest

NOW = datetime(2026, 8, 10, tzinfo=UTC)


def visible(markup: str) -> str:
    return render_markup(markup).plain


def spoken(renderable: RenderableType, width: int = 60) -> str:
    console = Console(width=width, no_color=True)
    with console.capture() as capture:
        console.print(renderable)
    return "\n".join(line.rstrip() for line in capture.get().splitlines())


def turns(renderable: RenderableType) -> list[Text]:
    return list(renderable.renderables) if isinstance(renderable, Group) else [renderable]


def spans(text: Text) -> list[tuple[str, str]]:
    return [(text.plain[span.start : span.end], str(span.style)) for span in text.spans]


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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("10m", timedelta(minutes=10)),
        ("4h", timedelta(hours=4)),
        ("7d", timedelta(days=7)),
        ("  7D  ", timedelta(days=7)),
        ("0s", timedelta(0)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "7", "d", "7w", "-1d", "seven days", "7 d", "1.5h"])
def test_parse_duration_refuses_what_does_not_name_one(text):
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration(text)


@pytest.mark.parametrize("delta", [timedelta(seconds=30), timedelta(minutes=10), timedelta(hours=4), timedelta(days=7)])
def test_parse_duration_reads_back_what_format_duration_writes(delta):
    assert parse_duration(format_duration(delta)) == delta


def test_worktree_cell_is_empty_for_a_session_outside_one(tmp_path):
    assert worktree_cell(Session(session_id="a-b", cwd=tmp_path)) == "-"


def test_worktree_cell_names_a_worktree_that_still_stands(tmp_path):
    cwd = tmp_path / ".claude" / "worktrees" / "search-index"
    cwd.mkdir(parents=True)

    assert worktree_cell(Session(session_id="a-b", cwd=cwd)) == "search-index"


def test_worktree_cell_marks_a_worktree_that_has_gone(tmp_path):
    cwd = tmp_path / ".claude" / "worktrees" / "search-index"

    assert worktree_cell(Session(session_id="a-b", cwd=cwd)) == "search-index (gone)"


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
    console.print(build_table(sessions, now=NOW, columns=default_columns(200, show_pid=True, show_tty=True)))
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
    console.print(build_table(sessions, now=NOW, columns=default_columns(200, show_pid=True, show_tty=True)))
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


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ("status,name,where", (Column.STATUS, Column.NAME, Column.WHERE)),
        ("where,name", (Column.WHERE, Column.NAME)),
        (" NAME , Resume ", (Column.NAME, Column.RESUME)),
        ("name,,where", (Column.NAME, Column.WHERE)),
        ("name,name", (Column.NAME, Column.NAME)),
    ],
)
def test_parse_columns_keeps_the_order_it_was_given(selection, expected):
    assert parse_columns(selection) == expected


@pytest.mark.parametrize(
    ("selection", "complaint"),
    [("", "no columns named"), ("   ", "no columns named"), ("nope", "unknown column nope"), (",", "no columns named")],
)
def test_parse_columns_refuses_what_is_not_a_column(selection, complaint):
    with pytest.raises(ValueError, match=complaint):
        parse_columns(selection)


def test_parse_columns_names_every_column_it_did_not_recognise():
    with pytest.raises(ValueError, match="unknown columns nope, worse"):
        parse_columns("name,nope,worse")


def test_default_columns_drop_the_timing_ones_when_narrow():
    assert default_columns(60) == (Column.STATUS, Column.NAME, Column.WHERE)


def test_default_columns_add_the_optional_ones_only_when_asked():
    assert default_columns(200) == (Column.STATUS, Column.NAME, Column.QUIET, Column.AGE, Column.WHERE, Column.RESUME)
    assert Column.PID in default_columns(200, show_pid=True)
    assert Column.TTY in default_columns(200, show_tty=True)
    assert Column.WORKTREE in default_columns(200, show_worktree=True)


def test_build_table_shows_the_resume_command_when_the_column_is_asked_for():
    session = Session(session_id="4e020900-df7c", cwd=Path("/tmp/repo"), name="one")

    console = Console(width=200, record=True)
    console.print(build_table([session], now=NOW, width=200, columns=(Column.NAME, Column.RESUME)))
    lines = console.export_text().splitlines()

    assert "RESUME" in lines[0]
    assert "(cd /tmp/repo && claude --resume 4e020900-df7c)" in lines[1]


def test_build_table_keeps_the_resume_command_whole_when_there_is_room():
    session = Session(session_id="4e020900-df7c", cwd=Path("/tmp/repo"), name="one")
    command = "(cd /tmp/repo && claude --resume 4e020900-df7c)"

    console = Console(width=len(command) + len("NAME") + 2, record=True)
    console.print(
        build_table([session], now=NOW, width=len(command) + len("NAME") + 2, columns=(Column.NAME, Column.RESUME))
    )

    assert command in console.export_text()


def test_build_table_renders_the_columns_in_the_order_given():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one", pid=77730)

    console = Console(width=200, record=True)
    console.print(build_table([session], now=NOW, width=200, columns=(Column.WHERE, Column.PID, Column.NAME)))
    header = console.export_text().splitlines()[0].split()

    assert header == ["WHERE", "PID", "NAME"]


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
    console.print(build_table(sessions, now=NOW, width=width, columns=tuple(Column)))

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


def test_describe_leaves_the_resume_command_to_y_and_the_resume_column():
    """The longest line the board prints, and the only one here that wrapped."""
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one")

    assert "claude --resume" not in describe(session, now=NOW)


def test_conversation_names_both_speakers():
    messages = [
        Message(role="user", text="show history in detail view"),
        Message(role="assistant", text="Enter opens the conversation beside the fleet."),
    ]

    said = spoken(conversation(messages, now=NOW))

    assert "you\nshow history in detail view" in said
    assert "claude\nEnter opens the conversation beside the fleet." in said


def test_conversation_dates_each_turn():
    messages = [
        Message(role="user", text="squash them", at=NOW - timedelta(hours=3)),
        Message(role="assistant", text="squashed", at=NOW - timedelta(minutes=4)),
    ]

    said = spoken(conversation(messages, now=NOW))

    assert "you 3h ago" in said
    assert "claude 4m ago" in said


def test_conversation_leaves_an_undated_turn_undated():
    said = spoken(conversation([Message(role="user", text="squash them")], now=NOW))

    assert said == "you\nsquash them"


def test_conversation_reads_a_turn_from_the_future_as_just_said():
    messages = [Message(role="assistant", text="squashed", at=NOW + timedelta(minutes=9))]

    assert "claude 0s ago" in spoken(conversation(messages, now=NOW))


def test_conversation_spends_no_blank_lines_between_turns():
    messages = [
        Message(role="user", text="squash them"),
        Message(role="assistant", text="squashed"),
    ]

    assert spoken(conversation(messages, now=NOW)) == "you\nsquash them\nclaude\nsquashed"


def test_conversation_lays_your_own_turns_on_a_background_of_their_own():
    messages = [
        Message(role="user", text="squash them"),
        Message(role="assistant", text="squashed"),
    ]

    asked, answered = turns(conversation(messages, now=NOW))

    assert str(asked.style) == YOUR_TURN_BACKGROUND
    assert not str(answered.style)


def test_conversation_carries_your_background_to_the_edge_of_the_panel():
    messages = [Message(role="user", text="squash them")]

    console = Console(width=24, no_color=True)
    with console.capture() as capture:
        console.print(conversation(messages, now=NOW))

    assert capture.get().splitlines() == ["you".ljust(24), "squash them".ljust(24)]


@pytest.mark.parametrize(
    ("text", "spoken_text", "marked", "style"),
    [
        ("run `mise run check` first", "run mise run check first", "mise run check", "cyan"),
        ("that is **the** point", "that is the point", "the", "bold"),
    ],
)
def test_conversation_picks_out_the_marked_up_spans(text, spoken_text, marked, style):
    (turn,) = turns(conversation([Message(role="user", text=text)], now=NOW))

    assert spoken_text in turn.plain
    assert (marked, style) in spans(turn)


def test_conversation_escapes_markup():
    (turn,) = turns(conversation([Message(role="user", text="use [bold]red[/] here")], now=NOW))

    assert "use [bold]red[/] here" in turn.plain
    assert ("red", "bold") not in spans(turn)


def test_conversation_escapes_markup_inside_a_marked_span():
    (turn,) = turns(conversation([Message(role="user", text="run `claude [bold]--resume[/]`")], now=NOW))

    assert "run claude [bold]--resume[/]" in turn.plain
    assert ("claude [bold]--resume[/]", "cyan") in spans(turn)


def test_conversation_with_nothing_said():
    assert "nothing said yet" in spoken(conversation([]))


def test_describe_a_closed_session_has_no_process_left():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), status=Status.CLOSED)

    detail = describe(session, now=NOW)

    assert "gone" in detail


def test_describe_escapes_markup_in_paths():
    session = Session(session_id="a-b", cwd=Path("/tmp/[weird]/repo"), name="one")

    detail = describe(session, now=NOW)

    assert r"/tmp/\[weird]/repo" in detail


def test_build_table_handles_missing_tty_and_timestamps():
    console = Console(width=200, record=True)
    console.print(build_table([Session(session_id="a-b", cwd=Path("/tmp/x"))], now=NOW))

    assert "-" in console.export_text()


def test_describe_names_the_pull_requests_a_session_worked_on():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one")

    detail = describe(session, now=NOW, pulls=[PullRequest("widgets", 7, "acme")])

    assert "prs" in detail
    assert "acme/widgets#7" in detail


def test_describe_counts_the_pull_requests_a_line_has_no_room_for():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one")
    many = [PullRequest("widgets", number, "acme") for number in range(1, 7)]

    detail = describe(session, now=NOW, pulls=many)

    assert "acme/widgets#1 · acme/widgets#2 · acme/widgets#3 (+3 more)" in detail


def test_describe_leaves_the_line_out_until_the_transcript_has_been_read():
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="one")

    assert "prs" not in describe(session, now=NOW, pulls=None)
    assert "prs" not in describe(session, now=NOW, pulls=[])


def test_build_pull_rows_renders_what_has_landed_and_leaves_the_rest_blank(a_pull):
    pull = a_pull(updated="2026-08-09T21:00:00Z")

    row = build_pull_rows([pull], {}, {}, now=NOW)[0]

    assert row.reference == "acme/widgets#7"
    assert row.checks == "?"
    assert row.review == "?"
    assert row.sessions == "0"
    assert row.updated == "3h"


def test_build_pull_rows_folds_in_the_status_and_the_sessions_as_they_arrive(a_pull):
    pull = a_pull()
    found = {pull.reference: PullStatus(failing=("test",), review="CHANGES_REQUESTED")}

    row = build_pull_rows([pull], found, {pull.reference: ["a", "b"]}, now=NOW)[0]

    assert row.checks == "✗ 1"
    assert row.review == "changes"
    assert row.sessions == "2"
    assert row.style == "bold red"


def test_build_pull_rows_dims_a_draft_whole_rather_than_labelling_it(a_pull):
    row = build_pull_rows([a_pull(is_draft=True)], {}, {}, now=NOW)[0]

    assert row.style == "dim"


def test_build_pull_table_prints_the_headers_and_a_row(a_pull):
    console = Console(width=200, record=True)
    pull = a_pull()
    found = {pull.reference: PullStatus(ran=True, review="APPROVED")}

    console.print(build_pull_table([pull], found, {pull.reference: ["a"]}, now=NOW))
    printed = console.export_text()

    assert "PR" in printed and "SESSIONS" in printed
    assert "acme/widgets#7" in printed
    assert "approved" in printed


def test_describe_pull_names_the_checks_that_went_red(a_pull):
    pull = a_pull()
    status = PullStatus(failing=("lint", "test"), review="NONE", merge_state="DIRTY")

    detail = describe_pull(pull, status, [])

    assert "https://github.com/acme/widgets/pull/7" in detail
    assert "✗ lint, test" in detail
    assert "dirty" in detail
    assert "none on this machine" in detail


def test_describe_pull_names_the_sessions_that_worked_on_it(a_pull):
    session = Session(session_id="a-b", cwd=Path("/tmp/repo"), name="payments-api-7c")

    detail = describe_pull(a_pull(), PullStatus(ran=True), [session])

    assert "payments-api-7c" in detail
    assert "✓ all passing" in detail


def test_describe_pull_says_it_is_still_reading_rather_than_that_there_are_none(a_pull):
    detail = describe_pull(a_pull(), None, None)

    assert "reading transcripts…" in detail
    assert "not read" in detail


def test_describe_pull_marks_a_draft(a_pull):
    assert "draft" in describe_pull(a_pull(is_draft=True), None, [])


def test_describe_pull_counts_the_checks_a_pane_has_no_room_to_name(a_pull):
    failing = tuple(f"test ({shard}/6)" for shard in range(1, 12))
    status = PullStatus(failing=failing)

    detail = describe_pull(a_pull(), status, [])

    assert "✗ test (1/6), test (2/6), test (3/6) (+8 more)" in detail


def test_describe_pull_names_a_short_list_of_checks_whole(a_pull):
    status = PullStatus(running=("lint", "test"))

    assert "⟳ lint, test" in describe_pull(a_pull(), status, [])


def test_build_pull_rows_says_it_has_not_looked_rather_than_counting_no_sessions(a_pull):
    """`0` is a claim about the machine; `?` is the truth before the transcripts are read."""
    row = build_pull_rows([a_pull()], {}, None, now=NOW)[0]

    assert row.sessions == "?"


def test_build_pull_rows_counts_none_once_it_has_looked(a_pull):
    row = build_pull_rows([a_pull()], {}, {}, now=NOW)[0]

    assert row.sessions == "0"


def a_session(session_id: str = "a-b", name: str = "one") -> Session:
    return Session(session_id=session_id, cwd=Path("/tmp/repo"), name=name)


def test_prs_column_names_the_freshest_and_counts_the_rest():
    named = {"a-b": [PullRequest("widgets", 7, "acme"), PullRequest("gadgets", 9, "acme")]}

    row = build_rows([a_session()], NOW, named)[0]

    assert row.prs == "widgets#7 +1"


def test_prs_column_drops_the_owner_the_number_cannot_afford():
    """A column is narrow, and every row in it is usually the same organisation."""
    named = {"a-b": [PullRequest("widgets", 7, "acme")]}

    assert build_rows([a_session()], NOW, named)[0].prs == "widgets#7"


def test_prs_column_says_it_has_not_read_the_transcripts_yet():
    assert build_rows([a_session()], NOW, None)[0].prs == "?"
    assert build_rows([a_session()], NOW, {})[0].prs == "?"


def test_prs_column_says_a_session_named_nothing_once_it_has_looked():
    assert build_rows([a_session()], NOW, {"a-b": []})[0].prs == "-"


@pytest.mark.parametrize(
    ("text", "width", "expected"),
    [
        ("widgets#7", 24, "widgets#7"),
        ("ai-development-toolkit#464", 24, "ai-development-tool…#464"),
        ("ai-development-toolkit#150 +47", 24, "ai-development-…#150 +47"),
        ("widgets#7", 5, "wi…#7"),
        ("widgets#7", 2, "w…"),
    ],
)
def test_shorten_reference_gives_up_the_repository_before_the_number(text, width, expected):
    shortened = shorten_reference(text, width)

    assert shortened == expected
    assert len(shortened) <= width


def test_prs_cell_never_outgrows_its_cap():
    named = {"a-b": [PullRequest("a" * 80, 12345, "acme")] * 40}

    assert len(build_rows([a_session()], NOW, named)[0].prs) <= PRS_CAP


def test_prs_is_off_unless_asked_for():
    assert Column.PRS not in default_columns(200)
    assert Column.PRS in default_columns(200, show_prs=True)


def test_build_table_draws_the_prs_column_when_it_is_named():
    console = Console(width=120, record=True)
    named = {"a-b": [PullRequest("widgets", 7, "acme")]}

    console.print(build_table([a_session()], now=NOW, columns=(Column.NAME, Column.PRS), pulls=named))
    printed = console.export_text()

    assert "PRS" in printed
    assert "widgets#7" in printed
