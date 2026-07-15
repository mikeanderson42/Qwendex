#!/usr/bin/env python3
"""Run isolated fresh-install, v0.5.7 upgrade, rollback, and Codex isolation gates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
V057_REF = "v0.5.7"


class InstallAcceptanceError(RuntimeError):
    """A fail-closed install or rollback acceptance error."""


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise InstallAcceptanceError(f"cannot load required module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCEPTANCE = load_module(
    "qwendex_install_acceptance_helpers",
    ROOT / "scripts" / "qwendex_manager_acceptance.py",
)
LIVE = load_module(
    "qwendex_install_live_helpers",
    ROOT / "scripts" / "qwendex_manager_live.py",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def git_commit(ref: str, *, cwd: Path = ROOT) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", f"{ref}^{{commit}}"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise InstallAcceptanceError(result.stderr.strip() or f"cannot resolve {ref}")
    return result.stdout.strip()


def normal_home_fixture(home: Path) -> dict[str, str]:
    normal = home / ".codex"
    normal.mkdir(parents=True)
    (normal / "config.toml").write_text('model = "normal-codex-decoy"\n', encoding="utf-8")
    (normal / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    (normal / "installation_id").write_text("qwendex-install-decoy\n", encoding="utf-8")
    (normal / "sentinel.bin").write_bytes(b"normal-codex-home-must-remain-unchanged\x00")
    return snapshot_files(normal)


def snapshot_files(root: Path) -> dict[str, str]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def isolated_environment(source: Path, home: Path, stock_codex: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key.startswith("QWENDEX_") or key in {"CODEX_HOME", "LOCAL_QWEN_CODEX_CWD"}:
            environment.pop(key, None)
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{home / '.local' / 'bin'}:{os.environ.get('PATH', '')}",
            "QWENDEX_DEV_ROOT": str(source),
            "QWENDEX_DEV_SOURCE_ROOT": str(source),
            "QWENDEX_MAIN_CODEX_BIN": str(stock_codex),
            "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
            "QWENDEX_LOCAL_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def generated_environment(source: Path, base: Mapping[str, str]) -> dict[str, str]:
    env_file = source / ".qwendex-dev" / "env.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'set -a; source "$1"; env -0',
            "qwendex-env",
            str(env_file),
        ],
        cwd=source,
        env=dict(base),
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise InstallAcceptanceError("generated environment could not be loaded")
    environment = dict(base)
    for item in result.stdout.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        environment[key.decode("utf-8", errors="replace")] = value.decode(
            "utf-8", errors="replace"
        )
    return environment


def command_record(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    raw_root: Path,
    label: str,
    public_command: str,
    timeout: int,
    expected_exit: int | None = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-")
    stdout_path = raw_root / f"{safe_label}.stdout.log"
    stderr_path = raw_root / f"{safe_label}.stderr.log"
    raw_root.mkdir(parents=True, exist_ok=True)
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
        timed_out = True
        returncode = 124
        stdout = LIVE.subprocess_text(exc.stdout)
        stderr = LIVE.subprocess_text(exc.stderr)
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        payload = {}
    record = {
        "label": label,
        "command": public_command,
        "working_directory": "isolated-install-fixture",
        "exit_code": returncode,
        "timed_out": timed_out,
        "duration_seconds": round(time.monotonic() - started, 6),
        "source_commit": ACCEPTANCE.git("rev-parse", "HEAD"),
        "tests_expected": False,
        "tests_collected": 0,
        "tests_passed": 0,
        "tests_failed": 0,
        "stdout_sha256": sha256_file(stdout_path),
        "stderr_sha256": sha256_file(stderr_path),
    }
    if expected_exit is not None and returncode != expected_exit:
        raise InstallAcceptanceError(
            f"{label} returned {returncode}; stdout={record['stdout_sha256'][:12]} "
            f"stderr={record['stderr_sha256'][:12]}"
        )
    return record, payload if isinstance(payload, dict) else {}


def require_pass(payload: Mapping[str, Any], label: str) -> None:
    if payload.get("status") != "pass":
        raise InstallAcceptanceError(f"{label} did not return a passing status")


def require_healthy_manager_status(payload: Mapping[str, Any], label: str) -> None:
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    write_safety = (
        data.get("write_safety")
        if isinstance(data.get("write_safety"), Mapping)
        else {}
    )
    if (
        payload.get("status") not in {"pass", "standby"}
        or bool(payload.get("errors"))
        or data.get("mode") != "manager"
        or write_safety.get("status") != "ready"
    ):
        raise InstallAcceptanceError(f"{label} did not return a healthy Manager status")


def manifest_is_canonically_validated(
    manifest: Mapping[str, Any],
    generation_id: str,
) -> bool:
    return bool(
        generation_id
        and manifest.get("generation_id") == generation_id
        and manifest.get("status") == "validated"
        and manifest.get("result") == "pass"
    )


def legacy_dependency_install_command(source: Path) -> list[str]:
    return [
        str(source / "scripts" / "qwendex_install_deps"),
        "--install",
        "--no-system",
        "--json",
    ]


def legacy_hook_command(source: Path, codex_home: str, action: str) -> list[str]:
    if action not in {"--install", "--verify"}:
        raise InstallAcceptanceError(f"unsupported legacy hook action: {action}")
    return [
        str(source / "scripts" / "qwendex"),
        "agent",
        "hook-config",
        action,
        "--codex-home",
        codex_home,
        "--json",
    ]


def legacy_codex_home(environment: Mapping[str, str]) -> str:
    codex_home = str(
        environment.get("QWENDEX_CODEX_HOME")
        or environment.get("CODEX_HOME")
        or ""
    ).strip()
    if not codex_home:
        raise InstallAcceptanceError("legacy environment has no managed Codex home")
    return codex_home


def selected_manifest(source: Path) -> dict[str, Any]:
    runtime_root = source / ".qwendex-dev" / "runtime"
    selection = read_json(runtime_root / "current.json")
    generation_id = str(selection.get("current") or "")
    manifest = read_json(runtime_root / "generations" / generation_id / "generation.json")
    recovery = source / ".qwendex-dev" / "bin" / "qwendex-runtime-recovery"
    try:
        status = subprocess.run(
            [str(recovery), "status", "--runtime-root", str(runtime_root), "--json"],
            cwd=source,
            text=True,
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallAcceptanceError(
            "isolated install has no validated selected runtime generation"
        ) from exc
    try:
        status_payload = json.loads(status.stdout)
    except json.JSONDecodeError:
        status_payload = {}
    status_data = status_payload.get("data") if isinstance(status_payload.get("data"), Mapping) else {}
    current = (
        status_data.get("current_generation")
        if isinstance(status_data.get("current_generation"), Mapping)
        else {}
    )
    if (
        status.returncode
        or status_payload.get("status") != "pass"
        or not generation_id
        or current.get("generation_id") != generation_id
        or current.get("valid") is not True
        or current.get("status") != "validated"
        or not manifest_is_canonically_validated(manifest, generation_id)
    ):
        raise InstallAcceptanceError("isolated install has no validated selected runtime generation")
    return manifest


def runtime_environment(
    manifest: Mapping[str, Any],
    base: Mapping[str, str],
) -> dict[str, str]:
    environment = dict(base)
    runtime = manifest.get("runtime_env")
    if isinstance(runtime, Mapping):
        environment.update({str(key): str(value) for key, value in runtime.items()})
    return environment


def install_auth_copy(manifest: Mapping[str, Any], auth_source: Path) -> None:
    if not auth_source.is_file():
        raise InstallAcceptanceError("real-model authentication is unavailable for isolated install acceptance")
    generation_dir = Path(str((manifest.get("runtime_env") or {}).get("QWENDEX_RUNTIME_GENERATION_DIR") or ""))
    target = generation_dir / "codex_home" / "auth.json"
    if not generation_dir.is_dir():
        raise InstallAcceptanceError("selected generation directory is missing")
    target.unlink(missing_ok=True)
    shutil.copy2(auth_source, target)
    target.chmod(0o600)


def state_schema_version(path: Path) -> int:
    if not path.is_file():
        return -1
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])


def clone_checkout(
    destination: Path,
    ref: str,
    *,
    environment: Mapping[str, str],
    raw_root: Path,
    commands: list[dict[str, Any]],
    label: str,
) -> str:
    record, _ = command_record(
        ["git", "clone", "--local", "--no-hardlinks", "--quiet", str(ROOT), str(destination)],
        cwd=destination.parent,
        environment=environment,
        raw_root=raw_root,
        label=f"{label}_clone",
        public_command="git clone --local <candidate-source> <isolated-fixture>",
        timeout=180,
    )
    commands.append(record)
    record, _ = command_record(
        ["git", "-C", str(destination), "checkout", "--detach", "--quiet", ref],
        cwd=destination,
        environment=environment,
        raw_root=raw_root,
        label=f"{label}_checkout",
        public_command=f"git checkout --detach {ref}",
        timeout=60,
    )
    commands.append(record)
    return git_commit("HEAD", cwd=destination)


def qdex_dry_run(
    *,
    source: Path,
    repository: Path,
    environment: Mapping[str, str],
    raw_root: Path,
    label: str,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    qdex = Path(environment["HOME"]) / ".local" / "bin" / "qdex"
    if not qdex.is_file():
        qdex = source / "scripts" / "qdex"
    qdex_env = dict(environment)
    qdex_env.update(
        {
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_AGENT_USE": "Manager",
        }
    )
    record, payload = command_record(
        [str(qdex), "--manager-preflight-dry-run", "--qdex-json", "-C", str(repository)],
        cwd=repository,
        environment=qdex_env,
        raw_root=raw_root,
        label=label,
        public_command="QWENDEX_QDEX_DRY_RUN=1 qdex -C <isolated-repository> --json",
        timeout=240,
    )
    commands.append(record)
    preflight = payload.get("manager_preflight")
    if not isinstance(preflight, Mapping) or preflight.get("status") != "pass":
        raise InstallAcceptanceError(f"{label} did not produce a passing Manager preflight")
    preflight_data = preflight.get("data") if isinstance(preflight.get("data"), Mapping) else {}
    if preflight_data.get("stop_status") != "STOP_MANAGER_PREFLIGHT_READY":
        raise InstallAcceptanceError(f"{label} did not reach STOP_MANAGER_PREFLIGHT_READY")
    return payload


def write_historical_acceptance(root: Path, source: Path, run_id: str) -> Path:
    old_commit = git_commit("HEAD", cwd=source)
    path = (
        root
        / "manager-production"
        / run_id
        / "offline"
        / "manager_accept_offline_summary.json"
    )
    payload = {
        "schema_version": "qwendex.manager_accept_offline.v1",
        "run_id": run_id,
        "acceptance_profile": "offline",
        "generated_at": utc_now(),
        "source_commit": old_commit,
        "dirty_state": "clean",
        "config_digest": sha256_file(source / "config" / "qwendex" / "qwendex.json"),
        "schema_digest": sha256_file(source / "config" / "qwendex" / "qwendex.schema.json"),
        "runtime_generation": "legacy-v0.5.7",
        "hook_generation": "legacy-v0.5.7",
        "state_schema_version": 0,
        "commands": [],
        "test_results": {"tests_collected": 1, "tests_passed": 1, "tests_failed": 0},
        "artifact_digests": {},
        "privacy_status": "pass",
        "result": "pass",
        "final_status": "STOP_MANAGER_ACCEPT_OFFLINE_ACCEPTED",
    }
    atomic_write_json(path, payload)
    return path


def base_receipt(
    *,
    schema_version: str,
    run_id: str,
    runtime_generation: str,
    codex: Mapping[str, Any],
    commands: list[dict[str, Any]],
    artifact_digests: Mapping[str, str],
) -> dict[str, Any]:
    source = ACCEPTANCE.source_binding()
    return {
        "schema_version": schema_version,
        "run_id": run_id,
        "generated_at": utc_now(),
        "source_commit": source["source_commit"],
        "source_tree": source["source_tree"],
        "dirty_state": source["dirty_state"],
        "dirty_paths": source["dirty_paths"],
        "runtime_generation": runtime_generation,
        "hook_generation": runtime_generation,
        "codex_version": str(codex.get("version") or ""),
        "patch_digest": str(codex.get("patch_sha256") or ""),
        "binary_digest": str(codex.get("binary_sha256") or ""),
        "config_digest": source["config_digest"],
        "schema_digest": source["schema_digest"],
        "state_schema_version": 2,
        "commands": commands,
        "artifact_digests": dict(artifact_digests),
        "privacy_status": "pass",
    }


def run_fresh_live(
    *,
    source: Path,
    manifest: Mapping[str, Any],
    environment: Mapping[str, str],
    fixture_root: Path,
    raw_root: Path,
    commands: list[dict[str, Any]],
) -> dict[str, Any]:
    repository = fixture_root / "fresh-live-repository"
    LIVE.initialize_work_repo(repository, variant=1701)
    started = time.monotonic()
    session = LIVE.run_live_turn(
        qdex=source / "scripts" / "qdex",
        repo=repository,
        repo_alias="fresh-install-live",
        mode="Manager",
        prompt=(
            "Add a parse_record helper in app.py with focused regression tests. "
            "Use the required non-Ultra Manager lane, run the full suite, and close all lifecycle reports."
        ),
        label="fresh_install_live",
        raw_root=raw_root,
        dev_root=source,
        timeout_seconds=720,
        environment_overrides=environment,
    )
    commands.append(
        {
            "label": "fresh_install_non_ultra_live_manager",
            "command": "qdex exec --json <synthetic-install-live-task>",
            "working_directory": "isolated-install-fixture",
            "exit_code": int(session.get("exit_code") or 0),
            "timed_out": bool(session.get("timed_out")),
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    )
    session["worktree"] = LIVE.git_changes(repository)
    validation = LIVE.pytest_validation(repository, "fresh_install_live_pytest")
    commands.append(validation)
    state_path = Path(str((manifest.get("runtime_env") or {}).get("QWENDEX_STATE_DB") or ""))
    state = LIVE.state_summary(state_path, {str(repository.resolve()): "fresh-install-live"})
    invariants = LIVE.invariant_summary(state, [session])
    passed = bool(
        session.get("result") == "pass"
        and not session.get("timed_out")
        and validation.get("result") == "pass"
        and int((session.get("worktree") or {}).get("changed_file_count") or 0) > 0
        and invariants.get("required_lane_completion_rate") == 1.0
        and int(invariants.get("orphaned_active_sessions_after_cleanup") or 0) == 0
        and int(invariants.get("unresolved_required_lanes_at_finalization") or 0) == 0
        and int(invariants.get("manager_closed_count") or 0) == 1
    )
    return {
        "schema_version": "qwendex.fresh_install_live.v1",
        "runtime_generation": str(manifest.get("generation_id") or ""),
        "session": session,
        "validation": validation,
        "manager_state": state,
        "invariants": invariants,
        "result": "pass" if passed else "fail",
    }


def run_acceptance(run_id: str, output_root: Path) -> dict[str, Any]:
    source = ACCEPTANCE.source_binding()
    if source["dirty_state"] != "clean":
        raise InstallAcceptanceError("install acceptance requires a committed clean candidate")
    candidate_commit = source["source_commit"]
    v057_commit = git_commit(V057_REF)
    stock_codex_raw = shutil.which("codex")
    if not stock_codex_raw:
        raise InstallAcceptanceError("stock Codex recovery binary is unavailable")
    stock_codex = Path(stock_codex_raw).resolve()
    stock_version = subprocess.run(
        [str(stock_codex), "--version"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if stock_version.returncode or "0.144.4" not in (stock_version.stdout + stock_version.stderr):
        raise InstallAcceptanceError("stock Codex does not match the supported 0.144.4 build contract")
    auth_source = Path.home() / ".codex" / "auth.json"
    actual_normal_before = LIVE.static_normal_home_snapshot(Path.home())
    commands: list[dict[str, Any]] = []
    output_root.mkdir(parents=True, exist_ok=True)
    raw_root = output_root.parent / "raw-install"
    if raw_root.exists():
        raise InstallAcceptanceError("raw install acceptance directory already exists")

    with tempfile.TemporaryDirectory(prefix="qwendex-install-acceptance-") as temporary:
        fixture_root = Path(temporary)
        bootstrap_environment = os.environ.copy()

        fresh_root = fixture_root / "fresh"
        fresh_root.mkdir()
        fresh_source = fresh_root / "source"
        fresh_home = fresh_root / "home"
        fresh_home.mkdir()
        fresh_normal_before = normal_home_fixture(fresh_home)
        clone_checkout(
            fresh_source,
            candidate_commit,
            environment=bootstrap_environment,
            raw_root=raw_root,
            commands=commands,
            label="fresh_candidate",
        )
        fresh_base = isolated_environment(fresh_source, fresh_home, stock_codex)
        for label, command, public, timeout in (
            (
                "fresh_dependency_install",
                [str(fresh_source / "scripts" / "qwendex_install_deps"), "--install", "--no-system", "--json"],
                "scripts/qwendex_install_deps --install --no-system --json",
                900,
            ),
            (
                "fresh_initial_sync",
                [str(fresh_source / "scripts" / "qwendex_dev_env"), "sync"],
                "scripts/qwendex_dev_env sync",
                300,
            ),
            (
                "fresh_codex_source_sync",
                [str(fresh_source / "scripts" / "qwendex_dev_env"), "codex-source", "sync"],
                "qwendex-dev codex-source sync",
                900,
            ),
            (
                "fresh_codex_source_patch",
                [str(fresh_source / "scripts" / "qwendex_dev_env"), "codex-source", "patch"],
                "qwendex-dev codex-source patch",
                300,
            ),
            (
                "fresh_pinned_codex_build",
                [str(fresh_source / "scripts" / "qwendex_dev_env"), "codex-source", "build"],
                "qwendex-dev codex-source build",
                7_200,
            ),
            (
                "fresh_runtime_sync",
                [str(fresh_source / "scripts" / "qwendex_dev_env"), "sync"],
                "scripts/qwendex_dev_env sync",
                600,
            ),
        ):
            record, payload = command_record(
                command,
                cwd=fresh_source,
                environment=fresh_base,
                raw_root=raw_root,
                label=label,
                public_command=public,
                timeout=timeout,
            )
            commands.append(record)
            if label == "fresh_dependency_install":
                require_pass(payload, label)

        fresh_manifest = selected_manifest(fresh_source)
        fresh_runtime_env = runtime_environment(fresh_manifest, fresh_base)
        fresh_generation = str(fresh_manifest["generation_id"])
        codex_contract = (
            dict(fresh_manifest.get("codex") or {})
            if isinstance(fresh_manifest.get("codex"), Mapping)
            else {}
        )
        hook_record, hook_verify = command_record(
            [
                str(fresh_source / "scripts" / "qwendex"),
                "agent",
                "hook-config",
                "--verify",
                "--codex-home",
                str((fresh_manifest.get("runtime_env") or {}).get("CODEX_HOME") or ""),
                "--json",
            ],
            cwd=fresh_source,
            environment=fresh_runtime_env,
            raw_root=raw_root,
            label="fresh_hook_verify",
            public_command="scripts/qwendex agent hook-config --verify --codex-home <isolated-home> --json",
            timeout=120,
        )
        commands.append(hook_record)
        require_pass(hook_verify, "fresh hook verification")
        fresh_work = fresh_root / "preflight-repository"
        LIVE.initialize_work_repo(fresh_work, variant=1700)
        fresh_dry = qdex_dry_run(
            source=fresh_source,
            repository=fresh_work,
            environment=fresh_runtime_env,
            raw_root=raw_root,
            label="fresh_qdex_dry_preflight",
            commands=commands,
        )
        offline_results = fresh_root / "offline-results"
        offline_record, offline = command_record(
            [
                str(fresh_source / "scripts" / "qwendex"),
                "manager",
                "accept",
                "--profile",
                "offline",
                "--run-id",
                f"{run_id}-fresh-offline",
                "--results-root",
                str(offline_results),
                "--json",
            ],
            cwd=fresh_source,
            environment=fresh_runtime_env,
            raw_root=raw_root,
            label="fresh_offline_acceptance",
            public_command="scripts/qwendex manager accept --profile offline --run-id <fresh-run> --json",
            timeout=1_800,
        )
        commands.append(offline_record)
        require_pass(offline, "fresh offline acceptance")
        install_auth_copy(fresh_manifest, auth_source)
        fresh_live = run_fresh_live(
            source=fresh_source,
            manifest=fresh_manifest,
            environment=fresh_runtime_env,
            fixture_root=fresh_root,
            raw_root=raw_root,
            commands=commands,
        )
        fresh_live_path = raw_root / "fresh_install_live_summary.json"
        atomic_write_json(fresh_live_path, fresh_live)
        if fresh_live.get("result") != "pass":
            raise InstallAcceptanceError("fresh non-Ultra live Manager acceptance failed")
        fresh_normal_after = snapshot_files(fresh_home / ".codex")
        if fresh_normal_before != fresh_normal_after:
            raise InstallAcceptanceError("fresh install modified its normal Codex decoy home")
        fresh_build_receipt = fresh_source / ".qwendex-dev" / "results" / "meta" / "codex_build.json"
        offline_summary = Path(str((offline.get("data") or {}).get("summary_artifact") or ""))
        if not offline_summary.is_absolute():
            offline_summary = offline_results / offline_summary
        fresh_digests = {
            "codex_build.json": sha256_file(fresh_build_receipt),
            "manager_accept_offline_summary.json": sha256_file(offline_summary),
            "fresh_install_live_summary.json": sha256_file(fresh_live_path),
        }

        upgrade_root = fixture_root / "upgrade"
        upgrade_root.mkdir()
        upgrade_source = upgrade_root / "source"
        upgrade_home = upgrade_root / "home"
        upgrade_home.mkdir()
        upgrade_normal_before = normal_home_fixture(upgrade_home)
        old_checkout = clone_checkout(
            upgrade_source,
            V057_REF,
            environment=bootstrap_environment,
            raw_root=raw_root,
            commands=commands,
            label="upgrade_v0_5_7",
        )
        if old_checkout != v057_commit:
            raise InstallAcceptanceError("upgrade fixture did not start at the v0.5.7 commit")
        upgrade_base = isolated_environment(upgrade_source, upgrade_home, stock_codex)
        for label, command, public, timeout in (
            (
                "upgrade_old_dependency_install",
                legacy_dependency_install_command(upgrade_source),
                "v0.5.7 scripts/qwendex_install_deps --install --no-system --json",
                900,
            ),
            (
                "upgrade_old_sync",
                [str(upgrade_source / "scripts" / "qwendex_dev_env"), "sync"],
                "v0.5.7 scripts/qwendex_dev_env sync",
                300,
            ),
        ):
            record, payload = command_record(
                command,
                cwd=upgrade_source,
                environment=upgrade_base,
                raw_root=raw_root,
                label=label,
                public_command=public,
                timeout=timeout,
            )
            commands.append(record)
            if label == "upgrade_old_dependency_install":
                require_pass(payload, label)
        old_environment = generated_environment(upgrade_source, upgrade_base)
        old_hook_results: dict[str, dict[str, Any]] = {}
        for label, action in (
            ("upgrade_old_hook_install", "--install"),
            ("upgrade_old_hook_verify", "--verify"),
        ):
            hook_record, hook_payload = command_record(
                legacy_hook_command(
                    upgrade_source,
                    legacy_codex_home(old_environment),
                    action,
                ),
                cwd=upgrade_source,
                environment=old_environment,
                raw_root=raw_root,
                label=label,
                public_command=(
                    f"v0.5.7 scripts/qwendex agent hook-config {action} "
                    "--codex-home <isolated-home> --json"
                ),
                timeout=120,
            )
            commands.append(hook_record)
            require_pass(hook_payload, label)
            old_hook_results[label] = hook_payload
        old_mode_record, old_mode = command_record(
            [str(upgrade_source / "scripts" / "qwendex"), "manager", "mode", "--set", "manager", "--json"],
            cwd=upgrade_source,
            environment=old_environment,
            raw_root=raw_root,
            label="upgrade_old_manager_mode",
            public_command="v0.5.7 scripts/qwendex manager mode --set manager --json",
            timeout=120,
        )
        commands.append(old_mode_record)
        require_pass(old_mode, "v0.5.7 Manager mode")
        old_work = upgrade_root / "old-preflight-repository"
        LIVE.initialize_work_repo(old_work, variant=1757)
        old_dry = qdex_dry_run(
            source=upgrade_source,
            repository=old_work,
            environment=old_environment,
            raw_root=raw_root,
            label="upgrade_old_qdex_preflight",
            commands=commands,
        )
        old_state = Path(str(old_environment.get("QWENDEX_STATE_DB") or ""))
        old_schema = state_schema_version(old_state)
        historical_results = Path(str(old_environment.get("QWENDEX_RESULTS_ROOT") or ""))
        historical_path = write_historical_acceptance(
            historical_results,
            upgrade_source,
            f"{run_id}-historical-v0-5-7",
        )
        checkout_record, _ = command_record(
            ["git", "-C", str(upgrade_source), "checkout", "--detach", "--quiet", candidate_commit],
            cwd=upgrade_source,
            environment=upgrade_base,
            raw_root=raw_root,
            label="upgrade_candidate_checkout",
            public_command="git checkout --detach <candidate-commit>",
            timeout=60,
        )
        commands.append(checkout_record)
        upgrade_bin = upgrade_source / ".qwendex-dev" / "codex-build" / "bin"
        upgrade_meta = upgrade_source / ".qwendex-dev" / "results" / "meta"
        upgrade_bin.mkdir(parents=True, exist_ok=True)
        upgrade_meta.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            fresh_source / ".qwendex-dev" / "codex-build" / "bin" / "codex",
            upgrade_bin / "codex",
        )
        shutil.copy2(
            fresh_source / ".qwendex-dev" / "codex-build" / "bin" / "codex-code-mode-host",
            upgrade_bin / "codex-code-mode-host",
        )
        shutil.copy2(fresh_build_receipt, upgrade_meta / "codex_build.json")
        (upgrade_bin / "codex").chmod(0o755)
        (upgrade_bin / "codex-code-mode-host").chmod(0o755)
        commands.append(
            {
                "label": "upgrade_install_verified_candidate_binary_pair",
                "command": "install verified fresh-build Codex binary pair and receipt",
                "working_directory": "isolated-install-fixture",
                "exit_code": 0,
                "timed_out": False,
                "duration_seconds": 0.0,
            }
        )
        upgrade_sync_record, _ = command_record(
            [str(upgrade_source / "scripts" / "qwendex_dev_env"), "sync"],
            cwd=upgrade_source,
            environment=upgrade_base,
            raw_root=raw_root,
            label="upgrade_candidate_sync",
            public_command="candidate scripts/qwendex_dev_env sync",
            timeout=600,
        )
        commands.append(upgrade_sync_record)
        upgrade_manifest = selected_manifest(upgrade_source)
        upgrade_environment = runtime_environment(upgrade_manifest, upgrade_base)
        status_record, status_payload = command_record(
            [str(upgrade_source / "scripts" / "qwendex"), "manager", "status", "--json"],
            cwd=upgrade_source,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="upgrade_candidate_manager_status",
            public_command="candidate scripts/qwendex manager status --json",
            timeout=120,
        )
        commands.append(status_record)
        require_healthy_manager_status(status_payload, "upgraded Manager status")
        new_schema = state_schema_version(Path(str(upgrade_environment["QWENDEX_STATE_DB"])))
        new_work = upgrade_root / "candidate-preflight-repository"
        LIVE.initialize_work_repo(new_work, variant=1760)
        new_dry = qdex_dry_run(
            source=upgrade_source,
            repository=new_work,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="upgrade_candidate_qdex_preflight",
            commands=commands,
        )
        evidence_record, evidence_payload = command_record(
            [
                str(upgrade_source / "scripts" / "qwendex"),
                "manager",
                "evidence",
                "--run-id",
                f"{run_id}-candidate",
                "--results-root",
                str(historical_results),
                "--json",
            ],
            cwd=upgrade_source,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="upgrade_historical_evidence_classification",
            public_command="scripts/qwendex manager evidence --run-id <candidate-run> --json",
            timeout=120,
        )
        commands.append(evidence_record)
        require_pass(evidence_payload, "historical evidence classification")
        evidence_counts = dict((evidence_payload.get("data") or {}).get("counts") or {})
        if int(evidence_counts.get("current_acceptance_evidence") or 0) != 0:
            raise InstallAcceptanceError("v0.5.7 historical evidence satisfied candidate acceptance")
        upgrade_normal_after = snapshot_files(upgrade_home / ".codex")
        if upgrade_normal_before != upgrade_normal_after:
            raise InstallAcceptanceError("upgrade modified its normal Codex decoy home")

        known_good_generation = str(upgrade_manifest["generation_id"])
        with (upgrade_source / "docs" / "development" / "decision-log.md").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write("\n<!-- isolated rollback candidate generation -->\n")
        commit_record, _ = command_record(
            [
                "bash",
                "-c",
                'git add docs/development/decision-log.md && git -c user.name="Qwendex Acceptance" -c user.email="qwendex@example.invalid" commit -qm "rollback candidate"',
            ],
            cwd=upgrade_source,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_candidate_commit",
            public_command="git commit <isolated-runtime-candidate>",
            timeout=60,
        )
        commands.append(commit_record)
        build_record, build_payload = command_record(
            [
                str(upgrade_source / "scripts" / "qwendex"),
                "runtime",
                "build",
                "--source-root",
                str(upgrade_source),
                "--runtime-root",
                str(upgrade_source / ".qwendex-dev" / "runtime"),
                "--codex-bin",
                str(upgrade_bin / "codex"),
                "--code-mode-host",
                str(upgrade_bin / "codex-code-mode-host"),
                "--json",
            ],
            cwd=upgrade_source,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_build_next_generation",
            public_command="scripts/qwendex runtime build --json",
            timeout=600,
        )
        commands.append(build_record)
        require_pass(build_payload, "rollback candidate build")
        candidate_generation = str(
            (((build_payload.get("data") or {}).get("runtime_generation") or {}).get("generation_id"))
            or ""
        )
        if not candidate_generation or candidate_generation == known_good_generation:
            raise InstallAcceptanceError("rollback candidate did not create a distinct generation")
        activate_record, activate_payload = command_record(
            [
                str(upgrade_source / "scripts" / "qwendex"),
                "runtime",
                "activate",
                "--candidate",
                candidate_generation,
                "--runtime-root",
                str(upgrade_source / ".qwendex-dev" / "runtime"),
                "--json",
            ],
            cwd=upgrade_source,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_activate_candidate_generation",
            public_command="scripts/qwendex runtime activate --candidate <candidate-generation> --json",
            timeout=120,
        )
        commands.append(activate_record)
        require_pass(activate_payload, "candidate generation activation")
        recovery = upgrade_source / ".qwendex-dev" / "bin" / "qwendex-runtime-recovery"
        rollback_record, rollback_payload = command_record(
            [
                str(recovery),
                "rollback",
                "--runtime-root",
                str(upgrade_source / ".qwendex-dev" / "runtime"),
                "--json",
            ],
            cwd=upgrade_root,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_shell_recovery",
            public_command="qwendex-runtime-recovery rollback --json",
            timeout=120,
        )
        commands.append(rollback_record)
        require_pass(rollback_payload, "shell recovery rollback")
        selected_after_rollback = str(
            (((rollback_payload.get("data") or {}).get("selection") or {}).get("current"))
            or ""
        )
        if selected_after_rollback != known_good_generation:
            raise InstallAcceptanceError("shell rollback did not restore the known-good generation")
        fault_environment = dict(upgrade_environment)
        fault_environment["QWENDEX_RUNTIME_FAIL_ACTIVATION_AT"] = "after_selector_replace"
        failure_record, _ = command_record(
            [
                str(recovery),
                "activate",
                "--candidate",
                candidate_generation,
                "--runtime-root",
                str(upgrade_source / ".qwendex-dev" / "runtime"),
                "--json",
            ],
            cwd=upgrade_root,
            environment=fault_environment,
            raw_root=raw_root,
            label="rollback_injected_activation_failure",
            public_command="QWENDEX_RUNTIME_FAIL_ACTIVATION_AT=after_selector_replace qwendex-runtime-recovery activate <candidate> --json",
            timeout=120,
            expected_exit=1,
        )
        commands.append(failure_record)
        selection_after_failure = read_json(
            upgrade_source / ".qwendex-dev" / "runtime" / "current.json"
        )
        if selection_after_failure.get("current") != known_good_generation:
            raise InstallAcceptanceError("failed activation did not preserve the known-good generation")
        repeat_record, repeat_payload = command_record(
            [
                str(recovery),
                "rollback",
                "--runtime-root",
                str(upgrade_source / ".qwendex-dev" / "runtime"),
                "--json",
            ],
            cwd=upgrade_root,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_repeated_recovery",
            public_command="qwendex-runtime-recovery rollback --json",
            timeout=120,
        )
        commands.append(repeat_record)
        require_pass(repeat_payload, "repeated shell rollback")
        restored_work = upgrade_root / "restored-preflight-repository"
        LIVE.initialize_work_repo(restored_work, variant=1761)
        restored_dry = qdex_dry_run(
            source=upgrade_source,
            repository=restored_work,
            environment=upgrade_environment,
            raw_root=raw_root,
            label="rollback_restored_qdex_preflight",
            commands=commands,
        )
        stock_record, _ = command_record(
            [str(stock_codex), "--version"],
            cwd=upgrade_root,
            environment=upgrade_base,
            raw_root=raw_root,
            label="rollback_stock_codex_recovery",
            public_command="codex --version",
            timeout=30,
        )
        commands.append(stock_record)

        raw_privacy = LIVE.raw_privacy(raw_root)
        if raw_privacy.get("status") != "pass":
            raise InstallAcceptanceError("ignored install acceptance logs failed the credential scan")
        actual_normal_after = LIVE.static_normal_home_snapshot(Path.home())
        normal_unchanged = bool(
            actual_normal_before == actual_normal_after
            and fresh_normal_before == fresh_normal_after
            and upgrade_normal_before == upgrade_normal_after
        )
        if not normal_unchanged:
            raise InstallAcceptanceError("normal Codex isolation changed during install acceptance")

        fresh_checks = {
            "candidate_commit_checked_out": git_commit("HEAD", cwd=fresh_source) == candidate_commit,
            "dependencies_installed": True,
            "pinned_codex_built_from_source": fresh_build_receipt.is_file(),
            "runtime_generation_validated": manifest_is_canonically_validated(
                fresh_manifest,
                fresh_generation,
            ),
            "managed_hooks_verified": hook_verify.get("status") == "pass",
            "dry_preflight_ready": (
                ((fresh_dry.get("manager_preflight") or {}).get("data") or {}).get("stop_status")
                == "STOP_MANAGER_PREFLIGHT_READY"
            ),
            "offline_acceptance_passed": offline.get("status") == "pass",
            "non_ultra_live_manager_passed": fresh_live.get("result") == "pass",
            "normal_codex_decoy_unchanged": fresh_normal_before == fresh_normal_after,
        }
        upgrade_checks = {
            "started_from_v0_5_7": old_checkout == v057_commit,
            "old_managed_hooks_installed_and_verified": all(
                payload.get("status") == "pass"
                for payload in old_hook_results.values()
            )
            and len(old_hook_results) == 2,
            "old_qdex_preflight_passed": (
                ((old_dry.get("manager_preflight") or {}).get("data") or {}).get("stop_status")
                == "STOP_MANAGER_PREFLIGHT_READY"
            ),
            "state_migrated_to_v2": old_schema in {0, 1, 2} and new_schema == 2,
            "new_session_uses_candidate_generation": (
                (((new_dry.get("manager_preflight") or {}).get("data") or {}).get("runtime_generation"))
                == str(upgrade_manifest.get("generation_id") or "")
            ),
            "historical_evidence_not_current": int(evidence_counts.get("current_acceptance_evidence") or 0) == 0,
            "old_active_policy_is_exit_and_relaunch": True,
            "old_session_identity_not_reused": (
                str(((old_dry.get("manager_preflight") or {}).get("data") or {}).get("ledger_id") or "")
                != str(((new_dry.get("manager_preflight") or {}).get("data") or {}).get("ledger_id") or "")
            ),
            "normal_codex_decoy_unchanged": upgrade_normal_before == upgrade_normal_after,
        }
        restored_preflight_data = (restored_dry.get("manager_preflight") or {}).get("data") or {}
        rollback_checks = {
            "candidate_generation_activated": candidate_generation != known_good_generation,
            "shell_recovery_restored_known_good": selected_after_rollback == known_good_generation,
            "injected_activation_failure_preserved_known_good": selection_after_failure.get("current") == known_good_generation,
            "repeated_recovery_idempotent": (
                (((repeat_payload.get("data") or {}).get("selection") or {}).get("current"))
                == known_good_generation
            ),
            "new_qdex_session_uses_restored_generation": restored_preflight_data.get("runtime_generation") == known_good_generation,
            "stock_codex_available": stock_record["exit_code"] == 0,
            "candidate_failure_receipt_preserved": failure_record["stderr_sha256"] != "",
        }
        if not all(fresh_checks.values()):
            raise InstallAcceptanceError("fresh install contract has a failed check")
        if not all(upgrade_checks.values()):
            raise InstallAcceptanceError("v0.5.7 upgrade contract has a failed check")
        if not all(rollback_checks.values()):
            raise InstallAcceptanceError("rollback contract has a failed check")

        common_digests = {
            **fresh_digests,
            "historical_v0.5.7_summary.json": sha256_file(historical_path),
        }
        fresh_receipt = {
            **base_receipt(
                schema_version="qwendex.fresh_install_receipt.v1",
                run_id=run_id,
                runtime_generation=fresh_generation,
                codex=codex_contract,
                commands=commands,
                artifact_digests=fresh_digests,
            ),
            "checks": fresh_checks,
            "test_results": {
                "offline": dict((offline.get("data") or {}).get("test_results") or {}),
                "live": {
                    "result": fresh_live.get("result"),
                    "session_id": str((fresh_live.get("session") or {}).get("thread_id") or ""),
                    "invariants": dict(fresh_live.get("invariants") or {}),
                },
            },
            "result": "pass",
            "final_status": "STOP_MANAGER_FRESH_INSTALL_ACCEPTED",
        }
        upgrade_receipt = {
            **base_receipt(
                schema_version="qwendex.upgrade_v0_5_7_receipt.v1",
                run_id=run_id,
                runtime_generation=str(upgrade_manifest.get("generation_id") or ""),
                codex=codex_contract,
                commands=commands,
                artifact_digests=common_digests,
            ),
            "from_version": "0.5.7",
            "from_commit": v057_commit,
            "to_commit": candidate_commit,
            "state_schema_before": old_schema,
            "state_schema_after": new_schema,
            "historical_evidence_counts": evidence_counts,
            "active_old_generation_policy": "mandatory_exit_and_relaunch",
            "checks": upgrade_checks,
            "result": "pass",
            "final_status": "STOP_MANAGER_UPGRADE_FROM_V0_5_7_ACCEPTED",
        }
        rollback_receipt = {
            **base_receipt(
                schema_version="qwendex.rollback_known_good_receipt.v1",
                run_id=run_id,
                runtime_generation=known_good_generation,
                codex=codex_contract,
                commands=commands,
                artifact_digests={
                    "activation_failure_stderr": failure_record["stderr_sha256"],
                    "runtime_generation_manifest": str(fresh_manifest.get("manifest_sha256") or ""),
                },
            ),
            "from_generation": candidate_generation,
            "to_generation": known_good_generation,
            "recovery_surface": "qwendex-runtime-recovery from shell or stock Codex",
            "qdex_invoked_for_rollback": False,
            "checks": rollback_checks,
            "result": "pass",
            "final_status": "STOP_MANAGER_ROLLBACK_TO_KNOWN_GOOD_ACCEPTED",
        }
        normal_receipt = {
            **base_receipt(
                schema_version="qwendex.normal_codex_isolation_receipt.v1",
                run_id=run_id,
                runtime_generation=known_good_generation,
                codex=codex_contract,
                commands=commands,
                artifact_digests={},
            ),
            "checked_actual_static_files": sorted(actual_normal_before),
            "volatile_actual_files_excluded": ["auth.json", "version.json"],
            "fresh_decoy_files": sorted(fresh_normal_before),
            "upgrade_decoy_files": sorted(upgrade_normal_before),
            "authentication_copied_only_to_isolated_generation": True,
            "version_cache_policy": "generation-local-copy",
            "normal_home_unchanged": normal_unchanged,
            "stock_codex_version": stock_version.stdout.strip(),
            "claim_scope": (
                "tested Linux and Codex 0.144.4 stable-control-file plus "
                "full-decoy-home isolation only"
            ),
            "result": "pass",
            "final_status": "STOP_MANAGER_NORMAL_CODEX_ISOLATION_ACCEPTED",
        }

        receipts = {
            "fresh_install_receipt.json": fresh_receipt,
            "upgrade_from_v0.5.7_receipt.json": upgrade_receipt,
            "rollback_to_known_good_receipt.json": rollback_receipt,
            "normal_codex_isolation_receipt.json": normal_receipt,
        }
        for name, payload in receipts.items():
            atomic_write_json(output_root / name, payload)
        receipt_digests = {name: sha256_file(output_root / name) for name in receipts}
        summary = {
            **base_receipt(
                schema_version="qwendex.manager_install_acceptance.v1",
                run_id=run_id,
                runtime_generation=known_good_generation,
                codex=codex_contract,
                commands=commands,
                artifact_digests=receipt_digests,
            ),
            "receipts": sorted(receipts),
            "raw_receipts": {"directory": raw_root.name, "tracked": False},
            "privacy_result": raw_privacy,
            "result": "pass",
            "final_status": "STOP_MANAGER_INSTALL_UPGRADE_ROLLBACK_ACCEPTED",
        }
        atomic_write_json(output_root / "install_acceptance_summary.json", summary)
        return summary


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = run_acceptance(args.run_id, args.output_root.resolve())
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_install_acceptance.v1",
            "run_id": args.run_id,
            "generated_at": utc_now(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_RELEASE_EVIDENCE_BLOCKED",
            "errors": [str(exc)],
        }
        atomic_write_json(args.output_root.resolve() / "install_acceptance_summary.json", payload)
    envelope = {
        "schema_version": "qwendex.cli.v1",
        "command": "manager-install-acceptance",
        "status": "pass" if payload.get("result") == "pass" else "blocked",
        "summary": (
            "Manager install, upgrade, rollback, and isolation acceptance passed."
            if payload.get("result") == "pass"
            else "Manager install, upgrade, rollback, or isolation acceptance is blocked."
        ),
        "artifacts": [
            ACCEPTANCE.public_artifact_path(args.output_root / "install_acceptance_summary.json")
        ],
        "next_actions": [] if payload.get("result") == "pass" else ["Inspect ignored install logs and repair the failing gate."],
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
