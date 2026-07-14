# Manager Modes

Qwendex behaves like normal Codex in Off mode. Delegation duty is independent
of model reasoning effort: the main session keeps the user's selected model and
reasoning, while the Agent Manager mode determines whether and how bounded
native workers are planned. Changing reasoning does not silently change the
selected delegation mode.

Enforcement boundary: deterministic planning and status inspection are
available from the Qwendex CLI, but exact native parent/worker identity,
root-only collaboration management, SubagentStop/Stop continuity, and the
Heavy/Manager fail-closed guarantees require the supported canonical Codex
patch. Ordinary stock Codex is the independent Off-mode recovery plane; it is
not an equivalent enforced Manager runtime.

Public modes are ordered:

```text
Off -> Auto -> Lite -> Medium -> Heavy -> Manager Mode
```

`Alt+M` cycles Agent Manager through the duty levels in the patched TUI:

```bash
scripts/qwendex manager mode --toggle --json
```

The selected Agent Manager mode is the default backend `AgentPolicy` source.
`codex-status`, `manager status`, `agent policy`, and native `agent hook` gates
read the same persisted mode, so the visible footer and backend enforcement
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

`manager_deploy_policy` defaults to `auto`. With no attached prompt, Manager
Mode is healthy `standby` rather than waiting on a worker merely because it is
idle. An attached direct/trivial turn is also healthy without a lane. An
attached complex turn is blocked only when planned required lanes are missing
or unresolved; a stale writer lane is blocked until integration or an explicit
stop. Set `manager_deploy_policy` to `disabled` to opt out of deployment
requirements; explicit manual manager lifecycle commands remain
operator-directed.

## Manager Preflight

Every non-Off `qdex` launch runs a Manager preflight before Codex starts. Lite
and Medium keep missing-hook posture advisory; Heavy and Manager fail closed
without verified managed hooks or an explicitly recorded unhooked override.
The preflight honors the effective mode
selected by command handling, including `scripts/qwendex manager preflight
--mode manager`, rather than falling back to stored Auto state. The preflight
writes a `manager_decision` ledger record and receipt containing the effective
policy hash, active `CODEX_HOME`, hook status, local/cloud availability, prompt
digest or `interactive_prompt_unknown_prelaunch`, selected route, routing
reason, verifier requirement, validation plan, launcher-derived root ownership
id, and STOP status. `qdex` clears inherited per-launch Manager identities
before preflight so a restarted shell cannot reuse an earlier lease. Qdex also
binds the preflight policy hash to the earlier `codex-status` policy; a mode
change in that interval blocks launch instead of mixing two policies.

Useful dry-run commands:

```bash
scripts/qwendex manager preflight --interactive-prompt-unknown --dry-run --json
scripts/qwendex manager preflight --prompt "..." --json
qdex --manager-preflight-dry-run --json
```

Manager Mode may choose a `manager_subagents` route when a known prompt calls
for bounded lanes. Interactive `qdex` starts before the first prompt is known,
so it records a `direct_single_writer` exception with
`interactive_prompt_unknown_prelaunch`; hooks and Stop/finalization update the
same ledger when prompt and validation evidence are available.

On each root `UserPromptSubmit`, Qwendex attaches the real prompt to a turn
decision under the exported launch ledger (the Codex hook's own `session_id` is
not used as a manager id), recomputes the estimate and team plan, and injects
runtime-id registration templates. The first turn fills the preflight record;
later turns get a fresh ledger id and `agent_task_id` keyed by Codex `turn_id`,
so old verifier evidence cannot satisfy a new edit. Every spawned worker must
be registered with the exact agent id returned by Codex so its `SubagentStop`
event joins the current turn ledger.

Prompt admission stores only schema version, source, character length, and
SHA-256 metadata; raw prompt text is not persisted in the manager receipt.
Missing, empty, malformed, or unattached root prompt events block Heavy and
Manager turns with a stable admission error. Lite and Medium stay direct and
surface the admission warning. Child lifecycle events are not root prompt
authority and do not mutate the root admission record.

Missing or incomplete Qwendex Codex hooks block write-capable Manager Mode
launches by default:

```text
STOP_MANAGER_BLOCKED_UNHOOKED
```

