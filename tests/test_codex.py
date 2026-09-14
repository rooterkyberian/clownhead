from datetime import UTC, datetime
from pathlib import Path

import pytest

from clownhead import codex
from clownhead.codex import Unavailable
from clownhead.models import Harness, Kind, Process, Session, Status

THREAD_ID = "01a080d8-4ba6-78a2-9e17-e05ebdf1034e"
OTHER_ID = "01a0810b-bce7-7ec3-9229-2c97ed65f337"


def a_thread(**overrides) -> dict:
    """One app-server ``Thread``, shaped the way the daemon really answers."""
    thread = {
        "id": THREAD_ID,
        "sessionId": THREAD_ID,
        "cwd": "/Users/x/dev/data-platform",
        "status": {"type": "idle"},
        "name": "Explain usage limit resets",
        "preview": "what does it mean",
        "createdAt": 1788868112,
        "updatedAt": 1788868200,
        "source": "cli",
        "cliVersion": "0.153.4",
        "turns": [],
    }
    thread.update(overrides)
    return thread


def a_process(pid: int, command: str = "codex", tty: str = "/dev/ttys025") -> Process:
    return Process(pid=pid, ppid=1, tty=Path(tty), command=command)


@pytest.mark.parametrize(
    ("payload", "expected", "reason"),
    [
        ({"type": "idle"}, Status.IDLE, None),
        ({"type": "notLoaded"}, Status.CLOSED, None),
        ({"type": "systemError"}, Status.CLOSED, None),
        ({"type": "active", "activeFlags": []}, Status.BUSY, None),
        ({"type": "active", "activeFlags": ["waitingOnApproval"]}, Status.BLOCKED, "approval"),
        ({"type": "active", "activeFlags": ["waitingOnUserInput"]}, Status.WAITING, "input"),
        ({"type": "somethingNew"}, Status.UNKNOWN, None),
        (None, Status.UNKNOWN, None),
        ("idle", Status.UNKNOWN, None),
    ],
)
def test_parse_status_maps_every_thread_status(payload, expected, reason):
    assert codex.parse_status(payload) == (expected, reason)


def test_parse_status_prefers_approval_when_a_thread_is_stopped_on_both():
    status, reason = codex.parse_status({"type": "active", "activeFlags": ["waitingOnUserInput", "waitingOnApproval"]})

    assert (status, reason) == (Status.BLOCKED, "approval")


def test_parse_thread_reads_the_fields_the_board_shows():
    session = codex.parse_thread(a_thread())

    assert session.session_id == THREAD_ID
    assert session.harness is Harness.CODEX
    assert session.kind is Kind.INTERACTIVE
    assert session.cwd == Path("/Users/x/dev/data-platform")
    assert session.name == "Explain usage limit resets"
    assert session.status is Status.IDLE


def test_parse_thread_reads_the_seconds_the_app_server_counts_in():
    session = codex.parse_thread(a_thread())

    assert session.started_at == datetime(2026, 9, 8, 11, 48, 32, tzinfo=UTC)
    assert session.updated_at == datetime(2026, 9, 8, 11, 50, tzinfo=UTC)


def test_parse_thread_leaves_a_live_thread_unnamed():
    """A running session has no name until it ends, so the label falls back to the directory."""
    session = codex.parse_thread(a_thread(name=None))

    assert session.name is None
    assert session.label.startswith("data-platform:")


def test_parse_thread_falls_back_to_the_session_id_and_the_current_directory():
    session = codex.parse_thread({"sessionId": OTHER_ID})

    assert session.session_id == OTHER_ID
    assert session.cwd == Path()


def test_parse_items_keeps_what_was_said_and_drops_the_rest():
    payload = [
        {"item": {"type": "agentMessage", "text": "done"}},
        {"item": {"type": "commandExecution", "command": "ls"}},
        {"item": {"type": "webSearch", "query": "anything"}},
        {"item": {"type": "userMessage", "content": [{"type": "text", "text": "do it"}]}},
    ]

    messages = codex.parse_items(payload, limit=10)

    assert [(message.role, message.text) for message in messages] == [("user", "do it"), ("assistant", "done")]


