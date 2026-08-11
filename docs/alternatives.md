# Alternatives

Survey of existing tools for managing multiple Claude Code sessions, and why clownhead
exists alongside them. Star counts and push dates were collected on 2026-08-10 and will
drift.

## The built-in baseline

Before reaching for anything third-party, note what the CLI already does:

| Capability | Command |
|---|---|
| List interactive sessions | `claude agents --json` |
| Background-agent TUI | `claude agents` |
| Name a session | `claude -n <name>` |
| Worktree per session | `claude -w <name>`, plus `--tmux` |
| Resume | `claude -c`, `claude -r [id]`, `claude --from-pr <n>` |
| Remote access from a phone | `claude --remote-control` |

Two details matter and are easy to miss:

**`claude agents` (the TUI) is background-agents only.** Its help text calls itself
"Manage background agents". Only the `--json` flag includes interactive terminal sessions,
despite the command's own documentation being inconsistent about this. There is no
built-in TUI for the interactive herd — that gap is what clownhead fills.

**`--json` degrades silently under a sandbox.** Peer discovery reads per-process sockets
in `/tmp/cc-socks`. A sandboxed shell can execute the CLI but not list that directory, so
the command returns background agents only and exits zero. clownhead checks for this and
refuses rather than reporting an empty herd.

## Third-party tools

| Tool | ⭐ | What it is | Adopts running sessions? |
|---|---:|---|---|
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 27.7k | Kanban board that spawns an agent per task | ✗ launcher-owned |
| [claudecodeui](https://github.com/siteboon/claudecodeui) | 13.2k | Web/mobile GUI; drive sessions from a phone | ~ transcripts only |
| [claude-squad](https://github.com/smtg-ai/claude-squad) | 8.3k | Go TUI; tmux pane + worktree per agent | ✗ launcher-owned |
| [crystal](https://github.com/stravu/crystal) | 3.1k | Electron parallel-worktree app — **deprecated** | ✗ superseded by nimbalyst |
| [omnara](https://github.com/omnara-ai/omnara) | 2.7k | Self-hosted durable/remote agent infrastructure | ✗ different problem |
| [dmux](https://github.com/standardagents/dmux) | 1.7k | tmux + worktree multiplexer, lifecycle hooks | ✗ launcher-owned |
| [agentapi](https://github.com/coder/agentapi) | 1.5k | HTTP API so other software can drive the CLI | ✗ wraps what it starts |
| [nimbalyst](https://github.com/nimbalyst/nimbalyst) | 1.4k | Crystal's successor; desktop + mobile workspace | ✗ launcher-owned |
| [ccmanager](https://github.com/kbwo/ccmanager) | 1.2k | TUI, no tmux; busy/waiting/idle, worktree ops | ✗ launcher-owned |
| [tmux-claude-session-manager](https://github.com/craftzdog/tmux-claude-session-manager) | 349 | tmux popup; **reads `claude agents --json`** | ✓ if sessions live in tmux |
| [claude-tmux](https://github.com/nielsgroen/claude-tmux) | 201 | tmux popup, live output preview, worktree + PR | ~ tmux-scoped |
| [claude-tmux-status](https://github.com/alexose/claude-tmux-status) | 46 | Live session state in the tmux status bar | ✓ passive |
| [Moshi](https://getmoshi.app/) | closed | iOS/Android SSH/Mosh terminal and agent cockpit | ~ requires tmux |
| [Conductor](https://conductor.build/) | closed | macOS-native parallel worktree app | ✗ launcher-owned |

## Where clownhead differs

**It adopts sessions it did not start.** Almost every tool above is a *launcher*: it owns
the session lifecycle and keeps a private registry, so it cannot see sessions started by
hand in a terminal. Adopting one means restarting everything under it. clownhead reads the
CLI's own state, so it sees whatever is already running.

That dividing line is inversely correlated with popularity. The only third-party tool
built on `claude agents --json` sits at 349 stars; the two largest projects, at 41k stars
combined, solve a different problem entirely.

**It is not a launcher.** clownhead never spawns an agent, creates a worktree, or manages
a multiplexer. If you want parallel-worktree orchestration, use ccmanager or claude-squad —
they are good at it, and clownhead will happily watch the sessions they create.

**Reboot survival is a first-class feature.** A session is a transcript on disk, not a
process; what a reboot destroys is the mapping from session id to directory. Most tools
either keep this in a private database or leave it to tmux, which does not survive a
reboot either.

## Subscription vs API key

Every tool listed above works with a Claude subscription, because they all spawn the real
`claude` binary rather than calling the API directly. Auth lives in the CLI's own OAuth
token store, so any PTY wrapper inherits it. This is not a differentiator.

The real hazard is precedence. **Claude Code prefers `ANTHROPIC_API_KEY` over subscription
OAuth when both are present**, so a stray key in a shell profile silently moves a herd
onto metered billing. Both vibe-kanban (an opt-in `disable_api_key` toggle) and nimbalyst
(stripping the variable at process bootstrap) added defences only after users were burned.

clownhead leaves the environment alone: a session it resumes inherits whatever the shell
it was started from already had, exactly as one started by hand would.

## Worth stealing

[claude-tmux-status](https://github.com/alexose/claude-tmux-status) puts live session
state in the tmux status bar. It is passive, additive, and does not care who started the
session — the same design stance as `clownhead paint`.
