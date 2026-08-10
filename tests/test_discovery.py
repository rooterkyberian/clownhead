import json
from datetime import UTC, datetime
from pathlib import Path

from clownhead import discovery
from clownhead.models import Session, Status

PS_OUTPUT = """\
  16571 ttys004
  77730 ttys008
    501 ??
  12345 ?
 garbage
  99999 pts/3
"""


def test_parse_ps_output_skips_processes_without_a_tty():
    mapping = discovery.parse_ps_output(PS_OUTPUT)

    assert mapping == {
        16571: Path("/dev/ttys004"),
        77730: Path("/dev/ttys008"),
        99999: Path("/dev/pts/3"),
    }


def test_parse_sessions_builds_models():
    sessions = discovery.parse_sessions(
        [
            {"sessionId": "a-b", "cwd": "/tmp/one", "pid": 1, "status": "idle"},
            {"sessionId": "c-d", "cwd": "/tmp/two", "pid": 2, "status": "busy"},
        ]
    )

    assert [session.status for session in sessions] == [Status.IDLE, Status.BUSY]


def test_enrich_attaches_tty_and_heartbeat():
    heartbeat = datetime(2026, 5, 1, tzinfo=UTC)
    sessions = [Session(session_id="a-b", cwd=Path("/tmp"), pid=7)]

    enriched = discovery.enrich(sessions, ttys={7: Path("/dev/ttys007")}, heartbeats={7: heartbeat})

    assert enriched[0].tty == Path("/dev/ttys007")
    assert enriched[0].updated_at == heartbeat


def test_enrich_leaves_pidless_sessions_alone():
    sessions = [Session(session_id="a-b", cwd=Path("/tmp"))]

    enriched = discovery.enrich(sessions, ttys={7: Path("/dev/ttys007")})

    assert enriched[0].tty is None


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

    ordered = sorted([idle, busy, waiting], key=discovery.sort_key)

    assert [session.session_id for session in ordered] == ["a", "b", "c"]


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
    monkeypatch.setattr(discovery, "tty_map", dict)
    monkeypatch.setattr(discovery, "registry_heartbeats", dict)

    sessions = discovery.list_sessions(interactive_only=True)

    assert [session.session_id for session in sessions] == ["a-b"]


def test_list_sessions_keeps_background_when_asked(monkeypatch):
    monkeypatch.setattr(discovery, "fetch_payload", lambda *a, **k: MIXED_PAYLOAD)
    monkeypatch.setattr(discovery, "tty_map", dict)
    monkeypatch.setattr(discovery, "registry_heartbeats", dict)

    sessions = discovery.list_sessions(interactive_only=False)

    assert [session.session_id for session in sessions] == ["c-d", "a-b"]
