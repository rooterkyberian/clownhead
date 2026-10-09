import json
from pathlib import Path

import pytest

from clownhead import discovery, owners
from clownhead.models import Harness, Session, Status
from clownhead.state import state_dir


@pytest.mark.parametrize(
    "launcher",
    [
        "/usr/local/bin/kandev __backend",
        "/Applications/Kandev.app/Contents/MacOS/kandev",
        '"/Applications/My Apps/Kandev.app/Contents/MacOS/kandev"',
        "/opt/kandev/bin/agentctl --port 8765",
        "/home/me/.kandev/bin/agentctl",
        "/opt/bin/agentctl-linux-amd64",
        "/opt/bin/agentctl-darwin-arm64",
        "kandev --title someone's-task",
    ],
)
def test_detects_kandev_through_shell_and_agent_wrappers(launcher: str) -> None:
    processes = discovery.parse_ps_output(f"7 6 ? claude\n6 5 ? /bin/zsh\n5 4 ? node bridge.js\n4 1 ? {launcher}")

    assert owners.process_owner(7, processes) == "kandev"
    assert (
        discovery.enrich([Session(session_id="one", cwd=Path("/repo"), pid=7)], processes=processes)[0].owner
        == "kandev"
    )


@pytest.mark.parametrize(
    "command",
    [
        "claude --name kandev",
        "/bin/echo /Applications/Kandev.app/Contents/MacOS/kandev",
        "python /tmp/kandev/script.py",
        "/tmp/kandev/claude",
        "not-kandev",
        "agentctl",
        "agentctl-linux-amd64-backup",
        "'unfinished",
    ],
)
def test_leaves_unrelated_processes_visible(command: str) -> None:
    assert owners.process_owner(7, discovery.parse_ps_output(f"7 1 ? {command}")) is None


def test_missing_processes_and_cycles_leave_ownership_unknown() -> None:
    processes = discovery.parse_ps_output("7 6 ? claude\n6 7 ? zsh")

    assert owners.process_owner(7, processes) is None
    assert owners.process_owner(99, processes) is None
    assert owners.process_owner(None, processes) is None
    assert owners.process_owner(1, processes) is None


def test_ownership_survives_process_exit_and_is_scoped_to_the_harness() -> None:
    live = Session(session_id="same-id", cwd=Path("/repo"), owner="kandev")
    owners.restore([live])
    closed = live.model_copy(update={"owner": None, "status": Status.CLOSED})
    codex = closed.model_copy(update={"harness": Harness.CODEX})

    found = owners.restore([closed, codex])

    assert [session.owner for session in found] == ["kandev", None]
    path = state_dir() / "owners.json"
    written_at = path.stat().st_mtime_ns
    owners.restore(found)
    assert path.stat().st_mtime_ns == written_at


@pytest.mark.parametrize("stored", ["broken", "[]", '{"claude:one": null}', '{"claude:one": 4}'])
def test_bad_owner_state_does_not_hide_sessions(stored: str) -> None:
    state_dir().mkdir(parents=True)
    (state_dir() / "owners.json").write_text(stored)
    session = Session(session_id="one", cwd=Path("/repo"))

    assert owners.restore([session]) == [session]


def test_read_only_state_still_filters_by_detected_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    state_dir().mkdir(parents=True)
    (state_dir() / "owners.json").write_text(json.dumps({"claude:old": "kandev"}))

    def refuse(*args: object, **kwargs: object) -> None:
        raise PermissionError("read only")

    monkeypatch.setattr(owners, "NamedTemporaryFile", refuse)
    live = Session(session_id="one", cwd=Path("/repo"), owner="kandev")
    closed = Session(session_id="old", cwd=Path("/repo"), status=Status.CLOSED)

    assert [session.owner for session in owners.restore([live, closed])] == ["kandev", "kandev"]
