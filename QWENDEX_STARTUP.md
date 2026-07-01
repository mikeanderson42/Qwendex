# Qwendex Startup

## Hard Restrictions

- Use bounded commands and compact reads.
- Do not emit raw tool-call markup as prose.
- Stop on `LOCAL_MODEL_TOOL_CALL_TOO_LARGE`,
  `LOCAL_MODEL_TOOL_CALL_TRUNCATED`, `LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED`, or
  `LOCAL_MODEL_LOOP_DETECTED`.
- Local Qwen can draft, inspect, summarize, and run bounded commands, but release
  acceptance, public claims, security, and architecture decisions require GPT
  review.

## Context Discipline

Read only the files named by the task or the smallest files needed to verify the
current claim. Prefer Qwendex receipts and JSON summaries over long transcripts.
After orientation, answer `STARTUP_READ_COMPACT_OK`.
