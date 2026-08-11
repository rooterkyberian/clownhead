import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from clownhead import discovery
from clownhead.models import Session, Status


@pytest.fixture(autouse=True)
def isolated_stores(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-config"))


PS_OUTPUT = """\
  16571 55997 ttys004 claude
  77730 81946 ttys008 claude --resume
    501     1 ?? /Applications/iTerm.app/Contents/MacOS/iTerm2
  12345     1 ? /usr/sbin/cupsd -l
 garbage
  99999   501 pts/3 bash
"""


def test_parse_ps_output_reads_the_process_table():
    table = discovery.parse_ps_output(PS_OUTPUT)

    assert set(table) == {16571, 77730, 501, 12345, 99999}
    assert table[16571] == discovery.Process(pid=16571, ppid=55997, tty=Path("/dev/ttys004"), command="claude")
    assert table[77730].command == "claude --resume"


def test_parse_ps_output_leaves_processes_without_a_tty_unattached():
    table = discovery.parse_ps_output(PS_OUTPUT)

    assert table[501].tty is None
    assert table[12345].tty is None
    assert table[99999].tty == Path("/dev/pts/3")


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/Applications/iTerm.app/Contents/MacOS/iTerm2", Path("/Applications/iTerm.app")),
        ("/Users/x/Applications/PyCharm.app/Contents/MacOS/pycharm", Path("/Users/x/Applications/PyCharm.app")),
        ("/Applications/Corsair Gear/iCUE.app/Contents/MacOS/iCUE", Path("/Applications/Corsair Gear/iCUE.app")),
        ("/usr/bin/login -fpl m /Applications/iTerm.app/Contents/MacOS/iTerm2 --l", Path("/Applications/iTerm.app")),
        ("claude --resume", None),
        ("/usr/bin/login -pf maciej", None),
    ],
)
def test_application_bundle(command, expected):
    assert discovery.application_bundle(command) == expected


def test_owning_application_walks_up_to_the_terminal(monkeypatch):
    table = discovery.parse_ps_output(PS_OUTPUT)

    assert discovery.owning_application(99999, table) == Path("/Applications/iTerm.app")


def test_owning_application_gives_up_on_a_process_it_cannot_place():
    table = discovery.parse_ps_output(PS_OUTPUT)

    assert discovery.owning_application(16571, table) is None
    assert discovery.owning_application(None, table) is None


def test_owning_application_survives_a_process_loop():
    table = {
        1234: discovery.Process(pid=1234, ppid=5678, tty=None, command="one"),
        5678: discovery.Process(pid=5678, ppid=1234, tty=None, command="two"),
    }

    assert discovery.owning_application(1234, table) is None


def test_parse_sessions_builds_models():
    sessions = discovery.parse_sessions(
        [
            {"sessionId": "a-b", "cwd": "/tmp/one", "pid": 1, "status": "idle"},
            {"sessionId": "c-d", "cwd": "/tmp/two", "pid": 2, "status": "busy"},
        ]
    )

    assert [session.status for session in sessions] == [Status.IDLE, Status.BUSY]


def process_table(*rows: tuple[int, int, str | None, str]) -> dict[int, discovery.Process]:
    return {
        pid: discovery.Process(pid=pid, ppid=ppid, tty=Path(tty) if tty else None, command=command)
        for pid, ppid, tty, command in rows
    }


def test_enrich_attaches_tty_owning_application_and_heartbeat():
    heartbeat = datetime(2026, 5, 1, tzinfo=UTC)
    sessions = [Session(session_id="a-b", cwd=Path("/tmp"), pid=7)]
    processes = process_table(
        (7, 8, "/dev/ttys007", "claude"),
        (8, 1, None, "/Applications/iTerm.app/Contents/MacOS/iTerm2"),
    )

    enriched = discovery.enrich(sessions, processes=processes, heartbeats={7: heartbeat})

    assert enriched[0].tty == Path("/dev/ttys007")
    assert enriched[0].app == Path("/Applications/iTerm.app")
    assert enriched[0].updated_at == heartbeat


