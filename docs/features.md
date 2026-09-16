# Harness support

clownhead oversees two coding agents, Claude Code and Codex, on one board.
This page is the ledger: every capability the board has, and what each agent can answer for it.

A cell reads `yes`, `partial`, or `no`.
`partial` always says what the limit is.

Verified against Claude Code and `codex-cli 0.153.4`.

## Where the two differ at the root

Claude Code answers `claude agents --json` and keeps state under `~/.claude`.
Codex has no equivalent command.
Its state lives under `~/.codex`, and the only listing that includes live sessions comes from
the app-server daemon over a Unix socket at `~/.codex/app-server-control/app-server-control.sock`.

That socket speaks WebSocket.
A plain `GET /` is answered with silence and newline-delimited JSON-RPC is closed without a reply;
an `Upgrade: websocket` request gets `HTTP/1.1 101 Switching Protocols`,
after which JSON-RPC travels in text frames.
`clownhead.websocket` is the client half of that.

The daemon has to be running, and clownhead starts one when nothing answers the socket.
`codex app-server daemon start` is a no-op against a daemon already running,
and it is given the Codex home clownhead reads,
since the daemon holds that directory for every session it goes on to report.
`codex app-server daemon bootstrap` is the durable form, which keeps one running across logins.
A start that fails is attempted once per clownhead run,
and `clownhead doctor` names the command to try by hand.
A machine with no Codex installed loses the `HARNESS` column too,
since a column reading `claude` on every row answers a question nobody there can ask.

Discovery takes two calls, because no single one covers both halves.
`thread/list` reads the persisted index and omits sessions that are still running:
a session opened seconds earlier, newer than every row returned,
was absent from a `thread/list` of eight while `thread/loaded/list` named it.
So live sessions come from `thread/loaded/list` followed by `thread/read` per id,
and ended ones come from `thread/list`.
This is the same split clownhead already makes for Claude Code,
where the CLI answers for what is live and the transcripts answer for what has ended.

## Capabilities

| Capability | Claude Code | Codex |
|---|---|---|
| List live sessions | `claude agents --json` | yes, `thread/loaded/list` then `thread/read` |
| List ended sessions | registry and transcripts | yes, `thread/list` |
| Status vocabulary | `status`, `waitingFor`, `state` | yes, `ThreadStatus` with `activeFlags` |
| Account usage in the header | local `cachedUsageUtilization`; older readings marked stale | local app-server `account/rateLimits/read`, falling back to rollout rate-limit snapshots |
| Split `busy` from `shell` | registry beat, `discovery.refine` | **no**, the protocol has no such state |
| Working directory | payload `cwd` | yes, `Thread.cwd` |
| Session name | payload `name`, `--name` at launch | **partial**, model-written and null until the session ends |
| PID and TTY | registry `pid` joined to `ps` | **partial**, joined through `lsof` on the working directory |
| Interactive vs background | payload `kind` | **partial**, every thread reads as interactive |
| Trust before making a worktree | `hasTrustDialogAccepted` | yes, `trust_level` in `config.toml` |
| Relocated config directory | `CLAUDE_CONFIG_DIR` | yes, `CODEX_HOME` |
| Resume | `claude --resume <id>` | yes, `codex resume <id>` |
| Fork | `--fork-session` | yes, `codex fork <id>` |
| Switch harness on resume/fork | New conversation with recent context and transcript references | yes, in the same checkout |
| Start a session for a PR or issue | `--worktree`, `--name`, plan mode | yes, native `--enable worktrees --worktree` (Codex 0.154.0+) |
| Send a message to a live session | control socket | yes, `codex queue --thread --message` |
| Rename | control socket | yes, `thread/name/set` |
| Terminate, close the tab | SIGTERM and SIGHUP to the pid | **partial**, needs the pid join above |
| Archive | clownhead's own `archived.json` | yes, the same file |
| History pane | transcript tail | yes, `thread/items/list` |
| Scan transcripts for PR and issue mentions | `projects/*/*.jsonl` | yes, the rollout file `Thread.path` names |
| Worktree cleanup | git, per repository | yes, the same worktrees |
| Paint tabs, bell, notify, foreground | OSC to the session TTY | **partial**, needs the pid join above |
| Detect clownhead running inside a session | argv0 contains `claude` | yes, argv0 contains `codex` |

## Statuses

clownhead keeps one vocabulary and maps each agent onto it.

| clownhead | Claude Code | Codex |
|---|---|---|
| `busy` | `busy` | `active` with no flags |
| `shell` | registry `shell` | no equivalent |
| `idle` | `idle` | `idle` |
| `waiting` | `waitingFor` is set | `active` with `waitingOnUserInput` |
| `blocked` | `blocked` | `active` with `waitingOnApproval` |
| `failed` | `failed` | `systemError` |
| `completed` | `state: completed` | last `TurnStatus` is `completed` |
| `closed` | in the registry, absent from the listing | `notLoaded` |
| `archived` | clownhead state only | clownhead state only |

## What is on disk

| | Claude Code | Codex |
|---|---|---|
| Config directory | `~/.claude`, moved by `CLAUDE_CONFIG_DIR` | `~/.codex`, moved by `CODEX_HOME` |
| Trust | `~/.claude.json` | `~/.codex/config.toml` |
| Transcripts | `<config>/projects/<project>/<id>.jsonl` | `<config>/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl` |
| Live session channel | one socket per process in `/tmp/cc-socks` | one daemon socket in `<config>/app-server-control` |
| Session index | the `sessions` registry directory | `Thread.path` from `thread/list` |
| Worktrees | `<repo>/.claude/worktrees/<name>` | `$CODEX_HOME/worktrees/…` (default `~/.codex/worktrees/…`), plus legacy clownhead checkouts |

A Codex rollout file opens with a `session_meta` record naming the thread id and cwd,
then carries `event_msg` records for turn boundaries and `response_item` records for what was said.

## The three gaps, and why

**No `shell` state for Codex.**
The protocol reports a thread as active without saying whether the model is working
or a background command is.
Codex sessions show `busy` for both, which is what `claude agents --json` does before
`discovery.refine` puts the distinction back from the registry.
Codex publishes no equivalent record to read it out of.

**A pid only where the pairing is unambiguous.**
The app-server names no process, so the join is the working directory:
`ps` says which processes are Codex and `lsof` says where each one is.
A pairing is taken only where one session and one process share a directory.
Three threads and one terminal in one checkout is ordinary once an editor is also running
Codex there, and the directory says nothing about which of the three the terminal holds.
A pid is what terminating and signalling act on, so a wrong one costs somebody else's
session, and no answer is the better one.
Terminate, close-tab and the tab signalling all wait on this.

**Names arrive late.**
`Thread.name` is written by the model and stays null while a session runs,
so a live Codex row falls back to its directory and short id.
Renaming from the board works at any point and sticks.

## What Codex has that clownhead does not use

`turn/interrupt` stops a running turn without touching the process,
and `thread/archive` archives a session inside Codex itself.
Neither has a Claude Code counterpart, and clownhead's own archive already covers both
agents, so the board drives neither.
