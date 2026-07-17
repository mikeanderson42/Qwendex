#!/usr/bin/env python3
"""Source-bound Manager acceptance profiles for Qwendex."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMAS = {
    "offline": "qwendex.manager_accept_offline.v1",
    "live": "qwendex.manager_accept_live.v1",
    "production": "qwendex.manager_accept_production.v1",
}
PROFILE_FILENAMES = {
    "offline": "manager_accept_offline_summary.json",
    "live": "manager_accept_live_summary.json",
    "production": "manager_accept_production_summary.json",
}
OFFLINE_TESTS = (
    "tests/smoke/test_qdex_manager_attachment.py",
    "tests/smoke/test_qdex_delegation_policy.py",
    "tests/smoke/test_qwendex_runtime_generations.py",
    "tests/smoke/test_qwendex_manager_production.py",
    "tests/smoke/test_qwendex_cli.py::test_manager_runtime_identity_allows_in_place_qwendex_source_edits",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_codex_patch_preflight_version_manifest",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_codex_patch_preflight_rejects_partially_applied_source",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_route_prefer_local_respects_local_toggle_off",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_local_off_route_never_selects_qwen",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_primary_authority_and_local_off_cannot_be_overridden",
    "tests/smoke/test_qwendex_cli.py::test_qdex_manager_preflight_is_advisory_and_exports_env_when_available",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_mode_cycles_status_and_legacy_alias",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_mode_toggle_cycles_full_duty_order",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_selected_manager_mode_drives_agent_policy_and_hooks",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_untrusted_stop_allows_process_exit",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_root_tools_do_not_require_preflight_identity_or_locks",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_preflight_records_decision_ledger_and_hook_status",
    "tests/smoke/test_qwendex_cli.py::test_manager_prompt_routing_keeps_launch_local_routing_after_global_toggle",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_agent_status_alias_tracks_manager_ledger_and_bounded_close",
    "tests/smoke/test_qwendex_cli.py::test_manager_turn_classifier_and_auto_mode_matrix_are_deterministic",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_agent_plan_routes_direct_team_and_release",
    "tests/smoke/test_qwendex_cli.py::test_manager_prompt_bookkeeping_is_advisory_by_mode",
    "tests/smoke/test_qwendex_cli.py::test_manager_hook_messages_never_expose_configured_gpt_model",
    "tests/smoke/test_qwendex_cli.py::test_manager_subagent_start_attaches_advisory_plan_without_pretool_reservation",
    "tests/smoke/test_qwendex_cli.py::test_manager_ultra_source_survives_prompt_routing_and_session_status",
    "tests/smoke/test_qwendex_cli.py::test_manager_tampered_policy_snapshot_does_not_gate_session_start",
    "tests/smoke/test_qwendex_cli.py::test_manager_suggested_lanes_are_advisory_in_status_and_stop",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_worker_and_root_stop_contracts_are_advisory",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_root_work_never_requires_closeout_wording",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_agent_hook_config_generation_and_write_gate",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_root_pre_tool_allows_release_without_secondary_approval",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_pre_tool_keeps_intrinsic_child_boundaries_but_never_gates_root",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_read_only_shell_gate_is_fail_closed_and_quote_aware",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_non_shell_tools_allow_root_and_restrict_read_only_children",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_reconciles_stale_read_only_and_warns_on_stale_writers",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_repair_safe_closes_only_harmless_stale_sessions",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_context_pack_and_manager_decisions_are_repository_scoped_for_reused_task_ids",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_receipts_are_digest_verified_by_receipt_latest",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_reports_suggested_subagent_capacity_per_repository",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_concurrent_manager_assignments_record_capacity_advisories",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_launch_status_validates_process_repo_start_and_policy",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_concurrent_write_lock_acquisition_serializes_conflict_check_and_insert",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_begin_immediate_reports_bounded_busy_state",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_rolls_decision_and_validation_scope_per_turn",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_stop_uses_only_its_decision_task_sessions",
    "tests/smoke/test_qwendex_cli.py::test_qwendex_manager_status_counts_full_ledger_with_bounded_samples",
)
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"(?i:password|secret|api[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,})"
)
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/var/home/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/Users/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/root(?=/)"),
    re.compile(
        r"(?<![A-Za-z0-9_.-])/mnt/[a-z]/Users/[A-Za-z0-9_.-]+(?=/)", re.IGNORECASE
    ),
    re.compile(
        r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+(?=\\)", re.IGNORECASE
    ),
)
REQUIRED_ARTIFACT_FIELDS = frozenset(
    {
        "schema_version",
        "run_id",
        "source_commit",
        "dirty_state",
        "runtime_generation",
        "codex_version",
        "patch_digest",
        "binary_digest",
        "config_digest",
        "generated_at",
        "commands",
        "result",
        "artifact_digests",
        "privacy_status",
    }
)
PYTEST_RUNTIME_ISOLATION_KEYS = frozenset(
    {
        "CODEX_HOME",
        "QWENDEX_AGENT_ARTIFACT_ROOT",
        "QWENDEX_CODEX_HOME",
        "QWENDEX_CODEX_RUNTIME",
        "QWENDEX_CODEX_STATUS_FILE",
        "QWENDEX_DEV_ROOT",
        "QWENDEX_DEV_SOURCE_ROOT",
        "QWENDEX_HOOK_GENERATION",
        "QWENDEX_LEDGER_DB",
        "QWENDEX_META_ROOT",
        "QWENDEX_PERFORMANCE_DB",
        "QWENDEX_RESULTS_ROOT",
        "QWENDEX_ROOT",
        "QWENDEX_RUN_ID",
        "QWENDEX_STATE_DB",
    }
)


class AcceptanceError(RuntimeError):
    """A fail-closed Manager acceptance error."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def artifact_contract_errors(payload: Mapping[str, Any]) -> list[str]:
    errors = [f"missing:{name}" for name in sorted(REQUIRED_ARTIFACT_FIELDS - payload.keys())]
    if "commands" in payload and not isinstance(payload.get("commands"), list):
        errors.append("invalid:commands_not_list")
    if "artifact_digests" in payload and not isinstance(payload.get("artifact_digests"), Mapping):
        errors.append("invalid:artifact_digests_not_object")
    if "result" in payload and payload.get("result") not in {"pass", "fail"}:
        errors.append("invalid:result")
    if "privacy_status" in payload and payload.get("privacy_status") not in {"pass", "fail", "unknown"}:
        errors.append("invalid:privacy_status")
    return errors


