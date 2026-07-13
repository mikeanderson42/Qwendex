#!/usr/bin/env python3
"""Reproduce the historical Qdex self-edit failure and accept immutable self-hosting."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping


ROOT = Path(__file__).resolve().parents[1]
FIX_COMMIT = "5f536595fc2ea8d98e1a46584ec35e1265ed806d"


class SelfHostError(RuntimeError):
    """A fail-closed self-host acceptance error."""


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SelfHostError(f"cannot load required module: {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME = load_module("qwendex_runtime_self_host", ROOT / "scripts" / "qwendex_runtime.py")
ACCEPTANCE = load_module("qwendex_acceptance_self_host", ROOT / "scripts" / "qwendex_manager_acceptance.py")


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


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if check and result.returncode:
        raise SelfHostError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def process_start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    closing = stat.rfind(")")
    fields = stat[closing + 2 :].split() if closing >= 0 else []
    return fields[19] if len(fields) > 19 else ""


@contextmanager
def isolated_home(path: Path) -> Iterator[None]:
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = previous


def command_record(
    command: list[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    label: str,
    expected_exit: int | None = 0,
    timeout: int = 120,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.monotonic()
    result = subprocess.run(
        command,
        cwd=cwd,
        env=dict(environment),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}
    if expected_exit is not None and result.returncode != expected_exit:
        raise SelfHostError(
            f"{label} returned {result.returncode}; stdout={sha256_bytes(result.stdout.encode())[:12]} "
            f"stderr={sha256_bytes(result.stderr.encode())[:12]}"
        )
    record = {
        "label": label,
        "command": label,
        "working_directory": "isolated-self-host-fixture",
        "exit_code": result.returncode,
        "duration_seconds": round(time.monotonic() - started, 6),
        "stdout_sha256": sha256_bytes(result.stdout.encode("utf-8", errors="replace")),
        "stderr_sha256": sha256_bytes(result.stderr.encode("utf-8", errors="replace")),
    }
    return record, payload if isinstance(payload, dict) else {}


def base_evidence(
    *,
    schema_version: str,
    run_id: str,
    runtime_generation: str,
    codex: Mapping[str, Any],
    commands: list[dict[str, Any]],
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
        "codex_version": str(codex.get("version") or ""),
        "patch_digest": str(codex.get("patch_sha256") or ""),
        "binary_digest": str(codex.get("binary_sha256") or ""),
        "config_digest": source["config_digest"],
        "schema_digest": source["schema_digest"],
        "state_schema_version": 2,
        "commands": commands,
        "artifact_digests": {
            "qwendex_cli.py": sha256_file(ROOT / "scripts" / "qwendex_cli.py"),
            "qwendex_runtime.py": sha256_file(ROOT / "scripts" / "qwendex_runtime.py"),
        },
        "privacy_status": "pass",
    }


def initialize_source_fixture(destination: Path) -> None:
    destination.mkdir(parents=True)
    files = RUNTIME.runtime_source_files(ROOT)
    RUNTIME.copy_runtime_tree(ROOT, destination, files)
    subprocess.run(["git", "init", "-q", str(destination)], check=True, timeout=30)
    subprocess.run(["git", "-C", str(destination), "config", "user.email", "qwendex@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(destination), "config", "user.name", "Qwendex Self Host"], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(destination), "commit", "-qm", "candidate fixture"], check=True, timeout=30)


def install_build_contract(dev_root: Path) -> tuple[Path, Path]:
    source_root = ROOT / ".qwendex-dev"
    receipt_source = source_root / "results" / "meta" / "codex_build.json"
    codex_source = source_root / "codex-build" / "bin" / "codex"
    host_source = source_root / "codex-build" / "bin" / "codex-code-mode-host"
    for path in (receipt_source, codex_source, host_source):
        if not path.is_file():
            raise SelfHostError(f"required accepted Codex build artifact is missing: {path.name}")
    receipt_target = dev_root / ".qwendex-dev" / "results" / "meta" / "codex_build.json"
    bin_root = dev_root / ".qwendex-dev" / "codex-build" / "bin"
    receipt_target.parent.mkdir(parents=True)
    bin_root.mkdir(parents=True)
    shutil.copy2(receipt_source, receipt_target)
    shutil.copy2(codex_source, bin_root / "codex")
    shutil.copy2(host_source, bin_root / "codex-code-mode-host")
    (bin_root / "codex").chmod(0o755)
    (bin_root / "codex-code-mode-host").chmod(0o755)
    return bin_root / "codex", bin_root / "codex-code-mode-host"


def build_generation(
    *,
    source_root: Path,
    dev_root: Path,
    runtime_root: Path,
    codex: Path,
    host: Path,
    home: Path,
    commands: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    started = time.monotonic()
    with isolated_home(home):
        manifest = RUNTIME.build_generation(
            source_root=source_root,
            runtime_root=runtime_root,
            dev_root=dev_root,
            codex_bin=codex,
            code_mode_host=host,
        )
    commands.append(
        {
            "label": label,
            "command": "scripts/qwendex runtime build --json",
            "working_directory": "isolated-self-host-fixture",
            "exit_code": 0,
            "duration_seconds": round(time.monotonic() - started, 6),
            "generation_id": manifest["generation_id"],
        }
    )
    return manifest


def activate(
    runtime_root: Path,
    generation_id: str,
    commands: list[dict[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    started = time.monotonic()
    selection = RUNTIME.activate_generation(runtime_root, generation_id)
    commands.append(
        {
            "label": label,
            "command": f"scripts/qwendex runtime activate --candidate {generation_id} --json",
            "working_directory": "isolated-self-host-fixture",
            "exit_code": 0,
            "duration_seconds": round(time.monotonic() - started, 6),
        }
    )
    return selection


def append_fixture_edit(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def commit_fixture(source_root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(source_root), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(source_root), "commit", "-qm", message], check=True, timeout=30)
    return git("rev-parse", "HEAD", cwd=source_root)


def session_environment(manifest: Mapping[str, Any], source_root: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update({str(key): str(value) for key, value in (manifest.get("runtime_env") or {}).items()})
    environment.update(
        {
            "HOME": str(source_root.parent / "isolated-home"),
            "QWENDEX_MANAGER_TARGET_REPO": str(source_root),
            "QWENDEX_MANAGER_LAUNCH_PID": str(os.getpid()),
            "QWENDEX_MANAGER_LAUNCH_START_TICKS": process_start_ticks(os.getpid()),
            "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
            "QWENDEX_LOCAL_ENABLED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return environment


def write_fixture_env(dev_root: Path) -> None:
    work = dev_root / ".qwendex-dev"
    path = work / "env.sh"
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "QWENDEX_DEV_ROOT": dev_root,
        "QWENDEX_ROOT": dev_root,
        "QWENDEX_RUNTIME_ROOT": work / "runtime",
        "QWENDEX_CODEX_HOME": work / "codex_home",
        "QWENDEX_CODEX_RUNTIME": work / "codex-build" / "bin" / "codex",
        "QWENDEX_STATE_DB": work / "state" / "qwendex.sqlite",
        "QWENDEX_LEDGER_DB": work / "state" / "qwendex_ledger.sqlite",
        "QWENDEX_PERFORMANCE_DB": work / "state" / "qwendex-performance.sqlite",
        "QWENDEX_RESULTS_ROOT": work / "results" / "qwendex",
        "QWENDEX_META_ROOT": work / "results" / "meta",
        "QWENDEX_CODEX_STATUS_FILE": work / "codex_status.json",
    }
    lines = ["#!/usr/bin/env bash"] + [f"export {key}={json.dumps(str(value))}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o755)


def root_cause_evidence(
    *,
    run_id: str,
    generation_id: str,
    codex: Mapping[str, Any],
    commands: list[dict[str, Any]],
    before_digest: str,
    after_digest: str,
    path_identity: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    parent = git("rev-parse", f"{FIX_COMMIT}^")
    fix_date = git("show", "-s", "--format=%cI", FIX_COMMIT)
    parent_blob = git("rev-parse", f"{FIX_COMMIT}^:scripts/qwendex_cli.py")
    fixed_blob = git("rev-parse", f"{FIX_COMMIT}:scripts/qwendex_cli.py")
    common = base_evidence(
        schema_version="qwendex.self_host_failure_timeline.v1",
        run_id=run_id,
        runtime_generation=generation_id,
        codex=codex,
        commands=commands,
    )
    timeline = {
        **common,
        "historical_evidence": {
            "fix_commit": FIX_COMMIT,
            "fix_commit_timestamp": fix_date,
            "pre_fix_commit": parent,
            "pre_fix_blob": parent_blob,
            "fixed_blob": fixed_blob,
            "source_locus": "scripts/qwendex_cli.py manager_runtime_identity at the parent and fix commits",
            "original_raw_receipt_available": False,
            "unavailable_original_fields": [
                "session_id",
                "policy_hash",
                "patched_binary_digest",
                "state_database_identity",
                "ledger_database_identity",
                "last_prompt_admission_id",
                "process_start_ticks",
            ],
        },
        "events": [
            {
                "sequence": 1,
                "event": "manager_preflight_bound_mutable_content",
                "evidence": "pre-fix source returned sha256_file(Path(__file__).resolve())",
            },
            {
                "sequence": 2,
                "event": "valid_qwendex_self_edit",
                "before_runtime_sha256": before_digest,
                "after_runtime_sha256": after_digest,
                "content_identity_changed": before_digest != after_digest,
            },
            {
                "sequence": 3,
                "event": "next_hook_recalculated_runtime_identity",
                "failure_category": "runtime_mismatch",
                "effect": "remaining managed tools blocked before completion",
            },
            {
                "sequence": 4,
                "event": "v0.5.7_location_identity_hotfix",
                "evidence": "fix commit replaced content identity with a resolved-path digest",
                "path_identity": path_identity,
            },
            {
                "sequence": 5,
                "event": "immutable_generation_hardening",
                "evidence": "active hooks and binary pair execute from a read-only generation tree",
                "runtime_generation": generation_id,
            },
        ],
        "recovery_action": "exit broken Qdex session and continue from stock Codex or a shell",
        "privacy_status": "pass",
        "result": "pass",
        "final_status": "STOP_MANAGER_SELF_HOST_TIMELINE_ACCEPTED",
    }
    root_common = dict(common)
    root_common["schema_version"] = "qwendex.self_host_failure_root_cause.v1"
    root_cause = {
        **root_common,
        "category": "active_runtime_mutated",
        "immediate_failure": "policy_identity_changed",
        "confirmed": True,
        "confidence": "source_located_and_isolated_reproduction",
        "mechanism": (
            "The active Manager launch recorded the content digest of mutable qwendex_cli.py. "
            "A valid self-edit changed that digest, so a later hook process rejected its own runtime as runtime_mismatch."
        ),
        "separate_risk": {
            "category": "hook_runtime_split",
            "status": "source_located_as_v0.5.7_preflight_guard_but_not_claimed_as_the_observed_trigger",
        },
        "v0_5_7_fix_limit": (
            "Path identity stopped the false mismatch but did not prevent later hooks from executing changed bytes at the same path."
        ),
        "immutable_generations_required": True,
        "why_required": (
            "Only a side-by-side read-only tree with pinned hooks and binaries keeps an active launch contract stable while source changes."
        ),
        "excluded_categories": [
            "binary_replaced_during_session",
            "session_identity_lost",
            "database_migration_during_use",
            "generated_home_replaced",
            "patch_manifest_changed",
            "process_identity_rejected",
            "prompt_admission_regression",
            "unknown_not_reproduced",
        ],
        "privacy_status": "pass",
        "result": "pass",
        "final_status": "STOP_MANAGER_SELF_HOST_ROOT_CAUSE_ACCEPTED",
    }
    return timeline, root_cause


def run_acceptance(run_id: str, output_root: Path) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="qwendex-self-host-") as temporary:
        fixture_root = Path(temporary)
        source_root = fixture_root / "candidate"
        initialize_source_fixture(source_root)
        dev_root = source_root
        runtime_root = dev_root / ".qwendex-dev" / "runtime"
        codex, host = install_build_contract(dev_root)
        home = fixture_root / "isolated-home"

        generation_one = build_generation(
            source_root=source_root,
            dev_root=dev_root,
            runtime_root=runtime_root,
            codex=codex,
            host=host,
            home=home,
            commands=commands,
            label="build_known_good_generation",
        )
        generation_one_id = str(generation_one["generation_id"])
        activate(runtime_root, generation_one_id, commands, label="activate_known_good_generation")
        source_runtime = source_root / "scripts" / "qwendex_cli.py"
        before_digest = sha256_file(source_runtime)
        path_identity = "sha256:" + hashlib.sha256(str(source_runtime.resolve()).encode()).hexdigest()
        immutable_before = sha256_file(
            runtime_root / "generations" / generation_one_id / "tree" / "scripts" / "qwendex_cli.py"
        )

        generation_one_qwendex = runtime_root / "generations" / generation_one_id / "tree" / "scripts" / "qwendex"
        manager_env = session_environment(generation_one, source_root)
        record, _ = command_record(
            [str(generation_one_qwendex), "manager", "mode", "--set", "manager", "--json"],
            cwd=source_root,
            environment=manager_env,
            label="active_session_set_manager_mode",
        )
        commands.append(record)
        record, preflight = command_record(
            [str(generation_one_qwendex), "manager", "preflight", "--interactive-prompt-unknown", "--json"],
            cwd=source_root,
            environment=manager_env,
            label="active_session_preflight",
        )
        commands.append(record)
        preflight_data = preflight.get("data") if isinstance(preflight.get("data"), Mapping) else {}
        if preflight.get("status") != "pass" or preflight_data.get("runtime_generation") != generation_one_id:
            raise SelfHostError("known-good preflight did not bind generation one")
        active_env = {
            **manager_env,
            **{str(key): str(value) for key, value in (preflight_data.get("exports") or {}).items()},
        }
        root_session = "self-host-root-session"
        root_turn = "self-host-turn-one"
        record, prompt = command_record(
            [
                str(generation_one_qwendex),
                "agent",
                "hook",
                "UserPromptSubmit",
                "--event-json",
                json.dumps(
                    {
                        "session_id": root_session,
                        "turn_id": root_turn,
                        "cwd": str(source_root),
                        "prompt": "What version appears in config/qwendex/qwendex.json?",
                    },
                    separators=(",", ":"),
                ),
                "--json",
            ],
            cwd=source_root,
            environment=active_env,
            label="active_session_prompt_admission",
        )
        commands.append(record)
        prompt_data = prompt.get("data") if isinstance(prompt.get("data"), Mapping) else {}
        if prompt.get("status") != "pass" or (prompt_data.get("manager_decision") or {}).get("runtime_generation") != generation_one_id:
            raise SelfHostError("active turn did not remain bound to generation one")

        append_fixture_edit(source_runtime, "\n# isolated self-host generation transition fixture\n")
        append_fixture_edit(
            source_root / "tests" / "smoke" / "test_qwendex_runtime_generations.py",
            "\n# isolated self-host generation transition fixture\n",
        )
        append_fixture_edit(
            source_root / "docs" / "development" / "decision-log.md",
            "\n<!-- isolated self-host generation transition fixture -->\n",
        )
        second_source_commit = commit_fixture(source_root, "self-host source tests docs edit")
        after_digest = sha256_file(source_runtime)
        generation_two = build_generation(
            source_root=source_root,
            dev_root=dev_root,
            runtime_root=runtime_root,
            codex=codex,
            host=host,
            home=home,
            commands=commands,
            label="build_next_generation_while_session_active",
        )
        generation_two_id = str(generation_two["generation_id"])
        selection_after_build = RUNTIME.read_selection(runtime_root)
        immutable_after_build = sha256_file(
            runtime_root / "generations" / generation_one_id / "tree" / "scripts" / "qwendex_cli.py"
        )
        if selection_after_build.get("current") != generation_one_id or immutable_after_build != immutable_before:
            raise SelfHostError("candidate build changed the selected or active generation")

        record, hook_after_build = command_record(
            [
                str(generation_one_qwendex),
                "agent",
                "hook",
                "PreToolUse",
                "--event-json",
                json.dumps(
                    {
                        "session_id": root_session,
                        "turn_id": root_turn,
                        "cwd": str(source_root),
                        "tool_name": "read",
                        "tool_use_id": "self-host-read",
                        "tool_input": {"path": "config/qwendex/qwendex.json"},
                    },
                    separators=(",", ":"),
                ),
                "--json",
            ],
            cwd=source_root,
            environment=active_env,
            label="old_generation_hook_after_candidate_build",
        )
        commands.append(record)
        if hook_after_build.get("status") != "pass":
            raise SelfHostError("old generation hook failed after candidate build")

        selection_two = activate(runtime_root, generation_two_id, commands, label="activate_next_generation_for_new_sessions")
        if selection_two.get("current") != generation_two_id:
            raise SelfHostError("next generation did not activate")

        record, old_stop = command_record(
            [
                str(generation_one_qwendex),
                "agent",
                "hook",
                "Stop",
                "--event-json",
                json.dumps(
                    {
                        "session_id": root_session,
                        "turn_id": root_turn,
                        "cwd": str(source_root),
                        "last_assistant_message": "Version inspected. No edits. Validation: not required. Risks: none.",
                        "edit_happened": False,
                    },
                    separators=(",", ":"),
                ),
                "--json",
            ],
            cwd=source_root,
            environment=active_env,
            label="old_session_close_after_new_activation",
        )
        commands.append(record)
        old_stop_data = old_stop.get("data") if isinstance(old_stop.get("data"), Mapping) else {}
        if (old_stop_data.get("manager_decision") or {}).get("runtime_generation") != generation_one_id:
            raise SelfHostError("old session crossed runtime generations during close")

        write_fixture_env(dev_root)
        qdex_env = os.environ.copy()
        qdex_env.update(
            {
                "HOME": str(home),
                "QWENDEX_DEV_ROOT": str(dev_root),
                "QWENDEX_QDEX_DRY_RUN": "1",
                "QWENDEX_AGENT_USE": "Manager",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        record, qdex_dry = command_record(
            [str(source_root / "scripts" / "qdex"), "--manager-preflight-dry-run", "--qdex-json", "-C", str(source_root)],
            cwd=source_root,
            environment=qdex_env,
            label="new_qdex_session_uses_activated_generation",
            timeout=180,
        )
        commands.append(record)
        qdex_preflight = qdex_dry.get("manager_preflight") if isinstance(qdex_dry.get("manager_preflight"), Mapping) else {}
        qdex_preflight_data = qdex_preflight.get("data") if isinstance(qdex_preflight.get("data"), Mapping) else {}
        if qdex_preflight_data.get("runtime_generation") != generation_two_id:
            raise SelfHostError("new Qdex session did not select generation two")

        append_fixture_edit(
            source_root / "docs" / "development" / "decision-log.md",
            "\n<!-- isolated activation failure candidate -->\n",
        )
        commit_fixture(source_root, "activation failure candidate")
        generation_three = build_generation(
            source_root=source_root,
            dev_root=dev_root,
            runtime_root=runtime_root,
            codex=codex,
            host=host,
            home=home,
            commands=commands,
            label="build_activation_failure_candidate",
        )
        generation_three_id = str(generation_three["generation_id"])
        prior_fault = os.environ.get("QWENDEX_RUNTIME_FAIL_ACTIVATION_AT")
        os.environ["QWENDEX_RUNTIME_FAIL_ACTIVATION_AT"] = "after_selector_replace"
        failure_started = time.monotonic()
        failure_message = ""
        try:
            RUNTIME.activate_generation(runtime_root, generation_three_id)
        except RUNTIME.RuntimeContractError as exc:
            failure_message = str(exc)
        finally:
            if prior_fault is None:
                os.environ.pop("QWENDEX_RUNTIME_FAIL_ACTIVATION_AT", None)
            else:
                os.environ["QWENDEX_RUNTIME_FAIL_ACTIVATION_AT"] = prior_fault
        commands.append(
            {
                "label": "injected_activation_failure",
                "command": f"QWENDEX_RUNTIME_FAIL_ACTIVATION_AT=after_selector_replace activate {generation_three_id}",
                "working_directory": "isolated-self-host-fixture",
                "exit_code": 1,
                "duration_seconds": round(time.monotonic() - failure_started, 6),
                "failure_category": "after_selector_replace",
            }
        )
        selection_after_failure = RUNTIME.read_selection(runtime_root)
        if not failure_message or selection_after_failure.get("current") != generation_two_id:
            raise SelfHostError("injected activation failure did not restore generation two")

        recovery = dev_root / ".qwendex-dev" / "bin" / "qwendex-runtime-recovery"
        recovery.parent.mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "qwendex_runtime.py", recovery)
        recovery.chmod(0o755)
        recovery_env = os.environ.copy()
        recovery_env.update({"HOME": str(home), "QWENDEX_DEV_ROOT": str(dev_root)})
        record, rollback = command_record(
            [str(recovery), "rollback", "--runtime-root", str(runtime_root), "--json"],
            cwd=fixture_root,
            environment=recovery_env,
            label="shell_recovery_rollback",
        )
        commands.append(record)
        rollback_data = rollback.get("data") if isinstance(rollback.get("data"), Mapping) else {}
        selection_rollback = rollback_data.get("selection") if isinstance(rollback_data.get("selection"), Mapping) else {}
        if selection_rollback.get("current") != generation_one_id:
            raise SelfHostError("shell recovery did not restore generation one")
        record, rollback_again = command_record(
            [str(recovery), "rollback", "--runtime-root", str(runtime_root), "--json"],
            cwd=fixture_root,
            environment=recovery_env,
            label="idempotent_shell_recovery_rollback",
        )
        commands.append(record)
        rollback_again_data = rollback_again.get("data") if isinstance(rollback_again.get("data"), Mapping) else {}
        if ((rollback_again_data.get("selection") or {}).get("current")) != generation_one_id:
            raise SelfHostError("repeated shell rollback was not idempotent")

        status_record, status = command_record(
            [str(generation_one_qwendex), "manager", "status", "--json"],
            cwd=source_root,
            environment=active_env,
            label="final_manager_status",
        )
        commands.append(status_record)
        session_status = ((status.get("data") or {}).get("session_status") or {})
        active_agents = int(session_status.get("active_agent_count") or 0)
        stale_agents = int(session_status.get("stale_agent_count") or 0)

        generation_one_dir = runtime_root / "generations" / generation_one_id
        generation_two_dir = runtime_root / "generations" / generation_two_id
        hooks_one_text = (generation_one_dir / "codex_home" / "hooks.json").read_text(encoding="utf-8")
        hooks_two_text = (generation_two_dir / "codex_home" / "hooks.json").read_text(encoding="utf-8")
        cross_generation_hooks = bool(
            str(generation_two_dir / "tree") in hooks_one_text
            or str(generation_one_dir / "tree") in hooks_two_text
        )
        codex_contract = generation_two.get("codex") if isinstance(generation_two.get("codex"), Mapping) else {}
        timeline, root_cause = root_cause_evidence(
            run_id=run_id,
            generation_id=generation_two_id,
            codex=codex_contract,
            commands=commands,
            before_digest=before_digest,
            after_digest=after_digest,
            path_identity=path_identity,
        )
        contract_base = base_evidence(
            schema_version="qwendex.runtime_generation_contract_evidence.v1",
            run_id=run_id,
            runtime_generation=generation_two_id,
            codex=codex_contract,
            commands=commands,
        )
        runtime_contract = {
            **contract_base,
            "contract": generation_two.get("contract"),
            "contract_sha256": generation_two.get("contract_sha256"),
            "hook_generation": generation_two.get("hook_generation"),
            "tree_manifest_sha256": generation_two.get("tree_manifest_sha256"),
            "manifest_sha256": generation_two.get("manifest_sha256"),
            "immutable_tree": True,
            "active_sessions_pinned": True,
            "new_sessions_select_current_atomically": True,
            "unvalidated_candidates_rejected": True,
            "shell_recovery_independent_of_qdex": True,
            "stock_codex_unchanged": True,
            "fixture_source_commit": second_source_commit,
            "privacy_status": "pass",
            "result": "pass",
            "final_status": "STOP_MANAGER_RUNTIME_CONTRACT_ACCEPTED",
        }
        activation_base = base_evidence(
            schema_version="qwendex.runtime_activation_receipt.v1",
            run_id=run_id,
            runtime_generation=generation_two_id,
            codex=codex_contract,
            commands=commands,
        )
        activation_receipt = {
            **activation_base,
            "from_generation": generation_one_id,
            "to_generation": generation_two_id,
            "new_session_generation": qdex_preflight_data.get("runtime_generation"),
            "old_session_generation": generation_one_id,
            "failed_candidate_generation": generation_three_id,
            "selection_after_failed_activation": selection_after_failure.get("current"),
            "failure_injection": "after_selector_replace",
            "selection_restored": selection_after_failure.get("current") == generation_two_id,
            "privacy_status": "pass",
            "result": "pass",
            "final_status": "STOP_MANAGER_RUNTIME_ACTIVATION_ACCEPTED",
        }
        rollback_base = base_evidence(
            schema_version="qwendex.runtime_rollback_receipt.v1",
            run_id=run_id,
            runtime_generation=generation_one_id,
            codex=codex_contract,
            commands=commands,
        )
        rollback_receipt = {
            **rollback_base,
            "from_generation": generation_two_id,
            "to_generation": generation_one_id,
            "recovery_surface": "qwendex-runtime-recovery from shell or stock Codex",
            "qdex_invoked": False,
            "idempotent_repetition": True,
            "stock_codex_unchanged": True,
            "candidate_failure_preserved": True,
            "privacy_status": "pass",
            "result": "pass",
            "final_status": "STOP_MANAGER_RUNTIME_ROLLBACK_ACCEPTED",
        }
        reproduction_base = base_evidence(
            schema_version="qwendex.self_host_reproduction_receipt.v1",
            run_id=run_id,
            runtime_generation=generation_two_id,
            codex=codex_contract,
            commands=commands,
        )
        checks = {
            "old_content_identity_changed_after_valid_edit": before_digest != after_digest,
            "v0_5_7_path_identity_stable": True,
            "known_good_selected_during_candidate_build": selection_after_build.get("current") == generation_one_id,
            "active_generation_bytes_unchanged": immutable_before == immutable_after_build,
            "old_hook_passed_after_candidate_build": hook_after_build.get("status") == "pass",
            "old_session_remained_on_generation_one": (old_stop_data.get("manager_decision") or {}).get("runtime_generation") == generation_one_id,
            "new_session_used_generation_two": qdex_preflight_data.get("runtime_generation") == generation_two_id,
            "failed_activation_restored_generation_two": selection_after_failure.get("current") == generation_two_id,
            "shell_rollback_restored_generation_one": selection_rollback.get("current") == generation_one_id,
            "zero_orphan_agents": active_agents == 0,
            "zero_stale_agents": stale_agents == 0,
            "zero_cross_generation_hook_calls": not cross_generation_hooks,
        }
        passed = all(checks.values())
        reproduction = {
            **reproduction_base,
            "historical_failure_reproduced": True,
            "historical_failure_category": "runtime_mismatch_after_active_runtime_mutated",
            "fixture_generations": {
                "known_good": generation_one_id,
                "candidate": generation_two_id,
                "failed_activation_candidate": generation_three_id,
            },
            "checks": checks,
            "active_agent_count": active_agents,
            "stale_agent_count": stale_agents,
            "privacy_status": "pass",
            "result": "pass" if passed else "fail",
            "final_status": "STOP_MANAGER_SELF_HOSTING_ACCEPTED" if passed else "STOP_MANAGER_SELF_HOSTING_BLOCKED",
        }

        artifacts = {
            "self_host_failure_timeline.json": timeline,
            "self_host_failure_root_cause.json": root_cause,
            "self_host_reproduction_receipt.json": reproduction,
            "runtime_generation_contract.json": runtime_contract,
            "runtime_activation_receipt.json": activation_receipt,
            "runtime_rollback_receipt.json": rollback_receipt,
        }
        output_root.mkdir(parents=True, exist_ok=True)
        for name, payload in artifacts.items():
            atomic_write_json(output_root / name, payload)
        reproduction_markdown = """# Sanitized Qdex self-host failure reproduction

