# Qwendex Product Specification

Status: authoritative development specification for Qwendex 0.7.x.

## Product Definition

Qwendex is a source-distributed Codex customization and operator harness. It
adds an isolated launcher, a stable JSON control plane, advisory multi-agent
planning and lifecycle evidence, optional guarded local-model routing,
immutable runtime generations, and verification/release tooling around Codex.
Codex remains the execution and approval plane.

## Users And Jobs

- Operators launch a reproducible Qdex session without replacing stock Codex.
- Maintainers inspect routing, lifecycle, receipts, and compatibility through
  deterministic CLI/JSON contracts.
- Reviewers distinguish planned work from executed/validated evidence.
- Release managers bind source, patched Codex binaries, configs, tests, privacy
  checks, CI, tags, and runtime activation into one auditable release.

## Product Surfaces

| Surface | Audience | Contract |
| --- | --- | --- |
| `qwendex` | Operator | Stable Python/shell control plane and `qwendex.cli.v1` JSON envelope |
| `qdex` | Operator | Isolated Codex launcher with Qwendex policy, native project trust, and optional compiled integration |
| `llmstack` | Operator, optional | Local-model process facade and checks; no bundled models or hosting programs |
| `qwendex-dev` | Operator, maintainer | Source install/bootstrap, Codex patch/build, verification, staging, release, and activation |

## Stable Core

- Product/about, check, doctor, routing, seats, exec receipts, task/context/
  handoff/evidence state, Manager/Agent inspection, evals, and runtime status.
- Source-distributed installation with ignored mutable runtime state.
- Explicit JSON status, error, artifact, and next-action fields.
- Stock-Codex recovery and no in-place modification of the installed Codex CLI.
- Manager metadata is advisory; root authorization remains user/Codex/host
  controlled.

## Optional Components

- Local Qwen bridge and LLMStack are default-off and separately validated.
- The Codex Rust patch is version-pinned. Unknown versions or moved anchors
  fail closed before source mutation.
- Exploration telemetry is default-off, local-only, and metadata-only.

## Non-Goals

- Shipping model weights, a model server, credentials, host-specific service
  configuration, or private workspace logic.
- Reimplementing Codex plugins, skills, approvals, project trust, sandboxing,
  retained memories, or every upstream feature.
- Claiming comparative optimality, universal model availability, or release
  acceptance from planning metadata or synthetic receipts.
- Treating token/context budgets as a guaranteed billing limit.

## Authority And Lifecycle

The root is the default sole writer and integrator. Native Manager V2 tools are
root-only; children cannot recursively manage agents. Qwendex may recommend
bounded read-only lanes, record reservations and artifacts, and expose stale or
unvalidated rows. Only explicit lifecycle commands mutate those rows.

Validation requires a trusted, regular, bounded-size JSON receipt whose schema
and digest pass Qwendex validation and whose repository, task, and agent
identity bind to the target session. The file digest covers the exact bytes
parsed. An explicit validation waiver requires and records an actor, reason,
target identity, source, and timestamp but never impersonates evidence.

## Model Routing

- The root model and reasoning remain user-selected.
- Hosted workers use explicit profile routing. The 0.7 default is Terra/high;
  review and release lanes use Terra/xhigh.
- Luna/max is a one-shot `qwendex exec` seat until Codex advertises it as a V2
  worker model.
- Local Qwen is a separate `qwendex_exec` surface for allowlisted low-risk
  profiles only. Planning eligibility and execution-backed token saving are
  distinct receipt fields.
- Security, architecture/protocol, release, live acceptance, and public claims
  require GPT/Codex authority.

## Permission, Trust, Privacy, And Telemetry

Qdex exposes workspace-write, automatic-review, and explicit Yolo modes.
Automatic review maps to Codex `--approve-for-me`. Native permission flags and
permission-affecting config overrides are rejected so the selected Qdex mode
remains the effective reported posture. Project trust remains native. Qwendex
may diagnose an exact generated hook set, but Qdex never adds Codex's global
hook-trust bypass because that bypass also covers project, config-layer, and
plugin sources outside the generated-home inventory.

Tracked artifacts must not contain credentials, raw prompts, transcripts,
private paths, logs, model weights, runtime databases, or generated homes.
Telemetry capture remains off unless metadata mode is explicitly selected and
never stores raw prompts, commands, paths, tool input/output, or transcripts.

## Runtime And Compatibility

Each immutable generation binds Qwendex source, config, schema, hooks, state
schema, Codex source/patch, binary pair, and model cache. Activation affects new
sessions; rollback selects a previously validated generation. Release gates
must use the canonical built Codex binary and reject an ambient substitute.

## Connectedness Contract

Every public control or claim must have all four links:

| State/config | CLI/API | Test or receipt | Documentation |
| --- | --- | --- | --- |
| Product/surfaces and Codex policy | `qwendex about --json` | CLI smoke | README, compatibility, CLI reference |
| Hosted/local profiles | `route`, `exec`, `agent plan` | routing and receipt smoke | configuration, Manager, compatibility |
| Qdex permission mode | Qdex dry-run/launch | delegation-policy smoke | quickstart, configuration, CLI reference |
| Manager lifecycle/validation | `manager status/validate/validation-waive` | Manager state smoke | Manager and Agent docs |
| Native Codex patch | `codex-patch preflight/apply` | pristine source apply, rustfmt, Rust tests | compatibility and patching docs |
| Runtime generation | `runtime status/generations` | generation/release receipts | operations and verification |
| Documentation quality | `docs audit` | docs smoke and strict audit | documentation-quality guide |

## Acceptance Gates

1. Syntax, lint, JSON/schema, and targeted smoke tests pass.
2. The complete public smoke suite passes in an isolated state/home.
3. The current generator dry-runs and applies to a pristine pinned Codex
   checkout; anchors/signatures, rustfmt, and targeted Rust tests pass.
4. Docs audit has no missing/dead/private/stale index findings.
5. Full offline Qwendex and harness eval receipts pass.
6. Release-adjacent local-model changes pass the live bridge and fresh-home
   acceptance gates.
7. Candidate release binds source and canonical binaries; publication adds
   trusted remote, default-branch CI, annotated tag, and immutable release.
8. A generation built from the published tag is activated and rechecked.

## Maturity Labels

- Stable: source CLI/JSON, routing policy, receipts, diagnostics, runtime
  generations, release gates, stock recovery.
- Optional supported: canonical Codex 0.147 patch, local stack when its live
  gates pass.
- Experimental opt-in: MCP 2026, metadata telemetry, external learning tools.
- Deferred: native role/service-tier child control, Qdex retained memories,
  Luna V2 workers, binary/package distribution.