def test_parse_items_turns_the_newest_first_page_back_around():
    payload = [
        {"item": {"type": "agentMessage", "text": "third"}},
        {"item": {"type": "agentMessage", "text": "second"}},
        {"item": {"type": "agentMessage", "text": "first"}},
    ]

    assert [message.text for message in codex.parse_items(payload, limit=10)] == ["first", "second", "third"]


def test_parse_items_stops_at_the_limit_and_keeps_the_newest():
    payload = [{"item": {"type": "agentMessage", "text": f"turn {turn}"}} for turn in range(10)]

    assert [message.text for message in codex.parse_items(payload, limit=2)] == ["turn 1", "turn 0"]


def test_parse_items_dates_nothing_because_an_item_carries_no_moment():
    messages = codex.parse_items([{"item": {"type": "agentMessage", "text": "done"}}], limit=1)

    assert messages[0].at is None


def test_parse_items_joins_the_text_blocks_of_one_user_turn():
    payload = [
        {
            "item": {
                "type": "userMessage",
                "content": [
                    {"type": "text", "text": "first line"},
                    {"type": "image", "url": "ignored"},
                    {"type": "text", "text": "second line"},
                ],
            }
        }
    ]

    assert codex.parse_items(payload, limit=1)[0].text == "first line\nsecond line"


@pytest.mark.parametrize(
    "payload",
    [
        [{"item": {"type": "agentMessage", "text": "   "}}],
        [{"item": {"type": "userMessage", "content": []}}],
        [{"item": {"type": "userMessage", "content": "not a list"}}],
        [{"item": "not a mapping"}],
        ["not a mapping either"],
    ],
)
def test_parse_items_says_nothing_about_a_turn_with_nothing_in_it(payload):
    assert codex.parse_items(payload, limit=5) == []


def test_parse_trust_keeps_only_the_trusted_projects():
    payload = {
        "projects": {
            "/Users/x/dev/one": {"trust_level": "trusted"},
            "/Users/x/dev/two": {"trust_level": "untrusted"},
            "/Users/x/dev/three": {},
            "/Users/x/dev/four": "not a table",
        }
    }

    assert codex.parse_trust(payload) == {Path("/Users/x/dev/one")}


@pytest.mark.parametrize("payload", [{}, {"projects": "not a table"}, {"projects": []}])
def test_parse_trust_reads_a_config_with_no_projects_as_none_trusted(payload):
    assert codex.parse_trust(payload) == set()


def test_trusted_dirs_reads_the_config_file(no_codex):
    (no_codex / "config.toml").write_text('[projects."/Users/x/dev/one"]\ntrust_level = "trusted"\n')

    assert codex.trusted_dirs() == {Path("/Users/x/dev/one")}


def test_trusted_dirs_answers_nothing_known_when_there_is_no_config(no_codex):
    assert codex.trusted_dirs() is None


def test_trusted_dirs_answers_nothing_known_for_a_config_it_cannot_read(no_codex):
    (no_codex / "config.toml").write_text("this is not = = toml\n")

    assert codex.trusted_dirs() is None


def test_parse_lsof_reads_a_directory_per_process():
    text = "p101\nfcwd\nn/Users/x/one\np202\nfcwd\nn/Users/x/two\n"

    assert codex.parse_lsof(text) == {101: Path("/Users/x/one"), 202: Path("/Users/x/two")}


def test_parse_lsof_ignores_lines_before_it_has_been_told_a_process():
    assert codex.parse_lsof("n/Users/x/orphan\np7\nfcwd\nn/Users/x/kept\n") == {7: Path("/Users/x/kept")}


def test_parse_lsof_reads_nothing_out_of_nothing():
    assert codex.parse_lsof("") == {}


def test_process_directories_asks_nothing_when_there_are_no_processes():
    assert codex.process_directories([]) == {}


def test_process_directories_survives_a_machine_with_no_lsof(monkeypatch):
    def missing(*_args, **_keywords):
        raise OSError("no lsof")

    monkeypatch.setattr(codex.subprocess, "run", missing)

    assert codex.process_directories([1]) == {}


def a_codex_session(session_id: str, cwd: str) -> Session:
    return Session(session_id=session_id, cwd=Path(cwd), harness=Harness.CODEX)


