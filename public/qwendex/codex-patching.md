# Codex TUI Patching

Qwendex can run without patching Codex, but the native footer item and hotkeys
require a small Codex TUI source patch. The installed Codex npm package ships a
stripped native binary, so Qwendex treats the TUI integration as a versioned
source patch instead of mutating the binary in place.

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
anchors, writes only the known TUI/keymap and model-cache-path edits, then
reruns the source preflight state check. For a dry run:

```bash
scripts/qwendex codex-patch apply --source /path/to/codex --dry-run --json
```

Unknown Codex versions or moved anchors block the patch instead of guessing.
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

For the current Codex `0.144.0` target (`rust-v0.144.0`), the patch touches
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
- `codex-rs/models-manager/src/manager.rs`

Inspect the active manifest with:

```bash
scripts/qwendex codex-patch locations --json
```
