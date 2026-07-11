# Qwendex Failure Modes

Use this file when diagnosing Qwendex, local-Qwen, or patched Codex behavior.

## Critical Patterns

- Local-off routes to Qwen: routing contract failure; block release.
- Receipt schema or digest invalid: provenance failure; block release.
- `LOCAL_MODEL_TOOL_CALL_TOO_LARGE`: prompt/tool envelope too large; shrink the
  command or tool payload before retrying.
- `LOCAL_MODEL_TOOL_CALL_TRUNCATED`: generated command was cut off; rerun the
  smallest stage with bounded output.
- `LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED`: raw tool markup leaked toward the user;
  tighten bridge/parser handling.
- `LOCAL_MODEL_LOOP_DETECTED`: model repeated after artifact creation; stop and
  inspect bridge state before retrying.
- Visible XML/tool-call markup in stdout/transcripts: rendering or bridge
  corruption; block release.
- Dev ledger outside `.qwendex-dev/state`: state isolation failure; block dev
  release verification.
- Codex version unsupported by patch manifest: patched-TUI claim is blocked
  until anchors are refreshed.
- Bare `qdex` resolves to a shell function that injects `-C`: native argv
  parity failure; remove the shadow before diagnosing the installed launcher.
- One immutable Manager launch candidate exists but its root turn is empty:
  `turn_unattached`; bind only through the canonical resolver at the first
  trusted root event, never by newest row or repository.
- A live launch policy hash changes after an Agent Manager toggle: current-
  launch policy drift; hooks must use the preflight-exported effective policy
  and leave the toggle for the next launch.
- Missing or ambiguous Manager attachment blocks Stop: recovery failure; Stop
  must exit without guessed decision or lease mutation.

## Debug Order

1. Run `qwendex-dev doctor`.
2. Run `qwendex-dev verify --tier quick`.
3. Inspect `.qwendex-dev/results/meta/dev_status.json`.
4. For bridge issues, run `scripts/run_local_qwen_codex.sh --check`.
5. For parser/receipt issues, run `scripts/llm harness-gate --json` and
   `scripts/llm harness-eval --all --json`.
6. For Codex TUI issues, run `qwendex-dev codex-source preflight`.

Fix code-gated contracts before changing prompts.
