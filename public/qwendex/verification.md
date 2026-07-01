# Verification

Minimum local gates:

```bash
python3 -m py_compile scripts/qwendex_cli.py tests/smoke/test_qwendex_cli.py
python3 -m pytest tests/smoke/test_qwendex_cli.py -q
scripts/qwendex check --json
scripts/qwendex doctor --json
scripts/qwendex eval --json
```

`scripts/qwendex eval --json` runs the full offline harness suite by default.
Use `--case exact_marker` only for a quick marker probe.

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
