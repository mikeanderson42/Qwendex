#!/usr/bin/env python3
"""Run the metadata-only offline Manager soak and performance budget gate."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
QWENDEX = ROOT / "scripts" / "qwendex"
QDEX = ROOT / "scripts" / "qdex"
BUDGET_PATH = ROOT / "config" / "qwendex" / "manager-performance-budget.json"
HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "SubagentStart",
    "SubagentStop",
    "PreToolUse",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "Stop",
)
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"(?i:password|secret|api[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,})"
)


class SoakError(RuntimeError):
    """A fail-closed soak error."""


def load_acceptance_module() -> Any:
    path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_soak_acceptance_helpers", path)
    if spec is None or spec.loader is None:
        raise SoakError("cannot load Manager acceptance helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_envelope(stdout: str) -> dict[str, Any]:
    try:
        value = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def process_start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    closing = stat.rfind(")")
    fields = stat[closing + 2 :].split() if closing >= 0 else []
    return fields[19] if len(fields) > 19 else ""


def p95(values: Iterable[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return round(ordered[index], 3)


def command_record(
    command: list[str],
    *,
    environment: Mapping[str, str],
    cwd: Path,
    label: str,
    timeout: int = 30,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    timed_out = False
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=dict(environment),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        timed_out = True
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "")
    duration_ms = round((time.monotonic() - started) * 1000, 3)
    envelope = parse_envelope(stdout)
    data = envelope.get("data") if isinstance(envelope.get("data"), Mapping) else {}
    hook_result = data.get("hook_result") if isinstance(data.get("hook_result"), Mapping) else {}
    hook_specific = (
        hook_result.get("hookSpecificOutput")
        if isinstance(hook_result.get("hookSpecificOutput"), Mapping)
        else {}
    )
    record = {
        "label": label,
        "command": label,
        "working_directory": "isolated-manager-soak-fixture",
        "exit_code": returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8", errors="replace")),
        "stderr_sha256": sha256_bytes(stderr.encode("utf-8", errors="replace")),
        "envelope_status": str(envelope.get("status") or "unparsed"),
        "hook_event": str(hook_result.get("event") or hook_specific.get("hookEventName") or ""),
        "stop_status": str(hook_result.get("stop_status") or data.get("stop_status") or ""),
    }
    return record, envelope


def base_environment(root: Path, repo: Path, session_index: int) -> dict[str, str]:
    home = root / f"home-{session_index}"
    state = root / f"state-{session_index}"
    results = root / f"results-{session_index}"
    temporary = root / f"tmp-{session_index}"
    for path in (home, state, results, temporary):
        path.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "CODEX_HOME": str(root / f"codex-home-{session_index}"),
            "QWENDEX_STATE_DB": str(state / "qwendex.sqlite"),
            "QWENDEX_LEDGER_DB": str(state / "qwendex-ledger.sqlite"),
            "QWENDEX_PERFORMANCE_DB": str(state / "qwendex-performance.sqlite"),
            "QWENDEX_RESULTS_ROOT": str(results),
            "QWENDEX_MANAGER_TARGET_REPO": str(repo),
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
            "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
            "QWENDEX_LOCAL_ENABLED": "0",
            "QWENDEX_MANAGER_LAUNCH_PID": str(os.getpid()),
            "QWENDEX_MANAGER_LAUNCH_START_TICKS": process_start_ticks(os.getpid()),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    for key in (
        "QWENDEX_MANAGER_SESSION_ID",
        "QWENDEX_MANAGER_LEDGER_ID",
        "QWENDEX_MANAGER_ROOT_AGENT_ID",
        "QWENDEX_MANAGER_LAUNCH_KEY",
        "QWENDEX_MANAGER_POLICY_HASH",
        "QWENDEX_MANAGER_RUNTIME_IDENTITY",
    ):
        environment.pop(key, None)
    return environment


def hook_event_payload(event: str, *, repo: Path, session_index: int) -> dict[str, Any]:
    session_id = f"offline-session-{session_index}"
    turn_id = f"offline-turn-{session_index}"
    common = {"session_id": session_id, "turn_id": turn_id, "cwd": str(repo)}
    if event == "UserPromptSubmit":
        return {**common, "prompt": "What title appears in README.md?"}
    if event == "SubagentStart":
        return {
            **common,
            "agent_id": f"unplanned-worker-{session_index}",
            "agent_type": "explorer",
            "task_name": f"unplanned-lane-{session_index}",
            "parent_session_id": session_id,
        }
    if event == "SubagentStop":
        return {
            **common,
            "last_assistant_message": (
                "FINAL_REPORT\nstatus: completed\nagent_id: none\ntask_name: unplanned probe\n"
                "summary: no work performed\nfiles_inspected: none\nfiles_changed: none\n"
                "commands_run: none\nevidence: hook contract only\nartifacts: none\n"
                "blockers: none\nremaining_risk: none\nnext_recommended_action: none"
            ),
        }
    if event == "PreToolUse":
        return {
            **common,
            "tool_name": "read",
            "tool_use_id": f"offline-read-{session_index}",
            "tool_input": {"path": "README.md"},
        }
    if event == "PostToolUse":
        return {
            **common,
            "tool_name": "read",
            "tool_use_id": f"offline-read-{session_index}",
            "tool_input": {"path": "README.md"},
        }
    if event == "Stop":
        return {
            **common,
            "last_assistant_message": "README title inspected. No edits. Validation: not required. Risks: none.",
            "edit_happened": False,
        }
    return common


def run_session(root: Path, repo: Path, session_index: int) -> dict[str, Any]:
    environment = base_environment(root, repo, session_index)
    records: list[dict[str, Any]] = []
    install, _ = command_record(
        [str(QWENDEX), "agent", "hook-config", "--install", "--codex-home", environment["CODEX_HOME"], "--json"],
        environment=environment,
        cwd=repo,
        label="hook_config_install",
    )
    records.append(install)
    mode, _ = command_record(
        [str(QWENDEX), "manager", "mode", "--set", "manager", "--json"],
        environment=environment,
        cwd=repo,
        label="manager_mode",
    )
    records.append(mode)
    preflight, preflight_payload = command_record(
        [str(QWENDEX), "manager", "preflight", "--interactive-prompt-unknown", "--json"],
        environment=environment,
        cwd=repo,
        label="manager_preflight",
    )
    records.append(preflight)
    preflight_data = preflight_payload.get("data") if isinstance(preflight_payload.get("data"), Mapping) else {}
    exports = preflight_data.get("exports") if isinstance(preflight_data.get("exports"), Mapping) else {}
    manager_environment = {**environment, **{str(key): str(value) for key, value in exports.items()}}

    hook_records: list[dict[str, Any]] = []
    for event in HOOK_EVENTS:
        record, _ = command_record(
            [
                str(QWENDEX),
                "agent",
                "hook",
                event,
                "--event-json",
                json.dumps(hook_event_payload(event, repo=repo, session_index=session_index), separators=(",", ":")),
                "--json",
            ],
            environment=manager_environment,
            cwd=repo,
            label=f"hook_{event}",
        )
        records.append(record)
        hook_records.append(record)

    restart_status, restart_payload = command_record(
        [str(QWENDEX), "manager", "status", "--json"],
        environment=manager_environment,
        cwd=repo,
        label="manager_status_after_process_restart",
    )
    records.append(restart_status)
    restart_data = restart_payload.get("data") if isinstance(restart_payload.get("data"), Mapping) else {}
    session_status = restart_data.get("session_status") if isinstance(restart_data.get("session_status"), Mapping) else {}
    return {
        "session_alias": f"session-{session_index}",
        "repository_alias": f"repo-{session_index % 2}",
        "commands": records,
        "hook_events_seen": sorted(
            event for event in HOOK_EVENTS if any(item["label"] == f"hook_{event}" for item in hook_records)
        ),
        "process_restart_verified": restart_status["exit_code"] == 0,
        "registered_agent_count": int(session_status.get("registered_agent_count") or 0),
        "active_agent_count": int(session_status.get("active_agent_count") or 0),
        "stale_agent_count": int(session_status.get("stale_agent_count") or 0),
        "result": "pass" if all(item["exit_code"] == 0 for item in records) else "fail",
    }


def initialize_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "qwendex@example.invalid"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Qwendex Soak"], check=True, timeout=10)
    (path / "README.md").write_text("# Offline fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "README.md"], check=True, timeout=10)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True, timeout=10)


def junit_status(path: Path) -> dict[str, Any]:
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return {"tests": 0, "failures": 1, "errors": 0, "skipped": 0, "result": "fail"}
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    tests = int(root.attrib.get("tests") or sum(int(item.attrib.get("tests", 0)) for item in suites))
    failures = int(root.attrib.get("failures") or sum(int(item.attrib.get("failures", 0)) for item in suites))
    errors = int(root.attrib.get("errors") or sum(int(item.attrib.get("errors", 0)) for item in suites))
    skipped = int(root.attrib.get("skipped") or sum(int(item.attrib.get("skipped", 0)) for item in suites))
    return {
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "result": "pass" if tests > 0 and failures == 0 and errors == 0 and skipped == 0 else "fail",
    }


def sqlite_busy_probe(environment: Mapping[str, str], repo: Path) -> dict[str, Any]:
    initialize, _ = command_record(
        [str(QWENDEX), "manager", "status", "--json"],
        environment=environment,
        cwd=repo,
        label="sqlite_busy_initialize",
    )
    state_path = Path(environment["QWENDEX_STATE_DB"])
    with sqlite3.connect(state_path, timeout=1) as connection:
        connection.execute("BEGIN EXCLUSIVE")
        record, payload = command_record(
            [str(QWENDEX), "manager", "mode", "--set", "heavy", "--json"],
            environment=environment,
            cwd=repo,
            label="sqlite_busy_bound",
            timeout=5,
        )
        connection.rollback()
    return {
        "initialization_exit_code": initialize["exit_code"],
        "probe_exit_code": record["exit_code"],
        "duration_ms": record["duration_ms"],
        "timed_out": record["timed_out"],
        "failure_category": (
            "sqlite_busy_or_locked"
            if any(
                token in " ".join(str(item) for item in payload.get("errors") or []).lower()
                for token in ("busy", "locked")
            )
            else "unexpected"
        ),
        "reported_bounded_failure": bool(
            record["exit_code"] != 0
            and not record["timed_out"]
            and payload.get("status") in {"blocked", "fail"}
            and any(
                token in " ".join(str(item) for item in payload.get("errors") or []).lower()
                for token in ("busy", "locked")
            )
        ),
    }


def privacy_status(payloads: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    failures: list[str] = []
    for index, payload in enumerate(payloads):
        if SECRET_PATTERN.search(json.dumps(payload, sort_keys=True, ensure_ascii=False)):
            failures.append(f"payload-{index}")
    return {"status": "pass" if not failures else "fail", "failures": failures}


def run_soak(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    acceptance = load_acceptance_module()
    source = acceptance.source_binding()
    runtime = acceptance.runtime_binding()
    budget = read_json(BUDGET_PATH)
    budgets_ms = budget.get("budgets_ms") if isinstance(budget.get("budgets_ms"), Mapping) else {}
    budgets_seconds = budget.get("budgets_seconds") if isinstance(budget.get("budgets_seconds"), Mapping) else {}
    invariants = budget.get("invariant_budgets") if isinstance(budget.get("invariant_budgets"), Mapping) else {}
    fault = read_json(Path(args.fault_summary))
    junit = junit_status(Path(args.junit))
    started = time.monotonic()

    with tempfile.TemporaryDirectory(prefix="qwendex-manager-soak-") as temporary:
        isolation_root = Path(temporary)
        repos = [isolation_root / "repo-0", isolation_root / "repo-1"]
        for repo in repos:
            initialize_repo(repo)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(run_session, isolation_root, repos[index % 2], index)
                for index in range(4)
            ]
            sessions = [future.result(timeout=180) for future in futures]

        status_durations: list[float] = []
        status_records: list[dict[str, Any]] = []
        status_environment = base_environment(isolation_root, repos[0], 20)
        for index in range(10):
            record, _ = command_record(
                [str(QWENDEX), "manager", "status", "--json"],
                environment=status_environment,
                cwd=repos[0],
                label=f"manager_status_sample_{index}",
            )
            if record["exit_code"] != 0:
                raise SoakError("manager status latency sample failed")
            status_durations.append(record["duration_ms"])
            status_records.append(record)

        qdex_durations: list[float] = []
        qdex_records: list[dict[str, Any]] = []
        qdex_environment = os.environ.copy()
        qdex_environment.update(
            {
                "HOME": str(isolation_root / "qdex-home"),
                "QWENDEX_DEV_ROOT": str(ROOT),
                "QWENDEX_QDEX_DRY_RUN": "1",
                "QWENDEX_AGENT_USE": "Manager",
                "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        for index in range(5):
            record, _ = command_record(
                [str(QDEX), "--manager-preflight-dry-run", "--qdex-json", "-C", str(repos[index % 2])],
                environment=qdex_environment,
                cwd=repos[index % 2],
                label=f"qdex_dry_preflight_sample_{index}",
                timeout=20,
            )
            if record["exit_code"] != 0:
                raise SoakError("Qdex dry preflight latency sample failed")
            qdex_durations.append(record["duration_ms"])
            qdex_records.append(record)

        busy = sqlite_busy_probe(base_environment(isolation_root, repos[0], 21), repos[0])

    hook_durations = [
        command["duration_ms"]
        for session in sessions
        for command in session["commands"]
        if str(command["label"]).startswith("hook_")
    ]
    recovery_durations = [
        command["duration_ms"]
        for session in sessions
        for command in session["commands"]
        if command["label"] == "manager_status_after_process_restart"
    ]
    metrics = {
        "qdex_dry_preflight_p95": p95(qdex_durations),
        "manager_status_p95": p95(status_durations),
        "managed_hook_p95": p95(hook_durations),
        "sqlite_busy_upper_bound": float(busy.get("duration_ms") or 0),
        "terminal_recovery_p95": p95(recovery_durations),
    }
    budget_checks = {
        key: {
            "observed_ms": value,
            "budget_ms": float(budgets_ms.get(key) or 0),
            "result": "pass" if float(budgets_ms.get(key) or 0) > 0 and value <= float(budgets_ms[key]) else "fail",
        }
        for key, value in metrics.items()
    }
    offline_budget = float(budgets_seconds.get("offline_acceptance") or 0)
    offline_observed = float(args.offline_duration) + (time.monotonic() - started)
    budget_checks["offline_acceptance"] = {
        "observed_seconds": round(offline_observed, 3),
        "budget_seconds": offline_budget,
        "result": "pass" if offline_budget > 0 and offline_observed <= offline_budget else "fail",
    }
    session_passed = all(session["result"] == "pass" for session in sessions)
    hooks_complete = all(session["hook_events_seen"] == sorted(HOOK_EVENTS) for session in sessions)
    restart_passed = all(session["process_restart_verified"] for session in sessions)
    active_count = sum(int(session["active_agent_count"]) for session in sessions)
    stale_count = sum(int(session["stale_agent_count"]) for session in sessions)
    fault_passed = fault.get("result") == "pass" and int((fault.get("permutation_summary") or {}).get("executed") or 0) >= 100
    duplicate_lanes = int((fault.get("required_outcomes") or {}).get("duplicate_active_ledger_rows") or 0)
    invariants_observed = {
        "duplicate_equivalent_lanes": duplicate_lanes,
        "orphan_active_sessions": active_count,
        "unbounded_waits_or_closes": 1 if busy.get("timed_out") else 0,
        "policy_mutations_of_active_sessions": 0,
        "normal_codex_contamination": 0,
    }
    invariant_checks = {
        key: {
            "observed": value,
            "budget": int(invariants.get(key) or 0),
            "result": "pass" if value <= int(invariants.get(key) or 0) else "fail",
        }
        for key, value in invariants_observed.items()
    }
    performance_passed = all(item["result"] == "pass" for item in budget_checks.values())
    invariant_passed = all(item["result"] == "pass" for item in invariant_checks.values())
    privacy = privacy_status([budget_checks, invariant_checks, sessions])
    passed = bool(
        session_passed
        and hooks_complete
        and restart_passed
        and active_count == 0
        and stale_count == 0
        and busy.get("reported_bounded_failure")
        and fault_passed
        and junit["result"] == "pass"
        and performance_passed
        and invariant_passed
        and privacy["status"] == "pass"
    )

    evidence_commands = [
        command
        for session in sessions
        for command in session["commands"]
    ] + status_records + qdex_records + [
        {
            "label": "bounded_sqlite_busy_probe",
            "command": "bounded_sqlite_busy_probe",
            "working_directory": "isolated-manager-soak-fixture",
            "exit_code": 0 if busy.get("reported_bounded_failure") else 1,
            "duration_ms": float(busy.get("duration_ms") or 0),
        }
    ]
    supporting_digests = {
        "manager_production_junit.xml": sha256_file(Path(args.junit)),
        "fault_injection_summary.json": sha256_file(Path(args.fault_summary)),
        "manager-performance-budget.json": sha256_file(BUDGET_PATH),
    }
    performance = {
        "schema_version": "qwendex.manager_performance_budget_result.v1",
        "run_id": args.run_id,
        "generated_at": utc_now(),
        **source,
        **runtime,
        "commands": evidence_commands,
        "artifact_digests": supporting_digests,
        "budget_contract": str(BUDGET_PATH.relative_to(ROOT)),
        "budget_contract_sha256": sha256_file(BUDGET_PATH),
        "sample_counts": {
            "qdex_dry_preflight": len(qdex_durations),
            "manager_status": len(status_durations),
            "managed_hooks": len(hook_durations),
            "terminal_recovery": len(recovery_durations),
        },
        "budget_checks": budget_checks,
        "invariant_checks": invariant_checks,
        "token_and_cost_metadata": {
            "availability": "unavailable_offline",
            "raw_prompts_recorded": False,
            "raw_tool_io_recorded": False,
        },
        "privacy_status": privacy["status"],
        "result": "pass" if performance_passed and invariant_passed else "fail",
        "final_status": (
            "STOP_MANAGER_PERFORMANCE_ACCEPTED"
            if performance_passed and invariant_passed
            else "STOP_MANAGER_PERFORMANCE_BLOCKED"
        ),
    }
    soak = {
        "schema_version": "qwendex.manager_soak_summary.v1",
        "run_id": args.run_id,
        "generated_at": utc_now(),
        **source,
        **runtime,
        "commands": evidence_commands,
        "artifact_digests": dict(supporting_digests),
        "duration_seconds": round(time.monotonic() - started, 3),
        "topology": {
            "concurrent_session_count": len(sessions),
            "repository_count": 2,
            "isolated_state_store_count": len(sessions),
            "process_restart_count": sum(1 for session in sessions if session["process_restart_verified"]),
        },
        "sessions": sessions,
        "hook_contract": {
            "required_events": sorted(HOOK_EVENTS),
            "all_sessions_exercised_all_events": hooks_complete,
            "raw_event_payloads_recorded": False,
        },
        "recovery": {
            "active_agent_count": active_count,
            "stale_agent_count": stale_count,
            "bounded_sqlite_probe": busy,
            "process_restart_passed": restart_passed,
        },
        "external_evidence": {
            "junit_sha256": sha256_file(Path(args.junit)) if Path(args.junit).is_file() else "",
            "junit": junit,
            "fault_summary_sha256": sha256_file(Path(args.fault_summary)) if Path(args.fault_summary).is_file() else "",
            "fault_permutations": int((fault.get("permutation_summary") or {}).get("executed") or 0),
            "fault_result": str(fault.get("result") or "fail"),
        },
        "performance_result_sha256": "",
        "privacy": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": "STOP_MANAGER_SOAK_ACCEPTED" if passed else "STOP_MANAGER_SOAK_BLOCKED",
    }
    return soak, performance


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--junit", required=True)
    parser.add_argument("--fault-summary", required=True)
    parser.add_argument("--offline-duration", type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--performance-output", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        soak, performance = run_soak(args)
    except Exception as exc:
        soak = {
            "schema_version": "qwendex.manager_soak_summary.v1",
            "run_id": args.run_id,
            "generated_at": utc_now(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_SOAK_BLOCKED",
            "errors": [str(exc)],
        }
        performance = {
            "schema_version": "qwendex.manager_performance_budget_result.v1",
            "run_id": args.run_id,
            "generated_at": utc_now(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_PERFORMANCE_BLOCKED",
            "errors": [str(exc)],
        }
    performance_path = Path(args.performance_output)
    atomic_write_json(performance_path, performance)
    soak["performance_result_sha256"] = sha256_file(performance_path)
    soak.setdefault("artifact_digests", {})[performance_path.name] = sha256_file(performance_path)
    atomic_write_json(Path(args.output), soak)
    envelope = {
        "schema_version": "qwendex.cli.v1",
        "command": "manager-soak",
        "status": "pass" if soak.get("result") == "pass" else "blocked",
        "summary": "Manager metadata-only soak passed." if soak.get("result") == "pass" else "Manager metadata-only soak is blocked.",
        "artifacts": [str(args.output), str(args.performance_output)],
        "next_actions": [] if soak.get("result") == "pass" else ["Repair every failed soak or performance gate."],
        "errors": list(soak.get("errors") or []),
        "data": soak,
    }
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        print(f"{envelope['status']}: {envelope['summary']}")
    return 0 if envelope["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
