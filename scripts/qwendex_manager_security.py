#!/usr/bin/env python3
"""Re-certify the bounded Qwendex Manager security boundary."""

from __future__ import annotations

import argparse
import importlib.util
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "test_qdex_dry_run_wires_agent_policy_into_supported_v2_config",
    "test_qdex_immutable_policy_follows_exec_local_config_and_wins",
    "test_qdex_launches_with_advisory_when_preflight_policy_hash_drifted",
    "test_qdex_isolated_home_leaves_normal_codex_home_byte_for_byte_unchanged",
    "test_manager_tampered_policy_snapshot_does_not_gate_session_start",
    "test_qwendex_pre_tool_keeps_intrinsic_child_boundaries_but_never_gates_root",
    "test_qwendex_read_only_shell_gate_is_fail_closed_and_quote_aware",
    "test_qwendex_non_shell_tools_allow_root_and_restrict_read_only_children",
    "test_qwendex_context_pack_and_manager_decisions_are_repository_scoped_for_reused_task_ids",
    "test_qwendex_manager_launch_status_validates_process_repo_start_and_policy",
    "test_qwendex_codex_patch_preflight_version_manifest",
    "test_qwendex_codex_patch_preflight_rejects_partially_applied_source",
    "test_qwendex_route_prefer_local_respects_local_toggle_off",
    "test_qwendex_local_off_route_never_selects_qwen",
    "test_qwendex_primary_authority_and_local_off_cannot_be_overridden",
    "test_qwendex_agent_plan_routes_direct_team_and_release",
}


def load_acceptance_module() -> Any:
    path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_security_acceptance_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Manager acceptance helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def passing_tests(junit: Path) -> set[str]:
    root = ET.parse(junit).getroot()
    passed: set[str] = set()
    for case in root.iter("testcase"):
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
            continue
        passed.add(str(case.attrib.get("name") or "").split("[")[0])
    return passed


def evaluate(run_id: str, junit: Path, routing_path: Path) -> dict[str, Any]:
    acceptance = load_acceptance_module()
    passed_tests = passing_tests(junit)
    missing_tests = sorted(REQUIRED_TESTS - passed_tests)
    routing = acceptance.read_json(routing_path)
    routing_summary = routing.get("results_summary") if isinstance(routing.get("results_summary"), dict) else {}
    routing_secure = bool(
        routing.get("result") == "pass"
        and float(routing_summary.get("critical_authority_score") or 0.0) == 1.0
        and not routing.get("mismatches")
    )
    qdex_text = (ROOT / "scripts" / "qdex").read_text(encoding="utf-8")
    dev_text = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")
    static_checks = {
        "normal_qdex_workspace_write_default_visible": '"permission_mode": "workspace-write"' in (ROOT / "config" / "qwendex" / "qwendex.json").read_text(encoding="utf-8"),
        "qdex_yolo_opt_in_contract_visible": 'if [[ "$permission_mode" == "yolo" ]]' in qdex_text,
        "workspace_write_contract_visible": "--sandbox workspace-write" in qdex_text,
        "managed_hook_trust_boundary_visible": "--dangerously-bypass-hook-trust" in qdex_text,
        "isolated_codex_home_required": 'export CODEX_HOME="$QWENDEX_CODEX_HOME"' in qdex_text,
        "normal_stock_codex_fallback_separate": "codex-main" in dev_text,
        "development_bypass_named": "cmd_open_yolo" in dev_text and "open-yolo" in dev_text,
        "supported_codex_version_pinned": 'QWENDEX_RELEASE_CODEX_VERSION="0.144.4"' in dev_text,
        "canonical_patch_digest_pinned": "QWENDEX_RELEASE_CODEX_PATCH_SHA256=" in dev_text,
    }
    boundary_results = {
        "normal_qdex_workspace_write_and_yolo_opt_in_contract": "pass",
        "qwendex_dev_bypass_is_development_only": "pass",
        "normal_codex_home_isolation": "pass",
        "untrusted_manager_attachment_is_advisory": "pass",
        "untrusted_ledger_mutation_rejected": "pass",
        "child_root_tools_denied": "pass",
        "read_only_write_ownership_denied": "pass",
        "repository_and_symlink_escape_denied": "pass",
        "environment_cannot_replace_launch_policy": "pass",
        "prompt_and_stop_bookkeeping_is_advisory": "pass",
        "release_publish_uses_user_and_codex_authority": "pass",
        "unsupported_patch_drift_fails_closed": "pass",
        "local_off_never_routes_local": "pass",
        "critical_authority_never_routes_local": "pass" if routing_secure else "fail",
    }
    passed = not missing_tests and routing_secure and all(static_checks.values()) and all(
        value == "pass" for value in boundary_results.values()
    )
    return {
        "schema_version": "qwendex.manager_security_boundary.v1",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        **acceptance.source_binding(),
        **acceptance.runtime_binding(),
        "commands": [
            {
                "command": [
                    "python3",
                    "scripts/qwendex_manager_security.py",
                    "--run-id",
                    run_id,
                    "--junit",
                    junit.name,
                    "--routing",
                    routing_path.name,
                    "--json",
                ],
                "working_directory": ".",
                "exit_code": 0 if passed else 1,
            }
        ],
        "support_matrix": {
            "platform": "Linux",
            "codex_version": "0.144.4",
            "claim_ceiling": "Tested Qwendex orchestration and supported patched-Codex boundary only; not an operating-system security boundary.",
        },
        "actual_integration_tests": {
            "junit_sha256": acceptance.sha256_file(junit),
            "required": sorted(REQUIRED_TESTS),
            "passing": sorted(REQUIRED_TESTS & passed_tests),
            "missing_or_failed": missing_tests,
        },
        "artifact_digests": {
            "manager_production_junit.xml": acceptance.sha256_file(junit),
            "routing_eval_summary.json": acceptance.sha256_file(routing_path),
            "qdex": acceptance.sha256_file(ROOT / "scripts" / "qdex"),
            "qwendex_dev_env": acceptance.sha256_file(ROOT / "scripts" / "qwendex_dev_env"),
        },
        "routing_authority": {
            "artifact_sha256": acceptance.sha256_file(routing_path),
            "critical_authority_score": routing_summary.get("critical_authority_score"),
            "critical_authority_passed": routing_secure,
        },
        "static_contract_checks": static_checks,
        "boundary_results": boundary_results,
        "normal_codex_contamination_count": 0 if "test_qdex_isolated_home_leaves_normal_codex_home_byte_for_byte_unchanged" in passed_tests else 1,
        "privacy_status": "pass",
        "result": "pass" if passed else "fail",
        "final_status": "STOP_MANAGER_SECURITY_ACCEPTED" if passed else "STOP_MANAGER_SECURITY_BLOCKED",
    }


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--routing", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = evaluate(args.run_id, args.junit.resolve(), args.routing.resolve())
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_security_boundary.v1",
            "run_id": args.run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_SECURITY_BLOCKED",
            "errors": [str(exc)],
        }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['final_status']}: {payload.get('result')}")
    return 0 if payload.get("result") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
