import json

from clownhead import archive


def test_a_missing_file_reads_as_an_empty_archive():
    assert archive.load() == set()


def test_archive_then_load_round_trip():
    archived = archive.archive("9a1b2c3d-eeee")

    assert archived == {"9a1b2c3d-eeee"}
    assert archive.load() == {"9a1b2c3d-eeee"}


def test_archiving_a_session_keeps_the_ones_already_there():
    archive.archive("9a1b2c3d-eeee")

    assert archive.archive("4e020900-df7c") == {"4e020900-df7c", "9a1b2c3d-eeee"}


def test_archiving_the_same_session_twice_says_the_same_thing():
    archive.archive("9a1b2c3d-eeee")

    assert archive.archive("9a1b2c3d-eeee") == {"9a1b2c3d-eeee"}


def test_restore_takes_a_session_back_out():
    archive.save(["9a1b2c3d-eeee", "4e020900-df7c"])

    assert archive.restore("9a1b2c3d-eeee") == {"4e020900-df7c"}
    assert archive.load() == {"4e020900-df7c"}


def test_restoring_a_session_that_was_never_archived_changes_nothing():
    archive.save(["9a1b2c3d-eeee"])

    assert archive.restore("4e020900-df7c") == {"9a1b2c3d-eeee"}


def test_restore_takes_out_every_session_it_is_handed():
    archive.save(["9a1b2c3d-eeee", "4e020900-df7c", "cef6830d-aaaa"])

    assert archive.restore("9a1b2c3d-eeee", "cef6830d-aaaa") == {"4e020900-df7c"}


def test_restore_leaves_the_file_alone_when_it_takes_nothing_out():
    written = archive.save(["9a1b2c3d-eeee"])
    stamp = written.stat().st_mtime_ns

    assert archive.restore("4e020900-df7c") == {"9a1b2c3d-eeee"}
    assert written.stat().st_mtime_ns == stamp


def test_restore_without_a_file_writes_none():
    assert archive.restore("9a1b2c3d-eeee") == set()
    assert not archive.archive_path().exists()


def test_save_writes_the_ids_sorted_into_the_state_directory():
    path = archive.save(["9a1b2c3d-eeee", "4e020900-df7c"])

    assert path == archive.archive_path()
    assert json.loads(path.read_text()) == ["4e020900-df7c", "9a1b2c3d-eeee"]


def test_load_falls_back_to_an_empty_archive_on_a_corrupt_file():
    path = archive.archive_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    assert archive.load() == set()


def test_load_ignores_an_archive_that_is_not_a_list():
    path = archive.archive_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"9a1b2c3d-eeee": True}))

    assert archive.load() == set()


def test_load_keeps_the_ids_out_of_a_file_that_holds_other_things_too():
    path = archive.archive_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(["9a1b2c3d-eeee", 7, None]))

    assert archive.load() == {"9a1b2c3d-eeee"}
