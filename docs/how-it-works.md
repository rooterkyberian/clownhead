# How it works

What clownhead reads, what it writes, and why each of those is the way it is.
None of this is needed to use the board;
it is here for anyone wondering how a status board over other people's processes can work at all,
and for whoever has to fix it.

## Discovery

`claude agents --json` is the source of truth for what is live.
Each entry is enriched with the controlling TTY from `ps` and the last heartbeat from the session registry under the Claude Code config directory.

Interactive sessions are found through per-process sockets in `/tmp/cc-socks`.
A sandboxed shell can run the CLI but not list that directory,
in which case `claude agents --json` silently degrades to background agents only and still exits zero.
clownhead checks for that case and refuses,
instead of reporting an empty herd.

## Where it looks

Sessions, transcripts and the registry live under `~/.claude` unless `CLAUDE_CONFIG_DIR` moves them,
and the CLI scopes its listing to whichever directory it was invoked under.
clownhead reads the same variable,
so the herd is listed and enriched out of one directory.
Get that wrong and `QUIET` empties out:
the heartbeats are being looked for somewhere those sessions never wrote one.
A board watching a relocated directory says so in its top bar,
since a herd listed out of the wrong one looks exactly like a quiet machine;
`clownhead doctor` prints the directory it settled on either way.

## Pull requests

Nothing on a session records which pull request it belonged to,
so the question is answered from the transcripts,
matching the `repo/pull/309` of a URL and the `repo#309` of a mention,
subagents included.
What it finds is remembered until `^r` reads them again.

The same transcripts answer the question backwards.
Which pull requests did *this* session name?
That reads URLs alone, where a search for a named pull request also takes the `repo#309` shorthand:
anchored on a repository somebody named, the shorthand cannot mean anything else,
but asked of a whole transcript it reads `PLAT-4471#3` and half the diffs that ever scrolled past.
The most recently named comes first,
so a session that opened on one pull request and spent the afternoon on its follow-up belongs to the follow-up.

Asked of the session under the cursor it is one transcript and a few milliseconds,
which is what the detail pane spends and why it is kept until `^r`.
Asked of the fleet it is one pass that answers for every pull request at once —
around a fifth of a second over a few hundred megabytes —
where searching per pull request would read the whole corpus again for each.
That pass is what the pull request board's session counts are,
and what the optional `PRS` column is.

Both cost is why the column is off until asked for,
and both share one cache:
whichever rows the cursor has visited are already read when the column is switched on.
A cell says `?` until the read lands and `-` once it has and the session named nothing,
the same distinction the pull request board draws between a count it has and one it has not looked up.

The scan is anchored on a literal `github.com`,
and is the one pattern here that is case-sensitive.
Both are the same decision:
a pattern that may begin with `https://` has no first byte to look for,
so the engine tries at every offset instead of jumping between occurrences.
Measured over 138 MB that is 34 MB/s against 1580 MB/s,
for a flag that matches nothing extra —
transcripts hold URLs a browser or `gh` printed, in lower case.

## Your pull requests

The one thing clownhead cannot answer from this machine.
`gh search prs` lists what you have open in one request, whatever repository it lives in;
`gh pr list` cannot, since it only ever knows the checkout it was run in.
Then one `gh pr view` per pull request for the review decision, the merge state and the check rollup.

Failing to ask is kept apart from having nothing open.
No `gh`, no auth, no network and a `gh` that never answered all say which,
because a board that showed the same empty table for each would be lying most of the time.
A pull request whose *status* would not load is softer: the row stays with its status blank,
which is honest, rather than taking the board down with it.

The three answers arrive on their own schedules and the table is redrawn as each does.
Nothing waits for anything else,
because the list is one request, the statuses are a request apiece over several seconds,
and the transcripts are local and usually beat GitHub.

## Issues

The same search, over the `repo/issues/2` of a URL and the Jira key of a browse URL.
Both are read out of URLs alone.
A bare `#309` means a different pull request in every checkout on the machine,
and the pattern that would match a bare `PLAT-4471` also matches `UTF-8`, `SHA-256` and `ISO-8601`.
Either would turn a filter into a search of every transcript on the machine
that came back empty,
which reads as nothing having worked on the ticket.

GitHub numbers issues and pull requests together and writes both as `repo#2`.
That spelling stays a pull request,
and a search for either finds mentions of the other;
nothing in the text can separate them.

