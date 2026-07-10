# Configuration

Qwendex config lives in `config/qwendex/`.

- `qwendex.schema.json`: stable schema
- `qwendex.json`: repo-local default
- `profiles.json`: non-authoritative seat reference metadata
- `model-catalog.json`: non-authoritative model/guard reference metadata
- `qwendex.sample.json`: copy-safe sample with no credentials

Runtime policy comes from `qwendex.json` plus the documented override layers.
The two smaller catalog files are reference material only; editing them does
not change routing or launcher behavior.

Validate the two published configs against `qwendex.schema.json` with:

```bash
python3 scripts/validate_qwendex_config.py
```

The validator uses the exactly pinned `jsonschema` Draft 2020-12 implementation
and also requires matching schema identifiers, SemVer product versions, and
version parity between the default, sample, and current release heading.

Precedence is:

1. CLI flags
2. Environment variables
3. Project config
4. Safe user config
5. Built-in defaults

Supported environment overrides:

```bash
QWENDEX_DEFAULT_SEAT=qwen
QWENDEX_RESULTS_ROOT=results/qwendex
QWENDEX_STATE_DB=~/.local/state/qwendex/qwendex.sqlite
QWENDEX_LEARNING_MODE=stage_only
QWENDEX_ORCHESTRATION_MODE=auto
QWENDEX_MANAGER_MODE=heavy
QWENDEX_LOCAL_SUBAGENTS=on
QWENDEX_ROUTING_MODE=token_saver
QWENDEX_PREFER_LOCAL_QWEN=1
QWENDEX_LOCAL_QWEN_PROBE_URL=http://127.0.0.1:1234/v1/models
QWENDEX_LOCAL_QWEN_MODEL=qwen-local
QWENDEX_FALLBACK_SEAT=primary
```

Do not place credentials in Qwendex config. Use the existing provider-specific
environment handling outside the public Qwendex config surface.

## LLMStack Config

LLMStack config lives in `config/local_llm_stack/`.

- `stack_manager.json`: public copy-safe default
- `stack_manager.sample.json`: same default for reset/comparison
- `profiles.example.json`: backend snippets to copy into local config
- `stack_manager.local.json`: ignored machine-local override
- `local_harness.env.sample`: environment override template

The stack loader prefers `stack_manager.local.json` when present. Use
`QWENDEX_LLMSTACK_CONFIG` to point at another file.

## State

`state.db` stores Qwendex task, manager-session, context snapshot, handoff,
evidence, and receipt-link tables. It is local operator state and should not
contain credentials.

Use a temporary DB for isolated probes:

```bash
QWENDEX_STATE_DB=/tmp/qwendex.sqlite scripts/qwendex task status --json
```

## Context Reminders

`context.reminder_tool_call_threshold`, `context.reminder_repeat_interval`, and
`context.phase_boundary_labels` tune advisory compaction reminders. Qwendex does
not compact a live Codex session itself; it tells the operator whether to keep
going, create a snapshot first, or build a compact plan.

```bash
scripts/qwendex context reminder --task-id task_... --tool-calls 55 --phase after-milestone --json
```

## Routing

`routing` controls cost-aware seat selection:

- `mode`: `token_saver`, `manual`, or `primary_only`
- `prefer_local_qwen_when_available`: default `true`
- `local_probe_url`: Codex-facing `/v1/models` endpoint
- `local_model`: default `qwen-local`
- `fallback_seat`: default `primary`
- `prefer_for_task_classes`: bounded work that may use local Qwen
- `primary_required_for_task_classes`: work that stays on GPT/Codex authority

Inspect the current decision with:

```bash
scripts/qwendex route --task-class exec --json
```

`scripts/qwendex exec` defaults to `--seat auto`. Auto routing chooses `qwen`
only when the local model alias is visible through the guarded Codex-facing
endpoint; otherwise it falls back to the configured primary seat.
`exec` infers its task class from the prompt before routing; security,
architecture/protocol, release, and public-doc-claim prompts cannot be forced
onto the local Qwen seat. Use the validated `--task-class` option when the
prompt alone does not identify the lane.

Local routing separates intent from availability. `QWENDEX_LOCAL_SUBAGENTS=on`
or `Local: [Ready]` means Qwendex may consider local subagent lanes and the
probe has confirmed `local_model`. It still probes `local_probe_url` before
choosing the `qwen` seat. `Local: [Off]` or `QWENDEX_LOCAL_SUBAGENTS=off` is
operator intent to skip local lanes even when the endpoint is healthy. If local
intent is on but the probe cannot confirm the alias, the state is
`Local: [Unavailable]` and Qwendex falls back to `fallback_seat`.

`fallback_seat` must be a GPT/Codex authority seat (`primary`, `audit`, or
`release`). `--prefer-local` and an explicit `--seat qwen` do not override
`primary_required_for_task_classes`, and Local-Off also prevents explicit local
seat routing. These are release-blocking routing invariants, not cost hints.

The global `sandbox.mode` applies to `exec` commands. The `audit` and isolated
`sandbox` seats always use `read-only`, ignore user MCP/config surfaces, and do
not inject the local artifact-queue MCP. Local-Qwen minimal exec also clears MCP
configuration and passes the selected `read-only` or `workspace-write` mode
through its launcher. Results from either local seat (`qwen` or `sandbox`)
require GPT/Codex review.
Seat guard profiles, wall/tool budgets, context/compaction limits, and tool
output limits are carried into the child command/environment and recorded in
the receipt execution policy. `max_output_tokens` is recorded as a declared,
not-enforced value until the launcher/runtime consumes it; the receipt's
`enforcement` map names this boundary.
The published local seats and 32k backend profile use a `32768` context window
and a `28672` auto-compact limit. Every seat must keep its effective compact
limit below its context window; config validation rejects an inverted budget.

