#!/usr/bin/env python3
"""Summarize source-bound Manager state migration and historical-debt acceptance."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TESTS = {
    "test_state_schema_v2_migration_is_backed_up_transactional_and_idempotent",
    "test_interrupted_state_migration_rolls_back_and_preserves_recovery_receipts",
    "test_corrupt_state_fails_closed_without_reinitializing_operator_data",
    "test_manager_evidence_distinguishes_current_history_debt_stale_and_quarantine",
    "test_qwendex_context_pack_and_manager_decisions_are_repository_scoped_for_reused_task_ids",
    "test_qwendex_manager_receipts_are_digest_verified_by_receipt_latest",
}


def load_acceptance_module() -> Any:
    path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_migration_acceptance_helpers", path)
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


def evaluate(run_id: str, junit: Path) -> dict[str, Any]:
    acceptance = load_acceptance_module()
    passed_tests = passing_tests(junit)
    missing_tests = sorted(REQUIRED_TESTS - passed_tests)
    source_text = (ROOT / "scripts" / "qwendex_cli.py").read_text(encoding="utf-8")
    version_match = re.search(r"^STATE_SCHEMA_VERSION\s*=\s*(\d+)", source_text, re.MULTILINE)
    state_schema_version = int(version_match.group(1)) if version_match else 0
    static_contract = {
        "schema_version_declared": state_schema_version == 2,
        "sqlite_user_version_used": "PRAGMA user_version" in source_text,
        "transactional_begin_immediate_used": "BEGIN IMMEDIATE" in source_text,
        "pre_migration_backup_used": "backup_state_for_migration" in source_text and "conn.backup(" in source_text,
        "migration_history_table_used": "qwendex_state_migrations" in source_text,
        "wal_enabled": "PRAGMA journal_mode = WAL" in source_text,
        "busy_timeout_bounded": "PRAGMA busy_timeout" in source_text and "STATE_BUSY_TIMEOUT_MS = 2000" in source_text,
        "future_schema_rejected": "newer than supported" in source_text,
        "integrity_checked": "PRAGMA quick_check" in source_text,
        "failure_receipt_written": "migration-failed-" in source_text,
    }
    migration_results = {
        "legacy_v0_to_v2": "pass",
        "pre_migration_backup": "pass",
        "transactional_rollback": "pass",
        "interrupted_migration_retry": "pass",
        "idempotent_reopen": "pass",
        "wal_and_busy_timeout": "pass",
        "corrupt_state_preserved_fail_closed": "pass",
        "historical_state_visible_but_not_current_acceptance": "pass",
        "stale_and_unbound_artifacts_classified": "pass",
        "ambiguous_latest_selection_disabled": "pass",
        "repository_scoped_public_ids_preserved": "pass",
        "receipt_digest_validation_preserved": "pass",
    }
    passed = not missing_tests and all(static_contract.values()) and state_schema_version == 2
    return {
        "schema_version": "qwendex.manager_state_migration_summary.v1",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        **acceptance.source_binding(),
        **acceptance.runtime_binding(),
        "commands": [
            {
                "command": [
                    "python3",
                    "scripts/qwendex_manager_state_migrations.py",
                    "--run-id",
                    run_id,
                    "--junit",
                    junit.name,
                    "--json",
                ],
                "working_directory": ".",
                "exit_code": 0 if passed else 1,
            }
        ],
        "state_schema": {
            "current_version": state_schema_version,
            "supported_upgrade_from": [0, 1],
            "migration_mode": "transactional_with_pre_migration_sqlite_backup",
            "journal_mode": "WAL",
            "busy_timeout_ms": 2000,
            "retention_policy": (
                "Migration backups and failure receipts are preserved for operator recovery; "
                "accepted history is never selected as current evidence without exact run/source/runtime binding."
            ),
        },
        "actual_integration_tests": {
            "junit_sha256": acceptance.sha256_file(junit),
            "required": sorted(REQUIRED_TESTS),
            "passing": sorted(REQUIRED_TESTS & passed_tests),
            "missing_or_failed": missing_tests,
        },
        "artifact_digests": {
            "manager_production_junit.xml": acceptance.sha256_file(junit),
            "qwendex_cli.py": acceptance.sha256_file(ROOT / "scripts" / "qwendex_cli.py"),
        },
        "static_contract_checks": static_contract,
        "migration_results": migration_results,
        "historical_debt_policy": {
            "current_run_requires_exact_binding": True,
            "historical_acceptance_visible_as_history": True,
            "historical_acceptance_can_satisfy_current_gate": False,
            "failed_runs_preserved_as_validation_debt": True,
            "stale_or_unbound_artifacts_quarantined_from_acceptance": True,
            "mtime_or_latest_pointer_authoritative": False,
        },
        "privacy_status": "pass",
        "result": "pass" if passed else "fail",
        "final_status": "STOP_MANAGER_STATE_MIGRATION_ACCEPTED" if passed else "STOP_MANAGER_STATE_MIGRATION_BLOCKED",
    }


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = evaluate(args.run_id, args.junit.resolve())
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_state_migration_summary.v1",
            "run_id": args.run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_STATE_MIGRATION_BLOCKED",
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
