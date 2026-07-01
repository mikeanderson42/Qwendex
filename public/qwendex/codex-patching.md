# Codex TUI Patching

Qwendex can run without patching Codex, but the native footer item and hotkeys
require a small Codex TUI source patch. The installed Codex npm package ships a
stripped native binary, so Qwendex treats the TUI integration as a versioned
source patch instead of mutating the binary in place.

## Contract

Qwendex owns a stable runtime contract:

- `QWENDEX_CODEX_STATUS_FILE` points at a tiny JSON status file.
- `qwendex codex-status --write "$QWENDEX_CODEX_STATUS_FILE" --json` refreshes
  the footer text.
- `qwendex manager mode --toggle --json` toggles Manager Mode versus Auto.
- `qwendex manager local --toggle --json` toggles Local `[Y]` versus `[N]`.
- The Codex status-line item ID is `qwendex-manager`.

The intended footer text is:

```text
Qwendex manager: [Manager Mode], Local [Y]
```

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
anchors, writes only the known TUI/keymap edits, then reruns the source preflight
state check. For a dry run:

```bash
scripts/qwendex codex-patch apply --source /path/to/codex --dry-run --json
```

Unknown Codex versions or moved anchors block the patch instead of guessing.
After applying, rebuild/install Codex from that checkout and run:

```bash
scripts/qwendex codex-patch preflight --source /path/to/codex --require-applied --json
```

The default patched hotkeys are `Alt-M` for Manager Mode and `Alt-L` for Local
routing. They can be rebound through Codex `[tui.keymap.global]` as
`qwendex_toggle_manager` and `qwendex_toggle_local`.

## Source Locations

For Codex `0.142.4` (`rust-v0.142.4`), the patch touches these source areas:

- `codex-rs/tui/src/bottom_pane/status_line_setup.rs`
- `codex-rs/tui/src/chatwidget/status_surfaces.rs`
- `codex-rs/tui/src/bottom_pane/status_line_style.rs`
- `codex-rs/tui/src/bottom_pane/status_surface_preview.rs`
- `codex-rs/config/src/tui_keymap.rs`
- `codex-rs/tui/src/keymap.rs`
- `codex-rs/tui/src/app/input.rs`

Inspect the active manifest with:

```bash
scripts/qwendex codex-patch locations --json
```