def test_enrich_places_each_session_in_its_own_terminal():
    sessions = [
        Session(session_id="a-b", cwd=Path("/tmp"), pid=7),
        Session(session_id="c-d", cwd=Path("/tmp"), pid=9),
    ]
    processes = process_table(
        (7, 8, "/dev/ttys007", "claude"),
        (8, 1, None, "/Applications/iTerm.app/Contents/MacOS/iTerm2"),
        (9, 10, "/dev/ttys009", "claude"),
        (10, 1, None, "/Users/x/Applications/PyCharm.app/Contents/MacOS/pycharm"),
    )

    enriched = discovery.enrich(sessions, processes=processes)

    assert [session.app for session in enriched] == [
        Path("/Applications/iTerm.app"),
        Path("/Users/x/Applications/PyCharm.app"),
    ]


def test_enrich_leaves_pidless_sessions_alone():
    sessions = [Session(session_id="a-b", cwd=Path("/tmp"))]

    enriched = discovery.enrich(sessions, processes=process_table((7, 1, "/dev/ttys007", "claude")))

    assert enriched[0].tty is None
    assert enriched[0].app is None


def test_registry_heartbeats_reads_pid_keyed_files(tmp_path):
    (tmp_path / "77730.json").write_text(json.dumps({"pid": 77730, "updatedAt": 1786356508914}))
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "partial.json").write_text(json.dumps({"pid": 5}))

    heartbeats = discovery.registry_heartbeats(tmp_path)

    assert set(heartbeats) == {77730}
    assert heartbeats[77730] == datetime.fromtimestamp(1786356508914 / 1000, tz=UTC)


def test_registry_heartbeats_tolerates_missing_directory(tmp_path):
    assert discovery.registry_heartbeats(tmp_path / "nope") == {}


def test_sort_key_puts_attention_first_then_busy():
    waiting = Session(session_id="a", cwd=Path("/tmp"), status=Status.WAITING)
    busy = Session(session_id="b", cwd=Path("/tmp"), status=Status.BUSY)
    idle = Session(session_id="c", cwd=Path("/tmp"), status=Status.IDLE)
    closed = Session(session_id="d", cwd=Path("/tmp"), status=Status.CLOSED)

    ordered = sorted([closed, idle, busy, waiting], key=discovery.sort_key)

    assert [session.session_id for session in ordered] == ["a", "b", "c", "d"]


def test_sort_key_puts_the_most_recently_closed_session_first():
    older = Session(session_id="a", cwd=Path("/tmp"), status=Status.CLOSED, started_at=datetime(2026, 1, 1, tzinfo=UTC))
    newer = Session(session_id="b", cwd=Path("/tmp"), status=Status.CLOSED, started_at=datetime(2026, 5, 1, tzinfo=UTC))

    ordered = sorted([older, newer], key=discovery.sort_key)

    assert [session.session_id for session in ordered] == ["b", "a"]


def test_peer_discovery_available_when_socket_dir_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "SOCKET_DIR", tmp_path / "missing")

    assert discovery.peer_discovery_available()


def test_peer_discovery_available_when_socket_dir_listable(monkeypatch, tmp_path):
    monkeypatch.setattr(discovery, "SOCKET_DIR", tmp_path)

    assert discovery.peer_discovery_available()


def test_peer_discovery_blocked_when_listing_raises(monkeypatch, tmp_path):
    class Unlistable(type(tmp_path)):
        def is_dir(self) -> bool:
            return True

        def iterdir(self):
            raise PermissionError("sandboxed")

    monkeypatch.setattr(discovery, "SOCKET_DIR", Unlistable(tmp_path))

    assert not discovery.peer_discovery_available()


def test_claude_binary_is_overridable(monkeypatch):
    monkeypatch.setenv("CLOWNHEAD_CLAUDE_BIN", "/opt/claude")

    assert discovery.claude_binary() == "/opt/claude"


MIXED_PAYLOAD = [
    {"sessionId": "a-b", "cwd": "/tmp/one", "kind": "interactive", "pid": 1, "status": "idle"},
    {"sessionId": "c-d", "cwd": "/tmp/two", "kind": "background", "state": "blocked"},
]


