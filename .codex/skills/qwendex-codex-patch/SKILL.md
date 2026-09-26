---
name: qwendex-codex-patch
description: Manage Qwendex Codex TUI source patching, preflight, and dev binary builds.
---

# Qwendex Codex Patch

Use this skill for native Codex footer/hotkey integration.

## Workflow

For a version upgrade, preserve unrelated work in the primary worktree and use
a named task branch. Verify the official target tag/commit; when the operator
asks for the latest stable version, check both the official GitHub latest
release and npm `@openai/codex` latest tag before pinning it. Install that
exact stock Codex version side by side. Set `QWENDEX_MAIN_CODEX_BIN` to it.
During source sync, patch, and preflight, also set `QWENDEX_DEV_CODEX_BIN` to
that stock executable: these commands otherwise prefer an existing, older dev
binary and can select its manifest.

1. Update the supported manifest, installer/test pins, and current docs;
   retain historical manifests. Sync a fresh pinned source checkout.
2. Dry-run and apply the patch with the target stock binary. Check moved
   anchors, idempotence, and Rust formatting before compilation. Review new
   upstream API types and launch defaults as well as textual anchors; preserve
   Qdex embedded execution with `--no-daemon` and verify its private controls.
3. Normalize `Cargo.lock` using `cargo metadata --format-version 1` with an
   empty isolated `CARGO_HOME`, as the build does. Pin the official source
   commit, normalized lock SHA-256, and `git diff HEAD --binary --full-index
   --no-ext-diff` SHA-256 in `scripts/qwendex_dev_env`. Verify the sandboxed
   Rusty V8 artifact contract against the target source.
4. Run `qwendex-dev codex-source preflight`, then invoke
   `qwendex-dev codex-source build` once. It builds both `codex` and the matching
   `codex-code-mode-host`. Poll the same live process through observation
   timeouts; do not start a second build without a terminal failure.
5. Clear the temporary `QWENDEX_DEV_CODEX_BIN` override. Validate the build
   receipt and binary pair, run the required verification tiers, then use
   `qwendex-dev sync` to activate a generation for new sessions. Reuse the
   verified pair when updating other Qwendex installations.

The fresh-install acceptance script performs another source build; do not run
it as an incidental upgrade check when the operator requests one build.

Patch only supported Codex source versions. If anchors move or the installed
Codex version is unknown, refresh the Qwendex patch manifest before building.

The patched TUI contract is:

- status item: `qwendex-manager`
- status file env: `QWENDEX_CODEX_STATUS_FILE`
- manager toggle: `qwendex manager mode --toggle --json`
- Kaveman toggle: `qwendex manager kaveman --toggle --json`
- local toggle: `qwendex manager local --toggle --json`

Launch with `qdex`: its stable selector validates and enters the active
runtime generation containing the verified binary pair. The internal dev
runtime uses `.qwendex-dev/codex-build/bin/codex` before activation; ordinary
`codex` remains the independent stock installation.
