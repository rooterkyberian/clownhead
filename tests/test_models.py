from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clownhead.models import Kind, Session, Status

INTERACTIVE_PAYLOAD = {
    "pid": 16571,
    "cwd": "/Users/x/dev/payments-api",
    "kind": "interactive",
    "startedAt": 1785277524299,
    "sessionId": "4e020900-df7c-4665-a804-d973b14a1926",
    "name": "payments-api-7c",
    "status": "waiting",
    "waitingFor": "input needed",
}

BACKGROUND_PAYLOAD = {
    "id": "80c293ba",
    "cwd": "/Users/x/dev/notifier",
    "kind": "background",
    "startedAt": 1781213131586,
    "sessionId": "80c293ba-05bd-461b-b076-2c6f94ad26a2",
    "name": "Verify GHA migration",
    "state": "blocked",
}


def test_parses_interactive_payload():
    session = Session.model_validate(INTERACTIVE_PAYLOAD)

    assert session.pid == 16571
    assert session.kind is Kind.INTERACTIVE
    assert session.status is Status.WAITING
    assert session.waiting_for == "input needed"
    assert session.cwd == Path("/Users/x/dev/payments-api")
    assert session.started_at == datetime.fromtimestamp(1785277524299 / 1000, tz=UTC)


def test_background_state_maps_onto_status():
    session = Session.model_validate(BACKGROUND_PAYLOAD)

    assert session.kind is Kind.BACKGROUND
    assert session.status is Status.BLOCKED
    assert session.needs_attention


def test_unknown_status_does_not_raise():
    session = Session.model_validate({**INTERACTIVE_PAYLOAD, "status": "wat"})

    assert session.status is Status.UNKNOWN
    assert not session.needs_attention


def test_label_falls_back_to_cwd_and_pid():
    session = Session.model_validate({**INTERACTIVE_PAYLOAD, "name": None})

    assert session.label == "payments-api:16571"


def test_reason_prefers_waiting_for():
    assert Session.model_validate(INTERACTIVE_PAYLOAD).reason == "input needed"
    assert Session.model_validate({**INTERACTIVE_PAYLOAD, "waitingFor": None, "status": "idle"}).reason == "idle"


def test_short_id_is_first_uuid_segment():
    assert Session.model_validate(INTERACTIVE_PAYLOAD).short_id == "4e020900"


@pytest.mark.parametrize("status", [Status.WAITING, Status.BLOCKED, Status.FAILED])
def test_attention_states(status):
    assert Session.model_validate({**INTERACTIVE_PAYLOAD, "status": status.value}).needs_attention


@pytest.mark.parametrize("status", [Status.IDLE, Status.BUSY, Status.UNKNOWN])
def test_calm_states(status):
    payload = {**INTERACTIVE_PAYLOAD, "status": status.value, "waitingFor": None}
    assert not Session.model_validate(payload).needs_attention


def test_age_and_quiet_for():
    now = datetime(2026, 1, 2, tzinfo=UTC)
    session = Session.model_validate(INTERACTIVE_PAYLOAD).model_copy(
        update={"started_at": now - timedelta(hours=5), "updated_at": now - timedelta(minutes=3)}
    )

    assert session.age(now) == timedelta(hours=5)
    assert session.quiet_for(now) == timedelta(minutes=3)


def test_durations_are_none_without_timestamps():
    session = Session(session_id="a-b", cwd=Path("/tmp"))

    assert session.age() is None
    assert session.quiet_for() is None
