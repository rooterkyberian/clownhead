# How it works

What clownhead reads, what it writes, and why each of those is the way it is.
None of this is needed to use the board;
it is here for anyone wondering how a status board over other people's processes can work at all,
and for whoever has to fix it.

## Discovery

Two agents answer for the fleet, and each is asked in its own way.
`clownhead.harness` holds one class per agent, concatenates what they return and sorts the result as one board.
An agent that is not installed is left out; one that is installed but cannot be reached right now is skipped, and `clownhead doctor` says why.
[Harness support](features.md) is the ledger of what each can answer.

### Claude Code

`claude agents --json` is the source of truth for what is live.
Each entry is enriched with the controlling TTY from `ps`, and with the last heartbeat and the published status from the session registry under the Claude Code config directory.

Interactive sessions are found through per-process sockets in `/tmp/cc-socks`.
A sandboxed shell can run the CLI but not list that directory,
in which case `claude agents --json` silently degrades to background agents only and still exits zero.
clownhead checks for that case and refuses,
instead of reporting an empty herd.

### Codex

Codex has no equivalent command.
Its app-server daemon holds every session loaded on the machine and answers JSON-RPC on a Unix socket under the Codex home directory.
The socket speaks WebSocket rather than raw bytes: newline-delimited JSON onto it is closed without a reply, and an `Upgrade: websocket` request is answered `101 Switching Protocols`.
`clownhead.websocket` is the client half of that, about eighty lines of stdlib.

Discovery takes two calls.
`thread/list` reads a persisted index that a running session has not been written into yet, so a listing of the newest threads can start below a session opened minutes ago.
Live sessions therefore come from `thread/loaded/list` and one `thread/read` each, and everything that has ended comes from `thread/list`.
That is the same split as Claude Code's, where the CLI answers for what is live and the transcripts answer for what has ended.

The daemon has to be running, and clownhead starts one itself when the socket answers nothing.
The start command is a no-op against a daemon already running, and it carries the Codex home clownhead reads, so the daemon it starts and the sessions it lists agree on a directory.
`codex app-server daemon bootstrap` is the durable form of the same thing, for a machine that should always have one.
A socket file outlives the daemon that made it, so availability here means a connection to it was accepted.
A start that fails is reported on the board and attempted once per run, so a machine with no working daemon spends one process on finding that out.

The app-server names no process, so a pid is found by joining `ps` to `lsof`: which processes are Codex, and which directory each is sitting in.
The pairing is taken only where one session and one process share a directory.
Three threads and one terminal in one checkout is ordinary once an editor is also running Codex there, and the directory cannot say which of the three the terminal holds.
Terminating and signalling act on a pid, so the wrong one costs somebody else's session, and no answer is the better one.

## Statuses

A session publishes one of four states into its registry record: `busy`, `shell`, `idle`, `waiting`.
`busy` is a model turn in flight or a delegated agent working.
`shell` is a turn that has finished with a background command of its own still running.
`waiting` is a prompt or a dialog with nobody answering it.

`claude agents --json` hands out three of the four:
everything that is neither idle nor waiting arrives as `busy`,
which puts a session mid-turn and a session sitting on a half-hour-old background command under one word.
clownhead reads the registry record the listing was built from and puts `shell` back,
and undoes nothing else:
the CLI is what decides a session is live at all, and a record outlives the session that wrote it.

`shell` sorts below `busy` and above `idle`, and colours no terminal tab.
A tab is coloured for what the session is doing on your behalf,
and a session whose turn is over is one you can type into whatever its background command is still doing.

The record dates the status it names,
so `QUIET` is time in the current status rather than the last sign of life.
A record old enough to date only itself answers with that instead,
which was written the moment its status changed and so gives the same answer.

## Where it looks

Sessions, transcripts and the registry live under `~/.claude` unless `CLAUDE_CONFIG_DIR` moves them,
and the CLI scopes its listing to whichever directory it was invoked under.
clownhead reads the same variable,
so the herd is listed and enriched out of one directory.
Get that wrong and `QUIET` empties out and every `shell` reads `busy`:
both come from records being looked for somewhere those sessions never wrote one.
A board watching a relocated directory says so in its top bar,
since a herd listed out of the wrong one looks exactly like a quiet machine.
Codex scopes its threads the same way under `CODEX_HOME`,
so the bar names that directory too,
each one prefixed with the agent it belongs to once the machine has both agents;
`clownhead doctor` prints the directory every agent settled on either way.

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
55 ms over 151 MB of transcripts —
where searching per pull request would read the whole corpus again for each.
That pass is what the pull request board's session counts are,
and what the optional `PRS` column is.

Both cost is why the column is off until asked for,
and both share one cache:
whichever rows the cursor has visited are already read when the column is switched on.
A cell says `?` until the read lands and `-` once it has and the session named nothing,
the same distinction the pull request board draws between a count it has and one it has not looked up.

