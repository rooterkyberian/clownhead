"""Account allowance usage for the header and CLI, independent of session discovery.

Codex exposes this through its app-server and rate-limit events in local rollouts. Claude Code persists a
``cachedUsageUtilization`` snapshot in its global config. No credentials
are read, and fetching a fresh Claude snapshot belongs to Claude Code itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, AwareDatetime, BaseModel, Field, ValidationError
from rich.text import Text

from clownhead import codex, discovery
from clownhead.models import Harness
from clownhead.scan import mapped

REFRESH_INTERVAL = 60.0
CACHE_FRESH_AGE = 3600.0
"""Older snapshots remain useful until their windows reset, but are labelled stale."""


@dataclass(frozen=True)
class Window:
    """The percentage consumed in one allowance window."""

    label: str
    used_percent: float
    resets_at: datetime | None = None


@dataclass(frozen=True)
class Usage:
    """One account's windows, or a reason they could not be read."""

    windows: tuple[Window, ...] = ()
    problem: str | None = None
    sampled_at: datetime | None = None
    stale: bool = False


class ClaudeWindow(BaseModel):
    """One allowance as persisted by Claude Code; unknown fields are ignored."""

    utilization: float | None = Field(default=None, strict=True, ge=0, allow_inf_nan=False)
    resets_at: AwareDatetime | None = None


class ClaudeUtilization(BaseModel):
    """The account-wide allowances, excluding model-specific and extra-usage limits."""

    five_hour: ClaudeWindow | None = None
    seven_day: ClaudeWindow | None = None

    def windows(self) -> tuple[Window, ...]:
        """Normalize reported percentages without treating absent allowances as zero."""
        return tuple(
            Window(label, window.utilization, window.resets_at)
            for label, window in (("5h", self.five_hour), ("7d", self.seven_day))
            if window is not None and window.utilization is not None
        )


class ClaudeCache(BaseModel):
    """Claude's timestamped usage snapshot and the account it belongs to."""

    account_uuid: str | None = Field(default=None, alias="accountUuid")
    fetched_at_ms: float = Field(alias="fetchedAtMs", strict=True, ge=0, allow_inf_nan=False)
    utilization: ClaudeUtilization


class ClaudeAccount(BaseModel):
    """Only the account identity is needed to validate the usage cache's owner."""

    account_uuid: str | None = Field(default=None, alias="accountUuid")


class ClaudeState(BaseModel):
    """The two relevant fields of Claude's global config, parsed with Pydantic v2."""

    account: ClaudeAccount | None = Field(default=None, alias="oauthAccount")
    cache: ClaudeCache | None = Field(default=None, alias="cachedUsageUtilization")


class CodexWindow(BaseModel):
    """An allowance in either app-server camelCase or rollout snake_case."""

    used_percent: float | None = Field(
        default=None,
        validation_alias=AliasChoices("usedPercent", "used_percent"),
        strict=True,
        ge=0,
        allow_inf_nan=False,
    )
    minutes: int | None = Field(
        default=None, validation_alias=AliasChoices("windowDurationMins", "window_minutes"), strict=True, gt=0
    )
    resets_at: AwareDatetime | None = Field(default=None, validation_alias=AliasChoices("resetsAt", "resets_at"))

    def window(self, fallback: str) -> Window | None:
        """Keep the server's actual window duration, including weekly-only plans."""
        if self.used_percent is None:
            return None
        label = fallback
        if self.minutes is not None:
            if self.minutes % 1440 == 0:
                label = f"{self.minutes // 1440}d"
            elif self.minutes % 60 == 0:
                label = f"{self.minutes // 60}h"
            else:
                label = f"{self.minutes}m"
        return Window(label, self.used_percent, self.resets_at)


class CodexLimits(BaseModel):
    """One metered Codex product and its allowance windows."""

    limit_id: str | None = Field(default=None, validation_alias=AliasChoices("limitId", "limit_id"))
    primary: CodexWindow | None = None
    secondary: CodexWindow | None = None

    def windows(self) -> tuple[Window, ...]:
        """Normalize the account's two possible allowances."""
        return tuple(
            window
            for key, value in (("primary", self.primary), ("secondary", self.secondary))
            if value is not None and (window := value.window(key)) is not None
        )


class CodexResponse(BaseModel):
    """The app-server's legacy single bucket and optional product-specific buckets."""

    limits: CodexLimits | None = Field(default=None, alias="rateLimits")
    buckets: dict[str, CodexLimits] | None = Field(default=None, alias="rateLimitsByLimitId")


class CodexTokenCount(BaseModel):
    """Only real token-count events carry observed account allowances."""

    type: Literal["token_count"]
    rate_limits: CodexLimits | None = None


class CodexEvent(BaseModel):
    """A timestamped allowance snapshot from a local Codex rollout."""

    type: Literal["event_msg"]
    timestamp: AwareDatetime
    payload: CodexTokenCount


def read(kind: Harness) -> Usage:
    """Read account usage through local state, keeping failures out of session discovery."""
    try:
        if kind is Harness.CODEX:
            return read_codex()
        return read_claude()
    except OSError:
        return Usage(problem="could not reach account usage")
    except (ValueError, TypeError):
        return Usage(problem="account usage response could not be read")


def parse_claude(payload: object) -> tuple[Window, ...]:
    """Read Claude's shared 5-hour and weekly allowances, ignoring model-specific ones."""
    return ClaudeUtilization.model_validate(payload).windows()


def parse_codex(payload: object) -> tuple[Window, ...]:
    """Read the Codex bucket, retaining the window lengths the server actually reports."""
    response = CodexResponse.model_validate(payload)
    limits = response.buckets.get("codex") if response.buckets is not None else None
    if limits is None:
        limits = response.limits
    return limits.windows() if limits is not None else ()