def isolated_pytest_environment(environment: Mapping[str, str]) -> dict[str, str]:
    isolated = dict(environment)
    for key in tuple(isolated):
        if key in PYTEST_RUNTIME_ISOLATION_KEYS or key.startswith("QWENDEX_RUNTIME_"):
            isolated.pop(key, None)
    return isolated


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if check and result.returncode:
        raise AcceptanceError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.rstrip("\n")


def safe_run_id(raw: str) -> str:
    value = raw.strip() or f"manager-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:10]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{5,127}", value):
        raise AcceptanceError("run id must be 6-128 safe alphanumeric, dot, underscore, or dash characters")
    return value


def relative_command(command: Iterable[str]) -> list[str]:
    def normalize(value: str) -> str:
        if value == str(ROOT):
            return "."
        if value.startswith(str(ROOT) + os.sep):
            return str(Path(value).relative_to(ROOT))
        if "=" in value:
            option, assigned = value.split("=", 1)
            normalized_assigned = normalize(assigned)
            if normalized_assigned != assigned:
                return f"{option}={normalized_assigned}"
        return value

    normalized: list[str] = []
    for item in command:
        normalized.append(normalize(str(item)))
    return normalized


def public_artifact_path(path: str | Path) -> str:
    resolved = Path(path).resolve()
    for base in (ROOT, Path.cwd().resolve()):
        try:
            relative = resolved.relative_to(base)
        except ValueError:
            continue
        return str(relative) or "."
    return resolved.name


def source_binding() -> dict[str, Any]:
    status_lines = git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    dirty_paths = sorted(line[3:] for line in status_lines if len(line) > 3)
    config_path = ROOT / "config" / "qwendex" / "qwendex.json"
    schema_path = ROOT / "config" / "qwendex" / "qwendex.schema.json"
    return {
        "source_commit": git("rev-parse", "HEAD"),
        "source_tree": git("rev-parse", "HEAD^{tree}"),
        "dirty_state": "clean" if not status_lines else "in_scope_candidate",
        "dirty_paths": dirty_paths,
        "config_digest": sha256_file(config_path),
        "schema_digest": sha256_file(schema_path),
    }


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def selected_runtime_manifest() -> dict[str, Any]:
    generation_id_raw = str(os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or "").strip()
    generation_dir_raw = str(os.environ.get("QWENDEX_RUNTIME_GENERATION_DIR") or "").strip()
    if generation_dir_raw:
        manifest = read_json(Path(generation_dir_raw) / "generation.json")
        if manifest and (not generation_id_raw or manifest.get("generation_id") == generation_id_raw):
            return manifest
    if generation_id_raw:
        return {}
    dev_root = Path(str(os.environ.get("QWENDEX_DEV_ROOT") or ROOT)).expanduser()
    runtime_root = Path(str(os.environ.get("QWENDEX_RUNTIME_ROOT") or dev_root / ".qwendex-dev" / "runtime"))
    selector = read_json(runtime_root / "current.json")
    generation_id = str(selector.get("current") or "")
    if generation_id:
        return read_json(runtime_root / "generations" / generation_id / "generation.json")
    return {}


def runtime_binding() -> dict[str, Any]:
    manifest = selected_runtime_manifest()
    codex = manifest.get("codex") if isinstance(manifest.get("codex"), Mapping) else {}
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), Mapping) else {}
    if not codex:
        dev_root = Path(str(os.environ.get("QWENDEX_DEV_ROOT") or ROOT)).expanduser()
        receipt = read_json(dev_root / ".qwendex-dev" / "results" / "meta" / "codex_build.json")
        codex = {
            "version": receipt.get("binary_version"),
            "patch_sha256": receipt.get("source_patch_sha256"),
            "binary_sha256": receipt.get("binary_sha256"),
        }
    return {
        "codex_version": str(codex.get("version") or contract.get("codex_version") or ""),
        "patch_digest": str(codex.get("patch_sha256") or contract.get("codex_patch_sha256") or ""),
        "binary_digest": str(codex.get("binary_sha256") or contract.get("patched_binary_sha256") or ""),
        "runtime_generation": str(manifest.get("generation_id") or os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or ""),
        "hook_generation": str(manifest.get("hook_generation") or os.environ.get("QWENDEX_HOOK_GENERATION") or ""),
        "runtime_contract_digest": str(manifest.get("contract_sha256") or ""),
        "state_schema_version": int(contract.get("state_schema_version") or 2),
    }


def parse_junit(path: Path) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    attributes = root.attrib
    if root.tag == "testsuite":
        suites = [root]
    else:
        suites = list(root.findall("testsuite"))
    tests = int(attributes.get("tests") or sum(int(item.attrib.get("tests", 0)) for item in suites))
    failures = int(attributes.get("failures") or sum(int(item.attrib.get("failures", 0)) for item in suites))
    errors = int(attributes.get("errors") or sum(int(item.attrib.get("errors", 0)) for item in suites))
    skipped = int(attributes.get("skipped") or sum(int(item.attrib.get("skipped", 0)) for item in suites))
    duration = float(attributes.get("time") or sum(float(item.attrib.get("time", 0)) for item in suites))
    return {
        "tests_collected": tests,
        "tests_passed": max(0, tests - failures - errors - skipped),
        "tests_failed": failures + errors,
        "tests_skipped": skipped,
        "duration_seconds": round(duration, 6),
    }


