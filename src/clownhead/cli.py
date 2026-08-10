"""Command line interface for clownhead."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.live import Live

from clownhead import attention, discovery, snapshot
from clownhead.models import Session
from clownhead.render import build_table
from clownhead.snapshot import BILLING_SENSITIVE_VARS
from clownhead.terminal import detect_terminal

app = typer.Typer(
    name="clownhead",
    help="Overseer for local Claude Code sessions.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
error_console = Console(stderr=True)

CwdOption = Annotated[Path | None, typer.Option("--cwd", help="Only sessions started under this path.")]
AllOption = Annotated[bool, typer.Option("--all", help="Include background agents.")]


def _load(cwd: Path | None, include_background: bool) -> list[Session]:
    if not discovery.peer_discovery_available():
        error_console.print(
            f"[bold red]{discovery.SOCKET_DIR} is not listable[/] — interactive sessions cannot be "
            "discovered. Run clownhead from an unsandboxed shell."
        )
        raise typer.Exit(code=2)
    return discovery.list_sessions(cwd, interactive_only=not include_background)


@app.command("ls")
def list_sessions(cwd: CwdOption = None, include_background: AllOption = False) -> None:
    """Show the fleet, attention-first."""
    sessions = _load(cwd, include_background)
    if not sessions:
        console.print("[dim]no live sessions[/]")
        return
    console.print(build_table(sessions, width=console.width))


@app.command()
def watch(
    cwd: CwdOption = None,
    include_background: AllOption = False,
    interval: Annotated[float, typer.Option("--interval", "-n", help="Seconds between refreshes.")] = 5.0,
) -> None:
    """Continuously refresh the fleet table until interrupted."""
    with Live(build_table(_load(cwd, include_background), width=console.width), console=console, screen=False) as live:
        while True:
            time.sleep(interval)
            live.update(build_table(_load(cwd, include_background), width=console.width))


@app.command()
def paint(
    cwd: CwdOption = None,
    follow: Annotated[bool, typer.Option("--follow", "-f", help="Keep tab colours in sync.")] = False,
    interval: Annotated[float, typer.Option("--interval", "-n", help="Seconds between refreshes.")] = 5.0,
    reset: Annotated[bool, typer.Option("--reset", help="Clear every tab colour and exit.")] = False,
) -> None:
    """Colour each session's terminal tab to match its state."""
    terminal = detect_terminal()
    if not terminal.supports_tab_color:
        error_console.print(f"[yellow]{terminal.name} does not support tab colours; sessions will be belled.[/]")
    if reset:
        cleared = attention.reset(_load(cwd, include_background=False), terminal)
        console.print(f"cleared [bold]{sum(result.delivered for result in cleared)}[/] tabs")
        return
    while True:
        results = attention.paint(_load(cwd, include_background=False), terminal)
        for result in results:
            if not result.delivered:
                error_console.print(f"[yellow]skipped[/] {result.label}: {result.detail}")
        if not follow:
            return
        time.sleep(interval)


@app.command()
def ping(
    name: Annotated[
        str | None, typer.Argument(help="Session name or id prefix; omit to ping every stalled one.")
    ] = None,
    message: Annotated[str | None, typer.Option("--message", "-m", help="Notification text.")] = None,
) -> None:
    """Demand attention from a session's terminal."""
    terminal = detect_terminal()
    sessions = _load(None, include_background=False)
    if name is None:
        results = attention.ping_stalled(sessions, terminal)
        if not results:
            console.print("[dim]nothing is waiting on you[/]")
            return
    else:
        matches = [s for s in sessions if s.name == name or s.short_id == name or s.label == name]
        if not matches:
            error_console.print(f"[bold red]no session matching[/] {name}")
            raise typer.Exit(code=1)
        results = [attention.ping(match, terminal, message) for match in matches]
    for result in results:
        marker = "[green]pinged[/]" if result.delivered else "[yellow]skipped[/]"
        console.print(f"{marker} {result.label}: {result.detail}")


@app.command("snapshot")
def save_snapshot(cwd: CwdOption = None) -> None:
    """Record the current fleet so it can be rebuilt after a reboot."""
    sessions = _load(cwd, include_background=False)
    path = snapshot.save(snapshot.capture(sessions))
    console.print(f"saved [bold]{len(sessions)}[/] sessions to {path}")


@app.command()
def restore(
    use_tmux: Annotated[bool, typer.Option("--tmux", help="Open each session in its own tmux window.")] = False,
    tmux_session: Annotated[str, typer.Option("--tmux-session", help="Target tmux session name.")] = "clownhead",
) -> None:
    """Rebuild a snapshotted fleet, or print the commands that would."""
    try:
        recorded = snapshot.load()
    except FileNotFoundError:
        error_console.print(f"[bold red]no snapshot at[/] {snapshot.snapshot_path()} — run `clownhead snapshot` first")
        raise typer.Exit(code=1) from None

    if not use_tmux:
        for entry in recorded.entries:
            console.print(snapshot.resume_shell_command(entry), soft_wrap=True, markup=False, highlight=False)
        return

    for entry in recorded.entries:
        subprocess.run(snapshot.tmux_argv(entry, tmux_session), check=True)  # noqa: S603
    console.print(f"opened [bold]{len(recorded.entries)}[/] windows in tmux session {tmux_session}")


@app.command()
def doctor() -> None:
    """Check that clownhead can see and signal the fleet."""
    terminal = detect_terminal()
    reachable = discovery.peer_discovery_available()

    console.print(f"claude binary       {discovery.claude_binary()}")
    console.print(f"peer discovery      {'[green]ok[/]' if reachable else '[bold red]blocked (sandboxed?)[/]'}")
    console.print(f"terminal            {terminal.name}")
    console.print(
        "capabilities        "
        f"attention={terminal.supports_attention} "
        f"tab_color={terminal.supports_tab_color} "
        f"notifications={terminal.supports_notifications}"
    )

    leaked = [name for name in BILLING_SENSITIVE_VARS if os.environ.get(name)]
    if leaked:
        console.print(
            f"billing             [bold red]{', '.join(leaked)} set[/] — Claude Code prefers an API key "
            "over subscription OAuth, so restored sessions would bill per token"
        )
    else:
        console.print("billing             [green]subscription auth (no API key in env)[/]")

    path = snapshot.snapshot_path()
    console.print(f"snapshot            {path if path.exists() else '[dim]none[/]'}")


def main() -> None:
    """Entry point for the ``clownhead`` script."""
    app()
