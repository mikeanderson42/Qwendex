#!/usr/bin/env python3
"""Run the bounded real-model Qwendex Manager acceptance matrix."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9_]{16,}|github_pat_[A-Za-z0-9_]{16,}|"
    r"(?i:password|secret|api[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,})"
)


class LiveAcceptanceError(RuntimeError):
    """A fail-closed live acceptance error."""


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise LiveAcceptanceError(f"cannot load required module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SELF_HOST = load_module("qwendex_live_self_host_helpers", ROOT / "scripts" / "qwendex_manager_self_host.py")
RUNTIME = load_module("qwendex_live_runtime_helpers", ROOT / "scripts" / "qwendex_runtime.py")
ACCEPTANCE = load_module("qwendex_live_acceptance_helpers", ROOT / "scripts" / "qwendex_manager_acceptance.py")


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


def initialize_work_repo(path: Path, *, variant: int) -> None:
    path.mkdir(parents=True)
    (path / "app.py").write_text(
        """def normalize_name(value: str) -> str:
    return value.strip().lower()


def render_record(name: str, value: int) -> str:
    return f"{normalize_name(name)}:{value}"
""",
        encoding="utf-8",
    )
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        """from app import normalize_name, render_record


def test_normalize_name():
    assert normalize_name("  Alpha  ") == "alpha"


def test_render_record():
    assert render_record(" Alpha ", 3) == "alpha:3"
