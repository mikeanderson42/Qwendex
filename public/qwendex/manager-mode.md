# Manager Modes

Qwendex behaves like normal Codex by default. Manager orchestration is additive:
the main session keeps the user's selected model and reasoning, while Qwendex
routes only specific subagent lanes to local Qwen, GPT-5.5 low/medium, or
high/xhigh reasoning when the lane actually needs it.

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

`manager_deploy_policy` defaults to `auto`: when the selected mode is Manager
Mode, Qwendex expects at least one active registered agent lane. Routine
advisory health reports no-lane Manager Mode as `standby`; strict release
health reports it as blocked. Set `manager_deploy_policy` to `disabled` to opt
out of that requirement; explicit manual manager lifecycle commands remain
operator-directed.

## Manager Preflight

When Agent Manager resolves to Manager Mode, write-capable `qdex` launches run a
Manager preflight before Codex starts. The preflight honors the effective mode
selected by command handling, including `scripts/qwendex manager preflight
--mode manager`, rather than falling back to stored Auto state. The preflight
writes a `manager_decision` ledger record and receipt containing the effective
policy hash, active `CODEX_HOME`, hook status, local/cloud availability, prompt
digest or `interactive_prompt_unknown_prelaunch`, selected route, routing
reason, verifier requirement, validation plan, launcher-derived root ownership
id, and STOP status. `qdex` clears inherited per-launch Manager identities
before preflight so a restarted shell cannot reuse an earlier lease.

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

- `Off`: no manager delegation duty.
- `Auto`: deterministic checks recommend a mode; capacity is 4.
- `Lite`: bounded coordination with capacity 2.
- `Medium`: normal multi-lane coordination with capacity 4.
- `Heavy`: verification-oriented coordination with capacity 6.
- `Manager Mode`: full coordination capacity 10; the main session coordinates,
  reviews, and validates.

Legacy compatibility remains: the `manager_only` spelling maps to
`Manager Mode`.

## Status Semantics

Manager status separates operator intent, advisory health, and blocking state:

- `standby`: Manager Mode is off, not required by policy, or waiting for an
  operator-selected lane. This is not a failed health state.
- `warning`: Qwendex has advisory issues, such as non-blocking guidance or
  local availability drift, but no writer lifecycle problem requires repair.
- `blocked`: a required Manager Mode deployment contract is unmet, or a stale
  writer lane requires integration or an explicit stop.

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
- spawn instruction naming the selected model and reasoning
- review requirement

Prompt hooks tell the root orchestrator to spawn agents using Qwendex
model/reasoning assignments. When Kaveman is enabled, `SessionStart` and
`UserPromptSubmit` additional context also includes the configured Kaveman
directive. `SubagentStart` additional context includes the selected model and
reasoning from the hook event or the manager session ledger, so high-risk,
security, release, and protocol lanes surface `gpt-5.5` with high or xhigh
reasoning while eligible low-risk token-saver lanes surface `qwen-local` with
low reasoning.

Subagent output is advisory until reviewed and backed by artifacts or tests.
When a worker reaches `FINAL_REPORT`, `BLOCKED`, or `FAILED`, the native
SubagentStop gate stores the raw worker output under `.qwendex/runs/`, writes a
compact report JSON beside it, and records those artifact paths on the manager
ledger row. `context compact-plan` and `context pack` carry compact agent
outcomes and artifact links, not full raw transcripts.

## Lifecycle

Default manager settings:

- `max_subagents`: mode-specific, 1 to 10. The Qwendex product ceiling is 10;
  the same value drives manager registration and `AgentPolicy.max_threads`.
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
`AgentPolicy` for normal CLI commands, but a selected Manager Mode still makes
`qdex` run the Manager preflight so an env selector cannot silently skip the
decision ledger. The resolved `AgentPolicy`, source, and policy hash are
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