def run_recorded(
    command: list[str],
    *,
    environment: Mapping[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout: int,
) -> dict[str, Any]:
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=dict(environment),
                text=True,
                stdout=stdout,
                stderr=stderr,
                timeout=timeout,
                check=False,
            )
            returncode = result.returncode
            timed_out = False
        except subprocess.TimeoutExpired:
            returncode = 124
            timed_out = True
    return {
        "command": relative_command(command),
        "working_directory": ".",
        "exit_code": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 6),
        "source_commit": git("rev-parse", "HEAD"),
        "tests_expected": False,
        "tests_collected": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "stdout": stdout_path.name,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr": stderr_path.name,
        "stderr_sha256": sha256_file(stderr_path),
    }


def scan_privacy(paths: Iterable[Path]) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            failures.append({"artifact": path.name, "reason": "unreadable"})
            continue
        if SECRET_PATTERN.search(text):
            failures.append({"artifact": path.name, "reason": "credential_pattern"})
        if any(pattern.search(text) for pattern in PRIVATE_PATH_PATTERNS):
            failures.append({"artifact": path.name, "reason": "private_absolute_path"})
    return {
        "status": "pass" if not failures else "fail",
        "scanned_artifact_count": sum(1 for path in paths if path.is_file()),
        "failures": failures,
    }


