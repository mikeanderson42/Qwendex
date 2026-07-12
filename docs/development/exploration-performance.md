# Exploration Performance Telemetry

## Phase 1 Contract

Qwendex performance telemetry is a local, privacy-minimized attribution
surface. It measures observed exploration behavior; it does not change search
selection, tool output, routing, Manager decisions, or Codex behavior.

Capture is disabled by default. When an operator enables
`performance.capture: "metadata"`, Qwendex writes only to a separate local
SQLite database:

```text
~/.local/state/qwendex/qwendex-performance.sqlite
```

The generated development environment instead resolves the database under its
isolated `.qwendex-dev/state/` directory. `QWENDEX_PERFORMANCE_DB` changes only
that database location; it does not enable capture.
`QWENDEX_PERFORMANCE_CAPTURE=metadata` is the scoped launch-time opt-in for a
development or evaluation run.

This database is intentionally separate from the Manager ledger. High-frequency
measurement, retention, and purge operations must not add write contention,
migration risk, or retention growth to correctness-critical Manager state.

## Schema And Privacy Boundary

The event schema is `qwendex.performance_event.v1`; the aggregate schema is
`qwendex.performance_summary.v1`; the local database schema version is held in
SQLite's `user_version` pragma. Events contain safe metadata such as:

- a random event identifier and locally HMAC-derived run, launch, turn, and
  query identifiers;
- a repository-scope digest, role, phase, event kind, tool family, query class,
  scope class, timestamps, bounded size bucket, and terminal classification;
- optional duration, output-byte count, result count, success, truncation, and
  duplicate-within-run marker.

The hook adapter accepts raw event input only long enough to derive those
values. It does not persist prompts, commands, normalized queries, paths,
tool-input JSON, tool output, stdout, stderr, transcripts, credentials, or
tokens. Query comparison uses an install-local random salt and HMAC-SHA-256;
the query itself is immediately discarded. Output content is counted in memory
and immediately discarded.

Public summary and run commands omit event, run, and launch identifiers. A
blocked hook does not create telemetry; capture runs only after the existing
hook decision passes, and telemetry storage failures never change that decision.

## Producers And Lifecycle

`qdex` creates a fresh opaque `QWENDEX_RUN_ID` for each non-informational
launch. Help and version invocations remain stateless. The run value is HMACed
before it reaches the telemetry database.

The existing native hook evaluator supplies these observations when metadata
capture is enabled:

- `UserPromptSubmit` establishes a run and turn association without retaining
  the prompt;
- `PreToolUse` records an accepted tool start and classifies concrete shell
  commands as search, read, edit, validation, or another safe family;
- `PostToolUse` closes the matching observation and derives duration, output
  bytes, result count, success, and truncation without retaining output;
- `PreCompact`, `PostCompact`, `SubagentStart`, `SubagentStop`, and `Stop`
  record lifecycle and completion state.

A missing `PostToolUse` is later classified as `aborted_or_incomplete`; it is
not treated as corrupted telemetry. `SessionStart` is accepted if a trusted
integration emits it, but the generated managed hook set does not invent a
startup probe. Startup/preflight timing therefore remains `not_observed` until
an observable trusted source supplies it.

The Manager safety classifier remains stricter than telemetry classification.
For example, a shell-capable tool is still treated conservatively by the safety
gate, while a verified read-only `rg` command is attributed as a search event.
Telemetry never grants authority or changes a hook outcome.

## Operator Surface

```bash
scripts/qwendex performance status --json
scripts/qwendex performance summary --json
scripts/qwendex performance summary --repo-root <path> --since-days 7 --json
scripts/qwendex performance runs --limit 20 --json
scripts/qwendex performance purge --approve --json
scripts/qwendex performance benchmark --suite exploration --json
```

`summary` and `runs` default to the canonical current repository scope;
`summary --repo-root` selects a different canonical root explicitly. `summary`
applies the configured retention and maximum-event bounds before returning
deterministic aggregate metrics. `runs` returns numbered, safe run summaries
rather than identifiers. `purge` is explicit and resets the local fingerprint
salt with the deleted data.

The summary reports runs observed, tool calls by family, search/read calls per
run, search-output bytes, duplicate-query rate, root/subagent overlap,
compaction events, time to first edit, startup/preflight duration, validation
duration, telemetry coverage, incomplete-event rate, and instrumentation
overhead. A value with no reliable producer is `not_observed`, never a zero
performance claim.

The benchmark uses a temporary database and synthetic event sequence. It proves
the capture path, scans its own database for raw sentinels, and reports the
instrumentation timing distribution. It is not a claim that search, startup,
validation, model behavior, or end-to-end task performance improved. Its
paired-run wall-time field is deliberately `not_observed` until a separately
designed paired evaluation supplies evidence.

## Boundaries For Later Work

Phase 1 does not add a persistent index, raw transcript parser, network
exporter, search broker, generic tool recorder, or Codex patch. Do not promote
search-output compaction or another optimization from this data until real,
privacy-safe runs demonstrate a bottleneck and a separate task defines paired
quality and performance gates.

The v0.6.0 developer-only paired workflow and its explicitly activated
`search_evidence_compaction_v1` candidate are documented in
[Optimization Lab And Search-Evidence Candidate](optimization-lab.md). The
candidate is default-off, raw search evidence stays under ignored run artifacts,
and a controlled runner does not make a live-model or production-speed claim.
