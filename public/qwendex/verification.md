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
Use `--case exact_marker` only for a quick marker probe.

Development worktree gates:

```bash
qwendex-dev verify --tier quick
qwendex-dev verify --tier full
qwendex-dev verify --tier release
```

`quick` runs the dev lint and smoke-test path, then records Qwendex `check`,
`doctor`, Codex status writing, and Codex patch preflight receipts under the
dev results tree. `full` adds public JSON config validation, the offline
Qwendex eval suite, harness gate, and local harness eval receipts. `release`
uses strict checks with an isolated release state DB and writes the release
summary. `live` runs the live launcher check; set
`QWENDEX_RELEASE_REQUIRE_LIVE=1` only when the local stack is intentionally
running and release should include that live check.

Harness gates:

```bash
scripts/llm harness-gate --json
scripts/llm harness-eval --all --json
scripts/llm harness-ledger index --json
```

Live gates, only when the local stack is intentionally running:

```bash
scripts/run_local_qwen_codex.sh --check
python3 scripts/validate_local_qwen_reliability.py --require-live-bridge
```

Receipt reads verify supported schemas and SHA-256 digests. Eval output includes
a compact case-count metrics summary; pass@k, marker-rate, loop-rate, synthetic
recovery, Qwen handoff, and task-quality dashboards remain future release-gated
work.
