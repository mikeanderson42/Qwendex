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

## Mode Meaning

- `Off`: no manager delegation duty.
- `Auto`: deterministic checks plus the bounded estimator pick a mode.
- `Lite`: target 10-20% subagent offload.
- `Medium`: target 25-45% subagent offload.
- `Heavy`: target 50-75% subagent offload.
- `Manager Mode`: target 85-95% offload; default `max_subagents` is 10, and
  the main session coordinates, reviews, and validates.

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

## Auto Estimator

Auto uses deterministic signals first. If the task is ambiguous, it calls the
`qwendex-auto-manager-estimator` skill with a small prompt. The estimator
defaults to GPT-5.5 medium and returns bounded JSON: complexity, risk, likely
file scope, validation depth, subagent usefulness, recommended mode, confidence,
and any lane that needs high/xhigh reasoning.

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
- review requirement

Subagent output is advisory until reviewed and backed by artifacts or tests.

## Lifecycle

Default manager settings:

- `max_subagents`: mode-specific, 2 to 10. The Qwendex product ceiling is 10.
- `stale_after_minutes`: mode-specific, 15 to 45.
- Close completed agents after findings are integrated.
- Status refreshes reconcile idle read-only agents after the stale window.
- Do not close an active writer until its changes are integrated or stopped;
  stale writer lanes are advisory warnings during daily health and blockers
  during strict health.

Durable lifecycle commands:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --owner Rawls --write-surface read-only --stop-condition "return findings" --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager status --json
scripts/qwendex manager close --agent-id reviewer-1 --reason integrated --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

The CLI records `agent_id`, lane, task, owner, write surface, stop condition,
artifacts, context packet, heartbeat time, validation status, stop reason, and
close receipt metadata in the local Qwendex state DB. It does not forcibly
interrupt an external process.

`scripts/qwendex manager repair --safe --json` is the bounded safe repair path
for manager state. It closes stale read-only lanes and harmless empty stale
writer lanes, but leaves writer lanes with artifacts, receipt paths, exact
files, or non-pending validation open. Those lanes return an explicit
`manager close --agent-id ... --reason ... --json` command for operator review.

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
