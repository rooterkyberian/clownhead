import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from clownhead import codex, discovery, usage
from clownhead.models import Harness
from clownhead.usage import Usage, Window

NOW = datetime(2026, 9, 16, 18, tzinfo=UTC)


def claude_cache(now=NOW):
    return {
        "oauthAccount": {"accountUuid": "account-1"},
        "cachedUsageUtilization": {
            "accountUuid": "account-1",
            "fetchedAtMs": now.timestamp() * 1000,
            "utilization": {
                "five_hour": {"utilization": 25.5, "resets_at": (now + timedelta(hours=2)).isoformat()},
                "seven_day": {"utilization": 70, "resets_at": (now + timedelta(days=4)).isoformat()},
            },
        },
    }


def codex_limits(percent=35, minutes=300):
    return {"primary": {"usedPercent": percent, "windowDurationMins": minutes, "resetsAt": 1800000000}}


def test_claude_reads_cached_percentages_and_reset_times():
    found = usage.parse_claude_cache(claude_cache(), now=NOW)
    assert found.problem is None
    assert found.sampled_at == NOW
    assert found.windows == (
        Window("5h", 25.5, NOW + timedelta(hours=2)),
        Window("7d", 70, NOW + timedelta(days=4)),
    )


@pytest.mark.parametrize("age", [-1, float("inf"), float("nan")])
def test_claude_rejects_future_or_invalid_cache_timestamps(age):
    payload = claude_cache()
    payload["cachedUsageUtilization"]["fetchedAtMs"] = (NOW.timestamp() - age) * 1000
    found = usage.parse_claude_cache(payload, now=NOW)
    assert not found.windows
    assert found.problem


def test_claude_keeps_older_unexpired_readings_but_labels_them_stale():
    payload = claude_cache()
    payload["cachedUsageUtilization"]["fetchedAtMs"] -= 75 * 60 * 1000
    found = usage.parse_claude_cache(payload, now=NOW)
    assert found.problem is None
    assert found.stale
    assert [window.used_percent for window in found.windows] == [25.5, 70]
    assert usage.summary({Harness.CLAUDE: found}).plain == "claude 5h ~26% 7d ~70%"
    assert "stale; ~ means last observed usage" in usage.details({Harness.CLAUDE: found}).plain


def test_old_readings_without_a_reset_time_are_not_kept_forever():
    payload = claude_cache()
    payload["cachedUsageUtilization"]["fetchedAtMs"] -= 75 * 60 * 1000
    for window in payload["cachedUsageUtilization"]["utilization"].values():
        window["resets_at"] = None
    assert not usage.parse_claude_cache(payload, now=NOW).windows


@pytest.mark.parametrize("account", [None, {}, {"accountUuid": "another-account"}])
def test_claude_does_not_show_a_different_accounts_usage(account):
    payload = claude_cache()
    payload["oauthAccount"] = account
    assert not usage.parse_claude_cache(payload, now=NOW).windows


def test_claude_drops_windows_after_their_reset():
    payload = claude_cache()
    payload["cachedUsageUtilization"]["utilization"]["five_hour"]["resets_at"] = NOW.isoformat()
    found = usage.parse_claude_cache(payload, now=NOW)
    assert [window.label for window in found.windows] == ["7d"]
    payload["cachedUsageUtilization"]["utilization"]["seven_day"]["resets_at"] = NOW.isoformat()
    assert not usage.parse_claude_cache(payload, now=NOW).windows


@pytest.mark.parametrize("payload", [None, [], {}, {"cachedUsageUtilization": []}])
def test_claude_without_cache_is_unavailable(payload):
    found = usage.parse_claude_cache(payload, now=NOW)
    assert not found.windows
    assert found.problem


@pytest.mark.parametrize("percent", [-1, True, "23", float("nan"), float("inf")])
def test_malformed_percentages_are_not_displayed(percent):
    with pytest.raises(ValueError):
        usage.parse_claude({"five_hour": {"utilization": percent}})
    with pytest.raises(ValueError):
        usage.parse_codex({"rateLimits": codex_limits(percent)})


