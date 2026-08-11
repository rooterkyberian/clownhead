"""Command line interface for clownhead."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from clownhead import __version__, attention, discovery, search, tui, worktrees
from clownhead import settings as settings_store
from clownhead.models import Session
from clownhead.render import (
    NARROW_WIDTH,
    Column,
    build_table,
    default_columns,
    format_duration,
    parse_columns,
    parse_duration,
)
from clownhead.search import PullRequest
from clownhead.terminal import detect_terminal

app = typer.Typer(
    name="clownhead",
    help="Overseer for local Claude Code sessions.",
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)

CwdOption = Annotated[Path | None, typer.Option("--cwd", help="Only sessions started under this path.")]
AllOption = Annotated[bool, typer.Option("--all", help="Include background agents.")]
ClosedOption = Annotated[bool, typer.Option("--closed", help="Include sessions that have ended.")]
IntervalOption = Annotated[float | None, typer.Option("--interval", "-n", help="Seconds between refreshes.")]
PrOption = Annotated[
    str | None,
    typer.Option(
        "--pr",
        metavar="URL",
        help="Only sessions whose transcript names this pull request, by URL or owner/repo#123.",
    ),
]
ColumnsOption = Annotated[
    str | None,
    typer.Option(
        "--columns",
        metavar="LIST",
        help=f"Columns to show, comma separated and in the order given: {', '.join(Column)}.",
    ),
]
OlderThanOption = Annotated[
    str,
    typer.Option("--older-than", metavar="AGE", help="Only worktrees untouched for this long, e.g. 30m, 12h, 7d."),
]
MergedOption = Annotated[
    bool, typer.Option("--merged", help="Only worktrees whose work is already in the default branch.")
]
BranchesOption = Annotated[
    bool, typer.Option("--branches", help="Delete each worktree's branch as well. Implies --merged.")
]
DryRunOption = Annotated[bool, typer.Option("--dry-run", help="Show what would be removed and stop.")]
YesOption = Annotated[bool, typer.Option("--yes", "-y", help="Remove without asking first.")]

DEFAULT_AGE = "7d"
"""Long enough that a worktree gone quiet for it is finished with rather than merely idle
overnight, and the only guard here whose whole job is to be argued with."""


def _show_version(requested: bool) -> None:
    if requested:
        console.print(f"clownhead {__version__}")
        raise typer.Exit()


VersionOption = Annotated[
    bool,
    typer.Option("--version", "-V", callback=_show_version, is_eager=True, help="Show the version and exit."),
]


def _require_discovery() -> None:
    if not discovery.peer_discovery_available():
        error_console.print(
            f"[bold red]{discovery.SOCKET_DIR} is not listable[/] — interactive sessions cannot be "
            "discovered. Run clownhead from an unsandboxed shell."
        )
        raise typer.Exit(code=2)


def _load(cwd: Path | None, include_background: bool, include_closed: bool = False) -> list[Session]:
    _require_discovery()
    return discovery.list_sessions(cwd, interactive_only=not include_background, include_closed=include_closed)


def _pull_request(reference: str | None) -> PullRequest | None:
    """Read the ``--pr`` reference, refusing anything that does not name a pull request.

    Failing here rather than falling back to a plain text search is deliberate: a filter
    that quietly matched nothing would look exactly like a fleet that never touched the
    pull request, which is the answer the flag exists to give truthfully.
    """
    if reference is None:
        return None
    parsed = search.parse_pull_request(reference)
    if parsed is None:
        error_console.print(
            f"[bold red]{reference} does not name a pull request[/] — pass a GitHub URL or owner/repo#123."
        )
        raise typer.Exit(code=2)
    return parsed


def _search_note(reference: PullRequest, matched: int, searched: int, include_closed: bool) -> str:
    """What a transcript search covered and found, and where else it could have looked.

    A search that comes back empty is ambiguous — nothing worked on this pull request, or
    the session that did has ended and was never read — so an empty answer says which
    fleet it was empty of rather than leaving that to be guessed at.
    """
    note = f"{reference} · {matched} of {searched} sessions"
    if matched or include_closed:
        return note
    return f"{note} · --closed searches the ones that have ended too"


def _columns(selection: str | None) -> tuple[Column, ...]:
    """Resolve the ``--columns`` selection, or the usual columns when there was none.

    An unnamed selection answers to the saved column settings, which is what the overseer's
    switches write to — one place to say you always want them, whichever view is being
    read.
    """
    if selection is None:
        settings = settings_store.load()
        return default_columns(console.width, settings.show_pid, settings.show_tty, settings.show_worktree)
    try:
        return parse_columns(selection)
    except ValueError as error:
        error_console.print(f"[bold red]{error}[/] — choose from {', '.join(Column)}.")
        raise typer.Exit(code=2) from error


def _fleet_table(sessions: list[Session], columns: Sequence[Column]) -> Table:
    return build_table(sessions, width=console.width, columns=columns)


def _config_dir_line() -> str:
    """The Claude Code directory being read, and what pointed clownhead at it.

    A fleet listed out of one config directory and enriched from another is the failure
    this answers: the CLI scopes its listing to ``CLAUDE_CONFIG_DIR``, so a session board
    that is short of heartbeats is usually clownhead and the CLI disagreeing about where
    to look.
    """
    directory = discovery.config_dir()
    source = f" [dim](${discovery.CONFIG_DIR_VAR})[/]" if os.environ.get(discovery.CONFIG_DIR_VAR) else ""
    missing = "" if directory.is_dir() else " [yellow](missing)[/]"
    return f"{directory}{source}{missing}"


def _warn_about_tab_colours(sessions: Iterable[Session]) -> None:
    terminals = (attention.terminal_of(session) for session in sessions)
    blind = sorted({terminal.name for terminal in terminals if not terminal.supports_tab_color})
    if blind:
        error_console.print(f"[yellow]{', '.join(blind)} does not support tab colours; those tabs are left alone.[/]")


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context, version: VersionOption = False) -> None:
    """Overseer for local Claude Code sessions."""
    if ctx.invoked_subcommand is None:
        launch_tui()


@app.command("tui")
def launch_tui(
    cwd: CwdOption = None,
    include_background: AllOption = False,
    include_closed: ClosedOption = False,
    interval: IntervalOption = None,
) -> None:
    """Browse the fleet interactively — what bare `clownhead` runs."""
    _require_discovery()
    tui.run(
        loader=lambda closed: discovery.list_sessions(
            cwd, interactive_only=not include_background, include_closed=closed
        ),
        interval=interval,
        include_closed=include_closed or None,
    )


@app.command("ls")
def list_sessions(
    cwd: CwdOption = None,
    include_background: AllOption = False,
    include_closed: ClosedOption = False,
    pull_request: PrOption = None,
    columns: ColumnsOption = None,
) -> None:
    """Show the fleet, attention-first.

    ``--pr`` reads the transcripts rather than the listing, and answers for whichever
    fleet the other options chose. Work with a pull request to show for it has usually
    finished, so it is worth saying ``--closed`` as well — but that stays something you
    say rather than something the flag decides for you, and a search that found nothing
    says so.

    ``--columns`` says which columns to show and in what order. Both are read before the
    fleet is, so a selection with a typo in it costs nothing but the typo.
    """
    reference = _pull_request(pull_request)
    chosen = _columns(columns)
    sessions = _load(cwd, include_background, include_closed)
    if reference is not None:
        matched = search.sessions_mentioning(reference, sessions)
        console.print(f"[dim]{_search_note(reference, len(matched), len(sessions), include_closed)}[/]")
        sessions = [session for session in sessions if session.session_id in matched]
    elif not sessions:
        console.print("[dim]no live sessions[/]")
    if sessions:
        console.print(_fleet_table(sessions, chosen))


@app.command("worktrees-cleanup")
def worktrees_cleanup(
    cwd: CwdOption = None,
    older_than: OlderThanOption = DEFAULT_AGE,
    merged_only: MergedOption = False,
    branches: BranchesOption = False,
    dry_run: DryRunOption = False,
    assume_yes: YesOption = False,
) -> None:
    """Retire the worktrees Claude Code left behind.

    The worktrees are asked of git, in every repository the fleet is checked out in, so the
    ones nothing remembers any more are reachable — those are the ones that pile up, since
    a transcript ages out of the config directory long before the checkout it was written
    in goes anywhere.

    Nothing is removed that a live session is in, that a running session has locked, that
    has uncommitted changes, that holds commits on no remote, or that has been used more
    recently than ``--older-than``. What is removed loses only the checkout, unless
    ``--branches`` is given: the branch, and every commit on it, otherwise stays exactly
    where it was.

    ``--branches`` only ever deletes one whose work is upstream already, so it implies
    ``--merged`` rather than quietly widening what it takes.
    """
    age = _older_than(older_than)
    merged_only = merged_only or branches
    sessions = _load(cwd, include_background=True, include_closed=True)
    candidates = worktrees.survey(sessions, older_than=age)
    if merged_only:
        candidates = [candidate for candidate in candidates if candidate.merged]
    if not candidates:
        console.print("[dim]no worktrees[/]")
        return

    going = [candidate for candidate in candidates if candidate.removable]
    console.print(f"[dim]{_cleanup_note(candidates, merged_only)}[/]")
    console.print(_cleanup_table(candidates))
    if not going:
        return
    if dry_run:
        console.print("[dim]--dry-run · nothing removed[/]")
        return
    what = "worktree and branch" if branches else "worktree"
    if not assume_yes and not typer.confirm(f"remove {len(going)} {what}{'' if len(going) == 1 else 's'}?"):
        console.print("[dim]nothing removed[/]")
        return
    _remove_all(going, branches)


def _older_than(text: str) -> timedelta:
    try:
        return parse_duration(text)
    except ValueError as error:
        error_console.print(f"[bold red]{error}[/]")
        raise typer.Exit(code=2) from error


def _cleanup_note(candidates: Sequence[worktrees.Candidate], merged_only: bool) -> str:
    """What the sweep looked at and what it is offering, in one line."""
    going = sum(candidate.removable for candidate in candidates)
    note = f"{len(candidates)} worktree{'' if len(candidates) == 1 else 's'} · {going} to remove"
    if merged_only:
        return f"{note} · merged only"
    return note


def _cleanup_table(candidates: Sequence[worktrees.Candidate]) -> Table:
    """The sweep laid out, with the reason beside anything being kept.

    AGE and WHY are sized to their contents and BRANCH is dropped on a narrow terminal,
    because Rich shares a squeeze out proportionally and would otherwise starve the two
    narrow columns to nothing — leaving a table whose every row said a worktree was being
    kept without room to say why.
    """
    now = datetime.now(tz=UTC)
    rows = [
        (
            candidate.worktree.name,
            format_duration(now - candidate.last_used) if candidate.last_used else "-",
            candidate.worktree.branch or "detached",
            candidate.kept_for or ("merged" if candidate.merged else "-"),
            "" if candidate.removable else "dim",
        )
        for candidate in candidates
    ]
    wide = console.width >= NARROW_WIDTH
    table = Table(box=None, pad_edge=False, header_style="bold", padding=(0, 1))
    table.add_column("WORKTREE", no_wrap=True)
    table.add_column("AGE", no_wrap=True, justify="right", width=max(len(row[1]) for row in rows))
    if wide:
        table.add_column("BRANCH", no_wrap=True)
    table.add_column("WHY", no_wrap=True, width=max(len(row[3]) for row in rows))
    for name, age, branch, why, style in rows:
        cells = (Text(name, style=style), age, branch, why) if wide else (Text(name, style=style), age, why)
        table.add_row(*cells)
    return table


def _remove_all(going: Sequence[worktrees.Candidate], branches: bool = False) -> None:
    """Remove each worktree, reporting failures without abandoning the rest of the run."""
    removed = 0
    for candidate in going:
        try:
            worktrees.remove(candidate.worktree, branch=branches)
        except (LookupError, OSError) as error:
            error_console.print(f"[yellow]kept[/] {candidate.worktree.name}: {error}")
            continue
        removed += 1
    console.print(f"removed [bold]{removed}[/] of {len(going)} worktrees")


@app.command()
def paint(
    cwd: CwdOption = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep tab colours in sync.")] = False,
    interval: IntervalOption = None,
    reset: Annotated[bool, typer.Option("--reset", help="Clear every tab colour and exit.")] = False,
) -> None:
    """Colour each session's terminal tab to match its state."""
    every = interval if interval is not None else settings_store.load().interval
    if reset:
        cleared = attention.reset(_load(cwd, include_background=False))
        console.print(f"cleared [bold]{sum(result.delivered for result in cleared)}[/] tabs")
        return
    warned = False
    while True:
        sessions = _load(cwd, include_background=False)
        if not warned:
            _warn_about_tab_colours(sessions)
            warned = True
        results = attention.paint(sessions)
        for result in results:
            if not result.delivered:
                error_console.print(f"[yellow]skipped[/] {result.label}: {result.detail}")
        if not follow:
            return
        time.sleep(every)


