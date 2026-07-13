# Verification

Minimum local gates:

```bash
python3 -m py_compile scripts/qwendex_cli.py tests/smoke/test_qwendex_cli.py
python3 -m pytest tests/smoke/test_qwendex_cli.py -q
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex llmstack check --json
scripts/qwendex llmstack restart bridge --dry-run --json
scripts/qwendex eval --json
```

Advisory health is for daily operator visibility and strict health is for
staging, release, and public claims. Advisory mode may keep `check` and
`doctor` passing while surfacing manager warnings and repair hints; strict mode
must fail on those issues. Require strict receipts before making
release-readiness claims.

`scripts/qwendex eval --json` runs the full offline harness suite by default.
Use `--case exact_marker` only for an offline marker fixture; it is not live
model evidence.

The first-class Manager production profiles are separate from the local-Qwen
bridge gates and always require an explicit run ID:

```bash
scripts/qwendex manager accept --profile offline --run-id <run-id> --json
scripts/qwendex manager accept --profile live --run-id <run-id> --json
scripts/qwendex manager accept --profile production --run-id <run-id> --json
```

Offline rejects zero collection and any skip in its required Manager suite,
then runs the synthetic routing corpus, 100 deterministic fault permutations,
migration, security, four-session soak, performance budgets, and privacy scan.
Live runs fresh Medium, Heavy, repeated non-Ultra Manager, five-turn Manager,
Ultra-coexistence, and two-repository concurrent fixtures. Production runs
self-hosting first and adds fresh build/install, v0.5.7 upgrade, shell rollback,
normal-Codex isolation, and sanitized manifest assembly. No profile chooses an
artifact through an ambiguous latest pointer.

Development worktree gates:

```bash
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
qwendex-dev verify --tier release
```

`quick` runs the dev lint and smoke-test path, then records Qwendex `check`,
`doctor`, Codex status writing, and Codex patch preflight receipts under the
dev results tree. `full` adds public JSON syntax plus Draft 2020-12 schema and
version-parity validation, the offline Qwendex eval suite, harness gate, and
local harness eval receipts. `release` uses strict checks with an isolated
release state DB and writes the release summary. `live` runs all three live
acceptance gates below. Set `QWENDEX_RELEASE_REQUIRE_LIVE=1` only when the local
stack is intentionally running and the release summary should require and bind
those gates.

The live gate contracts are:

- `live_launcher`: launcher preflight reaches the configured model and the
  bridge's canonical `/status` endpoint.
- `live_reliability`: the validator parses JSON or SSE assistant output and
  accepts only an exact normalized `QWENDEX_OK`, never prompt echo or a
  substring match.
- `live_codex_acceptance`: a fresh isolated Codex home completes one to three
  bounded successful shell-tool commands producing `TOOL_OK`, emits an exact
  final `TOOL_OK`, leaves a decoy normal home/XDG tree unchanged, and uses the
  exact executable digest and size recorded by the validated Codex build.

Release verification writes per-run evidence under the ignored dev results
tree and invokes `scripts/qwendex_release_gate.py`. The gate hashes current-run
bootstrap, static, test, config, Codex-build, check/doctor, patch/status, eval,
and harness receipts. Each receipt has a run/command/source-bound payload digest;
strict native fields are cross-checked. Publish evidence additionally requires a
trusted configured origin, annotated tag, matching default-branch CI attestation,
and an unchanged source recheck. Publish-ready mode queries the trusted remote
branch and GitHub Actions online, downloads the authoritative artifact, compares
its attestation and artifact-report bytes, and ignores caller-supplied local CI
overrides. The sealed summary has a whole-receipt digest that must be reverified
before publication. That digest detects accidental/post-run content changes; it
is not a signature, so publish verification also replays the local gate hashes,
tracked-tree scan, source/tag state, trusted remote, workflow, and CI artifact
online. The gate scans every tracked release blob,
including binary data, for private/runtime material and scans every evidence
file for guard markers without a size or timeout exception. Candidate mode may
omit publish-only origin/tag/CI requirements, but cannot bypass local evidence
failures or return `publish-ready`.

Harness gates:

```bash
scripts/llm harness-gate --json
scripts/llm harness-eval --all --json
scripts/llm harness-ledger index --json
```

Live gates, only when the local stack is intentionally running:

```bash
qwendex-dev verify --tier live
```

The tier orchestrator invokes `scripts/run_local_qwen_codex.sh --check`,
`validate_local_qwen_reliability.py --require-live-bridge`, and
`validate_local_qwen_codex_acceptance.py` with unique ignored fresh-home,
normal-home-decoy, and final-output paths. Do not replace the fresh-home gate
with a seat-configuration receipt or the offline eval fixtures.

Receipt reads verify supported schemas, release bindings, source identity, and
SHA-256 digests. The matching `CI` artifact proves the actual checkout passed
compile/lint/tests, strict surface checks, tracked-artifact scanning, and a
same-root fresh installation. Eval output includes
a compact case-count metrics summary; pass@k, marker-rate, loop-rate, synthetic
recovery, Qwen handoff, and task-quality dashboards remain future release-gated
work.
