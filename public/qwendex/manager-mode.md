# Manager Modes

Qwendex behaves like normal Codex in Off mode. Delegation duty is independent
of model reasoning effort: the main session keeps the user's selected model and
reasoning, while the Agent Manager mode determines whether and how bounded
native workers are planned. Changing reasoning does not silently change the
selected delegation mode.

The supported Codex patch supplies native delegation capacity, depth and wait
limits, root-only collaboration management, no recursive child management, and
explicitly read-only child lanes. Qwendex planning, hooks, ledgers, reports,
and validation metadata are advisory: they do not authorize or block prompts,
root tools, publish/release commands, or final responses.

Public modes are ordered:

```text
Off -> Auto -> Lite -> Medium -> Heavy -> Manager Mode
```

`Alt+M` cycles Agent Manager through the duty levels in the patched TUI:

```bash
scripts/qwendex manager mode --toggle --json
```

The selected Agent Manager mode is the default backend `AgentPolicy` source.
`codex-status`, `manager status`, `agent policy`, and native lifecycle hooks
read the same persisted mode, so the visible footer and delegation guidance
move together when `Alt+M` changes modes. Explicit `--agent-use`,
`QWENDEX_AGENT_USE`, or `CODEX_AGENT_USE` selectors still override the selected
mode for that CLI session.

Visible indicators:

```text
{Qwendex} Agent Manager: [Auto] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)
```

`Alt+K` toggles Kaveman output mode:

```bash
scripts/qwendex manager kaveman --toggle --json
```

When Kaveman is `[Y]`, Qwendex writes a terse-output directive into the Codex
status file. The patched Codex TUI reads that directive and appends it to
developer instructions for thread start, resume, and fork flows. This is
lightweight Qwendex state, not a vendored copy of the external Caveman package.

`Alt+L` toggles whether local subagents may be used:

```bash
scripts/qwendex manager local --toggle --json
scripts/qwendex manager local --set off --json
```

When Local is `[Off]`, Qwendex skips local Qwen even if the endpoint is healthy.

## Agent Deploy Policy

`manager_deploy_policy` defaults to `auto`, allowing Qwendex to recommend and
observe useful lanes. Direct work remains valid, and missing, unresolved, or
stale lanes are advisory lifecycle state. Set it to `disabled` to turn off
deployment recommendations; explicit manual lifecycle commands remain
operator-directed.

## Manager Preflight

Every non-Off `qdex` launch may run a Manager preflight before Codex starts.
Preflight is an advisory snapshot and must not prevent Codex from launching
because hooks or Manager identity metadata are absent or stale. It honors the
effective mode selected by command handling, including
`scripts/qwendex manager preflight --mode manager`, rather than falling back to
stored Auto state. The preflight
writes a `manager_decision` ledger record and receipt containing the effective
policy hash, active `CODEX_HOME`, hook status, local/cloud availability, prompt
digest or `interactive_prompt_unknown_prelaunch`, selected route, routing
reason, recommended validation, and lifecycle status. `qdex` clears inherited
per-launch Manager identities before preflight so stale data is not reported as
current. Policy drift is reported for the operator; it does not grant or remove
Codex authority.

Useful dry-run commands:

```bash
scripts/qwendex manager preflight --interactive-prompt-unknown --dry-run --json
scripts/qwendex manager preflight --prompt "..." --json
qdex --manager-preflight-dry-run --json
```

Manager Mode may choose a `manager_subagents` route when a known prompt calls
for bounded lanes. Interactive `qdex` starts before the first prompt is known,
so it records a `direct_single_writer` exception with
`interactive_prompt_unknown_prelaunch`; lifecycle hooks update the same ledger
when prompt and validation evidence are available.

On each root `UserPromptSubmit`, Qwendex attaches the real prompt to a turn
decision under the exported launch ledger (the Codex hook's own `session_id` is
not used as a manager id), recomputes the estimate and team plan, and injects
runtime-id registration templates. The first turn fills the preflight record;
later turns get a fresh ledger id and `agent_task_id` keyed by Codex `turn_id`,
so lifecycle evidence stays turn-scoped. A spawned worker can be associated
with the exact agent id returned by Codex when available; failure to associate
it is an observability gap, not an execution failure.

Prompt observation stores only schema version, source, character length, and
SHA-256 metadata; raw prompt text is not persisted in the manager receipt.
Missing, empty, malformed, or unattached prompt events produce an advisory
diagnostic and never block the prompt. Child lifecycle events do not mutate the
root observation record.

Managed hooks are optional lifecycle-observability wiring. Install and verify
them when those diagnostics are useful:

