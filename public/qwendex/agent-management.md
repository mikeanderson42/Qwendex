# Agent Management

Qwendex Agent Management makes the selected agent-use mode visible to the CLI
and to child subprocesses. It is backed by the existing Qwendex manager ledger
in the local state DB, not by prompt memory alone.

## Select Mode

By default, the selected Agent Manager mode is the backend `AgentPolicy`
source. In the patched TUI, `Alt+M` runs
`scripts/qwendex manager mode --toggle --json`; the selected mode is persisted
in the local state DB and drives the policy reported by `agent`, `manager`,
`check`, `doctor`, `codex-status`, and native lifecycle hooks.

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
context policy, advisory verification guidance, bounded close timeout, and
root/child management tool surfaces.

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

Agent sessions, active limits, and write locks carry a canonical repository
root. Default status, wait, close, repair, and lock behavior is scoped to that
repository; full-ledger validation debt remains visible separately. Legacy
rows without scope are reported without being silently assigned or validated,
and active legacy write metadata remains visible until reviewed.

## Manager Decision Ledger

Normal non-Off `qdex` launches run `scripts/qwendex manager preflight` before
Codex starts. The preflight creates a `manager_decision` record separate
from subagent sessions and honors the effective mode passed by command handling,
including `--mode manager`. It stores the policy hash, hook status, local/cloud
availability, prompt digest or `interactive_prompt_unknown_prelaunch`, selected
route, routing reason, direct-work exception flag, verifier suggestion,
validation plan, receipt paths, launcher-derived root ownership id, and
lifecycle result.

The launch record also carries a stable idempotency key plus state-DB,
ledger-DB, Codex-home, runtime, PID, and process-start identities. Repeating
preflight for the same not-yet-admitted Qdex process reuses that launch record;
it does not create a newest-row fallback that later hooks must guess.

Inspect the latest decision or a specific ledger:

```bash
scripts/qwendex manager preflight --interactive-prompt-unknown --dry-run --json
scripts/qwendex manager decision --json
scripts/qwendex manager decision --agent-id <ledger-id> --json
```

`direct_single_writer` and `manager_subagents` are planning labels. Reasons,
hook status, validation evidence, and final closeout can be recorded when
available, but none of that metadata authorizes or blocks root work.

Agent plan assignments expose `spawn_instruction` alongside `assign_command` so
operators can see the generic model class and reasoning level to pass when
creating the subagent. Managed hook messages intentionally do not name a
configured GPT model. Eligible low-risk bounded artifact-summary lanes can
surface `qwen-local`, low reasoning, and token-saver routing when local Qwen is
enabled and usable.

## Execution Boundary

Codex remains the execution authority. Qwendex does not authorize or deny root
tools, shell commands, file writes, or release/publish commands, and Manager
metadata is not a second sandbox or approval layer. The selected Codex
sandbox/Yolo posture, native tool permissions, explicit user intent,
credentials, and host controls remain authoritative.

Qwendex may record repository, write-surface, and lifecycle metadata to help the
root avoid conflicting work. Those records are advisory and cannot block a root
tool. Explicitly read-only child lanes remain read-only, and the native tool
surface prevents children from recursively managing agents. Capacity, depth,
and wait limits continue to bound delegation.

## Context Control

`SubagentStop` observers may preserve raw child output before marking a worker
terminal. For a ledger-backed agent, Qwendex writes:

```text
.qwendex/runs/repo-<scope-digest>-<task-or-session>/<agent-id>/raw-output.md
.qwendex/runs/repo-<scope-digest>-<task-or-session>/<agent-id>/compact-report.json
.qwendex/runs/repo-<scope-digest>-<task-or-session>/raw-agent-output.md
```

