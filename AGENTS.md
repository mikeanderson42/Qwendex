# Qwendex Workspace Rules

This repository is scoped to the Qwendex/local-Qwen harness:

- Qwendex CLI routing, seats, manager mode, estimates, receipts, and evals
- Codex Responses bridge compatibility, parser recovery, runtime guards, and
  marker suppression
- Local model stack launchers, bridge checks, and operator-console wiring
- Public Qwendex docs and release validation summaries

Avoid adding project-specific research workflows, private workspace paths, or
domain-specific queue logic to this repo. Downstream projects should integrate
Qwendex through wrappers or environment configuration.

For targeted verification prefer:

```bash
python3 -m py_compile scripts/*.py scripts/local_qwen_bridge/*.py
python3 -m ruff check scripts tests --ignore E501
python3 -m json.tool config/qwendex/qwendex.json
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex eval --all --json
```

## Qwendex Development Lane

Use `~/qwendex-dev` as the primary development worktree for Qwendex product
work. Runtime state, receipts, Codex home, snapshots, Codex source checkouts,
and build outputs belong under `~/qwendex-dev/.qwendex-dev/` and must remain
untracked.

### Workspace Boundary

Use this worktree only for reusable Qwendex product and package work. Local PC
recovery, downstream assistant behavior, private project prompts, backup
procedures, machine layout, and host-specific service configuration belong in
the owning downstream repository or an ignored operator-local overlay. Do not
encode those details, their absolute paths, or personal history in tracked
Qwendex files.

At the start of a fresh dev session, establish posture before editing:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev status-json
qwendex-dev doctor
scripts/qwendex manager status --json
git status --short
```

Before staging product changes from the dev worktree, run:

```bash
qwendex-dev doctor
qwendex-dev verify --tier quick
```

For durable package/product work, use a named task branch, track new source,
tests, docs, configs, and scripts intentionally, keep generated/private
artifacts ignored, split commits by lane, and do not push unless explicitly
requested.

When generating Qwendex goals from this workspace, use the repo-local
`.codex/skills/operator-goal-compiler/` skill. If the operator says "the goal
skill", treat it as this Qwendex goal compiler workflow.
Support both fresh-intent compilation and `next_goal_from_result` compilation:
when the operator pastes a prior Codex closeout, extract verified state, branch/commit,
dirty paths, validation results, STOP status, blockers, and the next frontier.
Choose an effort budget (`micro`, `focused`, `standard`, `heavy`, or `max`),
include an artifact contract, prevent repeated report-only or harness-rebuild
loops, and recommend the next goal in the handoff.

For release-adjacent changes, run `qwendex-dev verify --tier full`; use
`qwendex-dev verify --tier release` before making release-readiness claims.
Local Qwen can assist with bounded drafting and inspection, but GPT/Codex review
is required for release, security, architecture, and public claims.

### Connectedness Rule

No Qwendex-facing control, status label, hotkey, wrapper command, config key, or
public doc claim should be visible unless it is connected end to end:

- a canonical state source or config field
- a CLI/API command path that reads or mutates it
- at least one smoke test or receipt proving the behavior
- public or dev docs that name the supported workflow

If a feature is only a mock, placeholder, planned patch, or depends on a custom
Codex build, label that boundary explicitly in docs and status output.

Exploration-performance telemetry is a separate local data plane. Keep its
capture default-off and metadata-only: never route raw prompts, commands,
paths, tool input, tool output, transcripts, credentials, or tokens into its
database or aggregate CLI. Preserve hook decisions, record only after an
accepted event, and run the privacy/benchmark coverage before changing its
schema, hook ingestion, or retention behavior.

### Product Guardrails

- Keep private machine paths, credentials, local logs, transcripts, model
  weights, and host-program installs out of public release artifacts.
- Prefer sample configs and wiring instructions over bundling external hosting
  programs.
- Update `docs/development/decision-log.md` when making durable architecture,
  release, patching, or public/private-boundary decisions.
- Stop and repair before continuing if Local-off routes to Qwen, fresh-home
  checks write to the normal safe-home, receipts fail schema/digest validation,
  or live output contains local-model guard markers or visible tool markup.
  Unexpected active or stale Manager rows are advisory lifecycle state: inspect
  and reconcile them, but do not let them block unrelated operator work.
