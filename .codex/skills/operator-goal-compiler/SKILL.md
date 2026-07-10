---
name: operator-goal-compiler
description: Compile rough Qwendex operator intent or prior Codex closeouts into bounded Qwendex development goals with git custody, effort budgets, anti-loop rules, artifact contracts, validation gates, STOP statuses, and next-goal routing.
---

# Qwendex Operator Goal Compiler

Compile a paste-ready Qwendex goal when the operator asks for a goal, project
prompt, next goal, execution packet, "the goal skill", or analysis of a prior
Codex closeout. Do not edit code unless the operator explicitly asks to execute
the compiled goal.

## Inputs And Modes

Accept rough intent, pasted results, transfer notes, validation output, or
Qwendex project direction. Prefer best-effort compilation over questions unless
ambiguity would make the goal unsafe.

- `compile_goal`: turn fresh intent into a complete goal packet.
- `next_goal_from_result`: extract verified state, branch/commit, dirty paths,
  validation, STOP status, blockers, and the true next frontier from a prior
  closeout. Generate the next highest-value goal, not a recap.
- `execute_goal`: inspect and execute only when explicitly requested. Validate
  before committing and never push unless explicitly authorized.

Resolve the Qwendex workspace from the operator's target or
`$QWENDEX_DEV_ROOT`, defaulting to `~/qwendex-dev`.

## Lane And Effort

Choose one primary Qwendex lane. Split combined work into ordered goals unless
the operator explicitly requests one sprint.

- `product`: CLI, config, routing, receipts, seats, and reusable packages.
- `manager`: manager mode, agent policy, lifecycle, hooks, and orchestration.
- `local_bridge`: Responses compatibility, parser recovery, runtime guards,
  marker suppression, and local stack wiring.
- `codex_patch`: supported Codex source patching and patched-TUI boundaries.
- `release`: packaging, public docs, release receipts, and publication gates.
- `git_docs_context`: Qwendex instructions, skills, branch custody, and docs.

Set one effort budget:

- `micro`: one file, wording, docs, or a focused bug.
- `focused`: one lane or artifact family with focused validation.
- `standard`: normal implementation with scoped tests and final validation.
- `heavy`: multi-phase Qwendex work with docs, receipts, and full validation.
- `max`: only for an explicitly requested long autonomous sprint.

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
`generated_private`. Track durable source, docs, tests, configs, and scripts.
Keep generated/private artifacts ignored. Do not reset, delete, revert,
broad-chown/chmod, commit, or push outside the goal's explicit authority.

## Anti-Loop Rules

- Consume existing harness outputs instead of rebuilding the harness.
- Resolve an existing queue before generating another queue.
- Cluster and repair known blockers instead of repeating discovery.
- Execute or machine-close emitted source contracts next.
- After two runs on the same blocker, pivot to deterministic contract, state,
  schema, or boundary repair.
- Do not produce two consecutive report-only goals unless analysis-only work was
  requested.

## Artifact And Validation Contract

Require concrete Qwendex evidence appropriate to the lane:

- changed source/docs/tests/config paths
- routing, manager, bridge, patch, receipt, or eval JSON when applicable
- connectedness proof for visible controls or public claims
- private/generated artifact classification
- focused tests plus `qwendex-dev verify --tier quick`
- `qwendex-dev verify --tier full` for shared contracts
- `qwendex-dev verify --tier release` for release-readiness claims

Local Qwen output is advisory. Require GPT/Codex review for release, security,
architecture, protocol, and public claims.

Read `templates/qwendex_product_goal.md` for the detailed packet shape. Read
`examples/rough_intent_to_goal_examples.md` only when an example is useful.

## Goal Packet

Use this structure:

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

Require final reports with source/branch checks, dirty-state classification,
change and artifact summaries, validation and gate results, commit status,
deferred items, the next recommended goal, and a specific final STOP status.
Never report ready when custody is unclear, unrelated dirty state was discarded
or staged, required validation lacks evidence, or public/runtime boundaries are
overclaimed.
