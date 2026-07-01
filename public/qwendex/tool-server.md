# Tool Server

Qwendex delegates to bounded MCP-backed tools for workflows that local Qwen can
handle reliably. These are internal/eval-covered capabilities; the public CLI
currently exposes them through `eval`, receipts, and the local stack delegates.

Required tool surfaces:

- Queue workflow
- Document section upsert
- Bounded report runner
- Capped local search
- Qwendex status
- Receipt lookup
- Eval summary
- Learning proposal summary

The public CLI also provides a thin local facade over the artifact queue tools:

```bash
scripts/qwendex queue status --dir . --json
scripts/qwendex queue next --dir . --json
```

Tool rules:

- Schema-first inputs
- Trusted-root checks
- Bounded outputs
- Deterministic errors
- Recovery hints
- Explicit stop conditions

Large report generation should be split into sections. Queue tools should keep
one item in progress, verify artifacts before marking them complete, and stop on
blocked items.