```bash
scripts/qwendex agent hook-config --install --codex-home "$CODEX_HOME" --json
scripts/qwendex agent hook-config --verify --codex-home "$CODEX_HOME" --json
```

In a generated Qwendex dev environment, these commands target the same runtime
used by Qdex. Missing or partial hooks are reported, but no override variable is
needed because hooks do not gate launch or work.

## Mode Meaning

- `Off`: zero workers; Qdex skips manager preflight and Codex uses explicit-only
  native delegation behavior.
- `Auto`: capacity 4; the deterministic task classifier and Codex judgment
  select bounded delegation when it can save root context or user tokens,
  while small or tightly coupled work stays direct.
- `Lite`: capacity 1; direct work is the default, with at most one bounded
  read-only lookup for an explicit or clearly read-heavy need.
- `Medium`: capacity 2; independent mapping, investigation, or verification
  lanes may be delegated while small or tightly coupled work stays direct.
- `Heavy`: capacity 3; non-trivial edits receive proactive read-only exploration
  and verification guidance.
- `Manager Mode`: capacity 4; the root plans and integrates bounded explorer,
  verifier, and risk-review lanes while remaining the sole default writer.

Legacy compatibility remains: the `manager_only` spelling maps to
`Manager Mode`.

The preflight policy is immutable for the life of the Qdex process. Its
content hash includes the selected Manager level, Kaveman output policy, Local
enabled state, and launch-relevant local routing configuration. A later global
Manager, Kaveman, or Local change affects only a new launch. Existing-session status reports
`policy_drift`, `restart_required`, `policy_hash`,
`desired_global_policy_hash`, and `session_policy_valid`; later turns retain the
original selected mode, Local routing eligibility, and policy hash until Qdex
restarts. Lifecycle planning uses launch-time Local availability and does not
re-probe or reinterpret Local state inside the prompt hook.

Qdex always enables the supported Codex V2 surface and injects the selected
worker cap plus root/worker usage hints. For non-Ultra reasoning it also injects
the Qwendex mode hint. When the final caller override selects Ultra, Qdex omits
only that custom mode hint so Codex's native Ultra proactive policy remains
active. Qwendex retains the immutable capacity, depth and wait limits,
root-only collaboration management, no recursive child management, explicitly
read-only child constraints, and Local routing. Lifecycle association and
duplicate observations remain advisory. The native proactive source
participates in status and preflight hashes so drift can be reported.

## Status Semantics

Manager status separates operator intent and advisory lifecycle health:

- `standby`: Manager Mode is off, not selected by policy, or waiting for an
  operator-selected lane. This is not a failed health state.
- `warning`: Qwendex has advisory issues, such as unresolved or stale lifecycle
  rows or local availability drift. An unstarted suggestion is informational,
  not a health warning.
- `blocked`: reserved for invalid Manager CLI requests or state mutations, not
  for normal Codex prompts, tools, publication, or finalization.

For an attached turn, `manager status --json` also exposes selected and
effective mode, task class, route and routing reason, prompt known/source/length
and observation code, planned profiles, `planned_lane_count`,
`suggested_lane_count`, `suggested_lanes`, `unstarted_suggested_lanes`,
`unresolved_suggested_lanes`, registered/active/terminal counts, validation
counts, why no worker was used, policy source/hash/drift, restart requirement,
native proactive source, waivers, and receipt paths. Suggested lanes are
planning guidance, not completion prerequisites; an unstarted suggestion does
not change health by itself.

Status JSON may expose these labels in manager health data before every wrapper
or footer renders them. Treat the JSON fields as the source of truth and verify
CLI help, smoke tests, and Codex footer receipts before documenting a label as a
visible TUI state.

Local state also has two dimensions:

- `Local Ready`: local subagents are enabled and the configured local model
  alias is visible through the guarded probe.
- `Local Off`: the operator intentionally disabled local subagents; Qwendex
  skips local Qwen even if the endpoint is healthy.
- `Local Unavailable`: local subagents may be enabled, but the probe cannot
  confirm the configured alias, so Qwendex falls back to the primary seat.

`Local: [Ready]` means local intent is on and availability is proven.
`Local: [Unavailable]` means intent is on but availability was not proven, so
routes fall back to primary.

## Deterministic Estimate

`manager estimate` uses only bounded, deterministic CLI rules. It does not call
a model or skill. The JSON explicitly records `kind: deterministic_heuristic`,
`model_invoked: false`, and `skill_invoked: false`, then reports complexity,
risk, likely file scope, validation depth, subagent usefulness, recommended
mode, confidence, and any lane that needs high/xhigh reasoning.

High/xhigh is reserved for specific architecture, security, release, protocol,
credential, or migration lanes. The main session is never escalated by Auto.

## Context Packets