def acceptance_root(results_root: Path, run_id: str, profile: str) -> Path:
    root = results_root / "manager-production" / run_id / profile
    if root.exists() and any(root.iterdir()):
        raise AcceptanceError(f"acceptance run already exists and will not be overwritten: {run_id}/{profile}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def base_environment(isolation_root: Path, results_root: Path, run_id: str) -> dict[str, str]:
    environment = os.environ.copy()
    home = isolation_root / "home"
    temp = isolation_root / "tmp"
    state = isolation_root / "state"
    home.mkdir(parents=True)
    temp.mkdir(parents=True)
    state.mkdir(parents=True)
    environment.update(
        {
            "HOME": str(home),
            "TMPDIR": str(temp),
            "QWENDEX_STATE_DB": str(state / "qwendex.sqlite"),
            "QWENDEX_LEDGER_DB": str(state / "qwendex-ledger.sqlite"),
            "QWENDEX_PERFORMANCE_DB": str(state / "qwendex-performance.sqlite"),
            "QWENDEX_RESULTS_ROOT": str(results_root),
            "QWENDEX_RUN_ID": run_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    for key in (
        "QWENDEX_MANAGER_SESSION_ID",
        "QWENDEX_MANAGER_LEDGER_ID",
        "QWENDEX_MANAGER_ROOT_AGENT_ID",
        "QWENDEX_MANAGER_LAUNCH_KEY",
        "QWENDEX_MANAGER_POLICY_HASH",
        "QWENDEX_STATE_MIGRATION_FAIL_AT",
        "QWENDEX_RUNTIME_FAIL_ACTIVATION_AT",
    ):
        environment.pop(key, None)
    return environment


def offline_profile(run_id: str, results_root: Path) -> dict[str, Any]:
    profile_started = time.monotonic()
    run_root = acceptance_root(results_root, run_id, "offline")
    isolation_root = run_root / "isolation"
    environment = base_environment(isolation_root, run_root / "receipts", run_id)
    junit = run_root / "pytest-junit.xml"
    pytest_command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit}",
        *OFFLINE_TESTS,
    ]
    with tempfile.TemporaryDirectory(prefix="qwendex-manager-offline-pytest-") as pytest_temp:
        pytest_environment = isolated_pytest_environment(environment)
        pytest_environment["TMPDIR"] = pytest_temp
        pytest_record = run_recorded(
            pytest_command,
            environment=pytest_environment,
            stdout_path=run_root / "pytest.stdout.log",
            stderr_path=run_root / "pytest.stderr.log",
            timeout=900,
        )
    test_results = parse_junit(junit) if junit.is_file() else {
        "tests_collected": 0,
        "tests_passed": 0,
        "tests_failed": 1,
        "tests_skipped": 0,
        "duration_seconds": pytest_record["duration_seconds"],
    }
    pytest_passed = bool(
        pytest_record["exit_code"] == 0
        and test_results["tests_collected"] > 0
        and test_results["tests_failed"] == 0
        and test_results["tests_skipped"] == 0
    )
    pytest_record.update(test_results)
    pytest_record["tests_expected"] = True

    routing_output = run_root / "routing_eval_summary.json"
    routing_command = [
        sys.executable,
        "scripts/qwendex_routing_eval.py",
        "--run-id",
        f"{run_id}-routing",
        "--output",
        str(routing_output),
        "--json",
    ]
    routing_record = run_recorded(
        routing_command,
        environment=environment,
        stdout_path=run_root / "routing.stdout.log",
        stderr_path=run_root / "routing.stderr.log",
        timeout=120,
    )
    routing = read_json(routing_output)
    routing_passed = routing_record["exit_code"] == 0 and routing.get("result") == "pass"
    routing_results = routing.get("results_summary") if isinstance(routing.get("results_summary"), Mapping) else {}
    routing_record.update(
        {
            "tests_expected": True,
            "tests_collected": int(((routing.get("corpus") or {}).get("case_count")) or 0),
            "tests_passed": int(routing_results.get("passed") or 0),
            "tests_failed": int(routing_results.get("failed") or 0),
        }
    )

    fault_output = run_root / "fault_injection_summary.json"
    fault_command = [
        sys.executable,
        "scripts/qwendex_manager_faults.py",
        "--run-id",
        f"{run_id}-faults",
        "--junit",
        str(junit),
        "--permutations",
        "100",
        "--output",
        str(fault_output),
        "--json",
    ]
    fault_record = run_recorded(
        fault_command,
        environment=environment,
        stdout_path=run_root / "faults.stdout.log",
        stderr_path=run_root / "faults.stderr.log",
        timeout=120,
    )
    faults = read_json(fault_output)
    faults_passed = fault_record["exit_code"] == 0 and faults.get("result") == "pass"
    fault_permutations = faults.get("permutation_summary") if isinstance(faults.get("permutation_summary"), Mapping) else {}
    fault_record.update(
        {
            "tests_expected": True,
            "tests_collected": int(fault_permutations.get("executed") or 0),
            "tests_passed": int(fault_permutations.get("passed") or 0),
            "tests_failed": int(fault_permutations.get("failed") or 0),
        }
    )
    idempotency_output = run_root / "event_idempotency_summary.json"
    idempotency_payload = {
        "schema_version": "qwendex.manager_event_idempotency.v1",
        "run_id": f"{run_id}-faults",
        "generated_at": utc_now(),
        **source_binding(),
        **runtime_binding(),
        "commands": [fault_record],
        "artifact_digests": {
            "fault_injection_summary.json": sha256_file(fault_output) if fault_output.is_file() else "",
        },
        "permutation_summary": faults.get("permutation_summary") or {},
        "required_outcomes": faults.get("required_outcomes") or {},
        "source_artifact_sha256": sha256_file(fault_output) if fault_output.is_file() else "",
        "privacy_status": faults.get("privacy_status") or "unknown",
        "result": faults.get("result") or "fail",
        "final_status": (
            "STOP_MANAGER_EVENT_IDEMPOTENCY_ACCEPTED"
            if faults_passed
            else "STOP_MANAGER_EVENT_IDEMPOTENCY_BLOCKED"
        ),
    }
    atomic_write_json(idempotency_output, idempotency_payload)

    migration_output = run_root / "state_migration_summary.json"
    migration_command = [
        sys.executable,
        "scripts/qwendex_manager_state_migrations.py",
        "--run-id",
        f"{run_id}-migrations",
        "--junit",
        str(junit),
        "--output",
        str(migration_output),
        "--json",
    ]
    migration_record = run_recorded(
        migration_command,
        environment=environment,
        stdout_path=run_root / "migrations.stdout.log",
        stderr_path=run_root / "migrations.stderr.log",
        timeout=120,
    )
    migration = read_json(migration_output)
    migration_passed = migration_record["exit_code"] == 0 and migration.get("result") == "pass"
    migration_record.update(
        {
            "tests_expected": True,
            "tests_collected": 1,
            "tests_passed": 1 if migration_passed else 0,
            "tests_failed": 0 if migration_passed else 1,
        }
    )

    security_output = run_root / "security_boundary_summary.json"
    security_command = [
        sys.executable,
        "scripts/qwendex_manager_security.py",
        "--run-id",
        f"{run_id}-security",
        "--junit",
        str(junit),
        "--routing",
        str(routing_output),
        "--output",
        str(security_output),
        "--json",
    ]
    security_record = run_recorded(
        security_command,
        environment=environment,
        stdout_path=run_root / "security.stdout.log",
        stderr_path=run_root / "security.stderr.log",
        timeout=120,
    )
    security = read_json(security_output)
    security_passed = security_record["exit_code"] == 0 and security.get("result") == "pass"
    security_record.update(
        {
            "tests_expected": True,
            "tests_collected": 1,
            "tests_passed": 1 if security_passed else 0,
            "tests_failed": 0 if security_passed else 1,
        }
    )
    soak_output = run_root / "manager_soak_summary.json"
    performance_output = run_root / "performance_budget.json"
    soak_command = [
        sys.executable,
        "scripts/qwendex_manager_soak.py",
        "--run-id",
        f"{run_id}-soak",
        "--junit",
        str(junit),
        "--fault-summary",
        str(fault_output),
        "--offline-duration",
        str(round(time.monotonic() - profile_started, 6)),
        "--output",
        str(soak_output),
        "--performance-output",
        str(performance_output),
        "--json",
    ]
    soak_record = run_recorded(
        soak_command,
        environment=environment,
        stdout_path=run_root / "soak.stdout.log",
        stderr_path=run_root / "soak.stderr.log",
        timeout=300,
    )
    soak = read_json(soak_output)
    performance = read_json(performance_output)
    soak_passed = soak_record["exit_code"] == 0 and soak.get("result") == "pass"
    performance_passed = performance.get("result") == "pass"
    soak_record.update(
        {
            "tests_expected": True,
            "tests_collected": 2,
            "tests_passed": int(soak_passed) + int(performance_passed),
            "tests_failed": int(not soak_passed) + int(not performance_passed),
        }
    )
    privacy = scan_privacy(
        [
            run_root / "pytest.stdout.log",
            run_root / "pytest.stderr.log",
            run_root / "routing.stdout.log",
            run_root / "routing.stderr.log",
            run_root / "faults.stdout.log",
            run_root / "faults.stderr.log",
            run_root / "migrations.stdout.log",
            run_root / "migrations.stderr.log",
            run_root / "security.stdout.log",
            run_root / "security.stderr.log",
            run_root / "soak.stdout.log",
            run_root / "soak.stderr.log",
            routing_output,
            fault_output,
            idempotency_output,
            migration_output,
            security_output,
            soak_output,
            performance_output,
            junit,
        ]
    )
    passed = (
        pytest_passed
        and routing_passed
        and faults_passed
        and migration_passed
        and security_passed
        and soak_passed
        and performance_passed
        and privacy["status"] == "pass"
    )
    source = source_binding()
    runtime = runtime_binding()
    artifacts = [
        junit,
        routing_output,
        fault_output,
        idempotency_output,
        migration_output,
        security_output,
        soak_output,
        performance_output,
    ]
    payload: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMAS["offline"],
        "run_id": run_id,
        "acceptance_profile": "offline",
        "generated_at": utc_now(),
        **source,
        **runtime,
        "commands": [
            pytest_record,
            routing_record,
            fault_record,
            migration_record,
            security_record,
            soak_record,
        ],
        "test_results": {
            **test_results,
            "pytest_exit_code": pytest_record["exit_code"],
            "pytest_zero_collection_rejected": True,
            "routing_case_count": int((routing.get("corpus") or {}).get("case_count") or 0),
            "routing_passed": int((routing.get("results_summary") or {}).get("passed") or 0),
            "routing_failed": int((routing.get("results_summary") or {}).get("failed") or 0),
            "event_permutations_executed": int((faults.get("permutation_summary") or {}).get("executed") or 0),
            "event_permutations_failed": int((faults.get("permutation_summary") or {}).get("failed") or 0),
            "soak_concurrent_sessions": int((soak.get("topology") or {}).get("concurrent_session_count") or 0),
            "soak_failed": 0 if soak_passed else 1,
            "performance_failed": 0 if performance_passed else 1,
            "state_migration_failed": 0 if migration_passed else 1,
        },
        "coverage_contract": {
            "all_agent_management_modes": True,
            "auto_classification": True,
            "root_and_child_tool_surfaces": True,
            "direct_root_work_supported": True,
            "advisory_lane_planning_and_verification": True,
            "ultra_deduplication": True,
            "policy_drift_and_dual_session_isolation": True,
            "stop_idempotency_and_slot_release": True,
            "bounded_close_and_tombstone": True,
            "runtime_and_receipt_binding": True,
            "privacy_scan": True,
            "metadata_only_soak": True,
            "performance_budgets": True,
            "state_migration_and_historical_debt": True,
        },
        "artifact_digests": {
            path.name: sha256_file(path)
            for path in artifacts
            if path.is_file()
        },
        "privacy_result": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": (
            "STOP_MANAGER_ACCEPT_OFFLINE_ACCEPTED"
            if passed
            else "STOP_MANAGER_ACCEPT_OFFLINE_BLOCKED"
        ),
    }
    summary_path = run_root / PROFILE_FILENAMES["offline"]
    atomic_write_json(summary_path, payload)
    payload["summary_artifact"] = str(Path("manager-production") / run_id / "offline" / summary_path.name)
    return payload