def test_absent_and_zero_allowances_are_distinct():
    assert usage.parse_claude({"five_hour": {"utilization": 0}, "seven_day": None}) == (Window("5h", 0),)
    assert usage.parse_claude({"five_hour": {"utilization": None}}) == ()
    assert usage.parse_codex({"rateLimits": {"primary": None, "secondary": None}}) == ()


def test_bad_reset_is_rejected_by_the_claude_schema():
    with pytest.raises(ValueError):
        usage.parse_claude({"five_hour": {"utilization": 5, "resets_at": "bad"}})


def test_claude_reads_the_config_directory_in_use(monkeypatch, tmp_path, real_trust_file):
    monkeypatch.setenv(discovery.CONFIG_DIR_VAR, str(tmp_path))
    monkeypatch.setattr(discovery, "trust_file", real_trust_file)
    (tmp_path / ".claude.json").write_text(json.dumps(claude_cache(datetime.now(tz=UTC))))
    assert usage.read(Harness.CLAUDE).windows[0].used_percent == 25.5


def test_claude_missing_and_broken_files_do_not_raise(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(discovery, "trust_file", lambda: path)
    assert usage.read(Harness.CLAUDE).problem
    path.write_text("{")
    assert usage.read(Harness.CLAUDE).problem


def test_claude_malformed_cache_becomes_an_unavailable_reading(monkeypatch, tmp_path):
    path = tmp_path / "config.json"
    monkeypatch.setattr(discovery, "trust_file", lambda: path)
    payload = claude_cache(datetime.now(tz=UTC))
    payload["cachedUsageUtilization"]["utilization"] = "broken"
    path.write_text(json.dumps(payload))
    assert usage.read(Harness.CLAUDE).problem


def test_codex_prefers_its_named_bucket_to_other_metered_products():
    found = usage.parse_codex(
        {
            "rateLimits": codex_limits(99),
            "rateLimitsByLimitId": {"other": codex_limits(98), "codex": codex_limits(35)},
        }
    )
    assert found[0].used_percent == 35


@pytest.mark.parametrize(("minutes", "label"), [(15, "15m"), (60, "1h"), (300, "5h"), (10080, "7d"), (None, "primary")])
def test_codex_reports_actual_window_durations(minutes, label):
    assert usage.parse_codex({"rateLimits": codex_limits(minutes=minutes)})[0].label == label


def test_codex_reads_both_windows_through_the_local_app_server(codex_server):
    limits = codex_limits(10)
    limits["secondary"] = {"usedPercent": 55, "windowDurationMins": 10080}
    codex_server.answers["account/rateLimits/read"] = {"rateLimits": limits}
    found = usage.read(Harness.CODEX)
    assert [window.used_percent for window in found.windows] == [10, 55]
    assert [window.label for window in found.windows] == ["5h", "7d"]
    assert ("account/rateLimits/read", {}) in codex_server.asked


def test_codex_no_sign_in_or_unsupported_method_is_unavailable(codex_server):
    found = usage.read(Harness.CODEX)
    assert not found.windows
    assert "unavailable" in found.problem


def test_codex_no_limits_is_not_zero_usage(codex_server):
    codex_server.answers["account/rateLimits/read"] = {"rateLimits": None}
    assert usage.read(Harness.CODEX) == Usage(problem="no account usage limits reported")


def test_codex_unreachable_daemon_does_not_raise(monkeypatch):
    monkeypatch.setattr(codex, "ensure_daemon", lambda: False)
    assert usage.read(Harness.CODEX).problem


def token_count(at, percent=34, *, limit_id="codex", minutes=10080):
    return {
        "type": "event_msg",
        "timestamp": at.isoformat(),
        "payload": {
            "type": "token_count",
            "rate_limits": {
                "limit_id": limit_id,
                "primary": {
                    "used_percent": percent,
                    "window_minutes": minutes,
                    "resets_at": (at + timedelta(days=4)).timestamp(),
                },
                "secondary": None,
            },
        },
    }


def rollout(home, name, *events):
    path = home / "sessions" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(event) for event in events) + "\n")
    return path


