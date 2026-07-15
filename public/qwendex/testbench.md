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

By default the project folder is the current working directory. Override it
with:

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
>_ OpenAI Codex (v0.144.4) /w Qwendex
```

The banner also prints the Qwendex mode, selected model, project folder,
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
Its working directory and trusted roots are limited to the selected project and
the isolated bench root. The Qwendex source checkout supplies executables and
configuration, but it is not added as a writable or MCP-trusted root unless it
is itself the selected project.

Normal Qdex launches also allocate a per-launch metadata directory under the
configured metadata root. Status and preflight JSON files are therefore not
shared by concurrent launches. Manager capacity, locks, and worker ledgers are
keyed by canonical repository root, so two repositories can hold independent
live sessions without consuming one another's lanes.

## Manager Delegation Acceptance

The Manager runtime acceptance matrix covers Lite direct work, Medium bounded
mapping, Heavy edit plus post-edit verification, full Manager root-only
integration, Ultra/native proactive coexistence, immutable policy drift, and
two simultaneous repository sessions. Each non-Off launch must prove prompt
admission, a sealed launch policy, deterministic plan data, final worker
reports, validation evidence, and a closed Stop status.

Run the repository smoke suite and inspect the durable acceptance bundle:

```bash
python3 -m pytest -q tests/smoke/test_qdex_delegation_policy.py tests/smoke/test_qdex_manager_attachment.py
scripts/qwendex manager status --json
scripts/qwendex manager accept --profile offline --run-id <run-id> --json
qwendex-dev verify --tier full
```

Development acceptance artifacts live below the ignored results root at
`qwendex/manager-delegation/`. `validation_summary.json` links the mode,
prompt-admission, policy-drift, native-capability, dual-session, and status
schema receipts. The current production bundle lives under
`manager-production/<run-id>/`; live receipt rows identify workers by exact
native ID and sanitized repository alias. Raw prompts, worker transcripts,
credentials, and local databases remain ignored and are not copied into the
tracked validation summary.

## Notes

When the installed Codex version changes, rerun `scripts/qwendex codex-patch
preflight --json` before opening the bench. Unknown versions or moved source
anchors block the patched-TUI claim until the Qwendex patch manifest is
refreshed for the new Codex source layout.
