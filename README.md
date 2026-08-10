# clownhead

An overseer for the Claude Code sessions already running on your machine.

If you keep a dozen terminals open across git worktrees, there is no built-in way to see
which one is blocked waiting on you. `claude agents` is a background-agent view; its
`--json` flag is the only listing that includes interactive sessions. clownhead builds a
status board on top of that, paints your terminal tabs to match, and records enough state
to rebuild the fleet after a reboot.

```
$ clownhead ls
STATUS        NAME                   QUIET  AGE  TTY      WHERE
input needed  payments-api-7c          12d  12d  ttys004  ~/dev/payments-api
busy          index-rebuild-stage-3     0s   1h  ttys013  web-platform ⇢ search-index
idle          invoice-parser           32m   1h  ttys001  web-platform ⇢ invoice-parser
idle          web-platform-1d           4d   4d  ttys017  ~/dev/web-platform
```

`QUIET` is time since the session last touched its registry heartbeat — the useful
number. `AGE` is time since the process started.

## Install

```bash
uv tool install git+https://github.com/rooterkyberian/clownhead
```

Requires Python 3.12+ and the `claude` CLI on `PATH`.

## Commands

| Command | What it does |
|---|---|
| `clownhead ls` | Status board, attention-first. `--cwd` scopes to one tree, `--all` adds background agents. |
| `clownhead watch` | The same table, refreshed on an interval. |
| `clownhead paint` | Colour each session's tab to match its state. `--follow` keeps them in sync. |
| `clownhead ping [name]` | Bounce the dock and notify. With no argument, pings everything that is waiting on you. |
| `clownhead snapshot` | Record the fleet so it can be rebuilt later. |
| `clownhead restore` | Print resume commands, or `--tmux` to open a window per session. |
| `clownhead doctor` | Check discovery, terminal capabilities, and auth. |

## How it works

**Discovery.** `claude agents --json` is the source of truth. Each entry is enriched with
the controlling TTY (from `ps`) and the last heartbeat (from `~/.claude/sessions/<pid>.json`,
which the CLI writes but never prunes, so entries are only trusted when the CLI still
reports the session as live).

**Attention.** Signals are OSC escape sequences written to a session's TTY. The emulator
consumes them before the running application sees them, so they are safe to inject into a
live TUI — unlike plain text, which would land in the session's input stream. iTerm2 gets
`RequestAttention`, tab tinting, and notifications; other terminals fall back to the bell.

**Resurrection.** A session is a transcript on disk, not a process — killing the terminal
loses nothing. What a reboot destroys is the mapping from session id to the directory it
belonged to. `snapshot` records that; `restore` replays it as `claude --resume`.

## Two things worth knowing

**Run it unsandboxed.** Interactive sessions are discovered through per-process sockets in
`/tmp/cc-socks`. A sandboxed shell can run the CLI but not list that directory, in which
case `claude agents --json` silently degrades to background agents only. clownhead checks
for this and refuses rather than reporting an empty fleet.

**Restored sessions strip `ANTHROPIC_API_KEY`.** Claude Code prefers an API key over
subscription OAuth when both are present, so a stray key in a shell profile would quietly
move a restored fleet onto metered billing. `clownhead doctor` warns if one is set.

## Platform support

macOS with iTerm2 is the developed-against configuration. The discovery layer is portable
(`ps -axo pid=,tty=` behaves the same on Linux, yielding `/dev/pts/N`), and terminal
support is a small class per emulator — kitty is wired up, others fall back to the bell.
CI runs the suite on Linux and macOS.

## Development

```bash
mise install
mise run check    # lint + typecheck + test
```

## License

MIT
