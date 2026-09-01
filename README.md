# 🤡 clownhead

[![PyPI](https://img.shields.io/pypi/v/clownhead)](https://pypi.org/project/clownhead/)

A status board for the Claude Code sessions already running on your machine:
which are busy, which are idle, and which one is blocked waiting on you.

![A herd of Claude Code sessions, one waiting on you](https://github.com/rooterkyberian/clownhead/raw/main/docs/demo.gif)

## Install

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv tool install clownhead
```

`uv tool install git+https://github.com/rooterkyberian/clownhead` gets you whatever is on `main`,
which is ahead of the last release.

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
  Typing a slash command into a session takes tmux or iTerm2;
  messages reach a session in any of them.
- Sessions in a JetBrains IDE get their terminal tab selected as well,
  which takes an Accessibility grant for whichever application clownhead is running in:
  System Settings, Privacy & Security, Accessibility.
  Without it the window still comes up and the board says why the tab did not.

## Keys

`QUIET` is how long a live session has been in the status it is in,
`AGE` is time since its process started,
and the pane below the table carries the id, path, process and terminal the columns cannot fit.

`STATUS` reads `busy` while a turn is in flight and `shell` once the turn is over with a background command still running.
A row that has sat on `shell` for half an hour is usually a command nobody is waiting for any more.

- `→` (or a click on the row) opens that session's conversation beside the board,
  usually the fastest way to tell what it is actually doing.
  `↑` and `↓` scroll it,
  `←` closes it.
- `enter` gets you into that session:
  a live one has its terminal focused,
  and one that has ended is resumed here, which ends the board.
- `f` focuses its terminal:
  attention, then the window brought to the front,
  and in a JetBrains IDE the session's own tab selected once it is.
- `o` opens the pull request the session was working on,
  asking which when it named more than one.
- `/` filters by name, status, path, or session id —
  or by pull request or issue, below.
- `p` opens the pull requests you have open on GitHub, below.
- `n` starts a new session for the pull request or issue being filtered on.
- `s` sends the session a message:
  it joins the queue and is read at the end of whatever turn that session is on,
  and the answer turns up in the conversation `→` opens.
  A slash command takes the other door,
  typed into the session by tmux or by iTerm2,
  since a session runs what its own keyboard sends it
  and declines what a message asks of it.
  A session in a terminal neither of those owns says so instead of typing.
- `c` folds in the sessions that have already ended.
  The count in the top bar is that same switch,
  and clicking it works too.
- `r` resumes a session that has ended, leaving the board up:
  a tmux window, an iTerm2 tab, or the clipboard, whichever the settings say.
  On a session still running it asks whether to fork it,
  which copies the conversation so far into a session with an id of its own
  and leaves the live one alone.
- `R` renames it.
- `t` asks whether to send its process SIGTERM, and can close its tab behind it.
- `,` opens the settings.
- `^p` opens the command palette,
  which carries every key above by name and the three things with no key of their own:
  copying a session's resume command,
  retiring the worktree it worked in,
  and clearing out every worktree whose work has already merged.
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
Jira is named by its URL.

The pane below the table names the pull requests the selected session worked on,
freshest first,
which is the same reading done backwards —
and `o` opens one in the browser.

A `PRS` column puts the freshest one on every row instead.
Turn it on in the settings, or ask `ls` for it:

```bash
$ clownhead ls --closed --columns status,name,prs,where
STATUS  NAME                  PRS                       WHERE
busy    settlement-retries    payments-api#309 +2       ~/dev/acme/payments-api
idle    invoice-parser        data-platform#362         data-platform ⇢ invoice-parser
idle    index-rebuild         web-platf…#88 +14         web-platform ⇢ search-index
closed  design-system:87e26be1  -                       ~/dev/acme/design-system
```

It is the one column that costs a pass over the disk,
which is why it is off until asked for —
55 ms for 151 MB of transcripts,
read once and kept until `^r`.
A row says `?` until that read lands and `-` once it has and the session named nothing;
the repository gives way before the number does when the column is too narrow,
since the number is the only part you read a pull request by.

## Your pull requests

`p` asks GitHub what you have open,
and the transcripts which sessions here worked on each:

```
 49 open · reading status 31/49… · 6 with sessions here
 PR                                TITLE                              CHECKS  REVIEW    SESSIONS  UPDATED
 acme/payments-api#309             feature: settlement retries        ✗ 2     changes          1       3h
 acme/data-platform#362            chore: migrate httpx to httpx2     ✓       approved         2       1d
 acme/web-platform#88              spike: search index rebuild        ⟳ 1     required         0       4d
```

What wants you leads:
something red or objected to first,
then what is approved and green and waiting on somebody to press merge,
then what is still out for review.
Drafts sink below all of it.

`enter` leaves the list and points the board at the sessions that worked on that pull request,
ended ones folded in,
which is the same place pasting its URL into `/` arrives at.
`o` opens it on GitHub and `y` copies its URL.

This is the one view that asks somebody else,
so it needs `gh` and says which of no `gh`, no auth and no network it ran into
rather than showing an empty table.
The three answers arrive separately —
the list in one request, the statuses one apiece, the sessions from a pass over every transcript —
and the table fills in as each lands.

## Starting one

Every ticket starts with the same three steps:
find the checkout, make a worktree, tell a fresh session what to work on.
`clownhead <url>` does all three.

```bash
$ clownhead https://github.com/acme/data-platform/issues/2
```

The board opens filtered to that issue, the ended sessions already folded in.
`enter` gets you back into whichever one you pick;
`n` starts a new one in plan mode, in a worktree named after the issue.

## Commands

Every view is also a one-shot subcommand,
so the same data pipes into a script.

| Command | What it does |
|---|---|
| `clownhead` | The interactive overseer. Same as `clownhead tui`. |
| `clownhead open <ref>` | The board filtered to a pull request or issue, ended sessions included, ready to start one for it. What a bare `clownhead <url>` runs. Takes a GitHub pull request or issue URL, a Jira URL, or `owner/repo#123`. `--print` writes the sessions and the start command out instead of opening the board. |
| `clownhead prs` | What you have open on GitHub, and which sessions here worked on each. `--author` asks about somebody else, `--limit` caps how many to ask for, `--no-sessions` skips the transcript pass. Needs `gh`. |
| `clownhead ls` | Status board, attention-first. `--cwd` scopes to one tree, `--all` adds background agents, `--closed` adds sessions that have ended, `--pr` keeps only the ones whose transcript names a pull request, `--columns` picks the columns and their order. |
| `clownhead worktrees-cleanup` | Retire the worktrees Claude Code left behind. `--older-than` sets how long untouched is long enough (default `7d`), `--merged` keeps to the ones already in the default branch, `--branches` deletes those branches too, `--dry-run` shows what would go, `--yes` skips the question. |
| `clownhead paint` | Colour each session's tab to match its state, for a board you would rather not keep open. `--follow` keeps them in sync, `--reset` clears them. |
| `clownhead focus [name]` | Bounce the dock, raise the terminal, and notify. With no argument, takes every session that is waiting on you. `--no-foreground` leaves your windows where they are. |
| `clownhead doctor` | Check discovery, terminal capabilities, and auth. |
| `clownhead --version` | The installed version, which a problem report asks for. |

`--columns` names what `ls` shows and the order to show it in:
`status`, `name`, `quiet`, `age`, `pid`, `tty`, `worktree`, `prs`, `where`, `resume`.
Everything but `pid`, `tty`, `worktree` and `prs` is on by default,
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

[How it works](https://rooterkyberian.github.io/clownhead/how-it-works/):
discovery, the attention signals,
the control socket behind renaming,
and what happens to a session when it is terminated or resumed.
[Alternatives](https://rooterkyberian.github.io/clownhead/alternatives/)
surveys the other tools for managing a herd, and where this one differs.

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