Transcripts are mapped and read as raw bytes by RE2,
which is what makes a search of the whole corpus something a keystroke can start.
Both readings ask only whether some string appears and where,
and decoding megabytes of tool output to answer that costs far more than the answer is worth.

The scan is anchored on a literal `github.com`.
RE2 looks for a literal the pattern requires and runs its automaton only where that literal lands,
and a pattern that may begin with `https://` or `www.` leaves it nothing to look for:
over 151 MB that is 3910 MB/s anchored against 641 MB/s with the scheme in front.
The captures are identical either way,
because everything read out of a match lives to the right of the host.

It is also the one pattern here that is case-sensitive.
Transcripts hold URLs a browser or `gh` printed, in lower case,
so over the same corpus the flag costs 15 ms in 54 and turns up one spelling more in 324.

## Your pull requests

The one thing clownhead cannot answer from this machine.
`gh search prs` lists what you have open in one request, whatever repository it lives in;
`gh pr list` cannot, since it only ever knows the checkout it was run in.
Then one `gh pr view` per pull request for the review decision, the merge state, the check rollup and whether it is still open.

Archived repositories are excluded from the search.
GitHub spans them by default, and a pull request nobody can merge, review or close
would sit on the board forever as work that cannot be done.

Anything the `gh pr view` finds merged or closed leaves the table.
Search answers from an index that lags the repositories behind it, so it can list a pull request that has already merged;
the per-pull-request call asks the repository itself, which makes its `state` the one to believe.

`r` takes a pull request up in one step.
The board can already do it in two — `enter` points the board at the pull request and `enter` again gets into a session —
and the sheet asks the same two questions as one, where the pull request is what you are looking at.
Both answers are the board's to carry out:
resuming a session and starting one each hand this terminal to `claude`, which only the app itself can do,
and focusing a live one is the signal `f` already sends.
So the sheet's answer leaves with the screen and the board acts on it.

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

The herd is where the search starts rather than where it ends.
Checkouts sit beside each other — one clone lands in `~/dev/acme/widgets` and the next in `~/dev/acme/gadgets`,
because that is what `git clone` names a directory —
so the herd's own repositories say where clones are kept,
and the reference's repository is looked for by that name in each of those places.
The name is only the reason to ask git.
What makes one an answer is `origin` saying it is the repository the reference names,
so a directory that happens to share the name is dropped.
It costs one `git` call, and it is what puts a checkout at the top of the list
for a pull request opened from the web that no session here has ever touched.

A repository Claude Code has never been run in gets no worktree, and the sheet says so on the row and under the command.
Claude Code refuses to make one where its workspace-trust dialog has not been accepted,
and that dialog only comes up once a session is running there,
so the first session in a checkout works in the checkout and every later one gets a worktree.
Which checkouts those are is read from `hasTrustDialogAccepted` in the config directory's `.claude.json`
— `~/.claude.json` for the default directory, since that file predates the directory.
A file that cannot be read means nothing is known rather than nothing is trusted,
and the worktree stays: being wrong that way costs one dialog,
where the other way would drop the worktree from every start on the machine.

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

The question carries `[x] archive session`, ticked.
Terminating a session is usually the moment you are finished with it,
and the alternative is finding it again among the closed ones later to say so.
`a` unticks it for the session you are killing to start again.
The tick applies whether or not the process obeys the signal,
since the archive holds a session id rather than a claim that the process is gone.

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

## Messaging

Telling a session something means finding its terminal and typing there,
which is a window to hunt down and a train of thought to put back together afterwards.
`s` puts the line in its queue from here instead.

The channel is the one the rename goes down,
and the message is the one sessions already send each other:
Claude Code queues it, says it came from somewhere other than that session's own keyboard,
and the session reads it at the end of whatever turn it is on.
An idle one starts a turn on it, and pays for that turn as if you had typed it there.
Whoever is sitting at that terminal watches it arrive.

The framing is what makes the key safe to hand out.
A session told a message came from elsewhere holds on to the work it only takes from you:
renaming, compacting and settings changes are declined however the message asks for them.

A message that opens on a slash command therefore goes by another route.
The text would travel verbatim,
so `/compact` would reach the session as those eight characters
and come back a turn later as the session explaining that the command is yours to type.
Running one means being the keyboard it takes commands from,
which means asking whatever owns the pty to type on the board's behalf.

tmux is asked first, and answers for any pane it owns whatever emulator it is drawn in.
Where no multiplexer stands between them, iTerm2 is asked instead,
and puts the line in the session it draws on that tty.
Both are found by the tty the board already resolved,
because a line typed into the wrong session is a command run in the wrong repository.
A session in a terminal neither of them owns keeps the refusal, which names the emulator.