def summary(found: Mapping[Harness, Usage | None]) -> Text:
    """A compact header that labels each allowance and highlights usage near its limit."""
    text = Text()
    for kind, usage in found.items():
        if text:
            text.append(" · ", style="dim")
        text.append(f"{kind.value} ")
        if usage is None or not usage.windows:
            text.append("…" if usage is None else "--", style="dim")
            continue
        for index, window in enumerate(usage.windows):
            if index:
                text.append(" ")
            text.append(f"{window.label} ", style="dim")
            style = "bold red" if window.used_percent >= 95 else "yellow" if window.used_percent >= 80 else ""
            text.append(f"{'~' if usage.stale else ''}{window.used_percent:.0f}%", style=style)
    return text


def details(found: Mapping[Harness, Usage | None]) -> Text:
    """Explain the percentages and show reset times or why a reading is unavailable."""
    lines = ["Account allowance used"]
    for kind, usage in found.items():
        if usage is None or not usage.windows:
            reason = "loading" if usage is None else usage.problem or "unavailable"
            lines.append(f"{kind.value}: {reason}")
            continue
        if usage.sampled_at is not None:
            sampled = usage.sampled_at.astimezone().strftime("%a %H:%M %Z")
            note = " (stale; ~ means last observed usage)" if usage.stale else ""
            lines.append(f"{kind.value}: local snapshot from {sampled}{note}")
        for window in usage.windows:
            reset = window.resets_at.astimezone().strftime("%a %H:%M %Z") if window.resets_at else "unknown"
            percent = f"{'~' if usage.stale else ''}{window.used_percent:g}%"
            lines.append(f"{kind.value} {window.label}: {percent} used; resets {reset}")
    return Text("\n".join(lines))


def read_claude() -> Usage:
    """Read Claude's account-scoped cache without starting a session or fetching tokens."""
    try:
        payload = ClaudeState.model_validate_json(discovery.trust_file().read_text())
    except (OSError, ValueError):
        return Usage(problem="Claude usage cache unavailable; open /usage in Claude Code")
    return parse_claude_cache(payload)


def parse_claude_cache(payload: object, *, now: datetime | None = None) -> Usage:
    """Validate the cache's owner and age before using its allowance percentages."""
    now = now or datetime.now(tz=UTC)
    try:
        state = ClaudeState.model_validate(payload)
    except ValidationError:
        return Usage(problem="Claude usage cache could not be read")
    cache = state.cache
    if cache is None:
        return Usage(problem="Claude usage cache unavailable; open /usage in Claude Code")
    account_id = state.account.account_uuid if state.account is not None else None
    if not account_id or cache.account_uuid != account_id:
        return Usage(problem="Claude usage cache belongs to a different or signed-out account")
    if cache.fetched_at_ms / 1000 > now.timestamp():
        return Usage(problem="Claude usage cache timestamp is in the future")
    sampled_at = datetime.fromtimestamp(cache.fetched_at_ms / 1000, tz=UTC)
    found = _cached_usage(cache.utilization.windows(), sampled_at, now)
    if not found.windows:
        return Usage(problem="no current Claude usage limits cached; open /usage in Claude Code")
    return found


def _cached_usage(windows: tuple[Window, ...], sampled_at: datetime, now: datetime) -> Usage:
    """Retain unexpired windows, explicitly marking an older observation as stale."""
    stale = (now - sampled_at).total_seconds() > CACHE_FRESH_AGE
    current = tuple(
        window for window in windows if (window.resets_at > now if window.resets_at is not None else not stale)
    )
    return Usage(current, sampled_at=sampled_at, stale=stale)


def read_codex() -> Usage:
    """Ask an existing daemon, falling back to this profile's latest rollout snapshot."""
    problem = "Codex usage unavailable: no running app-server or local rate-limit snapshot"
    if codex.available():
        try:
            with codex.session() as client:
                windows = parse_codex(client.request("account/rateLimits/read"))
            if windows:
                return Usage(windows)
            problem = "no account usage limits reported"
        except (codex.Unavailable, OSError, ValueError):
            problem = "Codex usage unavailable: app-server could not report limits and no local snapshot is available"
    found = read_codex_rollouts()
    return found if found is not None else Usage(problem=problem)


def read_codex_rollouts() -> Usage | None:
    """Find the newest rate-limit observation in the selected Codex home's sessions."""
    paths = []
    for path in (codex.config_dir() / "sessions").rglob("*.jsonl"):
        try:
            paths.append((path.stat().st_mtime, path))
        except OSError:
            continue
    now = datetime.now(tz=UTC)
    newest: Usage | None = None
    for modified, path in sorted(paths, reverse=True):
        if newest is not None and newest.sampled_at is not None and modified < newest.sampled_at.timestamp():
            break
        found = _rollout_usage(path, now)
        if found is None or found.sampled_at is None:
            continue
        if newest is None or newest.sampled_at is None or found.sampled_at > newest.sampled_at:
            newest = found
    return newest


def _rollout_usage(path: Path, now: datetime) -> Usage | None:
    """Read backwards over rate-limit events without decoding a whole conversation."""
    for data in mapped(path):
        end = len(data)
        while (offset := data.rfind(b'"rate_limits"', 0, end)) >= 0:
            start = data.rfind(b"\n", 0, offset) + 1
            stop = data.find(b"\n", offset)
            end = start
            try:
                event = CodexEvent.model_validate_json(data[start : stop if stop >= 0 else len(data)])
            except ValidationError:
                continue
            limits = event.payload.rate_limits
            if event.timestamp > now or limits is None or limits.limit_id not in (None, "codex"):
                continue
            found = _cached_usage(limits.windows(), event.timestamp, now)
            if found.windows:
                return found
    return None
