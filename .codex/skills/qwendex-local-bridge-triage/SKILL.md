---
name: qwendex-local-bridge-triage
description: Triage Qwendex local-Qwen bridge, marker, parser, and receipt failures.
---

# Qwendex Local Bridge Triage

Use this skill for local-Qwen bridge or parser failures.

## Workflow

1. Check the launcher contract:

```bash
scripts/run_local_qwen_codex.sh --check
```

2. Run deterministic harness gates:

```bash
scripts/llm harness-gate --json
scripts/llm harness-eval --all --json
```

3. Inspect marker counts and latest receipts under `.qwendex-dev/results/`.
4. Treat these as bridge/prompt/parser failures first:

- `LOCAL_MODEL_TOOL_CALL_TOO_LARGE`
- `LOCAL_MODEL_TOOL_CALL_TRUNCATED`
- `LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED`
- `LOCAL_MODEL_LOOP_DETECTED`

5. Retry only the smallest failing stage with bounded output.

Do not promote local-Qwen output without receipt review by a GPT/Codex seat.
