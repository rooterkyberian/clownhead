# cctop

[cctop](https://github.com/st0012/cctop) is a macOS menubar app over AI coding sessions,
and the nearest neighbour clownhead has.
Both watch sessions they did not start,
and neither one launches an agent.
[Alternatives](alternatives.md) places it among the rest of the field.
This page is the feature-by-feature reading behind that placement,
taken from the source at v0.21.1 (`312bfa6`, 2026-08-11),
against clownhead 0.7.0.

## The difference everything else follows from

cctop is fed by hooks.
Its Claude Code plugin installs twelve of them,[^hooks]
covering `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `Stop`, `Notification`, `PermissionRequest`, `SubagentStart`, `SubagentStop`, `PreCompact` and `SessionEnd`.
Each fires a small native helper, `cctop-hook`, which rewrites one JSON file under `~/.cctop/sessions/`.
The menubar app watches that directory and renders whatever it finds.

clownhead asks `claude agents --json` what is live,
enriches it from the process table and the session registry,
and reads the transcripts for anything the CLI does not carry.

That single choice sets most of what follows.
cctop knows things no poll reaches: which tool is running this second, the prompt that opened the turn, a permission prompt at the moment it appears, how many subagents the session owns.
It pays for that with an install in every session's configuration,
and with a blind spot for any session already running when the plugin arrived.
clownhead sees a session the moment the CLI does, with nothing installed,
and is held to what the CLI and the transcripts will say.

## What each one knows about a session

| | cctop | clownhead |
|---|---|---|
| Status | `idle`, `working`, `compacting`, `waiting_permission`, `waiting_input`, `needs_attention`[^status] | `busy`, `idle`, `waiting`, `blocked`, `failed`, `completed`, `closed` |
| Why it is waiting | the harness's own notification text | `waitingFor`, as the CLI reports it |
| Running tool | `last_tool` plus a detail line, live on the card | the conversation pane, opened with `→` |
| Last prompt | stored on the record, shown truncated | the conversation pane |
| Git branch | recorded at every hook | absent |
| Worktree | the recorded project path | `WORKTREE` column, plus whether it is still on disk |
| Subagents | counted and listed on the parent session | `--all` lists background agents as rows of their own |
| Age and quiet | `started_at` and `last_activity` | `AGE` and `QUIET` columns |
| Process | `pid` and its start time, to catch pid reuse | `PID` and `TTY` columns |
| Terminal | program, bundle id, tty, remote-control socket, multiplexer pane id | resolved from the process tree at read time |
| Identity across resume | a cctop-owned UUID mapped to the harness reference | the Claude Code session id |
| Ended sessions | archived to `~/.cctop/history/`, then the live record is deleted[^lifecycle] | read back out of `~/.claude/projects` under `--closed` |

Every cctop field above has its own reference page.[^session-files]
Two rows are worth pulling out.

Branch is free for cctop and expensive for clownhead:
a hook already runs in the session's own directory,
whereas a board polling the whole herd would have to shell out to git once per session, every refresh.

Subagents are the reverse.
cctop counts them because `SubagentStart` tells it to.
clownhead cannot count the subagents inside a session,
though `--all` does surface the background agents the CLI reports as sessions in their own right.

## The board itself

| cctop | clownhead |
|---|---|
| Menubar icon summarising the whole herd, with a slim pill for notched displays | every session's own tab tinted to match its state, via `paint` |
| Draggable panel, position remembered, double-click snaps back | a full-screen TUI |
| Four themes (Claude, Tokyo Night, Gruvbox, Nord), light and dark each, plus a system/light/dark switch | the terminal's own colours |
| Three tabs: Sessions, Recent, Cleanup | one board, plus a conversation pane, a command palette and a settings screen |
| Cards ordered attention-first | rows ordered attention-first |
| Source badge per card | Claude Code only, so nothing to badge |
| Right-click to hide a session, with a confirmation and no way back | `/` filters the board down |

## Getting back into a session

This is where cctop has invested most, and where the gap is widest.[^jump]

| Host | cctop | clownhead |
|---|---|---|
| iTerm2 | selects the exact tab, via macOS Automation | writes `StealFocus` to the session's own tty |
| Ghostty, Apple Terminal | selects the tab by tty (1.3.0+ for Ghostty) | raises the application, marks the tab |
| Kitty | targets the window through its remote-control socket | raises the application, marks the tab |
| tmux, Zellij, cmux, Herdr | selects the pane, from ids recorded at session start | raises the application, marks the tab |
| VS Code, Cursor, Windsurf, Zed | opens the project, preferring a workspace file | not a target |
| Claude Desktop, Codex Desktop, Warp | activates the app, or opens the thread where the host allows it | not a target |
| Anything else | opens the project folder in Finder | raises the application, marks the tab |

Both fall back the same way when the exact target is out of reach.
clownhead's fallback is a renamed tab (`⚠ <session>: <why>`) and a bell,
which is findable by eye once the window is up;
cctop's is app activation.

cctop also has a keyboard route into that list.[^navigate]
A global hotkey (`ctrl-cmd-N` by default) overlays numbered badges on every card,
and `1` to `9` jumps to the matching session.
The numbers are frozen when the overlay opens,
so a session that disappears mid-navigation leaves its slot dead instead of shifting a different conversation into it.

## Worktrees

Both find worktrees that outlived their session and offer to retire them.
They disagree about where the list comes from.

cctop starts from session records: an ended session whose recorded path still exists on disk.
clownhead starts from git, in every repository the herd is checked out in,
on the argument that the worktrees worth finding are exactly the ones no session remembers,
since a transcript ages out of the config directory long before the checkout does.

On the evidence gathered per candidate, cctop is ahead:[^cleanup] [^git-inspector]

| Check | cctop | clownhead |
|---|---|---|
| Uncommitted tracked changes | yes | yes, via `git status --no-optional-locks` |
| Untracked files | yes, reported as their own reason | yes, folded into the same dirty answer |
| Ignored files | yes, reported as their own reason | invisible to a plain `--porcelain` status, so unchecked |
| Commits that are nowhere else | `@{u}..HEAD`, and a branch with no upstream is itself a review reason | counted against every remote ref, and a repository with no remote is not asked |
| Already merged | not attempted | ancestry, then `git cherry` for squash merges |
| Locked worktree | yes | yes, and a lock whose process died is cleared and the worktree retired |
| Initialised submodules | yes | no |
| Tracked files hidden by index flags (sparse checkout) | yes | no |
| Unknown or detached branch | flagged | no |
| Size on disk | measured and shown | no |
| Unreadable status | flagged as its own state | treated as a guard |
| Verdict | `clean`, `review` or `ignored`, with reasons attached | removable, or the one reason it is being kept |

cctop re-runs its checks immediately before removing anything and refuses if the answers changed.
clownhead leans on git refusing `git worktree remove` without `--force`,
which covers the same window from the other side.
Branch deletion is the one place clownhead goes further:
it will use `-D` only where its own squash-merge check says the work is already upstream,
never on git's refusal alone.

## Being told, rather than looking

cctop posts a macOS notification when a session moves into a state that wants you.[^notifications]
The notifications are keyed to its permanent session id, so a session cannot be notified about twice,
they are withdrawn when the session stops waiting,
and clicking one jumps straight to the session.

clownhead can raise attention on demand,
from `clownhead focus` or `f` on the board,
and can keep tab colours in sync in the background with `clownhead paint --follow`.
Both need somebody to be looking, or to have asked.
Nothing watches for the transition itself and reports it.

This is the widest functional gap on the page,
and the one that costs least to close:
the follow loop already walks the herd on an interval and already holds each session's previous state.

## Everything else cctop ships

Its README covers the rest of the surface.[^readme]

- Four harnesses: Claude Code and Claude Desktop, Codex CLI and Desktop, opencode, pi. Each has its own plugin or hook shim.
- A Stream Deck plugin, fed by `~/.cctop/display-state.json`, which publishes an ordered list of session id, name, status and colour so a physical key can carry a session.
- Settings that detect whether each integration is actually installed, install it, and say when a session restart is needed to pick it up.
- A `cctop-setup` skill that fires inside Claude Code when `cctop-hook` cannot be found.
- Sparkle for signed updates, and launch at login.
- Session state as plain JSON in a documented, inspectable schema, with no telemetry and no upload.

## Worth taking

Ranked by what each would add to clownhead against what it would cost.

1. Signalling a session the moment it starts waiting. This is what clownhead's own pitch promises and only half delivers: you have to be looking at the board, or run `focus` yourself. `paint --follow` already loops over the herd on an interval and already holds each session's previous state, so what is missing is the diff and a call into `attention.focus`. Highest value, lowest cost.
2. Number keys as jump targets. `1` to `9` selecting a row directly, with `enter` or `f` then acting on it. The board is ordered attention-first, so the digits already mean something. Cheap, and it retires the arrow-key walk down a long herd.
3. `ls --json`. clownhead advertises that every view pipes into a script, then emits a Rich table that truncates. cctop had to publish a whole second file, `display-state.json`, to serve the same need. A `--json` flag on `ls` is an afternoon, and it makes every integration after it somebody else's problem.
4. Muting a session. cctop's hide, with the sharp edges filed off: clownhead owns no session record to be irreversible about, so a muted set in `settings.json` plus a way to see it again is a friendlier version of the same idea. Earns its place on the session that sits blocked for a day.
5. Cleanup evidence. Size on disk, initialised submodules, and ignored files counted apart from untracked ones. Each is a small addition to `guard_for` and the cleanup table, and the first is what answers "is this worth reclaiming".
6. A column for what the session is doing. clownhead can reach the running tool from the transcript tail it already reads for the conversation pane, at the cost of a read per session where a hook gets it free.

## Left alone

- Other harnesses. clownhead is built on `claude agents --json`. Codex and opencode would mean a second discovery mechanism, and a second copy of every assumption underneath it.
- Hook-based capture. The detail is real and so is the price: an entry in every session's configuration, and a blind spot for anything already running. clownhead took that trade the other way on purpose, and [Alternatives](alternatives.md) says so.
- Themes, the draggable panel, the menubar icon. A TUI inherits the terminal's colours and the terminal's window management.
- Stream Deck. `ls --json` covers this and everything shaped like it.
- A cctop-style session identity. Claude Code's session id already survives resume, which is the property cctop built its own id to get.
- Editors as focus targets. Every clownhead session is a process on a tty, which is what all of its signalling needs.

## References

Every link is pinned to `312bfa6`, the commit this page was read at.
cctop moves quickly, so anything checked against `main` may already have changed.
Claims about clownhead come from this repository, and are covered by
[How it works](how-it-works.md).

[^hooks]: [`plugins/cctop/hooks/hooks.json`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/plugins/cctop/hooks/hooks.json), the Claude Code hook set the plugin installs
[^status]: [`SessionStatus.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Models/SessionStatus.swift) and [`HookEvent.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Models/HookEvent.swift), which map each hook onto a status
[^session-files]: [Session Files](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/docs/session-files.md), cctop's field-by-field reference, alongside [`SessionData.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Models/SessionData.swift)
[^lifecycle]: [Session Lifecycle](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/docs/session-lifecycle.md), covering archival to Recent Projects and the removal of the live record
[^jump]: [Jump Support](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/README.md#jump-support), with the multiplexer payloads in [Terminal Focus Metadata](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/docs/session-files.md#terminal-focus-metadata)
[^navigate]: [`NavigateController.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Services/NavigateController.swift) for the frozen slots, and [`AppSettings.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Models/AppSettings.swift) for the default shortcut
[^cleanup]: [`WorktreeCleanupCandidate.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Models/WorktreeCleanupCandidate.swift), which enumerates every review reason and the three verdicts
[^git-inspector]: [`GitWorktreeInspector.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Services/GitWorktreeInspector.swift), which runs the git checks behind them
[^notifications]: [`SessionManager+Notifications.swift`](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/menubar/CctopMenubar/Services/SessionManager%2BNotifications.swift)
[^readme]: [cctop README](https://github.com/st0012/cctop/blob/312bfa6e7b76f5de15ec806acd8b4f5c0e18149c/README.md)