def live_profile(run_id: str, results_root: Path) -> dict[str, Any]:
    run_root = acceptance_root(results_root, run_id, "live")
    environment = os.environ.copy()
    environment.update(
        {
            "QWENDEX_RESULTS_ROOT": str(run_root / "receipts"),
            "QWENDEX_RUN_ID": run_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    matrix_output = run_root / "manager_live_matrix.json"
    matrix_command = [
        sys.executable,
        "scripts/qwendex_manager_live.py",
        "--run-id",
        f"{run_id}-matrix",
        "--output",
        str(matrix_output),
        "--json",
    ]
    matrix_record = run_recorded(
        matrix_command,
        environment=environment,
        stdout_path=run_root / "live.stdout.log",
        stderr_path=run_root / "live.stderr.log",
        timeout=14_400,
    )
    matrix = read_json(matrix_output)
    sessions = list(matrix.get("sessions") or [])
    session_passed = sum(1 for item in sessions if item.get("result") == "pass")
    matrix_record.update(
        {
            "tests_expected": True,
            "tests_collected": len(sessions),
            "tests_passed": session_passed,
            "tests_failed": len(sessions) - session_passed if sessions else 1,
        }
    )
    privacy = scan_privacy(
        [
            run_root / "live.stdout.log",
            run_root / "live.stderr.log",
            matrix_output,
        ]
    )
    passed = bool(
        matrix_record["exit_code"] == 0
        and matrix.get("result") == "pass"
        and matrix.get("privacy_status") == "pass"
        and privacy["status"] == "pass"
        and sessions
        and session_passed == len(sessions)
    )
    source = source_binding()
    selected_runtime = runtime_binding()
    runtime = {
        "codex_version": str(matrix.get("codex_version") or selected_runtime["codex_version"]),
        "patch_digest": str(matrix.get("patch_digest") or selected_runtime["patch_digest"]),
        "binary_digest": str(matrix.get("binary_digest") or selected_runtime["binary_digest"]),
        "runtime_generation": str(matrix.get("runtime_generation") or selected_runtime["runtime_generation"]),
        "hook_generation": str(matrix.get("hook_generation") or selected_runtime["hook_generation"]),
        "runtime_contract_digest": str(
            matrix.get("runtime_contract_digest") or selected_runtime["runtime_contract_digest"]
        ),
        "state_schema_version": int(
            matrix.get("state_schema_version") or selected_runtime["state_schema_version"]
        ),
    }
    artifacts = [matrix_output]
    payload: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMAS["live"],
        "run_id": run_id,
        "acceptance_profile": "live",
        "generated_at": utc_now(),
        **source,
        **runtime,
        "commands": [matrix_record],
        "test_results": {
            "tests_collected": len(sessions),
            "tests_passed": session_passed,
            "tests_failed": len(sessions) - session_passed,
            "tests_skipped": 0,
            "live_matrix_contract": dict(matrix.get("matrix_contract") or {}),
            "manager_invariants": dict(matrix.get("invariants") or {}),
        },
        "live_session_ids": list(matrix.get("live_session_ids") or []),
        "usage": dict(matrix.get("usage") or {}),
        "normal_codex_isolation": dict(matrix.get("normal_codex_isolation") or {}),
        "raw_receipts": dict(matrix.get("raw_receipts") or {}),
        "artifact_digests": {
            path.name: sha256_file(path)
            for path in artifacts
            if path.is_file()
        },
        "privacy_result": {
            "sanitized_summary_scan": privacy,
            "raw_local_scan": dict(matrix.get("privacy_result") or {}),
        },
        "privacy_status": (
            "pass"
            if privacy["status"] == "pass" and matrix.get("privacy_status") == "pass"
            else "fail"
        ),
        "result": "pass" if passed else "fail",
        "final_status": (
            "STOP_MANAGER_ACCEPT_LIVE_ACCEPTED"
            if passed
            else "STOP_MANAGER_ACCEPT_LIVE_BLOCKED"
        ),
    }
    summary_path = run_root / PROFILE_FILENAMES["live"]
    atomic_write_json(summary_path, payload)
    payload["summary_artifact"] = str(Path("manager-production") / run_id / "live" / summary_path.name)
    return payload


def copy_required_artifact(source: Path, destination: Path) -> Path:
    if not source.is_file() or source.is_symlink():
        raise AcceptanceError(f"required production artifact is missing or unsafe: {source.name}")
    shutil.copy2(source, destination)
    return destination


def production_profile(run_id: str, results_root: Path) -> dict[str, Any]:
    run_root = acceptance_root(results_root, run_id, "production")
    environment = os.environ.copy()
    environment.update(
        {
            "QWENDEX_RUN_ID": run_id,
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    self_host_root = run_root / "self-host"
    self_host_command = [
        sys.executable,
        "scripts/qwendex_manager_self_host.py",
        "--run-id",
        f"{run_id}-self-host",
        "--output-root",
        str(self_host_root),
        "--json",
    ]
    self_host_record = run_recorded(
        self_host_command,
        environment=environment,
        stdout_path=run_root / "self-host.stdout.log",
        stderr_path=run_root / "self-host.stderr.log",
        timeout=900,
    )
    self_host_output = read_json(run_root / "self-host.stdout.log")
    self_host_data = self_host_output.get("data") if isinstance(self_host_output.get("data"), Mapping) else {}
    self_host_passed = bool(
        self_host_record["exit_code"] == 0
        and self_host_output.get("status") == "pass"
        and self_host_data.get("result") == "pass"
    )

    offline_run_id = f"{run_id}-production-offline"
    live_run_id = f"{run_id}-production-live"
    offline = offline_profile(offline_run_id, results_root)
    live = live_profile(live_run_id, results_root)
    offline_passed = offline.get("result") == "pass"
    live_passed = live.get("result") == "pass"

    install_root = run_root / "install"
    install_command = [
        sys.executable,
        "scripts/qwendex_manager_install_acceptance.py",
        "--run-id",
        f"{run_id}-install",
        "--output-root",
        str(install_root),
        "--json",
    ]
    install_record = run_recorded(
        install_command,
        environment=environment,
        stdout_path=run_root / "install.stdout.log",
        stderr_path=run_root / "install.stderr.log",
        timeout=21_600,
    )
    install = read_json(install_root / "install_acceptance_summary.json")
    install_passed = install_record["exit_code"] == 0 and install.get("result") == "pass"

    offline_root = results_root / "manager-production" / offline_run_id / "offline"
    live_root = results_root / "manager-production" / live_run_id / "live"
    canonical_sources = {
        "self_host_failure_timeline.json": self_host_root / "self_host_failure_timeline.json",
        "self_host_failure_root_cause.json": self_host_root / "self_host_failure_root_cause.json",
        "self_host_reproduction_receipt.json": self_host_root / "self_host_reproduction_receipt.json",
        "runtime_generation_contract.json": self_host_root / "runtime_generation_contract.json",
        "runtime_activation_receipt.json": self_host_root / "runtime_activation_receipt.json",
        "runtime_rollback_receipt.json": self_host_root / "runtime_rollback_receipt.json",
        "manager_accept_offline_summary.json": offline_root / PROFILE_FILENAMES["offline"],
        "manager_accept_live_summary.json": live_root / PROFILE_FILENAMES["live"],
        "fault_injection_summary.json": offline_root / "fault_injection_summary.json",
        "event_idempotency_summary.json": offline_root / "event_idempotency_summary.json",
        "state_migration_summary.json": offline_root / "state_migration_summary.json",
        "security_boundary_summary.json": offline_root / "security_boundary_summary.json",
        "routing_eval_summary.json": offline_root / "routing_eval_summary.json",
        "manager_soak_summary.json": offline_root / "manager_soak_summary.json",
        "performance_budget.json": offline_root / "performance_budget.json",
        "fresh_install_receipt.json": install_root / "fresh_install_receipt.json",
        "upgrade_from_v0.5.7_receipt.json": install_root / "upgrade_from_v0.5.7_receipt.json",
        "rollback_to_known_good_receipt.json": install_root / "rollback_to_known_good_receipt.json",
        "normal_codex_isolation_receipt.json": install_root / "normal_codex_isolation_receipt.json",
    }
    copied: list[Path] = []
    if self_host_passed and offline_passed and live_passed and install_passed:
        copied = [
            copy_required_artifact(source_path, run_root / name)
            for name, source_path in canonical_sources.items()
        ]

    component_payloads = {
        name: read_json(path)
        for name, path in canonical_sources.items()
        if path.is_file()
    }
    artifact_contract_failures = {
        name: artifact_contract_errors(payload)
        for name, payload in component_payloads.items()
        if artifact_contract_errors(payload)
    }
    missing_component_artifacts = sorted(set(canonical_sources) - set(component_payloads))
    component_failures = sorted(
        {
            *missing_component_artifacts,
            *artifact_contract_failures,
            *(
                name
                for name, payload in component_payloads.items()
                if payload.get("result") != "pass" or payload.get("privacy_status") != "pass"
            ),
        }
    )
    privacy = scan_privacy(
        [
            run_root / "self-host.stdout.log",
            run_root / "self-host.stderr.log",
            run_root / "install.stdout.log",
            run_root / "install.stderr.log",
            *copied,
        ]
    )
    source = source_binding()
    runtime = runtime_binding()
    candidate_version_match = re.search(
        r'^VERSION\s*=\s*["\']([^"\']+)["\']',
        (ROOT / "scripts" / "qwendex_cli.py").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    candidate_version = candidate_version_match.group(1) if candidate_version_match else "unknown"
    passed = bool(
        self_host_passed
        and offline_passed
        and live_passed
        and install_passed
        and not component_failures
        and privacy["status"] == "pass"
        and runtime["runtime_generation"]
        and runtime["hook_generation"] == runtime["runtime_generation"]
    )
    preliminary_digests = {
        path.name: sha256_file(path)
        for path in copied
        if path.is_file()
    }
    validation_summary = {
        "schema_version": "qwendex.manager_validation_summary.v1",
        "run_id": run_id,
        "generated_at": utc_now(),
        **source,
        **runtime,
        "acceptance_profile": "production",
        "candidate_version": candidate_version,
        "commands": [self_host_record, install_record],
        "test_results": {
            "offline": dict(offline.get("test_results") or {}),
            "live": dict(live.get("test_results") or {}),
            "self_hosting": "pass" if self_host_passed else "fail",
            "fresh_install_upgrade_rollback": "pass" if install_passed else "fail",
        },
        "artifact_contract_failures": artifact_contract_failures,
        "missing_component_artifacts": missing_component_artifacts,
        "artifact_digests": preliminary_digests,
        "privacy_result": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": (
            "STOP_MANAGER_PRODUCTION_VALIDATION_ACCEPTED"
            if passed
            else "STOP_MANAGER_VALIDATION_FAILED"
        ),
    }
    validation_path = run_root / "validation_summary.json"
    atomic_write_json(validation_path, validation_summary)
    release_readiness = {
        "schema_version": "qwendex.release_candidate_readiness.v1",
        "run_id": run_id,
        "generated_at": utc_now(),
        **source,
        **runtime,
        "acceptance_profile": "production",
        "candidate_version": candidate_version,
        "commands": [self_host_record, install_record],
        "gates": {
            "self_hosting": self_host_passed,
            "offline_acceptance": offline_passed,
            "live_acceptance": live_passed,
            "fresh_install_upgrade_rollback": install_passed,
            "component_artifacts": not component_failures,
            "artifact_contract": not artifact_contract_failures and not missing_component_artifacts,
            "privacy": privacy["status"] == "pass",
        },
        "component_failures": component_failures,
        "artifact_contract_failures": artifact_contract_failures,
        "missing_component_artifacts": missing_component_artifacts,
        "release_published": False,
        "tag_created": False,
        "artifact_digests": {
            **preliminary_digests,
            validation_path.name: sha256_file(validation_path),
        },
        "privacy_result": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": (
            "STOP_MANAGER_RELEASE_CANDIDATE_ACCEPTED"
            if passed
            else "STOP_MANAGER_RELEASE_EVIDENCE_BLOCKED"
        ),
    }
    readiness_path = run_root / "release_candidate_readiness.json"
    atomic_write_json(readiness_path, release_readiness)
    artifacts = [*copied, validation_path, readiness_path]
    payload: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMAS["production"],
        "run_id": run_id,
        "acceptance_profile": "production",
        "generated_at": utc_now(),
        **source,
        **runtime,
        "candidate_version": candidate_version,
        "commands": [self_host_record, install_record],
        "test_results": {
            "tests_collected": 4,
            "tests_passed": sum(
                int(item)
                for item in (self_host_passed, offline_passed, live_passed, install_passed)
            ),
            "tests_failed": sum(
                int(not item)
                for item in (self_host_passed, offline_passed, live_passed, install_passed)
            ),
            "tests_skipped": 0,
            "offline": dict(offline.get("test_results") or {}),
            "live": dict(live.get("test_results") or {}),
        },
        "live_session_ids": list(live.get("live_session_ids") or []),
        "component_runs": {
            "self_host": f"{run_id}-self-host",
            "offline": offline_run_id,
            "live": live_run_id,
            "install": f"{run_id}-install",
        },
        "component_failures": component_failures,
        "artifact_contract_failures": artifact_contract_failures,
        "missing_component_artifacts": missing_component_artifacts,
        "artifact_digests": {
            path.name: sha256_file(path)
            for path in artifacts
            if path.is_file()
        },
        "privacy_result": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": (
            "STOP_MANAGER_ACCEPT_PRODUCTION_ACCEPTED"
            if passed
            else "STOP_MANAGER_ACCEPT_PRODUCTION_BLOCKED"
        ),
    }
    summary_path = run_root / PROFILE_FILENAMES["production"]
    atomic_write_json(summary_path, payload)
    payload["summary_artifact"] = str(Path("manager-production") / run_id / "production" / summary_path.name)
    return payload