Typing costs what the socket was giving.
A pane takes the keys whatever the session is doing with them,
so a session sitting on a permission prompt reads the line as the answer to that prompt.
`r` is the rename that needs none of this, over a control message of its own.

The socket says nothing about what became of the message.
A session whose inbound settings drop it looks from here like one that took it,
so what confirms an arrival is the transcript `→` reads,
where the answer shows up like any other turn.

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
whether tabs are tinted at all,
and where `r` puts a session it resumes.

The PID, TTY and WORKTREE columns are off by default:
the first two matter when a session needs killing or signalling, not while reading the board,
and the third only in a repository that uses worktrees at all.
`clownhead ls --columns` overrides for a single run without touching what is saved.

## Closed sessions

`--closed` (or `c` in the overseer) folds in the sessions that have ended,
read from the transcripts under `~/.claude/projects` plus whatever the session registry still remembers.
Closed rows carry no PID or TTY:
the process is gone and its id may since have been reused by something else.

## Archiving

Sessions that have ended pile up faster than they are resumed,
and `a` is how the ones worth coming back to stay at the top.
It archives the selected row,
which sinks it under the closed sessions that have yet to be archived.
Everything else about the session is left alone.
It stays on the board, keeps its transcript, and `enter` still resumes it.
`a` on an archived row takes it back out, and so does activity.
Resuming from the board does it on the spot,
and a session the CLI reports as running leaves the archive the next time the fleet is listed,
whoever resumed it and wherever they resumed it from.
Forking is the exception, since a fork runs under an id of its own
and the session it was copied from is as ended as it was.

The archive is clownhead's own note.
Claude Code publishes no such state:
it is a list of session ids in `archived.json` under the state directory,
read when a session that has ended is discovered and applied to it as the `archived` status.
Holding the id is what lets `t` archive a session while that session is still being signalled,
and what lets the note outlive the registry record it was taken from.

## Resurrection

A session is a transcript on disk,
so killing the terminal loses nothing and `claude --resume <id>` in the original directory brings the conversation back.
Copying that command for the selected session is in the command palette, `cd` included.
`enter` runs it here,
which ends the board you were reading in order to decide.

`r` runs it somewhere else and leaves the board up,
which is the answer when the next thing you want is the row below.
Where that is comes from the settings.
A tmux window, if the board is inside tmux,
and a detached session named after the one being resumed if it is not,
since there is no client here to switch and a session outlives the board that made it.
An iTerm2 tab in the frontmost window.
Or the clipboard, which is the route that needs nothing to be running
and the one it falls back to by default.

When both harnesses are installed, resuming or forking first opens a harness choice.
`h` cycles the installed harnesses, defaulting to the conversation's original one.
`f` toggles resume/fork for an ended session; a live session always forks.
This choice also applies to `enter`, pull-request session selection, and copied commands.
Staying with the original harness uses its native resume/fork command.
Switching starts a new conversation in the recorded working directory, carrying the
last 20 messages (at most 12,000 characters) and paths to at most five of the original transcripts,
since Claude Code answers with one path per subagent a long session delegated to.
A session that carries a name hands it to the new conversation, so the row it lands on is the one you were looking for.
The new harness starts in plan/read-only mode, and the source's archive state stays unchanged.
A missing checkout prevents the handoff rather than moving the work to another directory.

The environment travels with the command in every case.
A tmux server outlives the shells that talk to it and keeps the one it first started with,
so `CLAUDE_CONFIG_DIR` reaches the new pane by being passed to tmux on the command that makes it;
the iTerm2 tab is handed the same shell line the palette copies, assignments and all.

A session still running is offered as a fork.
Its transcript is the file that process is writing,
and a second Claude Code resuming it would be the other writer,
so `--fork-session` goes on the command:
the conversation up to now is copied and carries on under an id of its own,
which is a session more than you had and why the question is asked at all.

Worktree sessions resume from the owning repository with `--worktree <name>`,
which attaches to the worktree that still stands and rebuilds the one that has been pruned.
Any other missing directory keeps its failing `cd` on purpose:
resuming somewhere else would hand the session a working directory full of the wrong project.

## Worktrees

A session records the worktree as its directory,
so the `WORKTREE` column reads the managed layout and checks the directory:
which worktree, and whether it is still on the disk.
Codex external worktrees are resolved back to their owning repository through
`.git` and `commondir`, including sessions inside monorepo subdirectories.
Both the default `~/.codex` and a relocated `CODEX_HOME` are recognized.
Missing external checkouts keep their label, but their owner cannot be inferred
from the opaque path alone; cleanup can still find them from another session
in the owning repository.
A directory under that root holding a `.git` directory of its own is a repository rather than a worktree,
whatever it is filed under, and is left alone.
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
