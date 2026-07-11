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

Agent sessions, active limits, and write locks carry a canonical repository
root. Default status, wait, close, repair, and lock behavior is scoped to that
repository; full-ledger validation debt remains visible separately. Legacy
rows without scope are reported without being silently assigned or validated,
and an active legacy write lock remains conservatively blocking until reviewed.

## Manager Decision Ledger

Normal `qdex` launches in Manager Mode run `scripts/qwendex manager preflight`
before Codex starts. The preflight creates a `manager_decision` record separate
from subagent sessions and honors the effective mode passed by command handling,
including `--mode manager`. It stores the policy hash, hook status, local/cloud
availability, prompt digest or `interactive_prompt_unknown_prelaunch`, selected
route, routing reason, direct-work exception flag, verifier requirement,
validation plan, receipt paths, launcher-derived root ownership id, and STOP
status.

Inspect the latest decision or a specific ledger:

```bash
scripts/qwendex manager preflight --interactive-prompt-unknown --dry-run --json
scripts/qwendex manager decision --json
scripts/qwendex manager decision --agent-id <ledger-id> --json
```

`direct_single_writer` is valid only when a reason, hook status or explicit
override, verifier expectation, validation evidence, and final closeout are
recorded. `manager_subagents` still requires bounded lane evidence and parent
review.

Agent plan assignments expose `spawn_instruction` alongside `assign_command` so
operators can see the model and reasoning to pass when creating the subagent.
High-risk security, release, architecture, and protocol lanes surface `gpt-5.5`
with high or xhigh reasoning. Eligible low-risk bounded artifact-summary lanes
can surface `qwen-local`, low reasoning, and token-saver routing when local
Qwen is enabled and usable.

## Write Safety

The first release uses a conservative single-writer strategy for the base
worktree. Qwendex records write ownership in the local state DB before a
write-capable tool runs. Root Codex sessions and registered subagents use
different identity contracts because Codex intentionally omits top-level
`agent_id` from root lifecycle events.

```bash
scripts/qwendex agent hook PreToolUse --event-json '{"tool_name":"apply_patch","agent_id":"writer-a","profile":"implementer","path":"scripts/qwendex_cli.py"}' --json
scripts/qwendex agent locks --json
```

Rules:

- a read-only profile cannot write source files; managed shell execution is
  fail-closed to the inspection allowlist below
- `qdex` Manager preflight derives and exports a stable root owner from the
  trusted launch ledger; prompt text and `tool_input.agent_id` cannot supply or
  override this identity
- an opaque root write takes the repository-wide `<repo-root>` lease. A root
  lease is scoped to its `tool_use_id` and released by `PostToolUse`; `Stop`
  releases all remaining launch leases. Codex emits `PostToolUse` only after a
  successful tool result, so an aborted tool remains conservatively blocking
  until `Stop`. A later `qdex` launch reclaims an orphan only after the recorded
  launcher PID/process-start identity is no longer live
- a native subagent write must carry Codex's top-level `agent_id`, match an
  active registered runtime id, repository, and current Manager task, and use
  target paths from the hook event or its registered exact-file/write surface.
  An opaque declared scope conservatively resolves to `<repo-root>`
- `create_goal`, `update_goal`, and `update_plan` are control-plane bookkeeping,
  not filesystem writes, and do not acquire file locks
- a scribe can write only under `.qwendex/runs/`
- a second writer is blocked while another agent owns an active write lock in
  the base worktree, even when the requested path differs
- final reports, explicit close, tombstone, stale close, and safe repair release
  that agent's active file locks

The status payload includes `write_safety.strategy`, active locks, and active
writer counts.

For the managed `PreToolUse` hook, read-only profiles may invoke only bare
`pwd`, `ls`, `rg`, `grep`, `cat`, `file`, `head`, `tail`, `stat`, `jq`, `find`,
and `wc` commands, Python's `-V`, `-VV`, or `--version` query, plus `git status`,
`git diff`, `git log`, `git show`, and `git rev-parse`. Git accepts only `-C`
and `--no-pager` before the subcommand. Quote-aware lists and pipelines using
newline, `;`, `&&`, `||`, and `|` are accepted only when every segment passes
the same allowlist.

