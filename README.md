# 🤡 clownhead

An overseer for the Claude Code sessions already running on your machine.

If you keep a dozen terminals open across git worktrees, there is no built-in way to see
which one is blocked waiting on you. `claude agents` is a background-agent view; its
`--json` flag is the only listing that includes interactive sessions. clownhead builds a
status board on top of that, paints your terminal tabs to match, and can put you back in
front of whichever session is asking for you.

Run it with no arguments and you land in the overseer:

```
$ clownhead
 🤡  4 sessions · 1 waiting on you                                            ⟳ 5s
 STATUS        NAME                   QUIET  AGE  WHERE                │ you
 input needed  payments-api-7c          12d  12d  ~/dev/payments-api   │ squash them, then run the suite
 busy          index-rebuild-stage-3     0s   1h  web-platform ⇢ searc │
 idle          invoice-parser           32m   1h  web-platform ⇢ invoi │ claude
 idle          web-platform-1d           4d   4d  ~/dev/web-platform   │ Squashed into one migration. The
                                                                       │ suite needs a decision first: two
                                                                       │ fixtures disagree about…
──────────────────────────────────────────────────────────────────────────────────────
 payments-api-7c  input needed
 session 4e020900-df7c-4665-a804-d973b14a1926
 where   /Users/you/dev/payments-api
 process pid 77730 · ttys004 · iterm2
 timing  started 12d ago · quiet 12d
 resume  (cd /Users/you/dev/payments-api && claude --resume 4e020900-df7c-…)

 → history  q quit  f focus  c closed  y copy  r rename  t terminate  R refresh  , settings
```

`QUIET` is time since the session last touched its registry heartbeat — the useful
number. `AGE` is time since the process started. The pane below the table describes
whichever session the cursor is on, with the facts the columns have no room for: the full
id and path, the process and terminal it belongs to, and the command that brings it back.

`→` opens that session's conversation beside the fleet and `←` closes it, which is usually
the fastest way to tell what a session is actually doing. It opens on the newest turn and
takes the arrow keys while it is up, so `↑` and `↓` read back through it rather than moving
the fleet cursor; `←` hands them back. Clicking a row does the same, so
reaching for the mouse reads a session rather than interrupting it. `f` focuses its
terminal: attention, then the window brought to the front. `c` folds in
sessions that have already ended; `y` copies its resume command; `r` renames it; `t` asks
whether to send its process SIGTERM, and can close its tab behind it; `/` filters by name, status, path, or session id;
`,` opens the settings. The board reloads on its own interval, and `R` reloads it now.

Paste a pull request URL into `/` and it stops filtering the board and starts reading it.
Which pull request a session belongs to is in none of the columns — it is in what was
said — so that needle sends clownhead through the transcripts of whatever the board is
showing. Finished work is usually in a session that has ended, so `c` first, then the URL:

```
 🤡  2 of 137 sessions · acme/data-platform#309                       ⟳ 5s
 STATUS  NAME                             WHERE
 idle    invoice-parser        data-platform ⇢ invoice-parser
 closed  design-system:87e26be1  ~/dev/acme/design-system
```

A search of the live fleet alone that comes back empty says so, and says that `c` would
widen it — rather than folding the closed ones in uninvited, which would answer a question
about the board by changing which board you were looking at.

`owner/repo#309` and `repo#309` name the same thing more briefly. A bare `#309` does not:
it means a different pull request in every checkout on the machine, so it stays an
ordinary filter needle rather than answering with all of them at once.

Every reload also tints each session's tab to match its state, so the fleet is readable
from the tab bar of a terminal the board is nowhere near. The board takes a colour of its
own — violet, which no status wears — so the one tab that is not a session says so, and
gives it back when it exits. Turn it all off in the settings and the tabs it tinted are
cleared on the way out.

Every view is also a one-shot subcommand, so the same data pipes into a script.

## Install

```bash
uv tool install git+https://github.com/rooterkyberian/clownhead
```

