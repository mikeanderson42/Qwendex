# Tool Server

The repo-owned `scripts/artifact_queue_mcp.py` server exposes exactly these MCP
tool names:

- `queue_status`
- `queue_next`
- `queue_init`
- `queue_start`
- `queue_done`
- `queue_blocked`
- `document_section_upsert`
- `search_web`
- `local_qwen_run_report`

The six queue tools operate on `TASK_QUEUE.md`. `document_section_upsert`
writes one named Markdown section under a trusted root. `search_web` is a
capped lookup against the configured loopback-only SearXNG origin.
`local_qwen_run_report` builds a bounded report internally from trusted repo
state; it never executes a report script from the target repository.

Qwendex may inject this server for eligible non-local Codex execution policies.
Read-only seats and the minimal local-Qwen launcher intentionally omit it. The
server does not expose Qwendex status, receipt lookup, eval summaries, or
learning summaries.

The public CLI provides a thin direct facade over the six queue handlers:

```bash
scripts/qwendex queue status --dir . --json
scripts/qwendex queue next --dir . --json
scripts/qwendex queue init --dir . --item report.md::"Bounded report" --json
scripts/qwendex queue start --dir . --file report.md --json
scripts/qwendex queue done --dir . --file report.md --json
scripts/qwendex queue blocked --dir . --file report.md --reason "needs input" --json
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
