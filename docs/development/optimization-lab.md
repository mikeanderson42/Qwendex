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
  --candidate search_evidence_compaction_v1 \
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

The current v0.6.0 runner is deliberately a controlled search-evidence runner:
it exercises live `rg`, Qwendex hooks/preflight, telemetry, freshness fixtures,
and deterministic task rubrics, but it does not send a prompt to a live model.
It therefore cannot prove live model adoption, live root-session binding, model
task success, API latency, or a production speedup. Those values are emitted
as `not_observed`, and a controlled-only result cannot promote a candidate.

## First Candidate: `search_evidence_compaction_v1`

The candidate remains default-off. Nothing changes normal Codex search behavior
and no hook automatically substitutes this command. A scoped Qdex launch can
opt in to one short managed instruction without persisting the setting:

```bash
QWENDEX_SEARCH_EVIDENCE_COMPACTION=1 qdex -C <repo>
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

The compact form groups matches by file, deduplicates identical line matches,
adds two lines of context, merges nearby ranges deterministically, and caps a
merged range at 24 lines. It ranks likely definitions before exact identifiers
and literals, then regex matches. For equally ranked broad matches it spreads
retained ranges through a file before allowing a definition-dense file to take
additional evidence slots. Per-file, file, total, and page budgets are explicit.
The model-facing form is stable:

```text
path:start-end — match-class
```

Results report retained/omitted ranges and files, raw/compact bytes, compression
ratio, candidate duration, binary handling, truncation, and a stable
continuation token. Qwendex never silently middle-truncates ranked evidence.

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

Performance gates require 12 valid pairs for an opt-in promotion decision, a
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
