import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_script_module(name):
    module_path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_llm_stack_parser_exposes_harness_eval_gate_and_skillopt():
    stack = load_script_module("local_llm_stack")
    parser = stack.command_line()

    eval_args = parser.parse_args(["harness-eval", "--case", "exact_marker", "--json"])
    gate_args = parser.parse_args(["harness-gate", "--json"])
    skillopt_args = parser.parse_args(["skillopt", "dry-run", "--json"])

    assert eval_args.command == "harness-eval"
    assert eval_args.case == "exact_marker"
    assert gate_args.command == "harness-gate"
    assert skillopt_args.command == "skillopt"
    assert skillopt_args.action == "dry-run"


def test_offline_harness_eval_writes_schema_receipt_and_indexes_ledger(tmp_path):
    eval_module = load_script_module("local_qwen_harness_eval")
    ledger = load_script_module("local_qwen_harness_ledger")
    results_root = tmp_path / "results"
    db_path = tmp_path / "ledger.sqlite"

    result = eval_module.run_harness_eval(
        repo_root=ROOT,
        results_root=results_root,
        ledger_db_path=db_path,
        case_id="exact_marker",
        run_all=False,
        live=False,
    )
    receipt_path = Path(result["receipts"][0])
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    summary = ledger.ledger_summary(db_path)

    assert result["success"] is True
    assert receipt["case_id"] == "exact_marker"
    assert receipt["functional_status"] == "pass"
    assert receipt["drift_status"] == "pass"
    assert receipt["success"] is True
    assert receipt["artifact_paths"] == []
    assert receipt["sha256"]
    assert summary["counts"]["artifact_observations"] == 1


def test_harness_eval_receipt_schema_rejects_missing_required_fields():
    eval_module = load_script_module("local_qwen_harness_eval")

    failures = eval_module.validate_eval_receipt({"case_id": "exact_marker"})

    assert "missing schema_version" in failures
    assert "missing run_id" in failures
    assert "missing drift_status" in failures


def test_harness_eval_includes_required_codex_v2_cases(tmp_path):
    eval_module = load_script_module("local_qwen_harness_eval")
    required_cases = {
        "mcp_queue_workflow",
        "hook_audit_output",
        "fresh_home_ab_probe",
        "bridge_v2_package_contract",
    }

    assert required_cases <= set(eval_module.CASES)

    result = eval_module.run_harness_eval(
        repo_root=ROOT,
        results_root=tmp_path / "results",
        case_id="hook_audit_output",
        run_all=False,
        live=False,
    )

    assert result["success"] is True
    receipt = json.loads(Path(result["receipts"][0]).read_text(encoding="utf-8"))
    assert receipt["case_id"] == "hook_audit_output"
    assert receipt["failure_marker"] == ""


def test_harness_gate_classifies_scope_and_blocks_mixed_staged_research():
    gate = load_script_module("local_qwen_harness_gate")

    assert gate.classify_path(Path("scripts/local_qwen_runtime_guard.py")) == "harness_core"
    assert gate.classify_path(Path("scripts/qwendex_cli.py")) == "harness_core"
    assert gate.classify_path(Path("config/qwendex/qwendex.json")) == "harness_core"
    assert gate.classify_path(Path("docs/generated/local_llm_stack/LOCAL_QWEN_NOTE.md")) == "harness_docs"
    assert gate.classify_path(Path("public/qwendex/README.md")) == "harness_docs"
    assert gate.classify_path(Path("tests/smoke/test_qwendex_cli.py")) == "harness_tests"
    assert gate.classify_path(Path("results/local_qwen_harness_hardening/run/receipt.json")) == "local_stack_receipt"
    assert gate.classify_path(Path("state/research/test_queue.csv")) == "research_surface"
    assert gate.classify_path(Path("random.tmp")) == "unknown"

    result = gate.evaluate_scope(
        staged_paths=[
            Path("scripts/local_qwen_runtime_guard.py"),
            Path("state/research/test_queue.csv"),
        ],
        dirty_paths=[],
    )

    assert result["status"] == "fail"
    assert "research_surface" in result["blocking_categories"]


def test_skillopt_wrapper_defaults_to_mock_and_refuses_casual_codex_budget(tmp_path):
    skillopt = load_script_module("local_qwen_skillopt_wrapper")

    dry_run = skillopt.build_skillopt_command("dry-run", project=tmp_path, backend="")
    refusal = skillopt.run_skillopt_action(
        "run",
        project=tmp_path,
        backend="codex",
        json_output=True,
        allow_codex_budget=False,
        execute=False,
    )

    assert dry_run[-2:] == ["--backend", "mock"]
    assert refusal["status"] == "blocked"
    assert "requires --allow-codex-budget" in refusal["message"]
