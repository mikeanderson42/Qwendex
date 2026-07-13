# Optimization Lab And Search-Evidence Candidate

Qwendex v0.6.0 development includes a reusable, local-only paired-evaluation
lab for testing an optimization without treating smaller output as success.
It is a developer workflow, not a public performance promise or a default
runtime mode.

## Workflow

Freeze and validate a private local workload before candidate behavior changes:

```bash
scripts/qwendex performance lab validate --manifest /ignored/workload.json --json
scripts/qwendex performance lab baseline --manifest /ignored/workload.json --json
```

Run an explicit candidate pair and verify its generated artifacts:

```bash
scripts/qwendex performance lab run \
  --manifest /ignored/workload.json \
  --candidate search_evidence_compaction_v2 \
  --json
scripts/qwendex performance lab compare --run-dir /ignored/paired-run --json
```

The lab requires a frozen full workload with at least 12 tasks: four each for
read-only localization, diagnosis/documentation verification, and bounded
implementation in temporary worktrees. It validates a fixed seed, balanced
baseline-first/candidate-first order, source commit/tree declarations, prompt
digests, private local prompt sources, expected files/regions, validation
commands, write surfaces, and candidate budgets before running anything.

The first four tasks are a pilot. Qwendex stops before the remaining tasks when
the pilot finds a file/region recall regression, a candidate-only task or
validation failure, a freshness/privacy/Manager failure, or more than one
unexplained wall-time ratio above 1.25. This is a valid completed evaluation;
it produces a rejection or invalid-evaluation decision rather than hiding a
bad candidate behind aggregate byte savings.

## Isolation And Claim Boundary

Each baseline/candidate task receives a fresh detached worktree and separate
`CODEX_HOME`, Manager state/ledger, performance database, results root, raw
evidence directory, and fixture write surface. The lab installs verified managed
hooks into that isolated home and runs an isolated Manager preflight before
searching. It also probes read-only write denial and Local-off routing.

The controlled v0.6.0 runner is deliberately a search-evidence runner:
it exercises live `rg`, Qwendex hooks/preflight, telemetry, freshness fixtures,
and deterministic task rubrics, but it does not send a prompt to a live model.
It therefore cannot prove live model adoption, live root-session binding, model
task success, API latency, or a production speedup. Those values are emitted
as `not_observed`, and a controlled-only result cannot promote a candidate.

The separate `live-run` surface accepts only a frozen live-agent manifest and
creates a fresh detached worktree, Codex home, Manager state, performance DB,
and ephemeral conversation for every arm. It preserves raw prompts and events
only in ignored local artifacts; safe receipts contain counts, digests, and
grades. A live timeout or other invalid pair is not a candidate rejection. It
requires a valid rerun/adjudication path and still cannot promote a candidate
without at least 12 valid pairs.

Before any live paired rerun, calibrate the runtime supervisor on a frozen
baseline task that previously reached the old wall-clock boundary:

```bash
scripts/qwendex performance lab calibrate \
  --manifest /ignored/workload.json \
  --auth-source /ignored/auth.json \
  --task-id <frozen-timeout-task> \
  --json
```

Calibration first observes the original single hard wall, then applies a
same-wall progress-aware diagnostic to any precisely classified timeout. It
selects a paired-evaluation policy only when a baseline completes or the
same-wall run shows continuous trusted lifecycle progress. The generated ignored
`05_selected_supervisor_budget_policy.json` is immutable input to the paired
rerun:

```bash
scripts/qwendex performance lab live-run \
  --manifest /ignored/workload.json \
  --candidate search_evidence_compaction_v2 \
  --auth-source /ignored/auth.json \
  --supervisor-policy /ignored/05_selected_supervisor_budget_policy.json \
  --json
```

The supervisor records separate startup/preflight, first-model-activity,
inactivity, hard-wall, graceful-termination, forced-cleanup, and pipe-drain
ceilings. It resets inactivity only on recognized structured lifecycle
transitions, never on raw byte arrival. After the Codex root starts, an
isolated metadata-capture arm may also contribute allowlisted completed
tool/subagent hook lifecycle categories from its own performance database. A
pending tool call, including native `wait_agent`, never extends the inactivity
deadline. For a pending native collaboration wait, the private profile may
record only a fixed timeout bucket; it never retains the numeric input, changes
the selected budget, or treats that observation as progress. The policy is
canonicalized and hashed;
every baseline and candidate arm must record the same identity. A profile is
written only below the ignored live run directory using
`qwendex.live_runtime_profile.v1`. It contains safe phase timestamps, duration
summaries, fixed event counts, process-state/RSS buckets, pipe byte counts,
timeout classification, and sanitized Manager health—never prompt text,
commands, queries, paths from task output, tool content, stdout/stderr,
transcripts, credentials, or tokens.

A progress-aware hard wall is not a speed claim and does not itself validate a
candidate. The pilot remains invalid when a failed arm lacks a precise timeout
classification, cleanup leaves state behind, or candidate timeouts materially
exceed baseline timeouts.

If calibration proves a nonprogress lifecycle blocker before a valid pair can
start, record the held result rather than manufacturing a paired outcome:

```bash
scripts/qwendex performance lab runtime-closeout \
  --prior-run /ignored/prior-closeout \
  --calibration-run /ignored/live-runtime-calibration \
  --validation-summary /ignored/safe-validation-summary.json \
  --json
```

