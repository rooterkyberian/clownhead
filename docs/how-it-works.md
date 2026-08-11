# How it works

What clownhead reads, what it writes, and why each of those is the way it is. None of this
is needed to use the board; it is here for anyone wondering how a status board over other
people's processes can work at all, and for whoever has to fix it.

**Discovery.** `claude agents --json` is the source of truth for what is live. Each entry
is enriched with the controlling TTY from `ps` and the last heartbeat from the session
registry under the Claude Code config directory.

Interactive sessions are found through per-process sockets in `/tmp/cc-socks`. A sandboxed
shell can run the CLI but not list that directory, in which case `claude agents --json`
silently degrades to background agents only; clownhead checks for this and refuses rather
than reporting an empty herd.

**Where it looks.** Sessions, transcripts and the registry live under `~/.claude` unless
`CLAUDE_CONFIG_DIR` moves them, and the CLI scopes its listing to whichever directory it
was invoked under. clownhead reads the same variable, so the herd is listed and enriched
out of one directory rather than listed from one and enriched from another. Get that wrong
and `QUIET` empties out: the heartbeats are being looked for somewhere those sessions never
wrote one. A board watching a relocated directory says so in its top bar, since a herd
listed out of the wrong one looks exactly like a quiet machine; `clownhead doctor` prints
the directory it settled on either way.

**Pull requests.** Nothing on a session records which pull request it belonged to, so the
question is answered from the transcripts, matching the `repo/pull/309` of a URL and the
`repo#309` of a mention, subagents included. What it finds is remembered until `^r` reads
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

**Worktrees.** A session records the worktree as its directory, so the `WORKTREE` column is
a string split and a stat — which worktree, and whether it is still on the disk. Everything
else is asked of git, in every repository the fleet is checked out in. Worktrees come from
`git worktree list --porcelain` rather than from the sessions, because the ones worth
finding are exactly the ones no session remembers: a transcript ages out of the config
directory long before the checkout it was written in goes anywhere.

Whether a branch is merged is two questions, since GitHub's default merge is a squash and
the cheap one misses it. First whether the branch tip is an ancestor of the default branch,
which covers a merge and a rebase; otherwise the branch is collapsed onto the point it left
from and `git cherry` asked whether that patch is upstream already. Both are heuristics, so
they decide what is offered and never what goes without being asked.

Age is read from git's own `index` and `HEAD` files rather than the directory holding them.
A directory's timestamp follows every file made or unmade inside it, and git makes
temporary files there merely to answer a question — so reading a worktree's state would
reset the very age the sweep asked for, and nothing would ever look old twice. For the same
reason the dirt check is `--no-optional-locks`, which stops `git status` rewriting the index
it refreshed.

A lock is not always a guard. Claude Code holds one for as long as a session is in the
worktree and drops it on the way out, so a lock outliving its process is what a crash left
behind — that one is cleared and the worktree retired, which is the leak nothing else
reaches. A lock somebody else took names no process, and is left alone.

Removal is `git worktree remove` and never `--force`. Git refuses a worktree with changes
in it, and that refusal is a last guard rather than an obstacle: everything clownhead knows
was read a moment ago, and a moment is long enough for somebody to have started typing.

The branch is a second question, asked separately and answered no by default. A worktree is
a checkout that can be made again from its branch; the branch is where the work is. Deleting
one is `git branch -d`, which refuses anything that is not an ancestor of what it would have
merged into — the same question the merged check asks, and answers better, since a squash
leaves no ancestry to find. `-D` follows only where clownhead's own check says the work is
upstream already, never on git's refusal alone. The checkout goes first either way, because
git will not delete a branch a worktree is on.

Neither command has a key. Both are occasional and destructive, and a letter spent on them
is a letter that can be pressed by accident over whichever row the cursor happened to be
on; the palette is arrived at by typing the name of the thing you went looking for. It
carries the keyed actions as well, since a board whose footer has to truncate needs
somewhere the dropped ones are still findable.