def test_attach_processes_binds_a_session_to_the_one_terminal_in_its_directory(monkeypatch):
    monkeypatch.setattr(codex, "process_directories", lambda pids: {77: Path("/Users/x/one")})
    sessions = [a_codex_session(THREAD_ID, "/Users/x/one")]

    attached = codex.attach_processes(sessions, {77: a_process(77)})

    assert (attached[0].pid, attached[0].tty) == (77, Path("/dev/ttys025"))


def test_attach_processes_refuses_to_guess_between_two_sessions_in_one_directory(monkeypatch):
    monkeypatch.setattr(codex, "process_directories", lambda pids: {77: Path("/Users/x/one")})
    sessions = [a_codex_session(THREAD_ID, "/Users/x/one"), a_codex_session(OTHER_ID, "/Users/x/one")]

    attached = codex.attach_processes(sessions, {77: a_process(77)})

    assert [session.pid for session in attached] == [None, None]


def test_attach_processes_refuses_to_guess_between_two_terminals_in_one_directory(monkeypatch):
    monkeypatch.setattr(codex, "process_directories", lambda pids: {77: Path("/Users/x/one"), 78: Path("/Users/x/one")})
    sessions = [a_codex_session(THREAD_ID, "/Users/x/one")]

    attached = codex.attach_processes(sessions, {77: a_process(77), 78: a_process(78)})

    assert attached[0].pid is None


def test_attach_processes_leaves_a_session_whose_directory_has_no_terminal_alone(monkeypatch):
    monkeypatch.setattr(codex, "process_directories", lambda pids: {77: Path("/Users/x/elsewhere")})
    sessions = [a_codex_session(THREAD_ID, "/Users/x/one")]

    assert codex.attach_processes(sessions, {77: a_process(77)})[0].pid is None


def test_attach_processes_asks_nothing_of_a_machine_running_no_codex():
    sessions = [a_codex_session(THREAD_ID, "/Users/x/one")]

    assert codex.attach_processes(sessions, {5: a_process(5, command="zsh")}) == sessions


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("codex", True),
        ("/Users/x/.local/bin/codex", True),
        ("codex exec 'do a thing'", True),
        ("codex-code-mode-host", True),
        ("claude --worktree codex", False),
        ("claude -w codex", False),
        ("zsh", False),
        ("", False),
    ],
)
def test_is_codex_reads_argv0_and_not_the_rest_of_the_line(command, expected):
    """A Claude session working in a worktree called ``codex`` is the case this has to survive."""
    assert codex.is_codex(command) is expected


def test_config_dir_defaults_to_the_codex_home(monkeypatch):
    monkeypatch.delenv(codex.CONFIG_DIR_VAR, raising=False)

    assert codex.config_dir() == codex.DEFAULT_CONFIG_DIR
    assert codex.relocated_config_dir() is None


def test_config_dir_follows_codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv(codex.CONFIG_DIR_VAR, str(tmp_path))

    assert codex.config_dir() == tmp_path
    assert codex.relocated_config_dir() == tmp_path
    assert codex.control_socket() == tmp_path / "app-server-control" / "app-server-control.sock"


def test_available_is_false_without_a_socket(no_codex):
    assert codex.available() is False


def test_available_is_true_once_the_app_server_is_listening(codex_server):
    assert codex.available() is True


def test_available_is_false_for_a_socket_the_daemon_left_behind(codex_server):
    codex_server.stop()

    assert codex_server.path.is_socket()
    assert codex.available() is False


def test_ensure_daemon_starts_nothing_while_one_is_answering(codex_server, monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(_recording_codex(tmp_path)))

    assert codex.ensure_daemon() is True
    assert not (tmp_path / "calls").exists()


def test_ensure_daemon_starts_one_carrying_the_codex_home_it_reads(monkeypatch, tmp_path, no_codex):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(_recording_codex(tmp_path)))

    assert codex.ensure_daemon() is False
    assert (tmp_path / "calls").read_text().split() == ["app-server", "daemon", "start", str(no_codex)]


