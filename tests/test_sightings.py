import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from clownhead import sightings
from clownhead.models import Harness, Session, Status

NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def a_session(session_id: str, status: Status = Status.IDLE, **overrides) -> Session:
    return Session(session_id=session_id, cwd=Path("/tmp/repo"), status=status, **overrides)


def stored() -> dict[str, str]:
    return json.loads(sightings.sightings_path().read_text())


def test_a_running_session_is_stamped_and_handed_back_unchanged():
    live = a_session("one")

    assert sightings.record([live], now=NOON) == [live]
    assert stored() == {"claude:one": NOON.isoformat()}


def test_an_ended_session_carries_the_last_stamp_it_got():
    sightings.record([a_session("one")], now=NOON)

    found = sightings.record([a_session("one", Status.CLOSED)], now=NOON + timedelta(hours=3))

    assert found[0].last_seen == NOON


def test_an_ended_session_nothing_saw_running_has_no_stamp():
    assert sightings.record([a_session("one", Status.CLOSED)], now=NOON)[0].last_seen is None


def test_stamps_are_scoped_to_the_harness():
    sightings.record([a_session("same-id")], now=NOON)

    found = sightings.record(
        [a_session("same-id", Status.CLOSED), a_session("same-id", Status.CLOSED, harness=Harness.CODEX)],
        now=NOON,
    )

    assert [session.last_seen for session in found] == [NOON, None]


def test_a_stamp_younger_than_the_resolution_is_left_alone():
    sightings.record([a_session("one")], now=NOON)
    written_at = sightings.sightings_path().stat().st_mtime_ns

    sightings.record([a_session("one")], now=NOON + sightings.RESOLUTION - timedelta(seconds=1))

    assert sightings.sightings_path().stat().st_mtime_ns == written_at
    assert stored() == {"claude:one": NOON.isoformat()}


def test_a_stamp_as_old_as_the_resolution_is_moved_forward():
    later = NOON + sightings.RESOLUTION

    sightings.record([a_session("one")], now=NOON)
    sightings.record([a_session("one")], now=later)

    assert stored() == {"claude:one": later.isoformat()}


def test_a_listing_keeps_the_stamps_of_sessions_it_did_not_see():
    sightings.record([a_session("one")], now=NOON)

    sightings.record([a_session("two")], now=NOON + timedelta(minutes=5))

    assert set(stored()) == {"claude:one", "claude:two"}


def test_stamps_past_the_retention_are_dropped_on_the_next_write():
    sightings.record([a_session("old")], now=NOON)
    later = NOON + sightings.RETENTION + timedelta(seconds=1)

    found = sightings.record([a_session("new"), a_session("old", Status.CLOSED)], now=later)

    assert stored() == {"claude:new": later.isoformat()}
    assert found[1].last_seen is None


def test_a_session_without_an_id_is_never_stamped():
    sightings.record([a_session("")], now=NOON)

    assert not sightings.sightings_path().exists()


@pytest.mark.parametrize(
    "content",
    [
        "broken",
        "[]",
        '{"claude:one": null}',
        '{"claude:one": 4}',
        '{"claude:one": "noon"}',
        '{"claude:one": "2026-10-12T12:00:00"}',
    ],
)
def test_unreadable_stamps_read_as_none(content: str):
    path = sightings.sightings_path()
    path.parent.mkdir(parents=True)
    path.write_text(content)

    assert sightings.load() == {}
    assert sightings.record([a_session("one", Status.CLOSED)], now=NOON)[0].last_seen is None


def test_a_state_directory_that_cannot_be_written_leaves_the_listing_alone(monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError("read only")

    monkeypatch.setattr(sightings, "NamedTemporaryFile", refuse)
    live = a_session("one")

    assert sightings.record([live], now=NOON) == [live]
    assert not sightings.sightings_path().exists()


def test_seen_since_keeps_closed_sessions_seen_at_or_after_the_cutoff_latest_first():
    sessions = [
        a_session("early", Status.CLOSED, last_seen=NOON - timedelta(minutes=1)),
        a_session("on-the-dot", Status.CLOSED, last_seen=NOON),
        a_session("late", Status.CLOSED, last_seen=NOON + timedelta(hours=1)),
        a_session("unseen", Status.CLOSED),
    ]

    assert [session.session_id for session in sightings.seen_since(sessions, NOON)] == ["late", "on-the-dot"]


@pytest.mark.parametrize("status", [Status.IDLE, Status.ARCHIVED, Status.COMPLETED])
def test_seen_since_leaves_out_everything_but_closed_sessions(status: Status):
    session = a_session("one", status, last_seen=NOON)

    assert sightings.seen_since([session], NOON - timedelta(hours=1)) == []