def test_list_sessions_drops_background_by_default(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_payload", lambda *a, **k: MIXED_PAYLOAD)
    monkeypatch.setattr(discovery, "process_table", dict)
    monkeypatch.setattr(discovery, "registry_heartbeats", dict)

    sessions = discovery.list_sessions(interactive_only=True)

    assert [session.session_id for session in sessions] == ["a-b"]


def test_list_sessions_keeps_background_when_asked(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_payload", lambda *a, **k: MIXED_PAYLOAD)
    monkeypatch.setattr(discovery, "process_table", dict)
    monkeypatch.setattr(discovery, "registry_heartbeats", dict)

    sessions = discovery.list_sessions(interactive_only=False)

    assert [session.session_id for session in sessions] == ["c-d", "a-b"]


def registry_file(
    directory: Path,
    pid: int,
    session_id: str,
    cwd: str = "/tmp/one",
    status: str = "busy",
    socket_path: str | None = None,
    updated_at: int = 1786356599914,
) -> None:
    entry = {
        "pid": pid,
        "sessionId": session_id,
        "cwd": cwd,
        "kind": "interactive",
        "name": f"session-{pid}",
        "status": status,
        "startedAt": 1786356508914,
        "updatedAt": updated_at,
    }
    if socket_path is not None:
        entry["messagingSocketPath"] = socket_path
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{pid}.json").write_text(json.dumps(entry))


def test_messaging_socket_reads_the_path_the_session_published(tmp_path):
    registry_file(tmp_path, 1, "a-b", socket_path="/tmp/cc-socks/1.sock")

    assert discovery.messaging_socket("a-b", tmp_path) == Path("/tmp/cc-socks/1.sock")


def test_messaging_socket_falls_back_to_the_conventional_path_when_it_is_listening(monkeypatch, tmp_path, socket_dir):
    registry_file(tmp_path, 1, "a-b")
    monkeypatch.setattr(discovery, "SOCKET_DIR", socket_dir)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_dir / "1.sock"))

    try:
        assert discovery.messaging_socket("a-b", tmp_path) == socket_dir / "1.sock"
    finally:
        server.close()


def test_messaging_socket_ignores_a_conventional_path_with_nothing_on_it(monkeypatch, tmp_path, socket_dir):
    registry_file(tmp_path, 1, "a-b")
    monkeypatch.setattr(discovery, "SOCKET_DIR", socket_dir)

    assert discovery.messaging_socket("a-b", tmp_path) is None


def test_messaging_socket_is_none_for_a_session_the_registry_never_knew(tmp_path):
    registry_file(tmp_path, 1, "a-b", socket_path="/tmp/cc-socks/1.sock")

    assert discovery.messaging_socket("c-d", tmp_path) is None


def test_messaging_socket_prefers_the_record_that_last_beat(tmp_path):
    registry_file(tmp_path, 1, "a-b", socket_path="/tmp/cc-socks/1.sock", updated_at=1786000000000)
    registry_file(tmp_path, 2, "a-b", socket_path="/tmp/cc-socks/2.sock", updated_at=1786999999999)

    assert discovery.messaging_socket("a-b", tmp_path) == Path("/tmp/cc-socks/2.sock")


def test_closed_sessions_are_the_ones_the_cli_no_longer_reports(tmp_path):
    registry_file(tmp_path, 1, "a-b")
    registry_file(tmp_path, 2, "c-d")
    live = [Session(session_id="a-b", cwd=Path("/tmp/one"), pid=1)]

    closed = discovery.closed_sessions(live, registry=tmp_path)

    assert [session.session_id for session in closed] == ["c-d"]
    assert closed[0].status is Status.CLOSED
    assert closed[0].name == "session-2"
    assert closed[0].updated_at == datetime.fromtimestamp(1786356599914 / 1000, tz=UTC)


def test_closed_sessions_drop_the_tty_and_pid_because_they_may_be_recycled(tmp_path):
    registry_file(tmp_path, 2, "c-d")

    closed = discovery.closed_sessions([], registry=tmp_path)

    assert closed[0].tty is None
    assert closed[0].pid is None


def test_closed_sessions_are_scoped_to_a_directory(tmp_path):
    registry_file(tmp_path, 1, "a-b", cwd="/tmp/one")
    registry_file(tmp_path, 2, "c-d", cwd="/tmp/two")

    closed = discovery.closed_sessions([], cwd=Path("/tmp/two"), registry=tmp_path)

    assert [session.session_id for session in closed] == ["c-d"]


def test_closed_sessions_skip_unreadable_records(tmp_path):
    registry_file(tmp_path, 1, "a-b")
    (tmp_path / "bad.json").write_text("not json")
    (tmp_path / "partial.json").write_text(json.dumps({"pid": 3}))

    closed = discovery.closed_sessions([], registry=tmp_path)

    assert [session.session_id for session in closed] == ["a-b"]