The read-only shell grammar rejects executable paths, environment assignments,
wrappers, interpreters, scripts, redirects, background jobs, command or
variable substitution, control-flow syntax, comments, brace expansion, and
unquoted glob characters. Quote glob and regular-expression metacharacters when
they are intended as literal arguments. The command-specific denials are
`rg --pre`, `rg --pre-glob`, `rg --hostname-bin`, mutating or command-running
`find` actions (`-delete`, `-exec*`, `-ok*`, `-fls`, `-fprint*`, and
`-fprintf`), and Git `--ext-diff`, `--textconv`, or `--output`. A rejected or
unparseable shell event returns `agent.write_rejected`; it is never treated as
read-only merely because no known mutator was recognized.

Non-shell tool events are also fail-closed for read-only profiles. Qwendex
allows explicit inspection actions such as read, get, list, search, view,
inspect, and status, plus the managed collaboration reporting/wait lifecycle.
Unknown actions and names containing mutating actions such as write, create,
update, delete, upload, apply, execute, or `write_stdin` are rejected. For a
writer profile, those mutating tool names require the same agent identity,
target-path metadata, and repository lock as shell writes.

The same classifier protects writer profiles from undeclared shell side
effects. Only a command proven to be in the inspection allowlist runs without a
write lock. Everything else—including tests, builds, interpreters, `awk`,
network clients, Git mutations, archive tools, `make`, and package managers—is
presumed write-capable. Registered workers resolve target metadata through
`path`, `paths`, `file`, `files`, equivalent `tool_input` fields, or their
declared manager scope. Qwendex does not guess destinations from arbitrary
shell syntax; an opaque root or declared worker scope takes the conservative
repository lease.

This is the Qwendex managed-hook classification boundary. It does not replace
the host sandbox, filesystem permissions, or stock Codex tool filtering, and it
applies only when the managed hook is installed and verified.

## Context Control

`SubagentStop` gates preserve raw child output before marking a worker
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
The repository digest keeps reused task/session ids from sharing an aggregate
index across repositories without exposing the repository path in the artifact
name.

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

- prompt hooks inject the active mode contract, Qwendex model/reasoning policy,
  and the AgentPolicy Kaveman output policy when enabled
- subagent-start hooks inject the worker execution and final-report contract,
  plus the selected model, reasoning, and AgentPolicy Kaveman output policy
- subagent-stop hooks require `FINAL_REPORT`, `BLOCKED`, or `FAILED`
- Manager stop hooks require a preflight decision ledger, block unresolved
  required agents, missing verifier evidence after edits, missing direct-work
  validation evidence, or final messages that omit agent outcomes, validation,
  and risks. Message evidence needs an explicit passing/successful outcome;
  structured command evidence needs a passing status or zero return code, and
  receipt paths must resolve to an existing digest-verified receipt under a
  trusted results root.
- pre-tool hooks deny recursive child `spawn_agent`, writes from read-only
  profiles, conflicting file locks, and release/publish commands without
  explicit release approval. Release approval must already be present as
  `QWENDEX_RELEASE_APPROVED` in the managed hook process environment; command
  text, inline `env`/`export` assignments, and agent-supplied event JSON cannot
  mint that authority. Mutating `gh api` methods and body/field forms are
  approval-gated for every endpoint, including GraphQL and Git refs/tags;
  explicit GET requests remain read-only.

Manager Stop resolves the current `turn_id` below the stable launch ledger and
reads only agent sessions whose per-turn task id and repository scope match that
decision. Historical agents from other tasks or earlier turns cannot satisfy
the gate. Required failed or blocked lanes remain blocking, and verifier
completion requires positive validation evidence plus captured artifacts.

Before a root `UserPromptSubmit`, Manager Mode validates the live launch PID and
start ticks, repository, launch ledger/session, derived root identity, isolated
Codex home, hook trust, and policy hash. Failure blocks the prompt with a
`qdex -C <repo>` recovery command before tools or subagents can run. Stop uses
the same validator. If the launch is untrusted, Stop returns a non-blocking,
non-mutating diagnostic and never searches for a decision by repository alone.

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
receipt root, status file, and root hints. Dynamic Qdex launch identity must
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
generated commands invoke the same native gate
evaluator through raw Codex-compatible stdout; hook files reinforce the runtime
policy but are not the only enforcement path. Manager Mode launches block when
no verified managed hook config is detected. Missing or partial hook configs
are treated as incomplete unless `QWENDEX_MANAGER_ALLOW_UNHOOKED=1` is set;
that override is recorded in the manager decision ledger only when verified
hooks are not already present. Verification also rejects stale Qwendex hook
commands that still use the diagnostic `--json` envelope because Codex merges
all configured hook commands and would execute the incompatible entry.

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