def test_a_daemon_that_would_not_start_is_not_asked_for_twice(monkeypatch, tmp_path, no_codex):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(_recording_codex(tmp_path)))

    assert codex.ensure_daemon() is False
    assert codex.ensure_daemon() is False
    assert len((tmp_path / "calls").read_text().splitlines()) == 1


def test_ensure_daemon_answers_for_the_one_it_started(monkeypatch, codex_server_to_come):
    def start() -> None:
        codex_server_to_come()

    monkeypatch.setattr(codex, "installed", lambda: True)
    monkeypatch.setattr(codex, "_start_daemon", start)

    assert codex.ensure_daemon() is True


def test_a_start_that_refused_is_what_the_board_is_told(monkeypatch, tmp_path, no_codex):
    binary = tmp_path / "codex"
    binary.write_text('#!/bin/sh\necho "Error: managed standalone Codex install not found" >&2\nexit 1\n')
    binary.chmod(0o755)
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(binary))

    assert codex.ensure_daemon() is False
    expected = "codex app-server daemon start failed: Error: managed standalone Codex install not found"
    assert codex.daemon_failure() == expected


def test_nothing_is_said_about_a_daemon_nobody_tried_to_start(no_codex):
    assert codex.daemon_failure() is None


def test_a_listing_starts_a_daemon_when_nothing_answers(monkeypatch, codex_server_to_come):
    def start() -> None:
        server = codex_server_to_come()
        server.answers["thread/loaded/list"] = {"data": [THREAD_ID]}
        server.answers["thread/read"] = {"thread": a_thread()}

    monkeypatch.setattr(codex, "installed", lambda: True)
    monkeypatch.setattr(codex, "_start_daemon", start)

    assert [session.session_id for session in codex.list_sessions()] == [THREAD_ID]


def test_installed_follows_the_binary_override(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(tmp_path / "absent"))
    assert codex.installed() is False

    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(binary))
    assert codex.installed() is True


def test_list_sessions_reads_the_live_threads_from_the_daemon(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID]}
    codex_server.answers["thread/read"] = {"thread": a_thread()}

    sessions = codex.list_sessions()

    assert [session.session_id for session in sessions] == [THREAD_ID]
    assert sessions[0].status is Status.IDLE


def test_list_sessions_leaves_the_ended_ones_out_until_they_are_asked_for(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": []}
    codex_server.answers["thread/list"] = {"data": [a_thread(status={"type": "notLoaded"})]}

    assert codex.list_sessions() == []
    assert [session.status for session in codex.list_sessions(include_closed=True)] == [Status.CLOSED]


def test_list_sessions_leaves_a_thread_that_died_on_an_error_out_with_them(codex_server):
    """The daemon keeps such a thread loaded for as long as it runs, whoever has closed it."""
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID]}
    codex_server.answers["thread/read"] = {"thread": a_thread(status={"type": "systemError"})}
    codex_server.answers["thread/list"] = {"data": []}

    assert codex.list_sessions() == []

    sessions = codex.list_sessions(include_closed=True)

    assert [session.status for session in sessions] == [Status.CLOSED]
    assert sessions[0].needs_attention is False


def test_list_sessions_ignores_the_threads_codex_opens_for_its_own_work(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID, OTHER_ID]}
    codex_server.answers["thread/read"] = lambda params: {
        "thread": a_thread(
            id=params["threadId"],
            threadSource="system" if params["threadId"] == OTHER_ID else "user",
        )
    }

    assert [session.session_id for session in codex.list_sessions()] == [THREAD_ID]


def test_list_sessions_keeps_a_thread_the_index_names_no_source_for(codex_server):
    """``thread/list`` answers with no ``threadSource``, and those are sessions all the same."""
    codex_server.answers["thread/loaded/list"] = {"data": []}
    codex_server.answers["thread/list"] = {"data": [a_thread(status={"type": "notLoaded"}, threadSource=None)]}

    assert [session.session_id for session in codex.list_sessions(include_closed=True)] == [THREAD_ID]


def test_list_sessions_never_lists_a_live_thread_twice(codex_server):
    """``thread/list`` can catch up mid-session, and the two halves would otherwise overlap."""
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID]}
    codex_server.answers["thread/read"] = {"thread": a_thread()}
    codex_server.answers["thread/list"] = {"data": [a_thread(status={"type": "notLoaded"})]}

    sessions = codex.list_sessions(include_closed=True)

    assert [session.session_id for session in sessions] == [THREAD_ID]
    assert sessions[0].status is Status.IDLE


