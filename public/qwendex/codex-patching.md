# Codex TUI Patching

Qwendex standalone CLI functions, checks, routing, and receipts work with stock
Codex. The supported canonical patch adds native delegation capacity, depth,
wait, root-only management, no recursive child management, and explicitly
read-only child behavior. The installed Codex npm package ships a stripped
native binary, so Qwendex treats the integration as a versioned source patch
instead of mutating the binary in place.

With stock Codex, the standalone Qwendex CLI, checks, routing, receipts, and
offline evals remain supported. Native patched behavior requires the canonical
Linux/Codex `0.144.6` patch, its matching `codex-code-mode-host`, and one
validated runtime generation. Managed hooks remain optional observability.
Unknown versions or anchor drift fail closed for patch/build claims, not for
ordinary root prompts, tools, publication, or final responses.

## Contract

Qwendex owns a stable runtime contract:

- `QWENDEX_CODEX_STATUS_FILE` points at a tiny JSON status file.
- `QWENDEX_MODELS_CACHE_FILE` selects the model-cache filename for the patched
  runtime, allowing each supported Codex build to avoid mixed-version cache
  overwrites while retaining one isolated `CODEX_HOME`.
- `qwendex codex-status --write "$QWENDEX_CODEX_STATUS_FILE" --json` refreshes
  the footer text.
- `qwendex manager mode --toggle --json` cycles Agent Manager duty levels.
- `qwendex manager kaveman --toggle --json` toggles terse Kaveman output mode.
- `qwendex manager local --toggle --json` toggles local intent between
  `Local: [Ready]`/`[Unavailable]` and `Local: [Off]`.
- The Codex status-line item ID is `qwendex-manager`.

The intended footer text is:

```text
{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)
```

The launched Codex process, the toggle commands, and the status file must share
the same `QWENDEX_STATE_DB`. If the footer shows defaults such as `Auto` or
`Kaveman: [N]` after a toggle, run:

```bash
scripts/qwendex codex-status --json
```

The JSON includes `data.state_db` and status-file diagnostics that identify a
stale or cross-environment status file.

## Preflight

Run preflight before opening a patched Codex build:

```bash
scripts/qwendex codex-patch preflight --json
```

To assert a source checkout is already patched, pass the Codex source root:

```bash
scripts/qwendex codex-patch preflight --source /path/to/codex --require-applied --json
```

Preflight detects the installed Codex CLI version, checks it against the Qwendex
patch manifest, and verifies source anchors when a checkout is supplied. If the
Codex version changes, preflight blocks until the manifest is updated for the new
source layout.

## Apply

When the installed Codex version is supported, Qwendex can patch a matching
source checkout:

```bash
scripts/qwendex codex-patch apply --source /path/to/codex --json
```

The apply step is idempotent. It first checks the version manifest and source
anchors, writes only the known TUI, keymap, model-cache, hook-identity, and V2
policy edits, then reruns the source preflight state check. For a dry run:

```bash
scripts/qwendex codex-patch apply --source /path/to/codex --dry-run --json
```

Unknown Codex versions or moved anchors block the patch instead of guessing.
Applying the patch to a fresh pinned checkout must reproduce the canonical
binary full-index Git diff digest after the isolated build normalizes
`Cargo.lock`. Marker presence and equivalent Rust formatting are not
substitutes for that byte-for-byte build boundary.
After applying, rebuild/install Codex from that checkout and run:

```bash
scripts/qwendex codex-patch preflight --source /path/to/codex --require-applied --json
```

The default patched hotkeys are `Alt+M` for Agent Manager, `Alt+K` for Kaveman,
and `Alt+L` for Local routing. They can be rebound through Codex
`[tui.keymap.global]` as `qwendex_toggle_manager`, `qwendex_toggle_kaveman`,
and `qwendex_toggle_local`.

Qwendex does not vendor the external Caveman package. Its Kaveman control is a
small persisted state bit plus directive for terse output. The patched TUI reads
the directive from `QWENDEX_CODEX_STATUS_FILE` and appends it to developer
instructions for thread start, resume, and fork flows. Projects that want the
upstream Git package can install it separately from
<https://github.com/juliusbrussee/caveman>.

## Source Locations

For the current Codex `0.144.6` target (`rust-v0.144.6`), the patch touches
these source areas (the retained `0.143.0` compatibility manifest uses the same
locations):

- `codex-rs/tui/src/bottom_pane/status_line_setup.rs`
- `codex-rs/tui/src/chatwidget/status_surfaces.rs`
- `codex-rs/tui/src/bottom_pane/status_line_style.rs`
- `codex-rs/tui/src/bottom_pane/status_surface_preview.rs`
- `codex-rs/config/src/tui_keymap.rs`
- `codex-rs/tui/src/keymap.rs`
- `codex-rs/tui/src/app/input.rs`
- `codex-rs/tui/src/terminal_visualization_instructions.rs`
- `codex-rs/hooks/src/events/session_start.rs`
- `codex-rs/hooks/src/schema.rs`
- `codex-rs/core/src/hook_runtime.rs`
- `codex-rs/core/src/tools/spec_plan.rs`
- `codex-rs/core/src/config/mod.rs`
- `codex-rs/core/src/config/config_tests.rs`
- `codex-rs/models-manager/src/manager.rs`

Inspect the active manifest with:

```bash
scripts/qwendex codex-patch locations --json
```

The Manager-specific edits add canonical `task_name` and parent identity to
`SubagentStart`, remove collaboration-management tools from child V2 threads,
and let V2 ignore a downstream legacy `agents.max_threads` value while using
its own per-session ceiling. Stock Codex remains a valid Off-mode recovery
binary, but it does not provide those exact Qwendex guarantees.

The canonical development workflow is `qwendex-dev codex-patch apply`, focused
Rust tests, `cargo fmt --check`, `codex-patch preflight --require-applied`, and
`qwendex-dev build-codex --release`. A successful build receipt records the
manifest/source digest and binary SHA-256; changing a required source edit
invalidates that evidence until the patch is reapplied and rebuilt.

Qwendex copies the validated binary pair into each immutable runtime
generation. It never replaces the pair used by an active Qdex process;
activation selects a side-by-side generation for new sessions, and rollback is
available from the sync-installed standard-library recovery command.
