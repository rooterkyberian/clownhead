# How it works

What clownhead reads, what it writes, and why each of those is the way it is. None of this
is needed to use the board; it is here for anyone wondering how a status board over other
people's processes can work at all, and for whoever has to fix it.

**Discovery.** `claude agents --json` is the source of truth for what is live. Each entry
is enriched with the controlling TTY from `ps` and the last heartbeat from the session
registry under the Claude Code config directory.

**Pull requests.** Nothing on a session records which pull request it belonged to, so the
question is answered from the transcripts, matching the `repo/pull/309` of a URL and the
`repo#309` of a mention, subagents included. What it finds is remembered until `R` reads
them again.

**Attention.** Signals are OSC escape sequences written to a session's TTY, which the
emulator consumes before the running application sees them — safe to inject into a live
TUI. iTerm2 gets `RequestAttention`, tab tinting and notifications; a terminal with none
of that gets the bell and its tab renamed to `⚠ <session>: <why>`. Claude Code manages the
title itself, so the mark lasts until the session next changes state — set
`CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` in that session to make it stick.

**Foreground.** A dock bounce is easy to miss, so focusing also raises the window:
`StealFocus` in iTerm2, `open` for everything else on macOS, a no-op elsewhere. Which
application to raise is resolved per session rather than from clownhead's own environment,
since a herd spans several terminals at once; `clownhead doctor` lists which ones.

**Termination.** `t` sends SIGTERM, never SIGKILL, and only after a confirmation: Claude
Code writes its transcript as it goes, so a session given the chance to shut down cleanly
leaves a file that can still be resumed.

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
That is no help at all once a herd is a dozen deep, so `r` renames the session under the
cursor to whatever the job actually is.

The rename is asked of the session rather than written to the record clownhead reads, so
it is the rename `/rename` performs: registry, transcript, prompt box and terminal title
all follow, and the session is told its new name. Sessions that have ended, and ones older
than Claude Code's control channel, have nothing listening and say so instead.

**Conversation.** The turns shown by `→` are read from the tail of the session's
transcript, never the whole file. Tool calls, their results, thinking and harness-injected
turns are dropped, and a run of turns by one speaker collapses to its last — Claude
narrates between tool calls, so an unfiltered tail is all its own voice and no
conversation at all.

Each turn is headed by who said it and how long ago, which is what tells a conversation
that stopped mid-question from one that stopped after an answer. `code` and **bold** are
rendered rather than left as punctuation, and your own turns are laid on a background of
their own: what you asked for is what a reader scans back through.

**Settings.** `,` opens them, changes apply live, and they persist to `settings.json`
under the state directory. They cover the columns the board shows, the refresh interval,
how many turns of history to read, whether closed sessions are in from the start, whether
focusing raises the window, whether a terminated session's tab is closed after it, and
whether tabs are tinted at all. The PID and TTY columns are off by default — they matter
when a session needs killing or signalling, not while reading the board — and
`clownhead ls --pid --tty` overrides for a single run without touching what is saved.

**Closed sessions.** `--closed` (or `c` in the overseer) folds in the sessions that have
ended, read from the transcripts under `~/.claude/projects` plus whatever the session
registry still remembers. Closed rows carry no PID or TTY: the process is gone and its id
may since have been reused by something else.

**Resurrection.** A session is a transcript on disk, not a process — killing the terminal
loses nothing, and `claude --resume <id>` in the original directory brings the conversation
back. `y` in the overseer puts that command for the selected session on the clipboard,
`cd` included.

Worktree sessions resume from the owning repository with `--worktree <name>`, which
attaches to the worktree that still stands and rebuilds the one that has been pruned. Any
other missing directory keeps its failing `cd` on purpose: resuming somewhere else would
hand the session a working directory full of the wrong project.