Every assigned lane records a context packet:

- objective
- task class
- allowed scope
- exact files or directories
- needed docs
- stop condition
- expected artifact
- receipt path
- context budget
- model/reasoning assignment
- spawn instruction naming the generic model class and reasoning
- review guidance

Prompt hooks offer the root a Qwendex plan and lifecycle context; the root may
delegate when that would save context or add useful independent evidence. When
Kaveman is enabled, `SessionStart` and `UserPromptSubmit`
additional context also includes the configured Kaveman directive.
`SubagentStart` additional context supplies the generic inherited reasoning
class and bounded assignment from the manager ledger. Hook-facing messages never
name a configured GPT model; eligible low-risk token-saver lanes may still name
`qwen-local` when local Qwen is enabled and usable.

Subagent output is advisory until reviewed and backed by artifacts or tests.
An ordinary worker message is a valid outcome: the native `SubagentStop`
observer marks the lifecycle row terminal and may store the raw output under
`.qwendex/runs/`, write a compact report JSON beside it, and record those
artifact paths on the manager ledger row. Structured `FINAL_REPORT`, `BLOCKED`,
or `FAILED` output is recognized when supplied, but that grammar is optional.
`context compact-plan` and `context pack` carry compact agent outcomes and
artifact links, not full raw transcripts.

After remediation, the root may ask the same verifier for final-state
revalidation when it would add value. Reusing that worker identity updates the
same ledger row rather than consuming a duplicate lane. Qwendex does not
require a verifier retry, prescribe an exact retry count, or make a retry a
condition of the root response; native capacity and wait limits still apply.

## Lifecycle

Default manager settings:

- `max_subagents`: defaults are mode-specific, from 0 to 4. Manager capacity is
  configurable up to the conservative hard ceiling of 8. The effective value
  drives manager registration and the supported Codex V2 worker ceiling; V2
  counts the root separately.
- `stale_after_minutes`: mode-specific, 15 to 45.
- Active subagent limits apply per canonical repository root, so independent
  repositories do not consume each other's lane capacity.
- Close completed agents after findings are integrated.
- Status refreshes reconcile idle read-only agents after the stale window.
- Do not close an active writer record until its changes are integrated or
  stopped; stale writer rows remain advisory in both daily and strict health.

Durable lifecycle commands:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --owner reviewer --write-surface read-only --stop-condition "return findings" --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager status --json
scripts/qwendex manager close --agent-id reviewer-1 --reason integrated --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

The public Agent Management alias layer reads and updates the same ledger:

```bash
scripts/qwendex --agent-use Manager agent policy --json
scripts/qwendex agent status --json
scripts/qwendex agent inspect reviewer-1 --json
scripts/qwendex agent close reviewer-1 --timeout 10s --json
scripts/qwendex agent locks --json
scripts/qwendex agent metrics --json
scripts/qwendex --agent-use Manager agent plan --prompt "Team, update routing and tests" --task-id task-routing --json
scripts/qwendex agent hook Stop --event-json '{"last_assistant_message":"Agent outcomes: ..."}' --json
```

Use `Alt+M`, `scripts/qwendex manager mode ...`, `QWENDEX_AGENT_USE`, or
`CODEX_AGENT_USE` to select `Off`, `Auto`, `Lite`, `Medium`, `Heavy`, or
`Manager` for a CLI session. Explicit selectors override the effective
`AgentPolicy` for normal CLI commands. A non-Off Qdex policy can record an
advisory preflight; an explicit Off selector selects stock delegation behavior.
The resolved `AgentPolicy`, source, and policy hash are
included in `agent`, `manager`, `check`, `doctor`, and `codex-status`
diagnostics. Native lifecycle hooks can record active lanes, validation
evidence, and final summaries, but missing or mismatched records never block a
prompt or final response.

Generic process supervisors can check the same canonical binding without
reading prompts, environment, or ledger contents:

```bash
scripts/qwendex manager launch-status --pid "$PID" --repo-root "$REPO" --json
```

The command reports whether PID/start-ticks, repository, preflight identity,
hooks, and current policy match. Its projection is limited to health booleans,
state labels, a reason code, and a suggested `qdex -C` recovery command; a
mismatch has no authority over the active Codex session.

A direct-work turn may record a routing reason, validation evidence, and dirty
worktree classification when available. Missing lanes, reports, validation, or
summary fields stay visible as advisory lifecycle metadata and never keep the
turn open. Bounded close timeouts preserve or tombstone lifecycle rows for
capacity accounting without delaying the root response.