## Starting a session

A session for a reference is
`claude --permission-mode plan --worktree <name> --name <name> <url>` run in the repository.
That is the same `--worktree` that rebuilds a pruned worktree on resume,
so Claude Code makes the checkout and clownhead asks git for nothing.

Plan mode is where it starts, because the prompt is a URL and nothing else.
The session has to go and read the ticket before there is anything to agree to,
and what comes back is a plan for work nobody has scoped yet.
Resuming imposes no mode, since a session that has been running already has one.

The name is the reference plus as much of its title as fits,
cut on a word boundary and reduced to what a directory and a branch will both take,
since it becomes each of those.
The title is `gh`'s answer.
Every way of failing to get one (no `gh`, no auth, no network, a Jira key) leaves the name as `issue-2`,
which is worth having on its own.

Which checkout is ranked and offered.
A repository whose `origin` is the reference's own leads,
then the ones holding a session that named it,
then every repository the herd is checked out in.
A Jira URL only ever has the second of those,
and a repository mirrored twice satisfies the first,
so the choice stays with whoever is reading the list.

`enter` and `n` both end the board and hand the terminal to `claude`.
The board puts the command down and whoever launched it runs it,
since a process replaced while a screen is still up would leave the shell wearing a terminal in raw mode.

## Attention

Signals are OSC escape sequences written to a session's TTY,
which the emulator consumes before the running application sees them,
so they are safe to inject into a live TUI.
iTerm2 gets `RequestAttention`, tab tinting and notifications;
a terminal with none of that gets the bell and its tab renamed to `⚠ <session>: <why>`.
Claude Code manages the title itself,
so the mark lasts until the session next changes state.
Set `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1` in that session to make it stick.

## Foreground

A dock bounce is easy to miss,
so focusing also raises the window:
`StealFocus` in iTerm2, `open` for everything else on macOS, a no-op elsewhere.
Which application to raise is resolved per session,
since a herd spans several terminals at once;
`clownhead doctor` lists which ones.

## IDE tabs

A JetBrains IDE keeps every terminal in one window,
so raising the application leaves a session sitting behind whichever tab was last looked at.
Selecting the right one goes through the macOS accessibility API,
where each tab is a static-text element named with the title clownhead marked it with,
carrying the rectangle it is drawn in.
That title is written again just before the lookup,
since a session repaints its own as it works.

The rectangle is what the click needs, and a click is what the strip requires.
Tabs there carry no press action;
`AXSelected` and `AXFocused` are reported settable and then ignored;
and System Events' own `click at` resolves a point to an element and presses it,
which for an element with nothing to press does nothing.
So the click is posted where a hardware click enters, through CoreGraphics,
and the pointer is warped back where it was found.

Reading the tree and posting the click both need the Accessibility grant
held by whichever application clownhead is running in,
and a focus that could not reach a tab says why on the board.
A tool window nobody can see leaves no tabs in the tree at all,
so a miss is worth one press of the stripe button that shows it,
and the press is undone when the tab still is not there.
Focusing everything that is waiting at once selects one tab per IDE,
since a window can only show one.

## Termination

`t` sends SIGTERM, never SIGKILL, and only after a confirmation:
Claude Code writes its transcript as it goes,
so a session given the chance to shut down cleanly leaves a file that can still be resumed.

Terminating a session leaves its terminal sitting at a shell prompt,
which is a tab to close by hand for every session ended.
The settings can close it instead,
off by default since a closed tab takes its scrollback with it.
It waits for the session to actually exit,
because a tab closed mid-transcript would take away what makes the session resumable.
Then it hangs up the shell the tab was opened with,
which is what a terminal does when its window is closed.
Nothing is asked of the emulator,
so an IDE's embedded terminal closes as readily as iTerm2.
A session under tmux closes its pane instead of the tab around it,
and one started from inside another session is left alone,
since the tab it would close is not its own.

## Renaming

Claude Code names a session after its directory with a couple of hex digits on the end:
`web-platform-1d`, and a second one in the same tree is `web-platform-0b`.
That is no help at all once a herd is a dozen deep,
so `r` renames the session under the cursor to whatever the job actually is.

The rename is asked of the session rather than written to the record clownhead reads,
so it is the rename `/rename` performs:
registry, transcript, prompt box and terminal title all follow,
and the session is told its new name.
Sessions that have ended, and ones older than Claude Code's control channel, have nothing listening and say so instead.

