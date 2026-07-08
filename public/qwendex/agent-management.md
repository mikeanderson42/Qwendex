# Agent Management

Qwendex Agent Management makes the selected agent-use mode visible to the CLI
and to child subprocesses. It is backed by the existing Qwendex manager ledger
in the local state DB, not by prompt memory alone.

## Select Mode

By default, the selected Agent Manager mode is the backend `AgentPolicy`
source. In the patched TUI, `Alt+M` runs
`scripts/qwendex manager mode --toggle --json`; the selected mode is persisted
in the local state DB and drives the policy reported by `agent`, `manager`,
`check`, `doctor`, `codex-status`, and native `agent hook` gates.

Use a CLI selector for one command:

```bash
scripts/qwendex --agent-use Manager agent policy --json
```

Or export a session selector:

```bash
export QWENDEX_AGENT_USE=Manager
scripts/qwendex agent status --json
```

`QWENDEX_AGENT_USE` takes precedence over `CODEX_AGENT_USE`. The CLI
`--agent-use` flag takes precedence over both. Valid values are `Off`, `Auto`,
`Lite`, `Medium`, `Heavy`, and `Manager`; common variants such as
`manager-mode` are normalized. Invalid explicit selectors fall back to `Medium`
with a warning unless `QWENDEX_AGENT_USE_STRICT=1`, which blocks the command.
When no explicit selector is present, the selected Agent Manager mode is the
source.

Each CLI invocation computes an effective `AgentPolicy` once, exports these
values to subprocesses, and includes them in diagnostics:

```text
QWENDEX_EFFECTIVE_AGENT_USE
QWENDEX_AGENT_POLICY_HASH
QWENDEX_AGENT_POLICY_SOURCE
```

## Inspect Policy

```bash
scripts/qwendex agent policy --json
scripts/qwendex check --json
scripts/qwendex doctor --json
```

The policy JSON includes the mode, source, hash, thread/depth limits, fork
context policy, verifier requirements, bounded close timeout, and root/child
management tool surfaces.

## Manage Agents

The `agent` command is the public alias layer over the manager ledger:

```bash
scripts/qwendex agent status --json
scripts/qwendex agent list --json
scripts/qwendex agent inspect <agent-id> --json
scripts/qwendex agent logs <agent-id> --json
scripts/qwendex agent wait all --json
scripts/qwendex agent close <agent-id|all> --timeout 10s --json
scripts/qwendex agent tombstone <agent-id> --reason "close timed out" --json
scripts/qwendex agent locks --json
scripts/qwendex agent metrics --json
```

The canonical state source is the SQLite DB configured by `state.db` or
`QWENDEX_STATE_DB`. Existing lifecycle writes still go through
`scripts/qwendex manager assign`, `heartbeat`, `close`, `close-stale`, and
`repair --safe`; `agent` commands read or update the same rows.

## Write Safety

The first release uses a conservative single-writer strategy for the base
worktree. `PreToolUse` write events must include both `agent_id` and target file
paths so Qwendex can record ownership in the local state DB.

```bash
scripts/qwendex agent hook PreToolUse --event-json '{"tool_name":"apply_patch","agent_id":"writer-a","profile":"implementer","path":"scripts/qwendex_cli.py"}' --json
scripts/qwendex agent locks --json
```

Rules:

- a read-only profile cannot write source files
- a scribe can write only under `.qwendex/runs/`
- a second writer is blocked while another agent owns an active write lock in
  the base worktree, even when the requested path differs
- final reports, explicit close, tombstone, stale close, and safe repair release
  that agent's active file locks

The status payload includes `write_safety.strategy`, active locks, and active
writer counts.

## Context Control

`SubagentStop` gates preserve raw child output before marking a worker
terminal. For a ledger-backed agent, Qwendex writes:

```text
.qwendex/runs/<task-or-session>/<agent-id>/raw-output.md
.qwendex/runs/<task-or-session>/<agent-id>/compact-report.json
.qwendex/runs/<task-or-session>/raw-agent-output.md
```

`.qwendex/runs/` is ignored local runtime state. `agent inspect`, `agent logs`,
`manager status`, `context compact-plan`, and `context pack` expose artifact
paths and compact agent outcomes so the root session can preserve evidence
without injecting full worker transcripts into context.

## Native Gates

Qwendex provides a native gate evaluator through the `agent hook` command. It
accepts a hook event name and a JSON event payload:

```bash
scripts/qwendex --agent-use Manager agent hook UserPromptSubmit --event-json '{}' --json
scripts/qwendex agent hook SubagentStart --event-json '{"agent_id":"a1","agent_type":"explorer"}' --json
scripts/qwendex agent hook SubagentStop --event-json '{"agent_id":"a1","last_assistant_message":"FINAL_REPORT\nstatus: completed"}' --json
scripts/qwendex agent hook Stop --event-json '{"last_assistant_message":"Agent outcomes: ...\nValidation: ...\nRisks: ..."}' --json
scripts/qwendex agent hook PreToolUse --event-json '{"tool_name":"spawn_agent","depth":1}' --json
```

Supported events are `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
`SubagentStop`, `Stop`, `PreToolUse`, `PostToolUse`, `PreCompact`, and
`PostCompact`.

The gates enforce the current CLI policy boundary:

- prompt hooks inject the active mode contract
- subagent-start hooks inject the worker execution and final-report contract
- subagent-stop hooks require `FINAL_REPORT`, `BLOCKED`, or `FAILED`
- Manager stop hooks block unresolved required agents, missing verifier
  evidence after edits, or final messages that omit agent outcomes,
  validation, and risks
- pre-tool hooks deny recursive child `spawn_agent`, writes from read-only
  profiles, conflicting file locks, and release/publish commands without
  explicit release approval

Generate Codex-compatible managed hook wiring with:

```bash
scripts/qwendex agent hook-config --json
scripts/qwendex agent hook-config --write .codex/hooks.json --approve --json
```

Writes are approval-gated and refuse to overwrite an existing file unless
`--force` is supplied. The generated commands invoke the same native gate
evaluator; hook files reinforce the runtime policy but are not the only
enforcement path.

## Profiles And Team

Built-in profiles are visible without project files:

```bash
scripts/qwendex agent profiles --json
scripts/qwendex agent team --json
scripts/qwendex --agent-use Manager agent plan --prompt "Team, update routing and tests" --task-id task-routing --json
```

The built-in roster is `explorer`, `implementer`, `verifier`,
`docs_researcher`, `release_manager`, and `scribe`. Child profiles cannot spawn
recursive agents by default. Release manager denies publish/push operations
unless an explicit release gate approves them.

`agent plan` classifies the prompt, applies the effective AgentPolicy, and
returns either a direct-work exception or concrete `manager assign` commands
for the selected profiles. Manager plans include a non-blocking scribe lane.
Release/publish plans select `release_manager` plus `verifier`; Lite plans stay
direct unless the prompt explicitly asks for agents.

`agent metrics` reports ledger counts, required incomplete lanes,
final-contract compliance, raw-output artifact counts, active file locks, and
managed hook/profile counts. It is read-only and does not claim release
acceptance by itself.

## Current Boundary

This CLI release enforces the effective policy at the Qwendex facade, native
gate evaluator, and manager ledger surface. Native Codex tool-registry
filtering and automatic/global hook installation are separate integration
points and must stay labeled as such until a patched Codex build proves them
end to end.