def transcript_file(root: Path, session_id: str, cwd: str = "/tmp/one", started: str = "2026-05-01T09:00:00.000Z"):
    project = root / cwd.replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{session_id}.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"type": "mode", "mode": "normal", "sessionId": session_id}),
                json.dumps({"sessionId": session_id, "cwd": cwd, "timestamp": started, "type": "user"}),
            ]
        )
    )
    return path


def test_config_dir_follows_claude_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "elsewhere"))

    assert discovery.config_dir() == tmp_path / "elsewhere"
    assert discovery.session_registry() == tmp_path / "elsewhere" / "sessions"
    assert discovery.transcript_root() == tmp_path / "elsewhere" / "projects"


def test_config_dir_expands_a_home_relative_override(monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "~/.claude-personal")

    assert discovery.config_dir() == Path.home() / ".claude-personal"


@pytest.mark.parametrize("override", [None, ""])
def test_config_dir_falls_back_to_the_claude_default(monkeypatch, override):
    if override is None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    else:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", override)

    assert discovery.config_dir() == Path.home() / ".claude"


def test_heartbeats_and_transcripts_are_read_from_the_configured_directory(monkeypatch, tmp_path):
    registry_file(tmp_path / "sessions", 9, "a-b")
    transcript_file(tmp_path / "projects", "c-d")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))

    assert discovery.registry_heartbeats()[9] == datetime.fromtimestamp(1786356599914 / 1000, tz=UTC)
    assert [session.session_id for session in discovery.transcript_sessions()] == ["c-d"]


def test_transcript_sessions_read_the_working_directory_out_of_the_transcript(tmp_path):
    transcript_file(tmp_path, "a-b", cwd="/tmp/dashed-repo-name")

    sessions = discovery.transcript_sessions(tmp_path)

    assert [session.session_id for session in sessions] == ["a-b"]
    assert sessions[0].cwd == Path("/tmp/dashed-repo-name")
    assert sessions[0].started_at == datetime(2026, 5, 1, 9, tzinfo=UTC)
    assert sessions[0].updated_at is not None


def test_transcript_sessions_ignore_subagent_transcripts(tmp_path):
    transcript_file(tmp_path, "a-b")
    nested = tmp_path / "-tmp-one" / "a-b"
    nested.mkdir(parents=True)
    (nested / "c-d.jsonl").write_text(json.dumps({"cwd": "/tmp/one"}))

    assert [session.session_id for session in discovery.transcript_sessions(tmp_path)] == ["a-b"]


def test_transcript_sessions_skip_transcripts_without_a_working_directory(tmp_path):
    transcript_file(tmp_path, "a-b")
    (tmp_path / "-tmp-one" / "c-d.jsonl").write_text("not json\n" + json.dumps({"type": "mode"}))

    assert [session.session_id for session in discovery.transcript_sessions(tmp_path)] == ["a-b"]


def test_transcript_sessions_tolerate_a_missing_root(tmp_path):
    assert discovery.transcript_sessions(tmp_path / "nope") == []


def conversation(root: Path, session_id: str, entries: list[dict], cwd: str = "/tmp/one") -> Path:
    path = transcript_file(root, session_id, cwd=cwd)
    with path.open("a") as handle:
        for entry in entries:
            handle.write("\n" + json.dumps(entry))
    return path


def said(role: str, text: str, **extra) -> dict:
    return {"type": role, "message": {"role": role, "content": [{"type": "text", "text": text}]}, **extra}


def test_recent_messages_returns_the_tail_of_the_conversation(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "first"),
            said("assistant", "second"),
            said("user", "third"),
            said("assistant", "fourth"),
        ],
    )

    messages = discovery.recent_messages("a-b", limit=3, root=tmp_path)

    assert [(message.role, message.text) for message in messages] == [
        ("assistant", "second"),
        ("user", "third"),
        ("assistant", "fourth"),
    ]


def test_recent_messages_carry_the_time_each_turn_was_said(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "first", timestamp="2026-05-01T09:04:00.000Z"),
            said("assistant", "second"),
        ],
    )

    messages = discovery.recent_messages("a-b", root=tmp_path)

    assert messages[0].at == datetime(2026, 5, 1, 9, 4, tzinfo=UTC)
    assert messages[1].at is None


