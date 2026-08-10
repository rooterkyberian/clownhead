"""Command line interface for clownhead."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table

from clownhead import attention, discovery, tui
from clownhead import settings as settings_store
from clownhead.models import Session
from clownhead.render import build_table
from clownhead.terminal import detect_terminal

app = typer.Typer(
    name="clownhead",
    help="Overseer for local Claude Code sessions.",
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)

BILLING_SENSITIVE_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

CwdOption = Annotated[Path | None, typer.Option("--cwd", help="Only sessions started under this path.")]
AllOption = Annotated[bool, typer.Option("--all", help="Include background agents.")]
ClosedOption = Annotated[bool, typer.Option("--closed", help="Include sessions that have ended.")]
IntervalOption = Annotated[float | None, typer.Option("--interval", "-n", help="Seconds between refreshes.")]
PidOption = Annotated[bool | None, typer.Option("--pid/--no-pid", help="Show the owning process id.")]
TtyOption = Annotated[bool | None, typer.Option("--tty/--no-tty", help="Show the controlling terminal.")]


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


def _fleet_table(sessions: list[Session], show_pid: bool | None, show_tty: bool | None) -> Table:
    settings = settings_store.load()
    return build_table(
        sessions,
        width=console.width,
        show_pid=settings.show_pid if show_pid is None else show_pid,
        show_tty=settings.show_tty if show_tty is None else show_tty,
    )


def _warn_about_tab_colours(sessions: Iterable[Session]) -> None:
    terminals = (attention.terminal_of(session) for session in sessions)
    blind = sorted({terminal.name for terminal in terminals if not terminal.supports_tab_color})
    if blind:
        error_console.print(f"[yellow]{', '.join(blind)} does not support tab colours; those sessions are belled.[/]")


@app.callback(invoke_without_command=True)
def default(ctx: typer.Context) -> None:
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
    show_pid: PidOption = None,
    show_tty: TtyOption = None,
) -> None:
    """Show the fleet, attention-first."""
    sessions = _load(cwd, include_background, include_closed)
    if not sessions:
        console.print("[dim]no live sessions[/]")
        return
    console.print(_fleet_table(sessions, show_pid, show_tty))


@app.command()
def watch(
    cwd: CwdOption = None,
    include_background: AllOption = False,
    include_closed: ClosedOption = False,
    interval: IntervalOption = None,
    show_pid: PidOption = None,
    show_tty: TtyOption = None,
) -> None:
    """Continuously refresh the fleet table until interrupted."""
    every = interval if interval is not None else settings_store.load().interval

    def fleet() -> Table:
        return _fleet_table(_load(cwd, include_background, include_closed), show_pid, show_tty)

    with Live(fleet(), console=console, screen=False) as live:
        while True:
            time.sleep(every)
            live.update(fleet())


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

    leaked = [name for name in BILLING_SENSITIVE_VARS if os.environ.get(name)]
    if leaked:
        console.print(
            f"billing             [bold red]{', '.join(leaked)} set[/] — Claude Code prefers an API key "
            "over subscription OAuth, so a resumed session would bill per token"
        )
    else:
        console.print("billing             [green]subscription auth (no API key in env)[/]")


def main() -> None:
    """Entry point for the ``clownhead`` script."""
    app()
