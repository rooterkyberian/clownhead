# 🤡 clownhead

A status board for the Claude Code sessions already running on your machine:
which are busy, which are idle, and which one is blocked waiting on you.

![A herd of Claude Code sessions, one waiting on you](docs/demo.gif)

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install git+https://github.com/rooterkyberian/clownhead
```

### Requirements

- Claude Code 2.1.227 or newer on `PATH`.
- macOS or Linux.
  Developed on macOS with iTerm2;
  CI runs the suite on both.
- Sessions are found whatever terminal they run in.
  Tab colours are iTerm2's alone,
  kitty gets notifications,
  and everything else (an IDE's embedded terminal included) falls back to the bell and a tab renamed to `⚠ <session>: <why>`.
  Raising a window is macOS-only.

## Keys

`QUIET` is time since the session last beat,
`AGE` is time since its process started,
and the pane below the table carries the id, path, process and terminal the columns cannot fit.

- `→` (or a click on the row) opens that session's conversation beside the board,
  usually the fastest way to tell what it is actually doing.
  `↑` and `↓` scroll it,
  `←` closes it.
- `enter` gets you into that session:
  a live one has its terminal focused,
  and one that has ended is resumed here, which ends the board.
- `f` focuses its terminal:
  attention, then the window brought to the front.
- `/` filters by name, status, path, or session id —
  or by pull request or issue, below.
- `n` starts a new session for the pull request or issue being filtered on.
- `c` folds in the sessions that have already ended.
  The count in the top bar is that same switch,
  and clicking it works too.
- `y` copies its resume command.
- `r` renames it.
- `t` asks whether to send its process SIGTERM, and can close its tab behind it.
- `,` opens the settings.
- `^p` opens the command palette.
- `q` quits.

## Tab colours

**iTerm2 only.**
Every reload tints each session's tab to match its state,
so the herd is readable from the tab bar of a terminal the board is nowhere near.
Turn it off in the settings and the tabs it tinted are cleared on the way out.

## Pull requests and issues

Nothing on a session records which pull request or ticket it belongs to,
so no column can show one.
Paste a pull request or issue URL into `/` and clownhead reads the transcripts of whatever the board is showing.
Finished work is usually in a session that has ended,
so `c` first, then the URL:

```
 🤡  2 of 137 sessions · acme/data-platform#309                        ⟳ 5s
 STATUS  NAME                    WHERE
 idle    invoice-parser          data-platform ⇢ invoice-parser
 closed  design-system:87e26be1  ~/dev/acme/design-system
```

A search of the live herd alone that comes back empty says so,
and says that `c` would widen it,
instead of folding the closed ones in uninvited.

`owner/repo#309` and `repo#309` name the same thing more briefly.
A bare `#309` does not:
it means a different pull request in every checkout on the machine,
so it stays an ordinary filter needle.
A bare `PLAT-4471` is refused too:
the pattern that matches it also matches `UTF-8` and `SHA-256`,
so Jira is named by its URL.

## Starting one

Every ticket starts with the same three steps:
find the checkout, make a worktree, tell a fresh session what to work on.
`clownhead <url>` does all three.

```bash
$ clownhead https://github.com/acme/data-platform/issues/2
```

The board opens filtered to that issue with the ended sessions already folded in,
which is where the session that worked on it last usually is.
`enter` gets you back into whichever one you pick.
`n` starts a new one instead:

```
┌────────────────────────────────────────────────────────────┐
│ Start a session for acme/data-platform#2                   │
│ in ~/dev/acme/data-platform                                │
│ (cd ~/dev/acme/data-platform && claude                     │
│  --worktree issue-2-rate-limit-the-scan-queue              │
│  --name issue-2-rate-limit-the-scan-queue <url>)           │
│ enter to start · y to copy · esc to cancel                 │
└────────────────────────────────────────────────────────────┘
```

The worktree is named for the issue and its title, which `gh` is asked for
and which is simply left off when it cannot answer.
The checkout is whichever one `origin` says is the right repository;
a Jira URL names no repository at all,
so those fall back to the ones the herd is already checked out in, best guess first,
and the list is there to be argued with either way.
The new session's first prompt is the URL you passed.

`enter` and `n` both end the board and hand this terminal to `claude`,
since becoming the session is the point.
`y` copies the command instead, for a session that belongs in another window.

## Commands

Every view is also a one-shot subcommand,
so the same data pipes into a script.

| Command | What it does |
|---|---|
| `clownhead` | The interactive overseer. Same as `clownhead tui`. |
| `clownhead open <ref>` | The board filtered to a pull request or issue, ended sessions included, ready to start one for it. What a bare `clownhead <url>` runs. Takes a GitHub pull request or issue URL, a Jira URL, or `owner/repo#123`. `--print` writes the sessions and the start command out instead of opening the board. |
| `clownhead ls` | Status board, attention-first. `--cwd` scopes to one tree, `--all` adds background agents, `--closed` adds sessions that have ended, `--pr` keeps only the ones whose transcript names a pull request, `--columns` picks the columns and their order. |
| `clownhead worktrees-cleanup` | Retire the worktrees Claude Code left behind. `--older-than` sets how long untouched is long enough (default `7d`), `--merged` keeps to the ones already in the default branch, `--branches` deletes those branches too, `--dry-run` shows what would go, `--yes` skips the question. |
| `clownhead paint` | Colour each session's tab to match its state, for a board you would rather not keep open. `--follow` keeps them in sync, `--reset` clears them. |
| `clownhead focus [name]` | Bounce the dock, raise the terminal, and notify. With no argument, takes every session that is waiting on you. `--no-foreground` leaves your windows where they are. |
| `clownhead doctor` | Check discovery, terminal capabilities, and auth. |
| `clownhead --version` | The installed version, which a problem report asks for. |

`--columns` names what `ls` shows and the order to show it in:
`status`, `name`, `quiet`, `age`, `pid`, `tty`, `worktree`, `where`, `resume`.
Everything but `pid`, `tty` and `worktree` is on by default,
`resume` included.
A listing you are reading in order to get back into something should hand you the command that does it:

```bash
$ clownhead ls --pr acme/payments-api#309 --closed --columns name,resume
acme/payments-api#309 · 2 of 74 sessions
NAME             RESUME
payments-api-7c  (cd /Users/you/dev/payments-api && claude --resume 4e020900-df7c-4665-a804-d973b14a1926)
index-rebuild    (cd /Users/you/dev/web-platform && claude --resume 8b1c4f22-0d31-4f0a-9c2e-3a7b1e5d6f08 --worktree search-index)
```

A resume command is the longest thing on the board and the one truncation ruins,
so naming fewer columns is how you get one whole;
`--columns name,resume` is the pair worth remembering.

## How it works

[docs/how-it-works.md](docs/how-it-works.md):
discovery, the attention signals,
the control socket behind renaming,
and what happens to a session when it is terminated or resumed.

## Problems

[Open an issue](https://github.com/rooterkyberian/clownhead/issues/new/choose).
The form asks for `clownhead --version` and `claude --version` up front,
since a surprising number of surprises are a disagreement between the two.

## Development

```bash
mise install
mise run check    # lint + typecheck + test
mise run demo     # re-record docs/demo.gif from docs/demo.tape, which needs vhs
```
