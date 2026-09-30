import importlib
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from clownhead import tray
from clownhead.models import Harness, Kind, Session, Status


def session(status: Status, harness: Harness = Harness.CLAUDE) -> Session:
    return Session(session_id=f"{harness}-{status}", cwd=Path("/tmp/project"), status=status, harness=harness)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (Status.WAITING, tray.Counts(blocked=1)),
        (Status.BLOCKED, tray.Counts(blocked=1)),
        (Status.FAILED, tray.Counts(blocked=1)),
        (Status.IDLE, tray.Counts(idle=1)),
        (Status.BUSY, tray.Counts(busy=1)),
        (Status.SHELL, tray.Counts(busy=1)),
        (Status.UNKNOWN, tray.Counts(unknown=1)),
        (Status.COMPLETED, tray.Counts()),
        (Status.CLOSED, tray.Counts()),
        (Status.ARCHIVED, tray.Counts()),
    ],
)
def test_status_groups(status: Status, expected: tray.Counts) -> None:
    assert tray.Counts.from_sessions([session(status)]) == expected


def test_counts_cover_both_harnesses_and_background_agents() -> None:
    sessions = [session(status, harness) for harness in Harness for status in Status]
    sessions.append(session(Status.BUSY).model_copy(update={"kind": Kind.BACKGROUND}))
    counts = tray.Counts.from_sessions(sessions)
    assert counts == tray.Counts(blocked=6, idle=2, busy=5, unknown=2)
    assert counts.title == "!6 ○2 ▶5 ?2"
    assert counts.summary == "Blocked: 6   Idle: 2   Busy: 5   Unknown: 2"


def test_empty_fleet_and_large_counts() -> None:
    assert tray.Counts.from_sessions([]).title == "🤡"
    assert tray.Counts(blocked=1234).title == "!1234"


def test_companion_exchanges_snapshots_and_focus_requests() -> None:
    companion = tray.Companion()
    try:
        reading = tray.Snapshot(tray.Counts(busy=3))
        companion.publish(reading)
        assert companion._child.poll(1)
        assert companion._child.recv() == reading
        companion._child.send("focus")
        assert companion.messages() == ["focus"]
        assert companion.messages() == []
        companion._child.close()
        companion.publish(reading)
        assert companion.messages() == []
        assert companion._closed
        companion.publish(reading)
        assert companion.messages() == []
    finally:
        companion.close()


def test_incoming_monitor_closes_when_parent_disconnects() -> None:
    parent, child = tray.multiprocessing.Pipe()
    monitor = tray._IncomingMonitor(child)
    try:
        monitor.focus()
        assert parent.poll(1)
        assert parent.recv() == "focus"
        parent.send(tray.Snapshot(tray.Counts(idle=2)))
        parent.close()
        monitor.start()
        monitor._worker.join(2)
        assert monitor.snapshot.counts == tray.Counts(idle=2)
        assert monitor.closed
        assert not monitor._worker.is_alive()
        monitor.focus()
    finally:
        monitor.close()
        parent.close()
        child.close()


@pytest.mark.parametrize("stuck", [False, True])
def test_companion_reaps_process(monkeypatch: pytest.MonkeyPatch, stuck: bool) -> None:
    companion = tray.Companion()
    process = MagicMock()
    process.is_alive.return_value = stuck
    monkeypatch.setattr(companion, "_process", process)
    companion.start()
    assert companion._child.closed
    companion.close()
    companion.close()
    process.start.assert_called_once()
    process.close.assert_called_once()
    assert process.terminate.call_count == int(stuck)
    assert process.kill.call_count == int(stuck)


def test_companion_reports_missing_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    parent, child = tray.multiprocessing.Pipe()
    monkeypatch.setattr(tray.os, "dup2", lambda *args: None)
    monkeypatch.setattr(tray, "_run_monitor", MagicMock(side_effect=tray.Unavailable("install tray dependencies")))
    try:
        tray._run_companion(child)
        assert parent.poll(1)
        assert parent.recv() == "Tray unavailable: install tray dependencies"
        assert child.closed
    finally:
        parent.close()
        child.close()


def test_reading_error_clears_counts_and_next_read_recovers() -> None:
    loader = MagicMock(side_effect=[[session(Status.BUSY)], OSError("offline"), []])
    monitor = tray.Monitor(loader, 5)
    assert monitor.snapshot.summary == "Reading sessions…"
    assert monitor.snapshot.title == "?"
    monitor.read()
    assert monitor.snapshot.counts == tray.Counts(busy=1)
    monitor.read()
    assert monitor.snapshot.title == "?"
    assert monitor.snapshot.summary == "Discovery failed: offline"
    monitor.read()
    assert monitor.snapshot.counts == tray.Counts()


