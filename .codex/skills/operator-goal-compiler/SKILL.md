---
name: operator-goal-compiler
description: Compile rough operator intent or prior Codex closeouts into bounded DeskAgent, Clippy, Qmsg, Jarvis, Qwendex, or GTM development goals with lane classification, git custody, effort budgets, anti-loop rules, artifact contracts, validation gates, STOP statuses, and next-goal routing.
---

# Operator Goal Compiler

Use this skill when Mike asks for a Codex/Qwendex goal, project prompt, next
goal, execution packet, "the goal skill", or analysis of a prior Codex
closeout. The default job is to compile a paste-ready goal, not to edit code,
unless Mike explicitly asks to execute.

## Inputs

Accept rough intent, pasted results, transfer notes, validation output, or
repo/project direction. Prefer direct best-effort compilation over questions
unless ambiguity would make the goal unsafe to execute.

Always identify:

- target project/repo
- primary lane
- mode
- effort budget
- git custody policy
- validation tier
- artifact contract
- STOP statuses

## Modes

`compile_goal`: turn rough intent into a complete goal packet.

`next_goal_from_result`: use when Mike pastes a prior Codex closeout, progress
summary, or validation result. Extract verified state, branch/commit, dirty
paths, validation status, STOP status, blockers, and the true next frontier.
Generate the next highest-value goal, not a recap.

`execute_goal`: only when Mike explicitly asks to execute. Inspect repo state,
compile internally, work through bounded phases, validate, commit only if
allowed, and never push unless the project policy allows it or Mike asks.

## Project Defaults

- `deskagent_clippy`: use `/home/tweak/repohome/jarvis` for local PC,
  DeskAgent runtime, Clippy/Jarvis/Qmsg, voice/tray/remote, and operator
  recovery. Do not push unless explicitly requested.
- `qwendex`: use `/home/tweak/repohome/qwendex-dev` for Qwendex product,
  manager mode, local-Qwen bridge, Codex patching, receipts, evals, reusable
  packages, and release docs. Do not push unless explicitly requested.
- `gtm_framework`: use the active GTM repo/project context. Push scoped,
  validated changes by default only when that project policy is in effect.

Preserve compatibility for `jarvis-*`, `clippy-*`, `qmsg`, `qmsg-tmux`,
`qmsg-watchdog`, Qmsg units, and `qwendex-phone:0.0` unless a migration goal
explicitly includes tested compatibility replacement.

## Lane Classifier

Pick one primary lane. Split multi-lane requests into ordered goals unless Mike
explicitly asks for a combined sprint.

- `deskagent_backend`: config, CLI, process registry, package boundaries,
  verification, docs.
- `qmsg_bridge`: `deskagent.messaging.qmsg`, qmsg status/verify, watchdog,
  legacy compatibility.
- `clippy_visual`: renderer, SVG assets, body/eyes/brows/paper, proof images.
- `clippy_animation`: animation catalog, idle policy, behavior controller.
- `live_acceptance`: wake model, live voice, remote keyboard, restart survival.
- `service_runtime`: systemd/user services, wrappers, runtime status.
- `doctor_verifier`: doctor/verify classification and live-gate boundaries.
- `qwendex_product`: manager mode, estimates, bridge/parser/runtime guards,
  receipts, evals, release docs.
- `git_docs_context`: project instructions, AGENTS, skills, branch custody,
  docs consolidation.
- `gtm_application`: admission review, source gaps, application harnesses,
  parser/schema repair, row queues, lineage, and patch-safe rows.

## Effort Budgets

- `micro`: one file, wording, docs, or focused bug; no broad audit.
- `focused`: one lane or artifact family with focused validation.
- `standard`: normal implementation with scoped tests and final validation.
- `heavy`: multi-phase repo work with docs, receipts, validation, and commit.
- `max`: only when Mike explicitly asks to push hard or run a long autonomous
  sprint.

The compiled goal must name the budget and validation tier. Do not choose heavy
validation for docs-only, visual-only, or prompt-only work unless requested.

## Git Custody

Every goal must require:

```bash
git branch --show-current
git rev-parse --short HEAD
git status --short
git diff --stat
git ls-files --others --exclude-standard
```

Classify dirty files as `in_scope`, `related_existing`, `unrelated`, or
`generated_private`. Track durable source/docs/tests/configs/scripts/services.
Keep generated/private artifacts ignored. Split commits by lane. Do not reset,
delete, revert, broad-chown/chmod, or push unless Mike explicitly asks.

## Anti-Loop Rules

- If a harness was built, consume its outputs next.
- If a queue was generated, resolve the queue next.
- If blockers were found, cluster and repair blockers next.
- If source contracts were emitted, execute or machine-close them next.
- If two consecutive runs end on the same machine-blocked condition, pivot to
  deterministic field/materialization repair, source-contract repair, or
  application-boundary review.
- If rows or patches are staged patch-safe, prefer admission-grade audit or
  application-rule extraction over discovery.
- Do not allow two consecutive report-only goals unless Mike asks for analysis
  only.

## Artifact Contracts

Use concrete deliverables:

- DeskAgent/Qmsg: status JSON, verify JSON, compatibility receipt, process
  registry diff, focused tests.
- Clippy visual: proof assets, review notes, changed visual sources,
  `human_review_required` if visual approval is needed.
- Doctor/verifier: classified failures, package failures, deferred live gates,
  command outputs.
- Qwendex product: receipt/eval output, connectedness proof, docs update,
  quick/full/release gate result as appropriate.
- Git/docs context: branch, commits, changed docs, ignored private artifacts,
  validation commands, remaining dirty-state classification.
- GTM/application: input rows/contracts, admitted/rejected rows, source-gap
  clusters, schema/parser diffs, lineage notes, application-boundary decisions.

## Template Selection

For detailed packet shape, read only the relevant template:

- `templates/deskagent_backend_goal.md`
- `templates/qmsg_bridge_goal.md`
- `templates/clippy_visual_goal.md`
- `templates/clippy_animation_goal.md`
- `templates/live_acceptance_goal.md`
- `templates/doctor_verifier_goal.md`
- `templates/qwendex_product_goal.md`
- `templates/git_docs_context_goal.md`
- `templates/gtm_application_goal.md`

For sample outputs, read `examples/rough_intent_to_goal_examples.md`.

## Goal Packet Format

Use this structure unless a template overrides it:

```text
GOAL
TARGET_WORKSPACE
MODE
EFFORT_BUDGET
LANE
CURRENT_BASELINE
OBJECTIVE
OWNER_DOCS_TO_READ
SCOPE
OUT_OF_SCOPE
GIT_CUSTODY
PHASES
ARTIFACT_CONTRACT
VALIDATION
NON_COMPLETION_RULES
COMMIT_PUSH_POLICY
FINAL_REPORT_FORMAT
STOP_STATUSES
NEXT_GOAL_ROUTING
```

## Final Report Requirements

Generated goals should require final reports with:

```text
SOURCE_CHECK
LANE_CLASSIFICATION
DIRTY_STATE_AUDIT
CHANGE_SUMMARY
ARTIFACTS
VALIDATION_RESULTS
GATE_RESULTS
COMMIT_STATUS
DEFERRED_ITEMS
NEXT_RECOMMENDED_GOAL
FINAL_STOP_STATUS
```

Never report a ready status if branch custody is unclear, unrelated dirty state
was discarded or staged, validation was skipped without explanation,
compatibility was broken, or live/human gates were falsely claimed complete.
