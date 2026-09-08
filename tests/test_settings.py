import json

import pytest
from pydantic import ValidationError

from clownhead import settings as settings_store
from clownhead.models import Column
from clownhead.settings import Settings


def test_defaults_leave_the_columns_to_the_view_and_raise_on_ping():
    settings = Settings()

    assert settings.columns is None
    assert settings.show_closed is False
    assert settings.foreground is True
    assert settings.paint_tabs is True
    assert settings.interval == 5.0


def test_save_then_load_round_trip():
    saved = Settings(columns=(Column.NAME, Column.PID), interval=12.5, history_turns=40)

    path = settings_store.save(saved)

    assert path == settings_store.settings_path()
    assert settings_store.load() == saved


def test_load_without_a_file_returns_defaults():
    assert settings_store.load() == Settings()


def test_load_falls_back_to_defaults_on_a_corrupt_file():
    path = settings_store.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not json")

    assert settings_store.load() == Settings()


def test_load_falls_back_to_defaults_on_an_impossible_value():
    path = settings_store.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"interval": -4}))

    assert settings_store.load() == Settings()


@pytest.mark.parametrize("interval", [0.5, 4000.0])
def test_interval_is_bounded(interval):
    with pytest.raises(ValueError):
        Settings(interval=interval)


def test_history_turns_is_bounded():
    with pytest.raises(ValueError):
        Settings(history_turns=0)


@pytest.mark.parametrize(
    "columns",
    [
        ("name", "where"),
        ("status", "harness", "name"),
        (),
    ],
)
def test_a_saved_column_selection_survives_the_round_trip(columns):
    saved = Settings(columns=columns or None)

    settings_store.save(saved)

    assert settings_store.load().columns == (tuple(Column(name) for name in columns) or None)


def test_a_column_nobody_named_is_refused():
    with pytest.raises(ValidationError):
        Settings(columns=("name", "not-a-column"))


def test_a_file_naming_a_column_that_no_longer_exists_falls_back_to_defaults():
    """A selection saved by a later version is unreadable rather than half-applied."""
    path = settings_store.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"columns": ["name", "sparkline"]}))

    assert settings_store.load() == Settings()