That closeout verifies the prior artifact hashes, preserves the frozen manifest
digest, records only safe timelines/classifications, and emits explicit
not-run pilot/full artifacts. It holds the candidate; it never treats an
incomplete arm as a regression or a success.

## V2 Candidate: `search_evidence_compaction_v2`

The v1 candidate was retained only as historical controlled evidence after its
broad-definition recall failure. V2 repairs that failure with definition-aware,
cross-file coverage and a deterministic retrieval contract. V2 remains
default-off and is currently held for valid live evidence; it is not a
supported opt-in workflow or a performance claim. Nothing changes normal Codex
search behavior and no hook automatically substitutes this command. A scoped
development launch can opt in to one short managed instruction without
persisting the setting:

```bash
QWENDEX_SEARCH_EVIDENCE_COMPACTION=v2 qdex -C <repo>
```

That instruction tells the agent to use compact content search for broad
discovery, retain direct `rg -F` for narrow exact checks, and avoid repeated
unchanged broad searches. The registry records its byte cost; the controlled
runner reports that cost but does not claim it was delivered to a live model.
An evaluation can also select the candidate, and an operator can explicitly
invoke one of these experimental commands:

```bash
scripts/qwendex search content <pattern> --root <repo-or-subtree> --literal --json
scripts/qwendex search content <pattern> --root <repo-or-subtree> --regex --json
scripts/qwendex search content <pattern> --root <repo-or-subtree> --regex --candidate v2 --json
scripts/qwendex search next <pattern> --root <repo-or-subtree> --regex --candidate v2 --cursor <cursor> --json
scripts/qwendex search paths <pattern> --root <repo-or-subtree> --json
```

Content search uses live ripgrep against current Git-enumerated worktree files.
It includes modified tracked, untracked, and tracked hidden files; it preserves
normal ignore behavior unless `--include-ignored` is requested explicitly.
An ignored override remains bounded by the file budget. Qwendex does not accept
a home-directory root, requires a Git worktree/subtree, and excludes a symlink
whose resolved target escapes that repository. Internal symlinks are searched
as current worktree content. It does not create a persistent index or cache
search content.

The v2 compact form groups matches by file, deduplicates identical line matches,
adds two lines of context, merges nearby ranges deterministically, and caps a
merged range at 24 lines. It prioritizes likely definitions, establishes a
per-file coverage floor, and round-robins across files before allowing a
definition-dense file to consume additional evidence slots. Per-file, file,
total, and page budgets are explicit.
The model-facing form is stable:

```text
path:start-end — match-class
```

Results report retained/omitted ranges and files, raw/compact bytes, compression
ratio, candidate duration, binary handling, truncation, and a stable,
snapshot-bound continuation cursor. A response explicitly says whether it is
`complete`, `partial_requires_next_cursor`, or a conservative
`baseline_fallback`; Qwendex never silently middle-truncates ranked evidence.

Use direct `rg -F` for a narrow exact check. Use compact search for broad
discovery that would otherwise return substantial evidence, begin at a known
repository or subtree, then stop broad exploration once ownership,
implementation, tests, and validation are found. Do not routinely search a
home directory, use `rg -uuu`, loop a subprocess per file, or repeat unchanged
queries. Duplicate detection can inform the evaluation but never returns cached
content in this release.

## Privacy And Raw Evidence

The separate performance SQLite database stays metadata-only. It receives HMAC
query correlation and safe count/timing fields, never raw prompts, commands,
paths, source snippets, tool input/output, transcripts, credentials, or tokens.
The lab scans the isolated performance databases and sanitized aggregate
artifacts for that boundary.

Complete raw ripgrep results may contain relative paths and snippets. The lab
writes them only below the ignored paired-run `raw/` directory with a schema,
candidate/version, repository digest, pair association, query fingerprint,
SHA-256, creation time, and retention boundary. Raw evidence is excluded from
the metadata DB, aggregate CLI output, tracked files, public documentation, and
release artifacts.

## Gate And Artifact Contract

Hard gates cover non-inferior relevant-file and region recall, task/validation
outcomes, modified/untracked visibility, symlink/repository boundaries,
privacy, Manager/Local safety, default-off behavior, schema validity, and raw
artifact hashes. A hard failure rejects the candidate regardless of byte
reduction.

Performance gates require 12 valid live pairs for an opt-in promotion decision, a
median search-evidence reduction of at least 70%, broad-search adoption of at
least 80%, no material call/wall-time regression, telemetry p95 below 5 ms,
and honest `not_observed` values. The aggregate decision is exactly one of
`promote_opt_in_experimental`, `hold_for_more_evidence`,
`reject_candidate`, or `invalid_evaluation`. Promotion never enables the
candidate by default.

Each ignored paired run contains scope/custody, Phase 1 baseline commit,
environment lock, workload digest, candidate registry, baseline/candidate
JSONL, pair CSV, quality/freshness/privacy/Manager results, performance summary,
gate decision, angle check, next goal, final report, raw artifacts, and a
digest manifest. `compare` validates required artifacts, JSONL/CSV row counts,
CSV width, and every companion hash.

For a live baseline-pass/candidate-fail or baseline-fail/candidate-pass pair,
the lab preserves both initial receipts and reruns each arm once under the same
frozen contract when the budget permits. Only a reproduced candidate-only
failure, or direct private evidence tying the failure to search evidence, can
be treated as a candidate correctness regression. Incomplete live samples are
held rather than converted into a speed or support claim.
