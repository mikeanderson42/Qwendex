---
name: qwendex-codex-patch
description: Manage Qwendex Codex TUI source patching, preflight, and dev binary builds.
---

# Qwendex Codex Patch

Use this skill for native Codex footer/hotkey integration.

## Workflow

```bash
qwendex-dev codex-source sync
qwendex-dev codex-source patch
qwendex-dev codex-source preflight
qwendex-dev codex-source build
```

Patch only supported Codex source versions. If anchors move or the installed
Codex version is unknown, refresh the Qwendex patch manifest before building.

The patched TUI contract is:

- status item: `qwendex-manager`
- status file env: `QWENDEX_CODEX_STATUS_FILE`
- manager toggle: `qwendex manager mode --toggle --json`
- local toggle: `qwendex manager local --toggle --json`

The dev `codex` wrapper uses the built binary from
`.qwendex-dev/codex-build/bin/codex` before falling back to main Codex.