`.qwendex/runs/` is ignored local runtime state. `agent inspect`, `agent logs`,
`manager status`, `context compact-plan`, and `context pack` expose artifact
paths and compact agent outcomes so the root session can preserve evidence
without injecting full worker transcripts into context.
For an immutable Qdex generation, the hook implementation remains in the
read-only generation tree while Qdex binds this artifact directory to the
writable operator root. A report-capture failure is reported but does not block
the worker or root response.
The repository digest keeps reused task/session ids from sharing an aggregate
index across repositories without exposing the repository path in the artifact
name.

## Native Lifecycle Hooks

Qwendex provides a native lifecycle evaluator through the `agent hook` command. It
accepts a hook event name and a JSON event payload:

```bash
scripts/qwendex --agent-use Manager agent hook UserPromptSubmit --event-json '{}' --json
scripts/qwendex agent hook SubagentStart --event-json '{"agent_id":"a1","agent_type":"explorer"}' --json
scripts/qwendex agent hook SubagentStop --event-json '{"agent_id":"a1","last_assistant_message":"Repository map complete; no changes made."}' --json
scripts/qwendex agent hook Stop --event-json '{"last_assistant_message":"Agent outcomes: ...\nValidation: ...\nRisks: ..."}' --json
scripts/qwendex agent hook PreToolUse --event-json '{"tool_name":"spawn_agent","depth":1}' --json
```

Supported events are `SessionStart`, `UserPromptSubmit`, `SubagentStart`,
`SubagentStop`, `Stop`, `PreToolUse`, `PostToolUse`, `PreCompact`, and
`PostCompact`.

When `performance.capture` is `metadata`, an observed native hook event also
writes privacy-minimized exploration metadata to the separate local performance
database. Telemetry never changes the hook outcome, and storage failure remains
non-blocking.
The adapter keeps only safe class/count/timing fields and local HMAC digests;
it discards prompts, commands, paths, raw queries, tool input/output, and
transcripts. Generated managed hooks carry the performance-database location
alongside the fixed state/ledger paths. `SessionStart` can be ingested when a
trusted integration emits it, but generated wiring does not invent a startup
probe, so an unavailable startup duration is reported as `not_observed`.

The hooks provide advisory delegation context and lifecycle observability:

- prompt hooks inject the active mode, planner, lifecycle, and AgentPolicy
  Kaveman output contracts when enabled; they never name a configured GPT model
- subagent-start hooks inject a bounded assignment, generic inherited reasoning,
  explicitly read-only child constraints, and the AgentPolicy Kaveman output
  policy; children cannot recursively manage agents
- subagent-stop hooks record ordinary outcomes and understand structured
  `FINAL_REPORT`, `BLOCKED`, or `FAILED` output when supplied, but do not require
  that grammar
- Manager stop hooks record unresolved lanes, validation evidence, and summary
  metadata without delaying or rejecting the root final response
- pre-tool hooks may observe lifecycle and repository metadata, but Qwendex does
  not authorize or deny root tools or release/publish commands.

Manager lifecycle association remains turn- and repository-scoped so historical
rows do not appear current. Missing or mismatched launch identity, hooks, lane
reports, or validation is an observability gap and never blocks a prompt, root
tool, publish action, or final response.

The canonical resolver reports `status`, an exact reason code, candidate count,
and sanitized match details. It associates a decision only when one matching
record exists. A continuation can begin without a prompt hook; later lifecycle
events may fill the association. Ambiguity stays diagnostic rather than becoming
an execution gate.

The effective AgentPolicy exported by preflight is pinned for that live launch.
Its content hash covers Agent Manager mode, Kaveman output policy, Local enabled
state, and launch-relevant local routing configuration. Changing any of those
updates the next launch but does not change the policy used by hooks in an
already-running Qdex process. Hooks report snapshot drift when observed. Status
reports the desired global hash, `policy_drift`,
`restart_required`, and `session_policy_valid`; later turns keep the recorded
launch policy and launch-time Local availability until restart.

