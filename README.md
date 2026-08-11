# 🤡 clownhead

A status board for the Claude Code sessions already running on your machine: which are
busy, which are idle, and which one is blocked waiting on you.

![A herd of Claude Code sessions, one waiting on you](docs/demo.gif)

`claude agents --json` is the only listing that includes interactive sessions. clownhead
builds a board on top of it, tints your terminal tabs to match, and puts you back in front
of whichever session is asking for you.

## Install

```bash
uv tool install git+https://github.com/rooterkyberian/clownhead
```

Requires Python 3.12+ and Claude Code 2.1.227 or newer on `PATH` — the latest release.
clownhead reads what a session publishes about itself and talks to it over the socket it
publishes, both of which move with the CLI, so it is developed against the current version
rather than a floor held open for older ones. `claude --version` reports yours.

## Keys

`QUIET` is time since the session last beat, `AGE` is time since its process started, and
the pane below the table carries the id, path, process and terminal the columns cannot fit.

- `→` opens that session's conversation beside the herd — usually the fastest way to tell
  what it is actually doing. It opens on the newest turn and takes the arrow keys while it
  is up, so `↑` and `↓` read back through it. `←` closes it and hands them back.
- click a row to read it, rather than interrupting it.
- `f` focuses its terminal: attention, then the window brought to the front.
- `/` filters by name, status, path, or session id — or by pull request, below.
- `c` folds in the sessions that have already ended. The count of them in the top bar is
  the same switch, and clicking it does the same thing.
- `y` copies its resume command.
- `r` renames it.
- `t` asks whether to send its process SIGTERM, and can close its tab behind it.
- `,` opens the settings.
- `R` reloads the board now; it also reloads on its own interval.
- `q` quits.

## Tab colours

Every reload tints each session's tab to match its state, so the herd is readable from the
tab bar of a terminal the board is nowhere near. The board takes violet, which no status
wears, and gives it back when it exits. Turn it off in the settings and the tabs it tinted
are cleared on the way out.

## Pull requests

Which pull request a session belongs to is in none of the columns — it is in what was said.
Paste a pull request URL into `/` and clownhead reads the transcripts of whatever the board
is showing. Finished work is usually in a session that has ended, so `c` first, then the
URL:

```
 🤡  2 of 137 sessions · acme/data-platform#309                       ⟳ 5s
 STATUS  NAME                             WHERE
 idle    invoice-parser        data-platform ⇢ invoice-parser
 closed  design-system:87e26be1  ~/dev/acme/design-system
```

A search of the live herd alone that comes back empty says so, and says that `c` would
widen it, rather than folding the closed ones in uninvited.

`owner/repo#309` and `repo#309` name the same thing more briefly. A bare `#309` does not:
it means a different pull request in every checkout on the machine, so it stays an ordinary
filter needle.

## Commands

Every view is also a one-shot subcommand, so the same data pipes into a script.

| Command | What it does |
|---|---|
| `clownhead` | The interactive overseer. Same as `clownhead tui`. |
| `clownhead ls` | Status board, attention-first. `--cwd` scopes to one tree, `--all` adds background agents, `--closed` adds sessions that have ended, `--pr` keeps only the ones whose transcript names a pull request, `--columns` picks the columns and their order. |
| `clownhead paint` | Colour each session's tab to match its state, for a board you would rather not keep open. `--follow` keeps them in sync, `--reset` clears them. |
| `clownhead focus [name]` | Bounce the dock, raise the terminal, and notify. With no argument, takes every session that is waiting on you. `--no-foreground` leaves your windows where they are. |
| `clownhead doctor` | Check discovery, terminal capabilities, and auth. |
| `clownhead --version` | The installed version, which a problem report asks for. |

`--columns` names what `ls` shows and the order to show it in: `status`, `name`, `quiet`,
`age`, `pid`, `tty`, `where`, `resume`. Everything but `pid` and `tty` is on by default,
`resume` included — a listing you are reading in order to get back into something should
hand you the command that does it:

```bash
$ clownhead ls --pr acme/payments-api#309 --closed --columns name,resume
acme/payments-api#309 · 2 of 74 sessions
NAME             RESUME
payments-api-7c  (cd /Users/you/dev/payments-api && claude --resume 4e020900-df7c-4665-a804-d973b14a1926)
index-rebuild    (cd /Users/you/dev/web-platform && claude --resume 8b1c4f22-0d31-4f0a-9c2e-3a7b1e5d6f08 --worktree search-index)
```

Columns holding a word or a duration are as wide as their widest cell. The ones holding a
name, a path or a command cannot be — any of them outgrows any terminal — so they share
what is left, the last of them taking the difference. A resume command is the longest thing
on the board and the one truncation ruins, so naming fewer columns is how you get one
whole; `--columns name,resume` is the pair worth remembering. Below about a hundred
columns the default drops the timing and resume columns rather than truncate every cell —
though a selection made by hand is never thinned, since dropping a column somebody named
would answer a narrow terminal by ignoring them.

## How it works

[docs/how-it-works.md](docs/how-it-works.md) — discovery, the attention signals, the
control socket behind renaming, and what happens to a session when it is terminated or
resumed.

## Two things worth knowing

**Run it unsandboxed.** Interactive sessions are discovered through per-process sockets in
`/tmp/cc-socks`. A sandboxed shell can run the CLI but not list that directory, in which
case `claude agents --json` silently degrades to background agents only. clownhead checks
for this and refuses rather than reporting an empty herd.

**It goes where `CLAUDE_CONFIG_DIR` says.** Sessions, transcripts and the registry live
under `~/.claude` unless that variable moves them, and the CLI scopes its listing to
whichever directory it was invoked under — a shell with it set lists that herd, a shell
without it lists the other. clownhead reads the same variable, so the herd is listed and
enriched out of one directory rather than listed from one and enriched from another. Get
that wrong and `QUIET` empties out: the heartbeats are being looked for somewhere those
sessions never wrote one. A board that is watching a relocated directory says so in its
top bar, since a herd listed out of the wrong one looks exactly like a quiet machine;
`clownhead doctor` prints the directory it settled on either way.

## Platform support

macOS with iTerm2 is the developed-against configuration. The discovery layer is portable
(`ps` behaves the same on Linux, yielding `/dev/pts/N`), and terminal support is a small
class per emulator — kitty is wired up, others fall back to the bell. An application with
no class of its own, such as an IDE's embedded terminal, still gets the bell, a marked tab
title and the foreground switch; only the tab tinting and rich notifications are iTerm2's.
Raising a window is macOS-only, because it goes through `open`, and it raises the
application: JetBrains IDEs expose no way to focus one terminal tab out of many, so the
marked title is what tells you which tab was asking. CI runs the suite on Linux and macOS.

## Problems

[Open an issue](https://github.com/rooterkyberian/clownhead/issues/new/choose). The form
asks for `clownhead --version` and `claude --version` up front, since a surprising number
of surprises are a disagreement between the two.

## Development

```bash
mise install
mise run check    # lint + typecheck + test
mise run demo     # re-record docs/demo.gif from docs/demo.tape, which needs vhs
```

## License

MIT