@app.command()
def focus(
    name: Annotated[
        str | None, typer.Argument(help="Session name or id prefix; omit to take every stalled one.")
    ] = None,
    message: Annotated[str | None, typer.Option("--message", "-m", help="Notification text.")] = None,
    foreground: Annotated[
        bool | None,
        typer.Option("--foreground/--no-foreground", help="Raise the terminal application above other windows."),
    ] = None,
) -> None:
    """Demand attention from a session's terminal and bring it to the front."""
    raise_window = settings_store.load().foreground if foreground is None else foreground
    sessions = _load(None, include_background=False)
    if name is None:
        results = attention.focus_stalled(sessions, foreground=raise_window)
        if not results:
            console.print("[dim]nothing is waiting on you[/]")
            return
    else:
        matches = [s for s in sessions if s.name == name or s.short_id == name or s.label == name]
        if not matches:
            error_console.print(f"[bold red]no session matching[/] {name}")
            raise typer.Exit(code=1)
        results = [attention.focus(match, message=message, foreground=raise_window) for match in matches]
    for result in results:
        marker = "[green]focused[/]" if result.delivered else "[yellow]skipped[/]"
        console.print(f"{marker} {result.label}: {result.detail}")


@app.command()
def doctor() -> None:
    """Check that clownhead can see and signal the fleet."""
    terminal = detect_terminal()
    reachable = discovery.peer_discovery_available()

    console.print(f"claude binary       {discovery.claude_binary()}")
    console.print(f"config dir          {_config_dir_line()}")
    console.print(f"peer discovery      {'[green]ok[/]' if reachable else '[bold red]blocked (sandboxed?)[/]'}")
    console.print(f"terminal            {terminal.name} (this shell)")
    if reachable:
        owners = sorted({attention.terminal_of(session).name for session in _load(None, include_background=False)})
        console.print(f"fleet terminals     {', '.join(owners) if owners else '[dim]none[/]'}")
    console.print(
        "capabilities        "
        f"attention={terminal.supports_attention} "
        f"tab_color={terminal.supports_tab_color} "
        f"notifications={terminal.supports_notifications} "
        f"foreground={terminal.supports_foreground}"
    )


def main() -> None:
    """Entry point for the ``clownhead`` script."""
    app()