Requires Python 3.12+ and Claude Code 2.1.227 or newer on `PATH` — the latest release.
clownhead reads what a session publishes about itself and talks to it over the socket it
publishes, both of which move with the CLI, so it is developed against the current version
rather than a floor held open for older ones. `claude --version` reports yours.

## Commands

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

**Discovery.** `claude agents --json` is the source of truth for what is live. Each entry
is enriched with the controlling TTY (from `ps`) and the last heartbeat (from
`sessions/<pid>.json` under the Claude Code config directory, which a crashed session
leaves behind, so entries are only trusted when the CLI still reports the session as live).

**Pull requests.** A session says nothing about which pull request it was for, so the
question is answered from the transcripts, matching both the `repo/pull/309` of a URL and
the `repo#309` of a mention — anchored at both ends, because `pull/309` is otherwise a
substring of `pull/3090` and `data-platform` one of `my-data-platform`. A subagent's
transcript counts as its parent's: a pull request read by a subagent and reported upwards
in the abstract was still worked on there. That is a few hundred megabytes of JSONL across
a fleet, scanned as bytes rather than parsed, and what it finds is remembered until you ask
for a reload — `R` in the overseer reads them again.

**Attention.** Signals are OSC escape sequences written to a session's TTY. The emulator
consumes them before the running application sees them, so they are safe to inject into a
live TUI — unlike plain text, which would land in the session's input stream. iTerm2 gets
`RequestAttention`, tab tinting, and notifications; a terminal with none of that gets the
bell and its tab renamed to `⚠ <session>: <why>`, which is the one signal an IDE's
embedded terminal still honours. Claude Code manages the title itself, so the mark lasts
until the session next changes state — set `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` in that
session to make it stick.

**Foreground.** A dock bounce is easy to miss, so focusing also raises the window. No
portable escape code covers that: iTerm2 has `StealFocus`, and everything else on macOS
is activated with `open`, which brings a running application forward without opening a
window. Elsewhere the raise is a no-op and the bounce stands on its own.

**Whose window, though.** A fleet spans several terminals at once, so the emulator is
resolved per session rather than read out of clownhead's own environment — that only ever
describes the terminal clownhead itself was started from. Walking up the process tree from
the session to the first ancestor running out of an application bundle answers it properly,
which is how a session in an IDE's embedded terminal gets its own window raised instead of
being sent escape codes meant for a terminal it is not in. `clownhead doctor` lists which
applications the current fleet is spread across.

**Termination.** `t` sends SIGTERM, never SIGKILL, and only after a confirmation: Claude
Code writes its transcript as it goes, so a session given the chance to shut down cleanly
leaves a file that can still be resumed. The process id is checked against the process
table first — a session's pid is up to a refresh old, and a process that has exited since
may have had its id handed to something else, which would otherwise be signalled in its
place.

Terminating a session leaves its terminal sitting at a shell prompt, which is a tab to
close by hand for every session ended. The settings can close it instead, off by default
since a closed tab takes its scrollback with it. It waits for the session to actually
exit — a tab closed mid-transcript would take away what makes the session resumable — and
then hangs up the shell the tab was opened with, which is what a terminal does when its
window is closed. Nothing is asked of the emulator, so an IDE's embedded terminal closes
as readily as iTerm2. A session under tmux closes its pane rather than the tab around it,
and one started from inside another session is left alone rather than closing a tab that
is not its own.

**Renaming.** Claude Code names a session after its directory with a couple of hex digits
on the end — `web-platform-1d`, and a second one in the same tree is `web-platform-0b`.
That is no help at all once a fleet is a dozen deep, so `r` renames the session under the
cursor to whatever the job actually is.

The rename goes to the session rather than to the registry record clownhead reads. Every
session listens for control messages on a per-process socket, and the rename performed
there is the one `/rename` performs: the registry record, the transcript, the prompt box
and the terminal title all follow, and the session is told its new name, which it takes as
a hint about what it is working on. Editing the registry file directly would move only the
copy the board reads and leave the session itself answering to the old name.