`local_probe_url` must end in `/v1/models` without a query or fragment. Qwendex
derives the launcher base by removing that suffix and passes both the derived
`LOCAL_QWEN_BASE` and configured `LOCAL_QWEN_MODEL` to the child, so the
endpoint/model that passed routing is the one execution uses. The launcher
normalizes that base and exports `CODEX_OSS_BASE_URL=<base>/v1` for Codex. A
conflicting inherited `CODEX_OSS_BASE_URL` blocks before execution instead of
silently sending the run to an endpoint different from the one that passed
preflight.

`qdex --repo <project>` makes the selected repository the manager target,
execution working directory, Codex add-dir, local-harness trusted root, and MCP
trusted root. The generated isolated `CODEX_HOME` remains authoritative by
default; preserving a caller's `CODEX_HOME` requires the explicit
`QWENDEX_QDEX_PRESERVE_CODEX_HOME=1` opt-in. `qdex` defaults to
`--dangerously-bypass-approvals-and-sandbox`; the repo binding is a
Qwendex/MCP routing boundary, not OS-level filesystem confinement.
For direct `exec --cwd`, the default artifact-queue MCP trusted write root is
only that execution directory; adding any other root requires an explicit
`QWENDEX_MCP_TRUSTED_ROOTS` override. Qwendex source is not included in those
repo-bound MCP roots merely because its launcher is providing the command.

The exact `QWENDEX_OK` probe executes normally by default. `--synthetic` is an
explicit offline-only shortcut for that exact marker; its receipt says
`synthetic_not_evidence` and cannot establish model, sandbox, tool, or endpoint
availability. `scripts/qwendex seat <name>` likewise confirms only that a seat
is configured; it does not probe availability.

## Orchestration

`orchestration` controls manager defaults:

- `mode`: `off`, `auto`, `lite`, `medium`, `heavy`, or `manager`
- `manager_deploy_policy`: `auto` by default; Manager Mode requires active
  registered agent lanes unless this is set to `disabled`
- `kaveman`: persisted enabled state and the enforced terse-output directive
- `local_subagents`: default Local enabled state
- `mode_profiles`: status label and enforced agent capacity per mode; Manager
  Mode has `max_subagents: 10`
- `local_qwen_eligibility`: task classes and max risk for local lanes
- `escalation_thresholds`: terms that move lanes to high or xhigh
- `stale_session_thresholds_minutes`: cleanup windows per mode

The canonical cycle order and patched-TUI hotkeys are code/keymap contracts,
not mutable Qwendex configuration. `Alt+M`, `Alt+K`, and `Alt+L` may be rebound
through the Codex TUI keymap. `manager estimate` is a deterministic CLI
heuristic; it does not invoke a model or skill and has no model-budget config.
The selected mode profile's `max_subagents` also supplies
`AgentPolicy.max_threads`, so status, ledger capacity, and backend policy share
one limit.

`QWENDEX_MANAGER_MODE` and `QWENDEX_ORCHESTRATION_MODE` override the configured
default mode for a fresh state DB. Once `scripts/qwendex manager mode ...` or
`Alt+M` persists a selected mode, that local state is the active mode source
until it is changed again.

Agent-use selectors are session-level runtime policy inputs. When no explicit
selector is set, the selected Agent Manager mode is the backend `AgentPolicy`
source:

```bash
scripts/qwendex manager mode --set manager --json
scripts/qwendex --agent-use Manager agent policy --json
QWENDEX_AGENT_USE=Heavy scripts/qwendex agent status --json
CODEX_AGENT_USE=Lite scripts/qwendex check --json
```

Precedence is CLI `--agent-use`, then `QWENDEX_AGENT_USE`, then
`CODEX_AGENT_USE`, then the selected Agent Manager mode. If no mode has been
persisted, `orchestration.mode` is the default. Invalid explicit values fall
back to `Medium` with a warning unless `QWENDEX_AGENT_USE_STRICT=1` is set.

The CLI exposes these settings with:

```bash
scripts/qwendex manager --json
scripts/qwendex agent policy --json
scripts/qwendex agent status --json
scripts/qwendex manager mode --set auto --json
scripts/qwendex manager kaveman --toggle --json
scripts/qwendex manager local --toggle --json
scripts/qwendex manager estimate --prompt "..." --json
```

Durable manager lifecycle commands write to `state.db`:

```bash
scripts/qwendex manager assign --agent-id reviewer-1 --lane review --task-id task_... --json
scripts/qwendex manager heartbeat --agent-id reviewer-1 --json
scripts/qwendex manager close --agent-id reviewer-1 --reason integrated --json
scripts/qwendex manager close-stale --stale-after-minutes 30 --json
```

`manager_only` remains a compatibility alias for `manager`.

`manager_deploy_policy` defaults to `auto`: when the selected mode is Manager
Mode, Qwendex expects at least one active registered agent lane. Routine
advisory health reports no-lane Manager Mode as `standby`; strict release
health reports it as blocked. Set `manager_deploy_policy` to `disabled` to opt
out of that requirement; explicit manual manager lifecycle commands remain
operator-directed.

`manager repair --safe` is the public manager-state reconciliation path. The
safe boundary is to reconcile read-only stale state and harmless empty stale
writer lanes while keeping non-empty writer lanes open for operator review.
Those remaining writer lanes are advisory warnings during daily health and
blockers during strict health.