def blocked_profile(profile: str, run_id: str, results_root: Path) -> dict[str, Any]:
    run_root = acceptance_root(results_root, run_id, profile)
    payload = {
        "schema_version": PROFILE_SCHEMAS[profile],
        "run_id": run_id,
        "acceptance_profile": profile,
        "generated_at": utc_now(),
        **source_binding(),
        **runtime_binding(),
        "commands": [],
        "test_results": {"tests_collected": 0, "tests_passed": 0, "tests_failed": 1},
        "privacy_status": "unknown",
        "result": "fail",
        "final_status": f"STOP_MANAGER_ACCEPT_{profile.upper()}_BLOCKED",
        "errors": [f"{profile} acceptance implementation is not yet complete"],
    }
    summary_path = run_root / PROFILE_FILENAMES[profile]
    atomic_write_json(summary_path, payload)
    payload["summary_artifact"] = str(Path("manager-production") / run_id / profile / summary_path.name)
    return payload


def run_profile(profile: str, run_id: str, results_root: Path) -> dict[str, Any]:
    if profile == "offline":
        return offline_profile(run_id, results_root)
    if profile == "live":
        return live_profile(run_id, results_root)
    if profile == "production":
        return production_profile(run_id, results_root)
    return blocked_profile(profile, run_id, results_root)