def test_recent_messages_skips_tool_traffic_and_thinking(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "run the tests"),
            {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "hmm"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "194 passed"}]}},
            said("assistant", "all green"),
        ],
    )

    messages = discovery.recent_messages("a-b", root=tmp_path)

    assert [message.text for message in messages] == ["run the tests", "all green"]


def test_recent_messages_collapse_a_run_by_one_speaker_to_its_last_turn(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "the question"),
            said("assistant", "working on it"),
            said("assistant", "still working"),
            said("assistant", "done"),
        ],
    )

    messages = discovery.recent_messages("a-b", root=tmp_path)

    assert [(message.role, message.text) for message in messages] == [
        ("user", "the question"),
        ("assistant", "done"),
    ]


def test_recent_messages_skip_harness_injected_turns(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "what I typed"),
            said("user", "<task-notification> <task-id>bttvwhxzg</task-id> </task-notification>"),
            said("user", "<system-reminder>be good</system-reminder>"),
        ],
    )

    assert [message.text for message in discovery.recent_messages("a-b", root=tmp_path)] == ["what I typed"]


def test_recent_messages_skips_subagents_and_meta(tmp_path):
    conversation(
        tmp_path,
        "a-b",
        [
            said("user", "mine"),
            said("assistant", "subagent chatter", isSidechain=True),
            said("user", "injected reminder", isMeta=True),
        ],
    )

    assert [message.text for message in discovery.recent_messages("a-b", root=tmp_path)] == ["mine"]


def test_recent_messages_reads_plain_string_content_and_collapses_whitespace(tmp_path):
    conversation(tmp_path, "a-b", [{"type": "user", "message": {"role": "user", "content": "one\n\n  two   three"}}])

    assert [message.text for message in discovery.recent_messages("a-b", root=tmp_path)] == ["one two three"]


def test_recent_messages_reaches_past_a_tail_full_of_tool_output(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery, "TRANSCRIPT_TAIL_BYTES", 200)
    noise = [{"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 500}]}}] * 5
    conversation(tmp_path, "a-b", [said("user", "buried question"), *noise])

    assert [message.text for message in discovery.recent_messages("a-b", root=tmp_path)] == ["buried question"]


def test_recent_messages_without_a_transcript(tmp_path):
    assert discovery.recent_messages("nope", root=tmp_path) == []
    assert discovery.recent_messages("nope", root=tmp_path / "missing") == []


def test_transcript_path_finds_a_session_in_any_project(tmp_path):
    transcript_file(tmp_path, "a-b", cwd="/tmp/one")
    transcript_file(tmp_path, "c-d", cwd="/tmp/two")

    assert discovery.transcript_path("c-d", tmp_path) == tmp_path / "-tmp-two" / "c-d.jsonl"


def test_closed_sessions_include_transcripts_the_registry_has_forgotten(tmp_path):
    transcript_file(tmp_path / "projects", "a-b")
    transcript_file(tmp_path / "projects", "c-d")
    live = [Session(session_id="a-b", cwd=Path("/tmp/one"), pid=1)]

    closed = discovery.closed_sessions(live, registry=tmp_path / "registry", transcripts=tmp_path / "projects")

    assert [session.session_id for session in closed] == ["c-d"]
    assert closed[0].status is Status.CLOSED


def test_closed_sessions_prefer_registry_metadata_over_the_transcript(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    registry_file(registry, 2, "c-d")
    transcript_file(tmp_path / "projects", "c-d")

    closed = discovery.closed_sessions([], registry=registry, transcripts=tmp_path / "projects")

    assert [(session.session_id, session.name) for session in closed] == [("c-d", "session-2")]


def test_list_sessions_appends_closed_sessions_when_asked(monkeypatch, tmp_path):
    registry_file(tmp_path / "sessions", 9, "e-f")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(discovery, "fetch_payload", lambda *a, **k: MIXED_PAYLOAD)
    monkeypatch.setattr(discovery, "process_table", dict)

    sessions = discovery.list_sessions(interactive_only=True, include_closed=True)

    assert [session.session_id for session in sessions] == ["a-b", "e-f"]
    assert sessions[-1].status is Status.CLOSED


def test_list_sessions_asks_the_cli_for_completed_agents_when_including_closed(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(discovery, "fetch_payload", lambda cwd=None, **kwargs: seen.update(kwargs) or [])
    monkeypatch.setattr(discovery, "process_table", dict)

    discovery.list_sessions(include_closed=True)

    assert seen["include_completed"] is True
