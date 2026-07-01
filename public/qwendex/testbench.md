# Qwendex Test Bench

The Qwendex test bench stages a visible local sandbox for trying Qwendex against
a real project folder. It keeps Qwendex state, receipts, bench metadata, and
Codex home files outside the project under an isolated bench root.

```bash
scripts/qwendex_testbench init
scripts/qwendex_testbench codex-preflight
scripts/qwendex_testbench status
scripts/qwendex_testbench tmux
```

By default the project folder is `$HOME/thehub` when that folder exists,
otherwise the current working directory. Override it with:

```bash
QWENDEX_BENCH_PROJECT=/path/to/project scripts/qwendex_testbench tmux
```

The default bench root is `$HOME/qwendex_testbench/current`. Override it with:

```bash
QWENDEX_BENCH_ROOT=/tmp/qwendex-bench scripts/qwendex_testbench tmux
```

## Visible Interface

The tmux session contains:

- `bench`: Qwendex console for status, route, smoke, receipt, and MCP checks.
- `qwendex-local`: Codex launched through the local Qwen bridge.
- `qwendex-full`: Codex launched through the full Codex model path.
- `receipts`: latest Qwendex receipt view.

The Codex TUI header itself is owned by Codex. Unpatched Codex builds get a
visible launch banner above the TUI and run without the alternate screen:

```text
>_ OpenAI Codex (v0.142.4) /w Qwendex
```

The banner also prints the Qwendex mode, selected model path, project folder,
Qwendex root, state DB, and receipt root.

Patched Codex TUI builds can add `qwendex-manager` to `[tui].status_line` and
bind Qwendex toggle actions through the Codex keymap. The bench exports
`QWENDEX_CODEX_STATUS_FILE` and refreshes it with:

```bash
scripts/qwendex codex-status --write "$QWENDEX_CODEX_STATUS_FILE" --json
scripts/qwendex codex-patch preflight --json
```

To patch a matching Codex source checkout, run:

```bash
scripts/qwendex codex-patch apply --source /path/to/codex --json
```

The patched keymap defaults are `Alt+M` for Agent Manager, `Alt+K` for Kaveman,
and `Alt+L` for Local routing.

## Commands

```bash
scripts/qwendex_testbench console
scripts/qwendex_testbench codex-preflight
scripts/qwendex_testbench status
scripts/qwendex_testbench route exec
scripts/qwendex_testbench smoke-local
scripts/qwendex_testbench smoke-full-dry-run
scripts/qwendex_testbench receipt latest
scripts/qwendex_testbench open-local
scripts/qwendex_testbench open-full
scripts/qwendex_testbench mcp
```

`smoke-local` runs a live local-Qwen Qwendex exec against the project folder and
verifies the latest receipt. `smoke-full-dry-run` verifies the full-model command
shape without spending a remote model call.

## Isolation

The bench sets:

- `QWENDEX_STATE_DB`
- `QWENDEX_RESULTS_ROOT`
- `QWENDEX_CODEX_STATUS_FILE`
- `QWENDEX_EXEC_CWD`
- `QWENDEX_MCP_TRUSTED_ROOTS`
- `CODEX_HOME`
- `LOCAL_QWEN_CODEX_ADD_DIRS`
- `LOCAL_QWEN_LOCAL_HARNESS_MCP`

The local-harness MCP is overridden on the Codex command line so a project-local
`.codex/config.toml` cannot silently route the bench back to another repo.

## Notes

When the installed Codex version changes, rerun `scripts/qwendex codex-patch
preflight --json` before opening the bench. Unknown versions or moved source
anchors block the patched-TUI claim until the Qwendex patch manifest is
refreshed for the new Codex source layout.