Install and verify hooks explicitly:

```bash
scripts/qwendex agent hook-config --install --codex-home "$CODEX_HOME" --json
scripts/qwendex agent hook-config --verify --codex-home "$CODEX_HOME" --json
```

In a generated Qwendex dev environment, these commands install the lifecycle
hooks against the same `$QWENDEX_DEV_ROOT/scripts/qwendex` runtime that `qdex`
preflights. After updating Qwendex, exit active Qdex sessions, run
`scripts/qwendex_dev_env sync`, then reinstall and verify the managed entries
before launching Qdex again.

Qwendex does not silently install hooks. An operator can use
`QWENDEX_MANAGER_ALLOW_UNHOOKED=1` to allow a launch without verified hooks; the
preflight records `hook_override=true` and the reason from
`QWENDEX_MANAGER_UNHOOKED_REASON` or `explicit_operator_unhooked_override`.
If hooks are already verified, a stale override environment variable is ignored
for the hook-status decision.

## Mode Meaning

- `Off`: zero workers; Qdex skips manager preflight and Codex uses explicit-only
  native delegation behavior.
- `Auto`: capacity 4; the deterministic task classifier selects the effective
  Lite, Medium, Heavy, or Manager behavior for each turn.
- `Lite`: capacity 1; direct work is the default, with at most one bounded
  read-only lookup for an explicit or clearly read-heavy need.
- `Medium`: capacity 2; independent mapping, investigation, or verification
  lanes may be delegated while small or tightly coupled work stays direct.
- `Heavy`: capacity 3; non-trivial edits are orchestration-first with a
  read-only explorer and a required post-edit verifier.
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
restarts. Prompt admission uses launch-time Local availability and does not
re-probe or reinterpret Local state inside the prompt hook.

Qdex always enables the supported Codex V2 surface and injects the selected
worker cap plus root/worker usage hints. For non-Ultra reasoning it also injects
the Qwendex mode hint. When the final caller override selects Ultra, Qdex omits
only that custom mode hint so Codex's native Ultra proactive policy remains
active. Qwendex still owns the immutable cap, root-only management surface,
single-writer rule, exact planned-lane binding, lifecycle ledger, and
duplicate-start suppression. The native proactive source participates in both
the `codex-status` and preflight policy hashes; a mismatch blocks launch rather
than combining an Ultra runtime with a non-Ultra authority snapshot.

## Status Semantics

Manager status separates operator intent, advisory health, and blocking state:

- `standby`: Manager Mode is off, not required by policy, or waiting for an
  operator-selected lane. This is not a failed health state.
- `warning`: Qwendex has advisory issues, such as non-blocking guidance or
  local availability drift, but no writer lifecycle problem requires repair.
- `blocked`: an attached complex turn has missing or unresolved required lanes,
  or a stale writer lane requires integration or an explicit stop.

For an attached turn, `manager status --json` also exposes selected and
effective mode, task class, route and routing reason, prompt known/source/length
and admission code, planned profiles, required/registered/active/terminal lane
counts, validation counts, why no worker was used, policy source/hash/drift,
restart requirement, native proactive source, waivers, and receipt paths.

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
- review requirement

Prompt hooks tell the root orchestrator to follow the Qwendex plan and lifecycle
contract. When Kaveman is enabled, `SessionStart` and `UserPromptSubmit`
additional context also includes the configured Kaveman directive.
`SubagentStart` additional context supplies the generic inherited reasoning
class and worker contract from the manager ledger. Hook-facing messages never
name a configured GPT model; eligible low-risk token-saver lanes may still name
`qwen-local` when local Qwen is enabled and usable.

Subagent output is advisory until reviewed and backed by artifacts or tests.
When a worker reaches `FINAL_REPORT`, `BLOCKED`, or `FAILED`, the native
SubagentStop gate stores the raw worker output under `.qwendex/runs/`, writes a
compact report JSON beside it, and records those artifact paths on the manager
ledger row. `context compact-plan` and `context pack` carry compact agent
outcomes and artifact links, not full raw transcripts.