def acceptance_evidence_status(results_root: Path, current_run_id: str = "") -> dict[str, Any]:
    source = source_binding()
    runtime = runtime_binding()
    base = results_root / "manager-production"
    categories: dict[str, list[dict[str, Any]]] = {
        "current_acceptance_evidence": [],
        "historical_accepted_evidence": [],
        "historical_validation_debt": [],
        "stale_or_unbound_artifacts": [],
        "quarantined_artifacts": [],
    }
    if base.is_dir():
        paths = sorted(base.glob("*/*/manager_accept_*_summary.json"))
    else:
        paths = []
    for path in paths:
        try:
            relative = path.relative_to(results_root).as_posix()
        except ValueError:
            relative = path.name
        payload = read_json(path)
        profile = str(payload.get("acceptance_profile") or "")
        run_id = str(payload.get("run_id") or "")
        binding_errors: list[str] = []
        if payload.get("schema_version") != PROFILE_SCHEMAS.get(profile):
            binding_errors.append("schema_version")
        if not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("source_commit") or "")):
            binding_errors.append("source_commit")
        for key in ("config_digest", "schema_digest"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(payload.get(key) or "")):
                binding_errors.append(key)
        if not run_id:
            binding_errors.append("run_id")
        if not str(payload.get("runtime_generation") or ""):
            binding_errors.append("runtime_generation")
        if not str(payload.get("hook_generation") or ""):
            binding_errors.append("hook_generation")
        if int(payload.get("state_schema_version") or 0) <= 0:
            binding_errors.append("state_schema_version")
        if str(payload.get("privacy_status") or "") != "pass":
            binding_errors.append("privacy_status")
        item = {
            "path": relative,
            "sha256": sha256_file(path),
            "run_id": run_id,
            "acceptance_profile": profile,
            "source_commit": str(payload.get("source_commit") or ""),
            "runtime_generation": str(payload.get("runtime_generation") or ""),
            "result": str(payload.get("result") or "unknown"),
            "final_status": str(payload.get("final_status") or ""),
            "binding_errors": binding_errors,
        }
        if "quarantine" in path.parts:
            categories["quarantined_artifacts"].append(item)
            continue
        if binding_errors:
            categories["stale_or_unbound_artifacts"].append(item)
            continue
        current_binding = bool(
            current_run_id
            and run_id == current_run_id
            and item["source_commit"] == source["source_commit"]
            and str(payload.get("config_digest") or "") == source["config_digest"]
            and str(payload.get("schema_digest") or "") == source["schema_digest"]
            and item["runtime_generation"] == runtime["runtime_generation"]
            and str(payload.get("hook_generation") or "") == runtime["hook_generation"]
        )
        if payload.get("result") == "pass" and current_binding:
            categories["current_acceptance_evidence"].append(item)
        elif payload.get("result") == "pass":
            categories["historical_accepted_evidence"].append(item)
        else:
            categories["historical_validation_debt"].append(item)
    quarantine_root = base / "quarantine"
    if quarantine_root.is_dir():
        known = {item["path"] for item in categories["quarantined_artifacts"]}
        for path in sorted(quarantine_root.rglob("*.json")):
            relative = path.relative_to(results_root).as_posix()
            if relative not in known:
                categories["quarantined_artifacts"].append(
                    {"path": relative, "sha256": sha256_file(path), "result": "quarantined"}
                )
    counts = {key: len(value) for key, value in categories.items()}
    return {
        "schema_version": "qwendex.manager_acceptance_evidence_status.v1",
        "generated_at": utc_now(),
        "current_run_id": current_run_id,
        **source,
        **runtime,
        **categories,
        "counts": counts,
        "selection_policy": "explicit_run_id_and_exact_source_config_schema_runtime_binding",
        "ambiguous_latest_selection": False,
        "privacy_status": "pass",
    }


