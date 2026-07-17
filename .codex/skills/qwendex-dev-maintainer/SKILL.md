---
name: qwendex-dev-maintainer
description: Develop Qwendex from the dedicated dev worktree with scoped edits, verification tiers, and safe staging.
---

# Qwendex Dev Maintainer

Use this skill for normal Qwendex product development in `~/qwendex-dev`.

## Workflow

1. Source the dev env if needed:

```bash
source ~/qwendex-dev/.qwendex-dev/env.sh
```

2. Start with:

```bash
qwendex-dev doctor
qwendex-dev review
```

3. Keep edits scoped to Qwendex harness surfaces.
4. Before making visible Qwendex controls, labels, hotkeys, wrapper commands,
   config keys, or public claims, prove the connectedness chain: state source,
   CLI/API command path, smoke test or receipt, and docs.
5. Run `qwendex-dev verify --tier quick` before staging.
6. Use `qwendex-dev verify --tier full` for shared contracts, evals, routing,
   manager mode, bridge/parser behavior, docs, or release-adjacent changes.
7. Stage through:

```bash
qwendex-dev stage
```

Do not stage `.qwendex-dev/`, private local configs, logs, transcripts, model
weights, or generated caches.

## Stop-The-Line Conditions

Pause and repair before continuing if any of these appear:

- Local-off routing selects Qwen or reports token-saver use.
- Fresh-home isolation writes to the normal safe-home logs/state.
- Receipts fail schema, success, or digest validation.
- Live stdout, stderr, receipts, or transcripts contain guard markers or visible
  tool markup.
- A feature depends on a patched Codex build but docs/status imply it works in
  stock Codex.

Unexpected active or stale Manager sessions are advisory lifecycle state.
Inspect and reconcile them, but do not let them block unrelated operator work.
