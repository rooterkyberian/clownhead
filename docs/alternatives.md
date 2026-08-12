# Alternatives

Survey of existing tools for managing multiple Claude Code sessions,
and why clownhead exists alongside them.
Star counts were collected on 2026-08-12 and will drift.

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

**`claude agents` (the TUI) is background-agents only.**
Its help text calls itself "Manage background agents".
Only the `--json` flag includes interactive terminal sessions,
despite the command's own documentation being inconsistent about this.
No built-in TUI covers the interactive herd,
which is the gap clownhead fills.

**`--json` degrades silently under a sandbox.**
Peer discovery reads per-process sockets in `/tmp/cc-socks`,
and a shell that can execute the CLI without listing that directory gets background agents back and a zero exit status.
clownhead tests for the condition up front and refuses,
so a sandbox never reads as a quiet machine.

## Third-party tools

| Tool | Stars | What it is | Adopts running sessions? |
|---|---:|---|---|
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban) | 27.7k | Kanban board that spawns an agent per task | ✗ launcher-owned |
| [claudecodeui](https://github.com/siteboon/claudecodeui) | 13.2k | Web/mobile GUI; drive sessions from a phone | ~ transcripts only |
| [claude-squad](https://github.com/smtg-ai/claude-squad) | 8.3k | Go TUI; tmux pane + worktree per agent | ✗ launcher-owned |
| [crystal](https://github.com/stravu/crystal) | 3.1k | Electron parallel-worktree app — **deprecated** | ✗ superseded by nimbalyst |
| [omnara](https://github.com/omnara-ai/omnara) | 2.7k | Self-hosted durable/remote agent infrastructure | ✗ different problem |
| [dmux](https://github.com/standardagents/dmux) | 1.7k | tmux + worktree multiplexer, lifecycle hooks | ✗ launcher-owned |
| [agentapi](https://github.com/coder/agentapi) | 1.5k | HTTP API so other software can drive the CLI | ✗ wraps what it starts |
| [nimbalyst](https://github.com/nimbalyst/nimbalyst) | 1.5k | Crystal's successor; desktop + mobile workspace | ✗ launcher-owned |
| [ccmanager](https://github.com/kbwo/ccmanager) | 1.2k | TUI, no tmux; busy/waiting/idle, worktree ops | ✗ launcher-owned |
| [tmux-claude-session-manager](https://github.com/craftzdog/tmux-claude-session-manager) | 355 | tmux popup; **reads `claude agents --json`** | ✓ if sessions live in tmux |
| [claude-tmux](https://github.com/nielsgroen/claude-tmux) | 202 | tmux popup, live output preview, worktree + PR | ~ tmux-scoped |
| [cctop](https://github.com/st0012/cctop) | 136 | macOS menubar app; one view over Claude Code, Codex, opencode and pi | ~ needs its hooks installed |
| [claude-tmux-status](https://github.com/alexose/claude-tmux-status) | 45 | Live session state in the tmux status bar | ✓ passive |
| [Moshi](https://getmoshi.app/) | closed | iOS/Android SSH/Mosh terminal and agent cockpit | ~ requires tmux |
| [Conductor](https://conductor.build/) | closed | macOS-native parallel worktree app | ✗ launcher-owned |

## Where clownhead differs

**It adopts sessions it did not start.**
Almost every tool above is a *launcher*:
it owns the session lifecycle and keeps a private registry,
so it cannot see sessions started by hand in a terminal.
Adopting one means restarting everything under it.
clownhead reads the CLI's own state,
so it sees whatever is already running.

That dividing line is inversely correlated with popularity.
The only third-party tool built on `claude agents --json` sits at 355 stars;
the two largest projects, at 41k stars combined, solve a different problem entirely.

cctop sits closest to the same stance.
It never launches a session either,
and it covers Codex, opencode and pi alongside Claude Code.
What it reads is a stream of hook events:
install its Claude Code plugin and every `SessionStart`, `PermissionRequest` and `Stop` lands in a local record the menubar app picks up.
That buys detail no polling reaches, subagents and permission prompts included,
and it costs a hook in every session's settings,
so one already running when the plugin arrived stays invisible until it restarts.
clownhead asks the CLI what is live and installs nothing into Claude Code,
which is the same trade taken the other way.

**It is not a launcher.**
clownhead never spawns an agent, creates a worktree, or manages a multiplexer.
If you want parallel-worktree orchestration, use ccmanager or claude-squad;
they are good at it,
and clownhead will happily watch the sessions they create.

**A reboot does not lose the herd.**
A session is a transcript on disk,
and what a reboot destroys is the mapping from session id to directory.
Most tools either keep this in a private database or leave it to tmux,
which does not survive a reboot either.

## Subscription vs API key

Every tool listed above works with a Claude subscription,
because they all spawn the real `claude` binary rather than calling the API directly.
Auth lives in the CLI's own OAuth token store,
so any PTY wrapper inherits it.
This is not a differentiator.

The hazard is precedence.
**Claude Code prefers `ANTHROPIC_API_KEY` over subscription OAuth when both are present**,
so a stray key in a shell profile silently moves a herd onto metered billing.
Both vibe-kanban (an opt-in `disable_api_key` toggle) and nimbalyst (stripping the variable at process bootstrap) added defences only after users were burned.

clownhead leaves the environment alone:
a session it resumes inherits whatever the shell it was started from already had,
exactly as one started by hand would.

## Worth stealing

[claude-tmux-status](https://github.com/alexose/claude-tmux-status) puts live session state in the tmux status bar.
It is passive, additive, and does not care who started the session,
which is the same design stance as `clownhead paint`.