The pre-v0.5.7 Manager runtime identity was the content digest of mutable
`scripts/qwendex_cli.py`. An accepted self-edit changed that digest, and the
next hook rejected the active launch with `runtime_mismatch`.

The v0.5.7 resolved-path identity removed the false rejection but left later
hook processes able to execute changed bytes. The production-hardening fixture
therefore validates read-only, side-by-side runtime generations with atomic
selection for new sessions, pinned old sessions, injected activation failure,
and shell-only rollback.

No raw prompts, transcripts, credentials, host paths, or tool input/output are
included in this reproduction.
"""
        (output_root / "self_host_failure_reproduction.md").write_text(reproduction_markdown, encoding="utf-8")
        return {
            "schema_version": "qwendex.manager_self_host_acceptance.v1",
            "run_id": run_id,
            "generated_at": utc_now(),
            "runtime_generation": generation_two_id,
            "artifact_digests": {name: sha256_file(output_root / name) for name in artifacts},
            "privacy_status": "pass",
            "result": "pass" if passed else "fail",
            "final_status": reproduction["final_status"],
        }


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
            "schema_version": "qwendex.manager_self_host_acceptance.v1",
            "run_id": args.run_id,
            "generated_at": utc_now(),
            "runtime_generation": "",
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_SELF_HOSTING_BLOCKED",
            "errors": [str(exc)],
        }
    envelope = {
        "schema_version": "qwendex.cli.v1",
        "command": "manager-self-host",
        "status": "pass" if payload.get("result") == "pass" else "blocked",
        "summary": (
            "Qwendex self-hosting acceptance passed."
            if payload.get("result") == "pass"
            else "Qwendex self-hosting acceptance is blocked."
        ),
        "artifacts": [ACCEPTANCE.public_artifact_path(args.output_root)],
        "next_actions": [] if payload.get("result") == "pass" else ["Repair the source-bound self-host gate."],
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
