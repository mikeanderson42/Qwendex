# Qwendex Goal Compiler Examples

## Rough Intent: "make manager mode reliable"

Compile a `manager` lane goal. Require current manager status, state-source and
hook boundaries, focused lifecycle tests, a connectedness proof, quick/full
verification as appropriate, and a specific ready or blocked STOP status.

## Pasted Result: Harness Built, Outputs Pending

Use `next_goal_from_result`. Extract the harness path, output files,
branch/commit, dirty paths, and validation. The next goal must consume those
outputs and target the blocker frontier instead of rebuilding the harness.

## Pasted Result: Two Runs Hit The Same Blocker

Prohibit another broad retry. Classify the blocker as source, state, schema,
protocol, validation, artifact, or public/private boundary, then compile a
deterministic repair goal.

## Rough Intent: "publish the next release"

Compile a `release` lane goal. Require exact version-source alignment, full
tracked-artifact scanning, immutable gate receipts bound to a clean default
branch commit and tag, full/release verification, and explicit commit, push,
tag, and GitHub release authority.