The socket is the one Claude Code publishes in its own registry record, falling back to
the conventional per-process path when a session binds one without naming it. The session
id travels with the request and Claude Code drops anything addressed elsewhere, so a
socket left behind by a recycled process id refuses the rename instead of applying it to a
stranger — a stronger check than the process-table lookup termination needs. Sessions
older than the control channel have no socket at all, and a session that has already ended
has nothing listening to be told; both say so rather than being renamed somewhere only
clownhead can see.

**Conversation.** The turns shown by `→` are read from the tail of the session's
transcript, never the whole file — they run to megabytes and only the end is ever shown.
Tool calls, their results, thinking and harness-injected turns are dropped, and a run of
turns by one speaker collapses to its last: Claude narrates between tool calls, so an
unfiltered tail is all its own voice and no conversation at all.

Each turn is headed by who said it and how long ago, which is what tells a conversation
that stopped mid-question from one that stopped after an answer. Turns are stacked without
a blank line between them, since that header already parts one from the next and the panel
is a narrow column; `code` and **bold** are rendered rather than left as punctuation. Your
own turns are laid on a background of their own, tinted to the edge of the panel: what you
asked for is what a reader scans back through, and a speaker line alone is a thin thing to
look for in a column of stacked turns.

**Settings.** `,` opens them, changes apply live, and they persist to
`settings.json` under the state directory. They cover the columns the board shows, the refresh
interval, how many turns of history to read, whether closed sessions are in from the
start, whether focusing raises the window, whether a terminated session's tab is closed
after it, and whether tabs are tinted at all — a tinted tab is a passive report rather
than a signal, so a terminal that cannot colour one is
left alone rather than belled about it. The PID and TTY columns are off by default:
they matter when a session needs killing or signalling, not while reading the board, and
every column they take is one the path loses. `clownhead ls --pid --tty` overrides for a
single run without touching what is saved.

**Closed sessions.** `--closed` (or `c` in the overseer) folds in the sessions that have
ended. They come from the transcripts under `~/.claude/projects`, which outlive the
processes that wrote them and are all `claude --resume` needs, plus whatever the session
registry still remembers — it holds the better metadata but is pruned on a clean exit, so
in practice it only contributes the sessions that crashed. Closed rows carry no PID or
TTY: the process is gone and its id may since have been reused by something else.

**Resurrection.** A session is a transcript on disk, not a process — killing the terminal
loses nothing, and `claude --resume <id>` in the original directory brings the conversation
back. `y` in the overseer puts that command for the selected session on the clipboard,
`cd` included.

A worktree session records the worktree itself as its directory, but `--worktree <name>`
from the owning repository is how Claude Code enters one: it attaches to the worktree that
still stands and rebuilds the one that has been pruned. Worktree sessions therefore resume
from the repository, `cd` included, which matters more than it sounds — worktrees get
pruned, and a fifth of the closed sessions on the machine this was built on point at one
that is gone. Any other missing directory keeps its failing `cd` on purpose: resuming a session
somewhere else would hand it a working directory full of the wrong project, and a command
that stops is easier to recover from than one that quietly does the wrong thing.

## Two things worth knowing

**Run it unsandboxed.** Interactive sessions are discovered through per-process sockets in
`/tmp/cc-socks`. A sandboxed shell can run the CLI but not list that directory, in which
case `claude agents --json` silently degrades to background agents only. clownhead checks
for this and refuses rather than reporting an empty fleet.

**It goes where `CLAUDE_CONFIG_DIR` says.** Sessions, transcripts and the registry live
under `~/.claude` unless that variable moves them, and the CLI scopes its listing to
whichever directory it was invoked under — a shell with it set lists that fleet, a shell
without it lists the other. clownhead reads the same variable, so the fleet is listed and
enriched out of one directory rather than listed from one and enriched from another. Get
that wrong and `QUIET` empties out: the heartbeats are being looked for somewhere those
sessions never wrote one. A board that is watching a relocated directory says so in its
top bar, since a fleet listed out of the wrong one looks exactly like a quiet machine;
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
```

## License

MIT
