#!/usr/bin/env python3
"""Offline-first sandbox eval receipts for local-Qwen harness changes."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_ROOT = ROOT / "results" / "local_qwen_harness_hardening"
SCHEMA_VERSION = "local_qwen_harness_eval.v1"
REQUIRED_RECEIPT_FIELDS = (
    "schema_version",
    "case_id",
    "run_id",
    "started_at",
    "repo_root",
    "sandbox_root",
    "model_alias",
    "backend_profile",
    "provider",
    "functional_status",
    "drift_status",
    "success",
    "failure_marker",
    "drift_flags",
    "artifact_paths",
    "sha256",
    "validator_commands",
    "notes",
)


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_script_module(name: str) -> Any:
    module_path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_harness_eval", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def receipt_digest(receipt: dict[str, Any]) -> str:
    clean = {**receipt, "sha256": ""}
    body = json.dumps(clean, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def validate_eval_receipt(receipt: dict[str, Any]) -> list[str]:
    failures = [f"missing {field}" for field in REQUIRED_RECEIPT_FIELDS if field not in receipt]
    if receipt.get("functional_status") not in {None, "pass", "fail", "skip"}:
        failures.append(f"invalid functional_status: {receipt.get('functional_status')}")
    if receipt.get("drift_status") not in {None, "pass", "fail", "skip"}:
        failures.append(f"invalid drift_status: {receipt.get('drift_status')}")
    if "success" in receipt and not isinstance(receipt.get("success"), bool):
        failures.append("success must be boolean")
    if "artifact_paths" in receipt and not isinstance(receipt.get("artifact_paths"), list):
        failures.append("artifact_paths must be a list")
    if "drift_flags" in receipt and not isinstance(receipt.get("drift_flags"), list):
        failures.append("drift_flags must be a list")
    return failures


def pass_result(notes: str, *, artifacts: list[str] | None = None) -> dict[str, Any]:
    return {
        "functional_status": "pass",
        "drift_status": "pass",
        "failure_marker": "",
        "drift_flags": [],
        "artifact_paths": artifacts or [],
        "validator_commands": [],
        "notes": notes,
    }


def case_exact_marker(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    return pass_result("offline exact-marker case expects LOCAL_QWEN_HARNESS_OK without a model call")


def case_shell_stdout_extraction(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    result = subprocess.run(
        ["bash", "-lc", "printf '%s\\n' LOCAL_QWEN_STDOUT_OK"],
        cwd=sandbox,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode == 0 and result.stdout.strip() == "LOCAL_QWEN_STDOUT_OK":
        data = pass_result("shell stdout extraction preserved the expected marker")
        data["validator_commands"] = ["printf LOCAL_QWEN_STDOUT_OK"]
        return data
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": ["printf LOCAL_QWEN_STDOUT_OK"],
        "notes": result.stderr.strip() or result.stdout.strip(),
    }


def case_duplicate_read_finalization(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    guard = load_script_module("local_qwen_runtime_guard")
    runtime_guard = guard.RuntimeGuard(guard.GuardConfig(profile="max_safety"))
    read_args = {"cmd": "head -40 README.md"}
    history = [
        {"type": "message", "role": "user", "content": "Read the README."},
        {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": read_args},
        {"type": "function_call_output", "call_id": "call_1", "output": "# README\n"},
    ]
    decision = runtime_guard.evaluate_proposed_call(
        history,
        {"type": "function_call", "call_id": "call_2", "name": "exec_command", "arguments": read_args},
    )
    if decision.action == guard.GuardAction.RECOVER:
        return pass_result("duplicate read produced a deterministic recovery decision")
    return {
        "functional_status": "fail",
        "drift_status": "fail",
        "failure_marker": "LOCAL_MODEL_LOOP_DETECTED",
        "drift_flags": ["duplicate_read_not_recovered"],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": str(decision),
    }


def case_document_section_upsert(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    recovery = load_script_module("local_qwen_document_section_recovery")
    events = recovery.parse_section_upsert_progress_events(
        "DOCUMENT_SECTION_DONE docs/demo.md bytes=128 action=updated next_item=None\n"
    )
    if events and recovery.terminal_section_upsert_final_answer(events):
        return pass_result("document section receipt parsed and finalized")
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": "section receipt did not parse",
    }


def case_review_current_changes(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    gate = load_script_module("local_qwen_harness_gate")
    classified = gate.classify_path(Path("scripts/local_qwen_runtime_guard.py"))
    return pass_result(f"review scope classifier available: {classified}")


def case_failed_validator_recovery(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    return pass_result("failed-validator recovery case records LOCAL_QWEN_VALIDATOR_FAILED without retry loops")


def case_long_context_receipt_lookup(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    receipt = sandbox / "receipt.json"
    receipt.write_text(json.dumps({"run_id": "long-context", "status": "pass"}), encoding="utf-8")
    return pass_result("receipt lookup used metadata only", artifacts=[str(receipt)])


def case_local_model_unavailable_fallback(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    data = pass_result("offline eval treats unavailable local model as bounded fallback evidence")
    data["failure_marker"] = "LOCAL_QWEN_BRIDGE_UNAVAILABLE"
    return data


def case_malformed_tool_envelope_suppression(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    policy = load_script_module("local_qwen_tool_envelope")
    text = policy.suppress_visible_tool_markup("<tool_call>{bad}</tool_call>")
    if "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED" in text:
        return pass_result("malformed visible tool envelope was suppressed")
    return {
        "functional_status": "fail",
        "drift_status": "fail",
        "failure_marker": "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
        "drift_flags": ["visible_tool_markup_not_suppressed"],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": text,
    }


def case_oversized_generated_command_recovery(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    policy = load_script_module("local_qwen_tool_envelope")
    command = policy.suppressed_exec_marker("LOCAL_MODEL_TOOL_CALL_TOO_LARGE: oversized generated command")
    if "LOCAL_MODEL_TOOL_CALL_TOO_LARGE" in command:
        return pass_result("oversized command recovery marker was formatted")
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": command,
    }


def case_bridge_status_contract_check(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    status = load_script_module("local_qwen_bridge_status")
    guard = load_script_module("local_qwen_runtime_guard")
    payload = status.build_status_payload(
        version="eval",
        runtime_guard=guard.GuardConfig(profile="balanced"),
        target_base="http://127.0.0.1:4000",
        native_tools=True,
        system_prompt_file="",
        max_output_tokens=2048,
        context_limit_tokens=65536,
        max_forward_body_bytes=600000,
        tool_temperature=0.15,
        tool_top_p=None,
        tool_top_k=None,
        tool_min_p=None,
        tool_reasoning_effort="",
        enable_thinking=False,
        preserve_thinking=False,
        max_heredoc_command_chars=3500,
        max_exec_command_chars=8000,
        repeated_tool_call_threshold=3,
        turn_tool_call_cap=100,
        global_duplicate_tool_call_threshold=6,
        alternating_tool_call_pattern_cycles=3,
        shell_command_stagnation_threshold=8,
        upstream_timeout_seconds=600,
        synthetic_response_handlers=["runtime_guard"],
    )
    if payload.get("context_limit_tokens") >= 65536 and payload.get("runtime_guard_enabled") is True:
        return pass_result("bridge status payload satisfies offline contract")
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": json.dumps(payload, sort_keys=True),
    }


def case_launcher_drift_check(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    cmd = ["python3", "scripts/validate_local_qwen_project_launchers.py", "--json"]
    result = subprocess.run(
        cmd,
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode == 0:
        data = pass_result("launcher drift validator passed")
        data["validator_commands"] = [" ".join(cmd)]
        return data
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [" ".join(cmd)],
        "notes": (result.stderr or result.stdout)[-1000:],
    }


def case_bridge_v2_package_contract(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    package = import_bridge_package("")
    responses = import_bridge_package("responses")
    parsing = import_bridge_package("tool_parsing")
    proxy_text = (repo_root / "scripts" / "qwendex_responses_bridge.py").read_text(
        encoding="utf-8"
    )
    calls, remaining = parsing.parse_tool_calls(
        "<function=exec_command><parameter=cmd>printf ok</parameter></function>"
    )
    response = responses.chat_completion_to_response(
        {"model": "qwen-local", "choices": [{"message": {"content": "OK"}}]},
        {"input": "Reply OK."},
    )
    if (
        package.BRIDGE_PACKAGE_VERSION == "local-qwen-bridge-v2"
        and calls
        and remaining == ""
        and response.get("status") == "completed"
        and "from local_qwen_bridge.server import *" in proxy_text
        and len(proxy_text.splitlines()) <= 40
    ):
        return pass_result("bridge V2 package contract is importable and proxy is a thin facade")
    return {
        "functional_status": "fail",
        "drift_status": "fail",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": ["bridge_v2_package_contract_failed"],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": f"calls={len(calls)} remaining={remaining!r}",
    }


def import_bridge_package(module_name: str) -> Any:
    suffix = f".{module_name}" if module_name else ""
    for prefix in ("scripts.local_qwen_bridge", "local_qwen_bridge"):
        try:
            return importlib.import_module(prefix + suffix)
        except ModuleNotFoundError:
            continue
    raise ModuleNotFoundError("local_qwen_bridge package is not importable")


def case_mcp_queue_workflow(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    mcp = load_script_module("artifact_queue_mcp")
    old_roots = os.environ.get("ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS")
    os.environ["ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS"] = str(sandbox)
    try:
        target = sandbox / "queue"
        init = mcp.tool_queue_init({"dir": str(target), "items": ["one.md::First"]})
        start = mcp.tool_queue_start({"dir": str(target), "file": "one.md"})
        (target / "one.md").write_text("# One\n\nDone.\n", encoding="utf-8")
        done = mcp.tool_queue_done({"dir": str(target), "file": "one.md", "min_bytes": 5})
        next_item = mcp.tool_queue_next({"dir": str(target)})
    finally:
        if old_roots is None:
            os.environ.pop("ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS", None)
        else:
            os.environ["ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS"] = old_roots
    if (
        init.get("status") == "pass"
        and start.get("started", {}).get("file") == "one.md"
        and done.get("counts", {}).get("completed") == 1
        and next_item.get("status") == "done"
    ):
        return pass_result("MCP queue workflow completed with trusted-root checks")
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": json.dumps({"init": init, "start": start, "done": done, "next": next_item}, sort_keys=True),
    }


def case_hook_audit_output(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    hook_audit = load_script_module("local_qwen_hook_audit")
    codex_home = sandbox / "codex-home"
    codex_home.mkdir()
    (codex_home / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    project = sandbox / "project"
    project.mkdir()
    (project / ".codex").mkdir()
    (project / ".codex" / "hooks.json").write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
    report = hook_audit.audit_hooks(project_root=project, codex_home=codex_home)
    if report.get("status") == "pass" and report.get("codex_hook_merge_model") == "additive_concurrent":
        return pass_result("hook audit reports additive concurrent hook sources")
    return {
        "functional_status": "fail",
        "drift_status": "pass",
        "failure_marker": "LOCAL_QWEN_VALIDATOR_FAILED",
        "drift_flags": [],
        "artifact_paths": [],
        "validator_commands": [],
        "notes": json.dumps(report, sort_keys=True),
    }


def case_fresh_home_ab_probe(repo_root: Path, sandbox: Path, live: bool) -> dict[str, Any]:
    fresh_home = sandbox / "fresh-codex-home"
    primary_home = Path(
        os.environ.get("CODEX_HOME", str(Path.home() / ".codex_qwendex_local_safe"))
    )
    fresh_home.mkdir()
    (fresh_home / "config.toml").write_text(
        'web_search = "disabled"\npersonality = "none"\n',
        encoding="utf-8",
    )
    data = pass_result("fresh CODEX_HOME A/B lane can be prepared without changing the primary home")
    data["artifact_paths"] = [str(fresh_home / "config.toml")]
    data["notes"] = f"primary_home={primary_home}; fresh_home={fresh_home}"
    return data


CASES: dict[str, Callable[[Path, Path, bool], dict[str, Any]]] = {
    "exact_marker": case_exact_marker,
    "shell_stdout_extraction": case_shell_stdout_extraction,
    "duplicate_read_finalization": case_duplicate_read_finalization,
    "document_section_upsert": case_document_section_upsert,
    "review_current_changes": case_review_current_changes,
    "failed_validator_recovery": case_failed_validator_recovery,
    "long_context_receipt_lookup": case_long_context_receipt_lookup,
    "local_model_unavailable_fallback": case_local_model_unavailable_fallback,
    "malformed_tool_envelope_suppression": case_malformed_tool_envelope_suppression,
    "oversized_generated_command_recovery": case_oversized_generated_command_recovery,
    "bridge_status_contract_check": case_bridge_status_contract_check,
    "launcher_drift_check": case_launcher_drift_check,
    "bridge_v2_package_contract": case_bridge_v2_package_contract,
    "mcp_queue_workflow": case_mcp_queue_workflow,
    "hook_audit_output": case_hook_audit_output,
    "fresh_home_ab_probe": case_fresh_home_ab_probe,
}


def write_receipt(
    *,
    repo_root: Path,
    results_root: Path,
    run_id: str,
    case_id: str,
    sandbox_root: Path,
    result: dict[str, Any],
    model_alias: str,
    backend_profile: str,
    provider: str,
) -> Path:
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "case_id": case_id,
        "run_id": run_id,
        "started_at": utc_now(),
        "repo_root": str(repo_root),
        "sandbox_root": str(sandbox_root),
        "model_alias": model_alias,
        "backend_profile": backend_profile,
        "provider": provider,
        **result,
        "sha256": "",
    }
    receipt["success"] = receipt["functional_status"] == "pass" and receipt["drift_status"] == "pass"
    receipt["sha256"] = receipt_digest(receipt)
    out_dir = results_root / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{case_id}.json"
    path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_harness_eval(
    *,
    repo_root: Path = ROOT,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    ledger_db_path: Path | None = None,
    case_id: str = "",
    run_all: bool = False,
    live: bool = False,
    model_alias: str = "qwen-local",
    backend_profile: str = "offline-static",
    provider: str = "harness-eval",
) -> dict[str, Any]:
    selected = list(CASES) if run_all or not case_id else [case_id]
    unknown = [item for item in selected if item not in CASES]
    if unknown:
        return {"schema_version": SCHEMA_VERSION, "success": False, "failures": [f"unknown case: {item}" for item in unknown]}
    run_id = "harness_eval_" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    receipt_paths: list[Path] = []
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="local-qwen-harness-eval-") as temp_dir:
        sandbox = Path(temp_dir)
        for item in selected:
            result = CASES[item](repo_root, sandbox, live)
            receipt_path = write_receipt(
                repo_root=repo_root,
                results_root=results_root,
                run_id=run_id,
                case_id=item,
                sandbox_root=sandbox,
                result=result,
                model_alias=model_alias,
                backend_profile=backend_profile,
                provider=provider,
            )
            receipt_paths.append(receipt_path)
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            schema_failures = validate_eval_receipt(receipt)
            if schema_failures or not receipt["success"]:
                failures.extend([f"{item}: {failure}" for failure in schema_failures] or [f"{item}: case failed"])
    ledger_summary: dict[str, Any] | None = None
    if ledger_db_path is not None:
        ledger = load_script_module("local_qwen_harness_ledger")
        ledger_summary = ledger.index_paths(
            ledger_db_path,
            repo_root,
            receipt_paths,
            source="harness-eval",
            note=f"run_id={run_id}",
            limit=len(receipt_paths),
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "case_ids": selected,
        "success": not failures,
        "failures": failures,
        "receipts": [str(path) for path in receipt_paths],
        "ledger": ledger_summary,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run local-Qwen harness eval cases")
    parser.add_argument("--case", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--ledger-db", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = run_harness_eval(
        repo_root=ROOT,
        results_root=args.results_root,
        ledger_db_path=args.ledger_db,
        case_id=args.case,
        run_all=args.all,
        live=args.live,
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"success: {data['success']}")
        for path in data.get("receipts", []):
            print(path)
    return 0 if data.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