""",
        encoding="utf-8",
    )
    (path / "README.md").write_text(
        f"# Live acceptance fixture {variant}\n\nSmall Python fixture for isolated Qwendex validation.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(path)], check=True, timeout=30)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "qwendex@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Qwendex Live"], check=True)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "live fixture"], check=True, timeout=30)


def static_normal_home_snapshot(home: Path) -> dict[str, str]:
    normal = home / ".codex"
    result: dict[str, str] = {}
    for name in ("config.toml", "hooks.json", "installation_id", "version.json"):
        path = normal / name
        if path.is_file():
            result[name] = sha256_file(path)
    return result


def prepare_runtime(fixture_root: Path, commands: list[dict[str, Any]]) -> tuple[Path, dict[str, Any], Path]:
    source_root = fixture_root / "candidate"
    SELF_HOST.initialize_source_fixture(source_root)
    codex, host = SELF_HOST.install_build_contract(source_root)
    runtime_root = source_root / ".qwendex-dev" / "runtime"
    manifest = SELF_HOST.build_generation(
        source_root=source_root,
        dev_root=source_root,
        runtime_root=runtime_root,
        codex=codex,
        host=host,
        home=Path.home(),
        commands=commands,
        label="build_live_runtime_generation",
    )
    generation_id = str(manifest["generation_id"])
    SELF_HOST.activate(runtime_root, generation_id, commands, label="activate_live_runtime_generation")
    SELF_HOST.write_fixture_env(source_root)
    generation_home = runtime_root / "generations" / generation_id / "codex_home"
    auth_source = Path.home() / ".codex" / "auth.json"
    auth_target = generation_home / "auth.json"
    if not auth_source.is_file():
        raise LiveAcceptanceError("normal Codex authentication is unavailable for the isolated live fixture")
    auth_payload = auth_source.read_bytes()
    auth_target.unlink(missing_ok=True)
    auth_target.write_bytes(auth_payload)
    auth_target.chmod(0o600)
    return source_root, manifest, runtime_root


def jsonl_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def subprocess_text(value: str | bytes | None) -> str:
    """Normalize captured subprocess output, including TimeoutExpired bytes."""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def run_live_turn(
    *,
    qdex: Path,
    repo: Path,
    repo_alias: str,
    mode: str,
    prompt: str,
    label: str,
    raw_root: Path,
    dev_root: Path,
    resume_thread: str = "",
    ultra: bool = False,
    timeout_seconds: int = 900,
    environment_overrides: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = os.environ.copy()
    if environment_overrides:
        environment.update({str(key): str(value) for key, value in environment_overrides.items()})
    environment.update(
        {
            "QWENDEX_DEV_ROOT": str(dev_root),
            "QWENDEX_AGENT_USE": mode,
            "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
            "QWENDEX_LOCAL_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    command = [
        str(qdex),
        "--qdex-permission-mode",
        "workspace-write",
        "-C",
        str(repo),
        "exec",
    ]
    if resume_thread:
        command.extend(["resume", resume_thread])
    command.append("--json")
    if ultra:
        command.extend(["-c", 'model_reasoning_effort="ultra"'])
    command.append(prompt)
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = 124
        stdout = subprocess_text(exc.stdout)
        stderr = subprocess_text(exc.stderr)
    duration = round(time.monotonic() - started, 6)
    raw_root.mkdir(parents=True, exist_ok=True)
    stdout_path = raw_root / f"{label}.stdout.jsonl"
    stderr_path = raw_root / f"{label}.stderr.log"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    events = jsonl_events(stdout)
    thread_ids = [str(event.get("thread_id") or "") for event in events if event.get("type") == "thread.started"]
    completed_turns = [event for event in events if event.get("type") == "turn.completed"]
    agent_messages = [
        event
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), Mapping)
        and event["item"].get("type") == "agent_message"
    ]
    usage = completed_turns[-1].get("usage") if completed_turns and isinstance(completed_turns[-1].get("usage"), Mapping) else {}
    return {
        "label": label,
        "repository_alias": repo_alias,
        "mode": mode.lower(),
        "reasoning_effort": "ultra" if ultra else "non_ultra",
        "resume": bool(resume_thread),
        "thread_id": thread_ids[-1] if thread_ids else resume_thread,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_seconds": duration,
        "turn_completed": bool(completed_turns),
        "agent_message_present": bool(agent_messages),
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "cached_input_tokens": int(usage.get("cached_input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_output_tokens": int(usage.get("reasoning_output_tokens") or 0),
        },
        "raw_stdout": stdout_path.name,
        "raw_stdout_sha256": sha256_file(stdout_path),
        "raw_stderr": stderr_path.name,
        "raw_stderr_sha256": sha256_file(stderr_path),
        "result": "pass" if exit_code == 0 and not timed_out and completed_turns and agent_messages else "fail",
    }


def git_changes(repo: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    paths = sorted(line[3:] for line in result.stdout.splitlines() if len(line) > 3)
    return {"changed_file_count": len(paths), "changed_paths": paths}


def pytest_validation(repo: Path, label: str) -> dict[str, Any]:
    started = time.monotonic()
    result = subprocess.run(
        ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=repo,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    passed_match = re.search(r"(\d+) passed", result.stdout)
    failed_match = re.search(r"(\d+) failed", result.stdout)
    passed_count = int(passed_match.group(1)) if passed_match else 0
    failed_count = int(failed_match.group(1)) if failed_match else 0
    return {
        "label": label,
        "command": "python3 -m pytest -q -p no:cacheprovider",
        "working_directory": "isolated-live-fixture",
        "exit_code": result.returncode,
        "source_commit": ACCEPTANCE.git("rev-parse", "HEAD"),
        "tests_expected": True,
        "tests_collected": passed_count + failed_count,
        "tests_passed": passed_count,
        "tests_failed": failed_count if passed_count + failed_count else 1,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout_sha256": sha256_bytes(result.stdout.encode("utf-8", errors="replace")),
        "stderr_sha256": sha256_bytes(result.stderr.encode("utf-8", errors="replace")),
        "result": "pass" if result.returncode == 0 and passed_count > 0 and failed_count == 0 else "fail",
    }


def state_summary(state_db: Path, repo_aliases: Mapping[str, str]) -> dict[str, Any]:
    if not state_db.is_file():
        return {"decisions": [], "agents": [], "errors": ["state database missing"]}
    decisions: list[dict[str, Any]] = []
    agents: list[dict[str, Any]] = []
    with sqlite3.connect(state_db) as connection:
        connection.row_factory = sqlite3.Row
        for row in connection.execute("SELECT * FROM qwendex_manager_decisions ORDER BY timestamp_created"):
            repo = str(row["repo_root"] or "")
            if repo not in repo_aliases:
                continue
            decisions.append(
                {
                    "repository_alias": repo_aliases[repo],
                    "ledger_id": str(row["ledger_id"] or ""),
                    "root_session_id": str(row["root_session_id"] or ""),
                    "turn_id": str(row["turn_id"] or ""),
                    "agent_task_id": str(row["agent_task_id"] or ""),
                    "prompt_known": bool(row["prompt_known"]),
                    "admission_error_code": str(row["admission_error_code"] or ""),
                    "selected_mode": str(row["selected_mode"] or row["mode"] or ""),
                    "effective_turn_mode": str(row["effective_turn_mode"] or ""),
                    "task_class": str(row["task_class"] or ""),
                    "selected_route": str(row["selected_route"] or ""),
                    "policy_drift": str(row["policy_hash"] or "") != str(row["desired_global_policy_hash"] or row["policy_hash"] or ""),
                    "runtime_generation": str(row["runtime_generation"] or ""),
                    "hook_generation": str(row["hook_generation"] or ""),
                    "final_status": str(row["final_status"] or ""),
                    "validation_result": str(row["validation_result"] or ""),
                    "stop_status": str(row["stop_status"] or ""),
                }
            )
        for row in connection.execute("SELECT * FROM qwendex_agent_sessions ORDER BY created_at"):
            repo = str(row["repo_root"] or "")
            if repo not in repo_aliases:
                continue
            try:
                packet = json.loads(str(row["context_packet_json"] or "{}"))
            except json.JSONDecodeError:
                packet = {}
            agents.append(
                {
                    "repository_alias": repo_aliases[repo],
                    "agent_id": str(row["agent_id"] or ""),
                    "task_id": str(row["task_id"] or ""),
                    "lane": str(row["lane"] or ""),
                    "required": bool(packet.get("required")),
                    "status": str(row["status"] or ""),
                    "validation_status": str(row["validation_status"] or ""),
                    "final_report_present": bool(row["final_report_present"]),
                    "runtime_generation": str(row["runtime_generation"] or ""),
                }
            )
    return {"decisions": decisions, "agents": agents, "errors": []}


def invariant_summary(state: Mapping[str, Any], sessions: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = list(state.get("decisions") or [])
    agents = list(state.get("agents") or [])
    groups: dict[tuple[str, str, str], int] = {}
    for agent in agents:
        key = (str(agent.get("repository_alias")), str(agent.get("task_id")), str(agent.get("lane")))
        groups[key] = groups.get(key, 0) + 1
    duplicate_lanes = sum(max(0, count - 1) for count in groups.values())
    unresolved_required = [
        agent
        for agent in agents
        if agent.get("required") and agent.get("status") not in {"completed", "waived", "closed"}
    ]
    orphaned = [agent for agent in agents if agent.get("status") in {"active", "reserved", "stale"}]
    required = [agent for agent in agents if agent.get("required")]
    required_completed = [agent for agent in required if agent.get("status") in {"completed", "waived", "closed"}]
    return {
        "prompt_known_failures": sum(1 for item in decisions if not item.get("prompt_known")),
        "prompt_admission_failures": sum(1 for item in decisions if item.get("admission_error_code")),
        "duplicate_equivalent_lanes": duplicate_lanes,
        "unresolved_required_lanes_at_finalization": len(unresolved_required),
        "orphaned_active_sessions_after_cleanup": len(orphaned),
        "unbounded_waits_or_closes": sum(1 for item in sessions if item.get("timed_out")),
        "policy_mutations_of_active_sessions": sum(1 for item in decisions if item.get("policy_drift")),
        "required_lane_count": len(required),
        "required_lane_completed_count": len(required_completed),
        "required_lane_completion_rate": round(len(required_completed) / len(required), 6) if required else 1.0,
        "manager_decision_count": len(decisions),
        "manager_closed_count": sum(1 for item in decisions if item.get("final_status") == "closed"),
    }


def raw_privacy(raw_root: Path) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    paths = list(raw_root.glob("*")) if raw_root.is_dir() else []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if SECRET_PATTERN.search(text):
            failures.append({"artifact": path.name, "reason": "credential_pattern"})
    return {"status": "pass" if not failures else "fail", "scanned_artifact_count": len(paths), "failures": failures}


def run_matrix(run_id: str, output: Path) -> dict[str, Any]:
    started = time.monotonic()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_root = output.parent / "raw-live"
    if raw_root.exists():
        raise LiveAcceptanceError("raw live output directory already exists")
    commands: list[dict[str, Any]] = []
    normal_home = Path.home()
    normal_before = static_normal_home_snapshot(normal_home)

    with tempfile.TemporaryDirectory(prefix="qwendex-live-accept-") as temporary:
        fixture_root = Path(temporary)
        dev_root, manifest, _runtime_root = prepare_runtime(fixture_root, commands)
        generation_id = str(manifest["generation_id"])
        qdex = dev_root / "scripts" / "qdex"
        workspaces = fixture_root / "workspaces"
        repos: dict[str, Path] = {}
        for index, alias in enumerate(
            [
                "medium-read",
                "heavy-edit",
                "manager-fresh-1",
                "manager-fresh-2",
                "manager-fresh-3",
                "manager-sequential",
                "manager-ultra",
                "concurrent-a",
                "concurrent-b",
            ]
        ):
            repo = workspaces / alias
            initialize_work_repo(repo, variant=index)
            repos[alias] = repo

        sessions: list[dict[str, Any]] = []
        validations: list[dict[str, Any]] = []
        medium = run_live_turn(
            qdex=qdex,
            repo=repos["medium-read"],
            repo_alias="medium-read",
            mode="Medium",
            prompt=(
                "Map how normalize_name and render_record flow through app.py and tests/test_app.py. "
                "Remain read-only, use Medium delegation when it materially helps, run no destructive commands, and return verified findings."
            ),
            label="medium_read_heavy",
            raw_root=raw_root,
            dev_root=dev_root,
        )
        medium["worktree"] = git_changes(repos["medium-read"])
        if medium["worktree"]["changed_file_count"] != 0:
            medium["result"] = "fail"
        sessions.append(medium)

        heavy = run_live_turn(
            qdex=qdex,
            repo=repos["heavy-edit"],
            repo_alias="heavy-edit",
            mode="Heavy",
            prompt=(
                "Implement a non-trivial validation feature across app.py and tests/test_app.py: reject negative record values with ValueError, "
                "add regression tests, use Heavy bounded delegation, and run the full test suite. Do not publish or access other repositories."
            ),
            label="heavy_edit",
            raw_root=raw_root,
            dev_root=dev_root,
        )
        heavy["worktree"] = git_changes(repos["heavy-edit"])
        validation = pytest_validation(repos["heavy-edit"], "heavy_edit_pytest")
        validations.append(validation)
        if heavy["worktree"]["changed_file_count"] == 0 or validation["result"] != "pass":
            heavy["result"] = "fail"
        sessions.append(heavy)

        fresh_prompts = [
            (
                "Add a slugify function in app.py with regression tests and README usage. "
                "Follow the Manager lane plan, use no Ultra reasoning, run tests, and leave a concise validated closeout."
            ),
            (
                "Change render_record so whitespace-only names raise ValueError; add tests and document the behavior. "
                "Use all required Manager lanes, non-Ultra reasoning, and run tests."
            ),
            (
                "Reject newline characters in normalize_name and add focused regression coverage. "
                "Use the planned Manager lanes, non-Ultra reasoning, and verify the complete suite."
            ),
        ]
        for index, prompt in enumerate(fresh_prompts, start=1):
            alias = f"manager-fresh-{index}"
            result = run_live_turn(
                qdex=qdex,
                repo=repos[alias],
                repo_alias=alias,
                mode="Manager",
                prompt=prompt,
                label=alias.replace("-", "_"),
                raw_root=raw_root,
                dev_root=dev_root,
            )
            result["worktree"] = git_changes(repos[alias])
            validation = pytest_validation(repos[alias], f"{alias}_pytest")
            validations.append(validation)
            if result["worktree"]["changed_file_count"] == 0 or validation["result"] != "pass":
                result["result"] = "fail"
            sessions.append(result)

        sequential_prompts = [
            "Map the repository implementation and test flow. Follow the required Manager lanes, remain read-only, and report verified findings.",
            "Add a parse_record function in app.py with regression tests. Follow required Manager lanes and run tests.",
            "Add regression coverage for empty parse_record input and fix the implementation if needed. Use the planned Manager verifier lane and run tests.",
            "Update README.md with parse_record usage and verify docs examples against the implementation. Follow the Manager turn plan.",
            "Review all current app.py, tests, and README changes, run the full regression suite, and close every required Manager lane with remaining risks.",
        ]
        sequential_thread = ""
        for index, prompt in enumerate(sequential_prompts, start=1):
            result = run_live_turn(
                qdex=qdex,
                repo=repos["manager-sequential"],
                repo_alias="manager-sequential",
                mode="Manager",
                prompt=prompt,
                label=f"manager_sequential_turn_{index}",
                raw_root=raw_root,
                dev_root=dev_root,
                resume_thread=sequential_thread,
            )
            if not sequential_thread:
                sequential_thread = str(result.get("thread_id") or "")
            elif result.get("thread_id") != sequential_thread:
                result["result"] = "fail"
            sessions.append(result)
            if not sequential_thread or result["result"] != "pass":
                break
        sequential_validation = pytest_validation(repos["manager-sequential"], "manager_sequential_pytest")
        validations.append(sequential_validation)

        ultra = run_live_turn(
            qdex=qdex,
            repo=repos["manager-ultra"],
            repo_alias="manager-ultra",
            mode="Manager",
            prompt=(
                "Add a safe_record helper with regression tests. Honor the Qwendex Manager lane plan while Ultra native "
                "multi-agent reasoning coexists, avoid duplicate equivalent lanes, and run tests."
            ),
            label="manager_ultra_coexistence",
            raw_root=raw_root,
            dev_root=dev_root,
            ultra=True,
        )
        ultra["worktree"] = git_changes(repos["manager-ultra"])
        validation = pytest_validation(repos["manager-ultra"], "manager_ultra_pytest")
        validations.append(validation)
        if ultra["worktree"]["changed_file_count"] == 0 or validation["result"] != "pass":
            ultra["result"] = "fail"
        sessions.append(ultra)

        concurrent_specs = [
            (
                "concurrent-a",
                "Add a compact_record helper with regression tests. Use required non-Ultra Manager lanes and run tests.",
            ),
            (
                "concurrent-b",
                "Add a display_record helper with regression tests. Use required non-Ultra Manager lanes and run tests.",
            ),
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    run_live_turn,
                    qdex=qdex,
                    repo=repos[alias],
                    repo_alias=alias,
                    mode="Manager",
                    prompt=prompt,
                    label=alias.replace("-", "_"),
                    raw_root=raw_root,
                    dev_root=dev_root,
                )
                for alias, prompt in concurrent_specs
            ]
            concurrent_results = [future.result(timeout=960) for future in futures]
        for result in concurrent_results:
            alias = str(result["repository_alias"])
            result["worktree"] = git_changes(repos[alias])
            validation = pytest_validation(repos[alias], f"{alias}_pytest")
            validations.append(validation)
            if result["worktree"]["changed_file_count"] == 0 or validation["result"] != "pass":
                result["result"] = "fail"
            sessions.append(result)

        state_db = dev_root / ".qwendex-dev" / "state" / "qwendex.sqlite"
        aliases = {str(path.resolve()): alias for alias, path in repos.items()}
        state = state_summary(state_db, aliases)
        invariants = invariant_summary(state, sessions)

    normal_after = static_normal_home_snapshot(normal_home)
    privacy = raw_privacy(raw_root)
    total_usage = {
        key: sum(int((session.get("usage") or {}).get(key) or 0) for session in sessions)
        for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
    }
    sequential_count = sum(1 for item in sessions if str(item.get("label") or "").startswith("manager_sequential_turn_"))
    non_ultra_fresh = sum(
        1
        for item in sessions
        if str(item.get("label") or "").startswith("manager_fresh_")
        and item.get("reasoning_effort") == "non_ultra"
        and item.get("result") == "pass"
    )
    invariant_passed = all(
        int(invariants.get(key) or 0) == 0
        for key in (
            "prompt_known_failures",
            "prompt_admission_failures",
            "duplicate_equivalent_lanes",
            "unresolved_required_lanes_at_finalization",
            "orphaned_active_sessions_after_cleanup",
            "unbounded_waits_or_closes",
            "policy_mutations_of_active_sessions",
        )
    ) and float(invariants.get("required_lane_completion_rate") or 0.0) == 1.0
    matrix_contract = {
        "medium_read_heavy": any(item["label"] == "medium_read_heavy" and item["result"] == "pass" for item in sessions),
        "heavy_edit": any(item["label"] == "heavy_edit" and item["result"] == "pass" for item in sessions),
        "three_fresh_non_ultra_manager": non_ultra_fresh == 3,
        "five_sequential_manager_turns": sequential_count == 5 and all(
            item["result"] == "pass" for item in sessions if str(item["label"]).startswith("manager_sequential_turn_")
        ),
        "ultra_coexistence": any(item["label"] == "manager_ultra_coexistence" and item["result"] == "pass" for item in sessions),
        "two_repository_concurrency": all(
            any(item["repository_alias"] == alias and item["result"] == "pass" for item in sessions)
            for alias in ("concurrent-a", "concurrent-b")
        ),
    }
    source = ACCEPTANCE.source_binding()
    codex = manifest.get("codex") if isinstance(manifest.get("codex"), Mapping) else {}
    passed = bool(
        all(matrix_contract.values())
        and all(item["result"] == "pass" for item in validations)
        and invariant_passed
        and normal_before == normal_after
        and privacy["status"] == "pass"
        and not state.get("errors")
    )
    payload = {
        "schema_version": "qwendex.manager_live_matrix.v1",
        "run_id": run_id,
        "generated_at": utc_now(),
        **source,
        "runtime_generation": generation_id,
        "hook_generation": str(manifest.get("hook_generation") or ""),
        "runtime_contract_digest": str(manifest.get("contract_sha256") or ""),
        "codex_version": str(codex.get("version") or ""),
        "patch_digest": str(codex.get("patch_sha256") or ""),
        "binary_digest": str(codex.get("binary_sha256") or ""),
        "state_schema_version": 2,
        "commands": commands + [
            {
                "label": item["label"],
                "command": "qdex exec --json <sanitized-live-prompt>",
                "working_directory": "isolated-live-fixture",
                "exit_code": item["exit_code"],
                "duration_seconds": item["duration_seconds"],
            }
            for item in sessions
        ] + validations,
        "matrix_contract": matrix_contract,
        "sessions": sessions,
        "live_session_ids": [str(item.get("thread_id") or "") for item in sessions if item.get("thread_id")],
        "manager_state": state,
        "invariants": invariants,
        "usage": total_usage,
        "normal_codex_isolation": {
            "static_files_checked": sorted(normal_before),
            "unchanged": normal_before == normal_after,
            "authentication_copied_to_isolated_home": True,
        },
        "raw_receipts": {
            "directory": raw_root.name,
            "tracked": False,
            "content_in_sanitized_summary": False,
        },
        "artifact_digests": {
            "live_sessions": ACCEPTANCE.canonical_digest(sessions),
            "manager_state": ACCEPTANCE.canonical_digest(state),
            "matrix_contract": ACCEPTANCE.canonical_digest(matrix_contract),
        },
        "duration_seconds": round(time.monotonic() - started, 6),
        "privacy_result": privacy,
        "privacy_status": privacy["status"],
        "result": "pass" if passed else "fail",
        "final_status": "STOP_MANAGER_ACCEPT_LIVE_ACCEPTED" if passed else "STOP_MANAGER_ACCEPT_LIVE_BLOCKED",
    }
    atomic_write_json(output, payload)
    return payload


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = run_matrix(args.run_id, args.output.resolve())
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_live_matrix.v1",
            "run_id": args.run_id,
            "generated_at": utc_now(),
            "runtime_generation": "",
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_ACCEPT_LIVE_BLOCKED",
            "errors": [str(exc)],
        }
        atomic_write_json(args.output.resolve(), payload)
    envelope = {
        "schema_version": "qwendex.cli.v1",
        "command": "manager-live",
        "status": "pass" if payload.get("result") == "pass" else "blocked",
        "summary": "Manager live matrix passed." if payload.get("result") == "pass" else "Manager live matrix is blocked.",
        "artifacts": [ACCEPTANCE.public_artifact_path(args.output)],
        "next_actions": [] if payload.get("result") == "pass" else ["Inspect the ignored raw live receipts and repair every failed gate."],
        "errors": list(payload.get("errors") or []),
        "data": payload,
    }
    if args.json:
        print(json.dumps(envelope, indent=2, sort_keys=True))
    else:
        print(f"{envelope['status']}: {envelope['summary']}")
    return 0 if envelope["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
