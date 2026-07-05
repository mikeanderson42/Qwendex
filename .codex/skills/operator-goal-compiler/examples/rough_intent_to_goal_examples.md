# Operator Goal Compiler Examples

## Rough Intent: "clean up qmsg"

Compile as `qmsg_bridge`, not a broad cleanup. Require Jarvis owner docs,
current branch/status, no service renames, compatibility for `qmsg`,
`qmsg-tmux`, `qmsg-watchdog`, Qmsg units, and `qwendex-phone:0.0`, focused Qmsg
tests, `qmsg status --json`, `qmsg-tmux status`, and a compatibility receipt.

STOP status should be `qmsg_bridge_ready` or a specific blocked status, never a
generic "done".

## Pasted Result: Harness Built, Outputs Pending

Use `next_goal_from_result`. Extract the harness path, generated output files,
branch/commit, dirty paths, and validation. The next goal must consume harness
outputs and target the blocker frontier. Do not rebuild the harness.

## Pasted Result: Two Machine-Blocked Runs

Use `next_goal_from_result`. Prohibit broad retries. Classify whether the
blocker is source, schema, split, uncertainty, leakage, lineage, policy, or
application boundary. Compile a deterministic repair or source-contract goal.

## Rough Intent: "make Clippy look right"

Compile as `clippy_visual`. Require canonical geometry/source docs, visual-only
scope, rendered proof assets, focused visual tests, and
`human_review_required` when subjective approval remains. Do not include Qmsg,
DeskAgent package cleanup, or live voice gates.

## Rough Intent: "make verify green"

Compile as `doctor_verifier` unless the user explicitly asks for live
acceptance. Require classified failures, package failures, deferred live gates,
focused doctor/verifier tests, and command outputs. Do not mark deferred live
gates as package failures.

## Rough Intent: "update project instructions"

Compile as `git_docs_context`. Require both repo statuses, tracked docs/skills
updates, ignored private context notes, `git diff --check`, Qwendex quick
verification if Qwendex skill/docs changed, scoped commits, and a remaining
dirty-state handoff.
