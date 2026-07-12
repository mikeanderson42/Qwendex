# Qwendex Startup

## Hard Restrictions

- Use bounded commands and compact reads.
- Do not emit raw tool-call markup as prose.
- Stop on `LOCAL_MODEL_TOOL_CALL_TOO_LARGE`,
  `LOCAL_MODEL_TOOL_CALL_TRUNCATED`, `LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED`, or
  `LOCAL_MODEL_LOOP_DETECTED`.
- Local Qwen can draft, inspect, summarize, and run bounded commands, but release
  acceptance, public claims, security, and architecture decisions require GPT
  review.

## Context Discipline

Read only the files named by the task or the smallest files needed to verify the
current claim. Prefer Qwendex receipts and JSON summaries over long transcripts.
After orientation, answer `STARTUP_READ_COMPACT_OK`.

For Qwendex goal/prompt generation, "the goal skill", or a pasted Codex
closeout that needs the next goal, use
`.codex/skills/operator-goal-compiler/`.

## Dev Worktree

For Qwendex product development, use `~/qwendex-dev` and start with:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
qwendex-dev status-json
qwendex-dev doctor
qwendex-dev review
```

Bare `qwendex-dev` opens Codex in `~/qwendex-dev` with the current Codex
yolo-equivalent flag, `--dangerously-bypass-approvals-and-sandbox`. Use the
named `status-json`, `doctor`, and `review` commands for posture checks.

Use `qwendex-dev verify --tier quick` before staging and
`qwendex-dev verify --tier release` before release-readiness claims.

## Exploration Telemetry

Exploration-performance capture is a separate, local, default-off metadata
store. Its aggregates are attribution evidence, not a performance-improvement
claim. Before changing it, read
`docs/development/exploration-performance.md`; preserve the no-raw-content
boundary and run the privacy and benchmark coverage with the hook regressions.

## Connectedness Check

Before adding or advertising a Qwendex control, status label, hotkey, wrapper
command, config key, or public workflow, verify it has a real state source, a
CLI/API command path, a smoke test or receipt, and docs. If it depends on a
patched Codex build or is only a placeholder, label that boundary plainly.

Stop and repair if Local-off routes to Qwen, fresh-home isolation touches the
normal safe-home, receipts fail schema/digest checks, manager sessions are
unexpectedly active or stale, or live output shows guard markers or tool markup.