If a verifier reports failed or pending evidence and the root then remediates
the finding, Manager Mode permits one bounded `followup_task` to that same
verifier for final-state revalidation. The second SubagentStop updates the same
ledger identity, so Qwendex neither admits nor counts a duplicate verification
lane. A second failed or pending result remains blocked and must be disclosed as
remaining risk; it does not authorize another retry loop.

## Lifecycle

Default manager settings:

- `max_subagents`: defaults are mode-specific, from 0 to 4. Manager capacity is
  configurable up to the conservative hard ceiling of 8. The effective value
  drives manager registration and the supported Codex V2 worker ceiling; V2
  counts the root separately.
- `stale_after_minutes`: mode-specific, 15 to 45.
- Active subagent limits and single-writer locks apply per canonical repository
  root, so independent repositories do not consume or block each other's lanes.
- Close completed agents after findings are integrated.
- Status refreshes reconcile idle read-only agents after the stale window.
- Do not close an active writer until its changes are integrated or stopped;
  stale writer lanes are advisory warnings during daily health and blockers
  during strict health.

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
`AgentPolicy` for normal CLI commands. Every resulting non-Off Qdex policy runs
preflight; an explicit Off selector intentionally selects the documented stock
recovery boundary. The resolved `AgentPolicy`, source, and policy hash are
included in `agent`, `manager`, `check`, `doctor`, and `codex-status`
diagnostics. Native
`agent hook` stop gates read the same ledger and block Manager Mode finalization
when required lanes remain active, verifier evidence is missing after edits, or
the final response omits agent outcomes, validation, and risks. A trusted Qdex
launch continues to enforce those gates. A process without the complete live
Qdex identity is rejected at `UserPromptSubmit` before model work. Its later
`Stop` event is allowed to terminate without attaching to or mutating any
Manager decision, preventing a validation loop.

Generic process supervisors can check the same canonical binding without
reading prompts, environment, or ledger contents:

```bash
scripts/qwendex manager launch-status --pid "$PID" --repo-root "$REPO" --json
```

The command succeeds only for a live PID/start-ticks match with the expected
repository, preflight identity, trusted hooks, and current policy. Its data
projection is limited to health booleans/state labels, a reason code, and the
`qdex -C` recovery command.

A direct single-writer exception closes only when it has a routing reason,
verified hooks or recorded hook override, verifier requirement, validation
evidence, and dirty worktree classification. Missing validation returns
`STOP_MANAGER_VALIDATION_PENDING`; successful managed-lane or direct-exception
completion records `STOP_MANAGER_CLOSED`.

Stop is reevaluated after a continuation request. Required active or failed
lanes, missing final reports, missing post-edit verifier evidence, or a root
summary without agent outcomes, validation, and risks keep the turn blocked.
Once those facts are present, the retry closes the decision and releases the
launch locks. Bounded close timeouts preserve the ledger row and tombstone an
uncloseable worker so capacity cannot remain silently held.

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

When status reports policy drift, finish or stop the current turn and restart
Qdex to adopt the desired mode; do not try to rewrite the live snapshot. If a
strict launch blocks on hook or patch health, inspect `manager preflight`,
reinstall the managed hooks, and run `codex-patch preflight --require-applied`
against the supported source checkout. Setting the next-launch mode to `Off`
provides a stock-Codex recovery path without granting the old Manager session
new authority. Preserve the decision and lifecycle receipts while diagnosing a
blocked close so stale capacity and tombstones remain auditable.

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

The first release also uses a single-writer file-lock strategy in the base
worktree. Codex root events intentionally have no top-level `agent_id`, so
Qwendex derives root ownership only from the matching `qdex` preflight ledger;
putting `agent_id` in prompt text or tool arguments has no authority. Opaque
root writes take a per-tool repository lease that `PostToolUse` releases, with
`Stop` as the turn-boundary fallback. Native workers still require
their exact registered top-level id, matching repository and current task, and
an event or registered write scope. A second writer is blocked while a lease is
active. Aborted tools remain locked until `Stop`; if Codex exits abruptly, the
next launcher reclaims the old root family only after its recorded process
identity is confirmed dead.

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
managed hooks after moving a dev home. The native ledger and
gate evaluator remain the source of truth, and Manager preflight names missing
hooks or explicit unhooked overrides in the decision receipt.

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