def test_codex_reads_current_profile_rollouts_without_starting_a_daemon(no_codex, monkeypatch, tmp_path):
    at = datetime.now(tz=UTC) - timedelta(seconds=10)
    rollout(no_codex, "current.jsonl", token_count(at))
    rollout(tmp_path / "other-profile", "other.jsonl", token_count(at + timedelta(seconds=1), 99))
    monkeypatch.setattr(codex, "ensure_daemon", lambda: pytest.fail("usage should not start a daemon"))

    found = usage.read(Harness.CODEX)

    assert found.problem is None
    assert found.sampled_at == at
    assert found.windows == (Window("7d", 34, at + timedelta(days=4)),)
    assert not found.stale


def test_codex_chooses_the_newest_observation_not_the_last_touched_file(no_codex):
    at = datetime.now(tz=UTC) - timedelta(minutes=1)
    rollout(no_codex, "newer.jsonl", token_count(at, 34))
    old = rollout(no_codex, "older.jsonl", token_count(at - timedelta(minutes=10), 33))
    os.utime(old, (at.timestamp() + 120, at.timestamp() + 120))
    assert usage.read(Harness.CODEX).windows[0].used_percent == 34


def test_codex_skips_partial_lines_other_products_and_events_without_usage(no_codex):
    at = datetime.now(tz=UTC) - timedelta(minutes=1)
    path = rollout(
        no_codex,
        "session.jsonl",
        token_count(at, 34),
        token_count(at + timedelta(seconds=1), 99, limit_id="another-product"),
        {"type": "event_msg", "timestamp": at.isoformat(), "payload": {"type": "token_count", "rate_limits": None}},
        {"type": "response_item", "timestamp": at.isoformat(), "payload": token_count(at, 99)["payload"]},
    )
    with path.open("a") as handle:
        handle.write('{"type":"event_msg","payload":{"rate_limits":')
    assert usage.read(Harness.CODEX).windows[0].used_percent == 34


def test_codex_marks_old_snapshots_and_drops_expired_windows(no_codex):
    at = datetime.now(tz=UTC) - timedelta(hours=2)
    path = rollout(no_codex, "session.jsonl", token_count(at))
    assert usage.read(Harness.CODEX).stale
    event = token_count(at)
    event["payload"]["rate_limits"]["primary"]["resets_at"] = at.timestamp()
    path.write_text(json.dumps(event))
    assert not usage.read(Harness.CODEX).windows


@pytest.mark.parametrize("response", [codex.Unavailable("unsupported method"), {"rateLimits": None}])
def test_codex_falls_back_when_a_running_app_server_cannot_report_limits(no_codex, monkeypatch, response):
    rollout(no_codex, "session.jsonl", token_count(datetime.now(tz=UTC) - timedelta(seconds=10)))

    class Client:
        def request(self, method):
            assert method == "account/rateLimits/read"
            if isinstance(response, Exception):
                raise response
            return response

    @contextmanager
    def session():
        yield Client()

    monkeypatch.setattr(codex, "available", lambda: True)
    monkeypatch.setattr(codex, "session", session)
    assert usage.read(Harness.CODEX).windows[0].used_percent == 34


def test_summary_labels_consumption_and_highlights_limits():
    found = {Harness.CLAUDE: Usage((Window("5h", 80), Window("7d", 95))), Harness.CODEX: Usage((Window("5h", 0),))}
    text = usage.summary(found)
    assert text.plain == "claude 5h 80% 7d 95% · codex 5h 0%"
    assert {span.style for span in text.spans} >= {"yellow", "bold red"}
    assert "allowance used" in usage.details(found).plain


def test_loading_and_failure_are_not_confused_with_zero():
    found = {Harness.CLAUDE: None, Harness.CODEX: Usage(problem="not signed in")}
    assert usage.summary(found).plain == "claude … · codex --"
    assert "not signed in" in usage.details(found).plain


def test_tooltip_includes_the_local_snapshot_time_and_reset():
    found = {Harness.CLAUDE: usage.parse_claude_cache(claude_cache(), now=NOW)}
    assert "local snapshot from" in usage.details(found).plain
    assert "25.5% used; resets" in usage.details(found).plain