def test_worker_refresh_is_serial_and_close_wakes_it() -> None:
    reading = Event()
    release = Event()
    refreshed = Event()
    calls = []

    def loader() -> list[Session]:
        calls.append(True)
        if len(calls) == 1:
            reading.set()
            release.wait(5)
        else:
            refreshed.set()
        return []

    monitor = tray.Monitor(loader, 3600)
    monitor.start()
    try:
        assert reading.wait(2)
        monitor.refresh()
        monitor.refresh()
        assert len(calls) == 1
        release.set()
        assert refreshed.wait(2)
    finally:
        release.set()
        monitor.close()
        monitor._worker.join(2)
    assert not monitor._worker.is_alive()
    assert len(calls) == 2


@pytest.mark.parametrize("interval", [0, -1, float("nan"), float("inf")])
def test_invalid_interval(interval: float) -> None:
    with pytest.raises(ValueError, match="finite number greater than zero"):
        tray.run(loader=lambda: [], interval=interval)


@pytest.mark.parametrize(("platform", "backend"), [("darwin", "_run_macos"), ("linux", "_run_linux")])
def test_backend_dispatch_and_cleanup(monkeypatch: pytest.MonkeyPatch, platform: str, backend: str) -> None:
    seen = []

    def fail(monitor: tray.Monitor) -> None:
        seen.append(monitor)
        raise tray.Unavailable("no desktop")

    monkeypatch.setattr(tray.sys, "platform", platform)
    monkeypatch.setattr(tray, backend, fail)
    with pytest.raises(tray.Unavailable, match="no desktop"):
        tray.run(loader=lambda: [], interval=5)
    assert seen[0]._stop.is_set()


def test_unsupported_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tray.sys, "platform", "win32")
    with pytest.raises(tray.Unavailable, match="macOS and Linux"):
        tray.run(loader=lambda: [], interval=5)


def test_missing_macos_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", MagicMock(side_effect=ImportError))
    with pytest.raises(tray.Unavailable, match=r"clownhead\[tray\]"):
        tray._run_macos(tray.Monitor(lambda: [], 5))


def test_macos_menu_updates_and_quits(monkeypatch: pytest.MonkeyPatch) -> None:
    rumps = MagicMock()
    monkeypatch.setattr(importlib, "import_module", lambda _: rumps)
    monitor = tray.Monitor(lambda: [], 5)
    monkeypatch.setattr(monitor, "start", lambda: None)

    def event_loop() -> None:
        monitor.snapshot = tray.Snapshot(tray.Counts(blocked=2, idle=3, busy=4))
        rumps.Timer.call_args.args[0](None)
        assert rumps.App.return_value.title == "!2 ○3 ▶4"
        rumps.App.return_value._nsapp.nsstatusitem.button.return_value.setAttributedTitle_.assert_called_once()
        assert rumps.MenuItem.return_value.title == "Blocked: 2   Idle: 3   Busy: 4"
        rumps.MenuItem.call_args_list[1].kwargs["callback"](None)
        assert monitor._stop.is_set()

    rumps.App.return_value.run.side_effect = event_loop
    tray._run_macos(monitor)
    assert [call.args[0] for call in rumps.MenuItem.call_args_list] == ["Reading sessions…", "Quit clownhead"]
    rumps.Timer.return_value.start.assert_called_once()
    rumps.Timer.return_value.stop.assert_called_once()
    rumps.quit_application.assert_called_once()


def test_macos_click_focuses_and_right_click_opens_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    rumps, appkit, target = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(importlib, "import_module", lambda name: rumps if name == "rumps" else appkit)
    action_class = MagicMock()
    action_class.alloc.return_value.init.return_value = target
    monkeypatch.setattr(tray, "_macos_action_class", lambda _: action_class)
    monitor = MagicMock(spec=tray._IncomingMonitor, can_focus=True, closed=False, quit_label="Close tray")
    monitor.snapshot = tray.Snapshot()
    item = rumps.App.return_value._nsapp.nsstatusitem

    def event_loop() -> None:
        rumps.App.return_value.menu = MagicMock()
        rumps.events.before_start.register.call_args.args[0]()
        item.setMenu_.assert_called_once_with(None)
        item.button.return_value.setTarget_.assert_called_once_with(target)
        item.button.return_value.setAction_.assert_called_once_with("clicked:")
        target.callback()
        monitor.focus.assert_called_once()
        item.popUpStatusItemMenu_.assert_not_called()
        appkit.NSApplication.sharedApplication.return_value.currentEvent.return_value.type.return_value = (
            appkit.NSEventTypeRightMouseUp
        )
        target.callback()
        monitor.focus.assert_called_once()
        item.popUpStatusItemMenu_.assert_called_once()
        rumps.MenuItem.call_args_list[1].kwargs["callback"](None)
        assert monitor.focus.call_count == 2

    rumps.App.return_value.run.side_effect = event_loop
    tray._run_macos(monitor)
    assert [call.args[0] for call in rumps.MenuItem.call_args_list] == ["Reading sessions…", "Focus TUI", "Close tray"]
    rumps.events.before_start.unregister.assert_called_once_with(rumps.events.before_start.register.call_args.args[0])