def test_list_sessions_filters_the_live_half_by_directory(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID, OTHER_ID]}
    codex_server.answers["thread/read"] = lambda params: {
        "thread": a_thread(id=params["threadId"], cwd="/Users/x/one" if params["threadId"] == THREAD_ID else "/other")
    }

    sessions = codex.list_sessions(Path("/Users/x/one"))

    assert [session.session_id for session in sessions] == [THREAD_ID]


def test_list_sessions_passes_the_directory_to_the_call_that_can_filter(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": []}
    codex_server.answers["thread/list"] = {"data": []}

    codex.list_sessions(Path("/Users/x/one"), include_closed=True)

    listed = dict(codex_server.asked)["thread/list"]
    assert listed["cwd"] == "/Users/x/one"


def test_list_sessions_skips_a_thread_the_daemon_will_not_read(codex_server):
    codex_server.answers["thread/loaded/list"] = {"data": [THREAD_ID]}

    assert codex.list_sessions() == []


def test_recent_messages_reads_the_newest_page_and_turns_it_around(codex_server):
    codex_server.answers["thread/items/list"] = {
        "data": [
            {"item": {"type": "agentMessage", "text": "second"}},
            {"item": {"type": "userMessage", "content": [{"type": "text", "text": "first"}]}},
        ]
    }

    messages = codex.recent_messages(THREAD_ID, limit=5)

    assert [(message.role, message.text) for message in messages] == [("user", "first"), ("assistant", "second")]
    assert dict(codex_server.asked)["thread/items/list"]["sortDirection"] == "desc"


def test_rename_asks_the_daemon_to_set_the_name(codex_server):
    codex_server.answers["thread/name/set"] = {}

    codex.rename(THREAD_ID, "search index")

    assert dict(codex_server.asked)["thread/name/set"] == {"threadId": THREAD_ID, "name": "search index"}


def test_a_request_the_daemon_refuses_is_reported(codex_server):
    with pytest.raises(Unavailable, match="thread/name/set failed"):
        codex.rename(THREAD_ID, "anything")


def test_a_daemon_that_is_not_running_is_reported_with_how_to_start_one(no_codex):
    with pytest.raises(Unavailable, match="app-server daemon start"):
        codex.list_sessions()


def test_a_cli_call_carries_the_codex_home_clownhead_reads(monkeypatch, tmp_path, no_codex):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(_recording_codex(tmp_path)))

    codex.send_message(THREAD_ID, "carry on")

    assert (tmp_path / "calls").read_text().split()[-1] == str(no_codex)


def test_send_message_queues_through_the_cli(monkeypatch, tmp_path):
    binary = tmp_path / "codex"
    binary.write_text('#!/bin/sh\necho "$@" > ' + str(tmp_path / "argv") + "\n")
    binary.chmod(0o755)
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(binary))

    codex.send_message(THREAD_ID, "carry on")

    assert (tmp_path / "argv").read_text().strip() == f"queue --thread {THREAD_ID} --message carry on"


def test_send_message_reports_a_cli_that_refused(monkeypatch, tmp_path):
    binary = tmp_path / "codex"
    binary.write_text("#!/bin/sh\nexit 3\n")
    binary.chmod(0o755)
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(binary))

    with pytest.raises(Unavailable, match="codex queue failed"):
        codex.send_message(THREAD_ID, "carry on")


def test_send_message_reports_a_cli_that_is_not_there(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOWNHEAD_CODEX_BIN", str(tmp_path / "absent"))

    with pytest.raises(Unavailable, match="codex queue failed"):
        codex.send_message(THREAD_ID, "carry on")


def _recording_codex(directory: Path) -> Path:
    """A stand-in Codex CLI that appends its arguments and Codex home to a ``calls`` file."""
    binary = directory / "codex"
    binary.write_text(f'#!/bin/sh\necho "$@ $CODEX_HOME" >> {directory / "calls"}\n')
    binary.chmod(0o755)
    return binary