Qdex also binds native collaboration waits to the immutable `AgentPolicy`.
Manager Mode launches use a 10-second minimum, 30-second default, and
60-second maximum native wait; the product-wide ceiling for non-Off modes is
120 seconds. Qdex appends these V2 settings after caller arguments, so a
per-launch Codex override cannot widen the approved lifecycle budget. Off mode
sets all three native wait values to zero. The supported Codex patch returns
immediately when `wait_agent` finds no running child. After a real wait timeout,
the root inspects `list_agents` once and does not retry unless a child is still
running; terminal evidence is integrated or the turn is finalized instead.

## Recovery And Rollback

When status reports policy drift, restart Qdex when convenient to adopt the
desired mode; do not rewrite the live snapshot. Missing hooks or patch features
reduce Manager observability and should produce repair guidance, not block the
session. Setting the next-launch mode to `Off` selects stock delegation
behavior. Preserve lifecycle receipts when useful for diagnosis.

Qdex sessions launched by this candidate are pinned to one immutable runtime
generation. Building a candidate does not change the active session; atomic
activation changes only the selector used by new processes. Inspect or recover
from a shell or stock Codex session with:

```bash
scripts/qwendex runtime status --json
scripts/qwendex runtime generations --json
scripts/qwendex runtime activate --candidate <generation-id> --json
scripts/qwendex runtime rollback --json
scripts/qwendex runtime prune --safe --json
```

The sync-installed `qwendex-runtime-recovery` copy is the preferred rollback
surface when Qdex or mutable source is broken. Safe prune retains selected,
known-good, and ledger-referenced generations. Manager lifecycle raw/compact
reports remain mutable under the writable operator-local `.qwendex/runs/`
root; hook code and source remain sealed in the selected generation.

Qwendex may record repository and write-surface metadata to help the root avoid
conflicting lanes, but it does not authorize or deny root tools. Explicitly
read-only child lanes remain read-only, and children cannot recursively manage
agents. Codex sandbox/Yolo posture and the native tool surface remain the
execution boundary.

Managed hook wiring is generated by:

```bash
scripts/qwendex agent hook-config --json
scripts/qwendex agent hook-config --write .codex/hooks.json --approve --json
scripts/qwendex agent hook-config --install --codex-home "$CODEX_HOME" --json
scripts/qwendex agent hook-config --verify --codex-home "$CODEX_HOME" --json
```

Writing hook config is explicit and overwrite-protected. `--install` updates
Qwendex-managed entries in place while preserving unrelated handlers;
`--install --force` replaces the complete file. Generated hook commands
use `agent hook ... --codex-hook-output`, which strips the diagnostic Qwendex
envelope and emits only Codex-compatible hook stdout. They also embed the active
Qwendex state DB, ledger DB, receipt root, status file, and root hints; reinstall
managed hooks after moving a dev home. Hooks and ledger associations are
optional observability, so missing entries are reported without an override or
admission gate.

The CLI records `agent_id`, lane, task, owner, write surface, stop condition,
artifacts, context packet, heartbeat time, validation status, stop reason, and
close receipt metadata in the local Qwendex state DB. It does not forcibly
interrupt an external process.

`scripts/qwendex manager repair --safe --json` is the bounded safe repair path
for manager state. It closes stale read-only lanes and harmless empty stale
writer lanes, but leaves writer lanes with artifacts, receipt paths, exact
files, or non-pending validation open. Those lanes return an explicit
`manager close --agent-id ... --reason ... --json` command for operator review.

Validation-debt visibility is separate from repair:

```bash
scripts/qwendex manager reconcile --pending-validation --json
scripts/qwendex manager reconcile --repair --dry-run --json
```

Reconcile classifies sessions as `validated`,
`closed_without_validation_evidence`, `stale_pending_validation`,
`orphaned_session`, or `needs_manual_review`. It does not mark stale historical
sessions validated without evidence.

Status and health use the full ledger for aggregate debt counts while returning
bounded classification samples. Operational active/stale health is scoped to
the current repository. Legacy rows without repository metadata remain visible
as `legacy_unscoped_count`; Qwendex neither assigns them to a project nor marks
them validated during migration.

## High-Value Add

`check`, `doctor`, `manager status`, and `eval` include one or two compact
high-value-add lines, such as:

```text
High-value add: run qwendex eval --live --json before release; local Qwen is available.
High-value add: escalate only the security-review lane to high; main session can stay user-selected.
```

## Pattern Sources

Qwendex keeps Codex CLI subagents and project roles as the runtime base. It
borrows patterns from LangGraph persistence/memory, AutoGen teams/termination,
Anthropic effective agents/contextual retrieval, SWE-agent trajectories,
OpenHands-style eval harnesses, SWE-bench Verified and tau-bench methodology,
MCP security guidance, and Berkeley function-calling/tool-call eval ideas.