## Conversation

The turns shown by `→` are read from the tail of the session's transcript.
Tool calls, their results, thinking and harness-injected turns are dropped,
and a run of turns by one speaker collapses to its last;
Claude narrates between tool calls,
so an unfiltered tail is all its own voice and no conversation at all.

Each turn is headed by who said it and how long ago,
which is what tells a conversation that stopped mid-question from one that stopped after an answer.
`code` and **bold** are rendered,
and your own turns are laid on a background of their own:
what you asked for is what a reader scans back through.

## Settings

`,` opens them,
changes apply live,
and they persist to `settings.json` under the state directory.
They cover the columns the board shows,
the refresh interval,
how many turns of history to read,
whether closed sessions are in from the start,
whether focusing raises the window,
whether a terminated session's tab is closed after it,
and whether tabs are tinted at all.

The PID, TTY and WORKTREE columns are off by default:
the first two matter when a session needs killing or signalling, not while reading the board,
and the third only in a repository that uses worktrees at all.
`clownhead ls --columns` overrides for a single run without touching what is saved.

## Closed sessions

`--closed` (or `c` in the overseer) folds in the sessions that have ended,
read from the transcripts under `~/.claude/projects` plus whatever the session registry still remembers.
Closed rows carry no PID or TTY:
the process is gone and its id may since have been reused by something else.

## Resurrection

A session is a transcript on disk,
so killing the terminal loses nothing and `claude --resume <id>` in the original directory brings the conversation back.
`y` in the overseer puts that command for the selected session on the clipboard,
`cd` included.
`enter` runs it here,
which ends the board you were reading in order to decide.

Worktree sessions resume from the owning repository with `--worktree <name>`,
which attaches to the worktree that still stands and rebuilds the one that has been pruned.
Any other missing directory keeps its failing `cd` on purpose:
resuming somewhere else would hand the session a working directory full of the wrong project.

## Worktrees

A session records the worktree as its directory,
so the `WORKTREE` column is a string split and a stat:
which worktree, and whether it is still on the disk.
Everything else is asked of git,
in every repository the fleet is checked out in.
Worktrees come from `git worktree list --porcelain` rather than from the sessions,
because the ones worth finding are exactly the ones no session remembers:
a transcript ages out of the config directory long before the checkout it was written in goes anywhere.

Whether a branch is merged is two questions,
since GitHub's default merge is a squash and the cheap one misses it.
First whether the branch tip is an ancestor of the default branch,
which covers a merge and a rebase;
otherwise the branch is collapsed onto the point it left from and `git cherry` asked whether that patch is upstream already.
Both are heuristics,
so they decide what is offered and never what goes without being asked.

Age is read from git's own `index` and `HEAD` files rather than the directory holding them.
A directory's timestamp follows every file made or unmade inside it,
and git makes temporary files there merely to answer a question.
Reading a worktree's state would reset the very age the sweep asked for,
and nothing would ever look old twice.
For the same reason the dirt check is `--no-optional-locks`,
which stops `git status` rewriting the index it refreshed.

Claude Code locks a worktree for as long as a session is in it and unlocks it on the way out,
so a lock outliving its process is what a crash left behind.
That one is cleared and the worktree retired,
which is the leak nothing else reaches.
A lock somebody else took names no process,
and is left alone.

Removal is `git worktree remove` and never `--force`.
Git refuses a worktree with changes in it,
and clownhead treats that refusal as a last guard:
everything it knows was read a moment ago,
and a moment is long enough for somebody to have started typing.

The branch is a second question,
asked separately and answered no by default.
A worktree is a checkout that can be made again from its branch;
the branch is where the work is.
Deleting one is `git branch -d`,
which refuses anything that is not an ancestor of what it would have merged into,
the same question the merged check asks and answers better,
since a squash leaves no ancestry to find.
`-D` follows only where clownhead's own check says the work is upstream already,
never on git's refusal alone.
The checkout goes first either way,
because git will not delete a branch a worktree is on.

Neither command has a key.
Both are occasional and destructive,
and a letter spent on them is a letter that can be pressed by accident over whichever row the cursor happened to be on;
the palette is arrived at by typing the name of the thing you went looking for.
It carries the keyed actions as well,
since a board whose footer has to truncate needs somewhere the dropped ones are still findable.