`manager launch-status --pid <pid> --repo-root <path> --json` exposes a stable,
read-only health projection for generic supervisors. It does not return
prompts, environment values, credentials, ledger identifiers, or raw decision
records.

`agent policy --json`, `agent plan --json`, `manager status --json`,
`codex-status --json`, and `manager preflight --json` all expose the same
`output_policy` object. When Kaveman is enabled, that policy requires terse
output, carries the configured directive, changes the policy hash, and exports
`QWENDEX_OUTPUT_POLICY=kaveman` plus the Kaveman directive for managed workflow
launches.

Manual `agent hook ... --json` commands return the stable Qwendex diagnostic
envelope. Managed Codex hook config uses `agent hook ... --codex-hook-output`
so hook stdout contains only the raw Codex event schema accepted by the Codex
hook parser. Generated hook commands carry fixed Qwendex state DB, ledger DB,
performance DB, receipt root, status file, and root hints. Dynamic Qdex launch identity must
still be inherited from the live process; fixed state paths cannot substitute
for that identity.

Generate Codex-compatible managed hook wiring with:

```bash
scripts/qwendex agent hook-config --json
scripts/qwendex agent hook-config --write .codex/hooks.json --approve --json
scripts/qwendex agent hook-config --install --codex-home "$CODEX_HOME" --json
scripts/qwendex agent hook-config --verify --codex-home "$CODEX_HOME" --json
```

Explicit `--write` operations are approval-gated and refuse to overwrite an
existing file unless `--force` is supplied. `--install` is an idempotent managed
upgrade: it replaces Qwendex lifecycle handlers while preserving unrelated hook
entries. `--install --force` replaces the complete hook file and is intended for
operator-approved recovery from an unparseable or discarded config. The
generated commands invoke the lifecycle evaluator through raw Codex-compatible
stdout. Missing or partial hook configs reduce observability and produce repair
guidance; no override variable is required because hooks do not gate launch or
work. Verification still rejects stale Qwendex hook
commands that still use the diagnostic `--json` envelope because Codex merges
all configured hook commands and would execute the incompatible entry.

## Profiles And Team

Built-in profiles are visible without project files:

```bash
scripts/qwendex agent profiles --json
scripts/qwendex agent team --json
scripts/qwendex --agent-use Manager agent plan --prompt "Team, update routing and tests" --task-id task-routing --json
```

The built-in roster remains available for manual lifecycle commands, but the
deterministic turn planner uses bounded read-only `explorer`, `verifier`,
`reviewer`, and `docs_researcher` lanes. Child profiles cannot spawn recursive
agents. The root is the sole default writer and integrator.

`agent plan` classifies the prompt without a model call, applies the effective
AgentPolicy, and returns either direct-work guidance or planned lane IDs and
concrete `manager assign` commands. Heavy recommends explorer and verifier
support; Manager also recommends risk review for security, release, protocol,
and live-acceptance tasks. Lite stays direct unless one
bounded lookup is explicitly or materially useful. Exact native task paths can
associate Codex worker IDs with planned lanes, and duplicate observations are
reported without consuming a second ledger slot.

`agent metrics` reports ledger counts,
`attention_flagged_incomplete_count`, `structured_outcome_observed_count`,
`structured_outcome_observation_rate`, raw-output artifact counts, active file
locks, and managed hook/profile counts. Structured outcomes are optional, so
their observation rate is telemetry rather than compliance. The command is
read-only and does not claim release acceptance by itself.

## Current Boundary

This release applies delegation guidance through the Qwendex facade, manager
ledger, Qdex V2 launch configuration, and supported patched Codex runtime. The
patch provides exact SubagentStart task/parent identity, removes
management tools from child V2 threads, and permits V2 to ignore a downstream
legacy `agents.max_threads` setting while retaining Qwendex's V2 cap. Stock
Codex lacks those exact Qwendex patch guarantees. Managed hook installation
remains explicit and operator-controlled rather than global or automatic.