@pytest.mark.parametrize("activation_supported", [True, False])
def test_linux_focus_menu_and_activation(monkeypatch: pytest.MonkeyPatch, activation_supported: bool) -> None:
    gtk, glib, appindicator = MagicMock(), MagicMock(), MagicMock()
    gtk.init_check.return_value = (True, [])
    monitor = MagicMock(spec=tray._IncomingMonitor, can_focus=True, closed=False, quit_label="Close tray")
    monitor.snapshot = tray.Snapshot()
    items = [MagicMock() for _ in range(3)]
    gtk.MenuItem.side_effect = items
    indicator = appindicator.Indicator.new.return_value
    if not activation_supported:
        indicator.connect.side_effect = TypeError("unknown signal activate")
    monkeypatch.setattr(tray, "_linux_modules", lambda: (gtk, glib, appindicator))

    def event_loop() -> None:
        items[1].connect.call_args.args[1](items[1])
        monitor.focus.assert_called_once()
        if activation_supported:
            indicator.connect.call_args.args[1](indicator, 0, 0)
            assert monitor.focus.call_count == 2

    gtk.main.side_effect = event_loop
    tray._run_linux(monitor)
    assert [call.kwargs["label"] for call in gtk.MenuItem.call_args_list] == [
        "Reading sessions…",
        "Focus TUI",
        "Close tray",
    ]
    indicator.set_secondary_activate_target.assert_called_once_with(items[1])


@pytest.mark.parametrize("legacy", [False, True])
def test_linux_imports_indicator_variants(monkeypatch: pytest.MonkeyPatch, legacy: bool) -> None:
    gi = MagicMock()
    modules = {"gi": gi}

    def require_version(name: str, version: str) -> None:
        if legacy and name == "AyatanaAppIndicator3":
            raise ValueError("missing namespace")

    def load(name: str) -> object:
        return modules.setdefault(name, object())

    gi.require_version.side_effect = require_version
    monkeypatch.setattr(importlib, "import_module", load)
    gtk, glib, indicator = tray._linux_modules()
    assert gtk is modules["gi.repository.Gtk"]
    assert glib is modules["gi.repository.GLib"]
    assert indicator is modules[f"gi.repository.{'AppIndicator3' if legacy else 'AyatanaAppIndicator3'}"]
    gi.require_version.assert_any_call("Gtk", "3.0")


@pytest.mark.parametrize("error", [ImportError, ValueError])
def test_missing_linux_dependency(monkeypatch: pytest.MonkeyPatch, error: type[Exception]) -> None:
    monkeypatch.setattr(importlib, "import_module", MagicMock(side_effect=error))
    with pytest.raises(tray.Unavailable, match="PyGObject, GTK 3 and Ayatana"):
        tray._linux_modules()


def test_linux_requires_display(monkeypatch: pytest.MonkeyPatch) -> None:
    gtk = SimpleNamespace(init_check=lambda _: (False, []))
    monkeypatch.setattr(tray, "_linux_modules", lambda: (gtk, None, None))
    with pytest.raises(tray.Unavailable, match="graphical Linux desktop"):
        tray._run_linux(tray.Monitor(lambda: [], 5))


def test_linux_updates_menu_and_removes_indicator(monkeypatch: pytest.MonkeyPatch) -> None:
    gtk, glib, appindicator = MagicMock(), MagicMock(), MagicMock()
    gtk.init_check.return_value = (True, [])
    menu_items = [MagicMock() for _ in range(2)]
    gtk.MenuItem.side_effect = menu_items
    monkeypatch.setattr(tray, "_linux_modules", lambda: (gtk, glib, appindicator))
    monitor = tray.Monitor(lambda: [], 5)
    monkeypatch.setattr(monitor, "start", lambda: None)
    indicator = appindicator.Indicator.new.return_value

    def event_loop() -> None:
        monitor.snapshot = tray.Snapshot(tray.Counts(idle=7))
        assert glib.timeout_add.call_args.args[1]() is True
        assert indicator.set_label.call_args.args == ("⚪7", "")
        menu_items[0].set_label.assert_called_with("Blocked: 0   Idle: 7   Busy: 0")
        menu_items[1].connect.call_args.args[1](None)
        gtk.main_quit.assert_called_once()

    gtk.main.side_effect = event_loop
    tray._run_linux(monitor)
    assert [call.kwargs["label"] for call in gtk.MenuItem.call_args_list] == ["Reading sessions…", "Quit clownhead"]
    glib.source_remove.assert_called_once_with(glib.timeout_add.return_value)
    indicator.set_status.assert_called_with(appindicator.IndicatorStatus.PASSIVE)