def command_evidence(args: argparse.Namespace) -> dict[str, Any]:
    raw_results = str(getattr(args, "results_root", "") or os.environ.get("QWENDEX_RESULTS_ROOT") or ROOT / "results" / "qwendex")
    results_root = Path(raw_results).expanduser().resolve()
    current_run_id = str(getattr(args, "run_id", "") or os.environ.get("QWENDEX_RUN_ID") or "").strip()
    try:
        data = acceptance_evidence_status(results_root, current_run_id)
        return {
            "schema_version": "qwendex.cli.v1",
            "command": "manager",
            "action": "evidence",
            "status": "pass",
            "summary": "Classified current, historical, stale, and quarantined Manager acceptance evidence.",
            "artifacts": [],
            "next_actions": [],
            "errors": [],
            "data": data,
        }
    except Exception as exc:
        return {
            "schema_version": "qwendex.cli.v1",
            "command": "manager",
            "action": "evidence",
            "status": "blocked",
            "summary": "Manager acceptance evidence classification failed.",
            "artifacts": [],
            "next_actions": ["Repair unreadable or malformed acceptance artifacts before production acceptance."],
            "errors": [str(exc)],
            "data": {},
        }


def stable_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    passed = payload.get("result") == "pass"
    profile = str(payload.get("acceptance_profile") or "unknown")
    return {
        "schema_version": "qwendex.cli.v1",
        "command": "manager",
        "action": "accept",
        "status": "pass" if passed else "blocked",
        "summary": (
            f"Qwendex Manager {profile} acceptance passed."
            if passed
            else f"Qwendex Manager {profile} acceptance is blocked."
        ),
        "artifacts": [str(payload.get("summary_artifact") or "")],
        "next_actions": [] if passed else ["Inspect the source-bound acceptance summary and repair every failing gate."],
        "errors": list(payload.get("errors") or []),
        "data": dict(payload),
    }


def command(args: argparse.Namespace) -> dict[str, Any]:
    run_id = safe_run_id(str(getattr(args, "run_id", "") or ""))
    profile = str(getattr(args, "profile", "") or "offline")
    raw_results = str(getattr(args, "results_root", "") or os.environ.get("QWENDEX_RESULTS_ROOT") or ROOT / "results" / "qwendex")
    results_root = Path(raw_results).expanduser().resolve()
    try:
        payload = run_profile(profile, run_id, results_root)
    except Exception as exc:
        payload = {
            "schema_version": PROFILE_SCHEMAS.get(profile, "qwendex.manager_accept.v1"),
            "run_id": run_id,
            "acceptance_profile": profile,
            "generated_at": utc_now(),
            "result": "fail",
            "final_status": f"STOP_MANAGER_ACCEPT_{profile.upper()}_BLOCKED",
            "privacy_status": "unknown",
            "errors": [str(exc)],
        }
    return stable_envelope(payload)


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=sorted(PROFILE_SCHEMAS), required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--results-root", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    envelope = command(args)
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        print(f"{envelope['status']}: {envelope['summary']}")
    return 0 if envelope["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
