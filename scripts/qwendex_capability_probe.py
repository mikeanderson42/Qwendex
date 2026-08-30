#!/usr/bin/env python3
"""No-model proof of the Qwendex manager launch contract.

The probe is deliberately narrower than runtime/provider attestation.  It
validates the selected generation manifest and structural contract, inspects
the actual ``qdex`` wrapper that would be launched, and runs the manager
preflight in an isolated temporary state directory.  It never sends a model
request, opens a prompt, starts a service, or treats local bookkeeping as
trust.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "qwendex.runtime_capability_receipt.v1"
GENERATION_RE = re.compile(r"^rtg-[A-Za-z0-9][A-Za-z0-9_-]{7,95}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_ARTIFACT_BYTES = 512 * 1024
EXPECTED_WORKER_CAP = 4
EXPECTED_MAX_DEPTH = 2


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if DIGEST_RE.fullmatch(text) else ""


def _int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not stat.S_ISLNK(info.st_mode) and info.st_size <= MAX_ARTIFACT_BYTES


def _empty_receipt(*, status: str, blockers: list[str]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": status,
        "trust": {
            "structural_only": True,
            "live_process_verified": False,
            "provider_attestation_verified": False,
        },
        "generated_at": "",
        "generation_id": "",
        "runtime_generation_digest": "",
        "runtime_tree_digest": "",
        "manifest_digest": "",
        "policy_digest": "",
        "selector_digest": "",
        "selector_target_digest": "",
        "checks": {},
        "launch_contract": {
            "permission_mode": "read-only",
            "sandbox": "read-only",
            "worker_cap": EXPECTED_WORKER_CAP,
            "max_depth": EXPECTED_MAX_DEPTH,
            "native_reservation_mode": "strict",
            "children_can_spawn": False,
            "argv_has_prompt": False,
            "argv_has_plan": False,
        },
        "blockers": sorted(set(blockers)),
        "receipt_digest": "",
    }


def _seal(receipt: dict[str, Any]) -> dict[str, Any]:
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest", None)
    receipt["receipt_digest"] = hashlib.sha256(_canonical(unsigned)).hexdigest()
    return receipt


def _persist(receipt: Mapping[str, Any], output_path: Path | None) -> None:
    if output_path is None:
        return
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(dict(receipt), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, output_path)


def _generation_fields(runtime_root: Path, *, generation_id: str = "") -> tuple[dict[str, Any] | None, list[str]]:
    """Load the selected generation or an explicitly named candidate.

    Candidate inspection deliberately bypasses ``current.json`` so a freshly
    built runtime can be qualified without changing the active selector.
    """

    requested_generation = str(generation_id or "").strip()
    if requested_generation:
        generation_id = requested_generation
    else:
        selection = _read_json(runtime_root / "current.json")
        if selection is None:
            return None, ["runtime_selection_missing_or_invalid"]
        generation_id = str(selection.get("current") or "").strip()
    if not GENERATION_RE.fullmatch(generation_id):
        return None, ["runtime_generation_id_invalid"]
    generation_dir = runtime_root / "generations" / generation_id
    manifest = _read_json(generation_dir / "generation.json")
    if manifest is None:
        return None, ["runtime_generation_manifest_missing_or_invalid"]
    contract_digest = _digest(manifest.get("contract_sha256"))
    if not contract_digest:
        return None, ["runtime_contract_digest_invalid"]
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), Mapping) else {}
    artifacts = manifest.get("artifact_digests") if isinstance(manifest.get("artifact_digests"), Mapping) else {}
    fields = {
        "generation_id": generation_id,
        "runtime_generation_digest": contract_digest,
        "runtime_tree_digest": _digest(artifacts.get("runtime_tree")),
        "manifest_digest": _digest(manifest.get("manifest_sha256")),
        "policy_digest": _digest(contract.get("config_sha256") or manifest.get("config_digest")),
        "generation_dir": generation_dir,
        "manifest": manifest,
    }
    blockers: list[str] = []
    if str(manifest.get("status") or "") != "validated" or str(manifest.get("result") or "") != "pass":
        blockers.append("runtime_generation_not_validated")
    for key in ("runtime_tree_digest", "policy_digest"):
        if not fields[key]:
            blockers.append(f"runtime_{key}_missing")
    return fields, blockers


def _static_wrapper_checks(wrapper: Path, selector: Path) -> tuple[dict[str, bool], list[str]]:
    checks: dict[str, bool] = {}
    blockers: list[str] = []
    for name, path in (("selected_wrapper", wrapper), ("stable_selector", selector)):
        key = f"{name}_regular"
        checks[key] = _regular_file(path)
        if not checks[key]:
            blockers.append(f"{name}_missing_or_invalid")
    texts: dict[str, str] = {}
    for name, path in (("selected_wrapper", wrapper), ("stable_selector", selector)):
        if checks.get(f"{name}_regular"):
            try:
                texts[name] = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                texts[name] = ""
    selected = texts.get("selected_wrapper", "")
    selector_text = texts.get("stable_selector", "")
    checks["wrapper_permission_resolution"] = "QWENDEX_QDEX_PERMISSION_MODE" in selected
    checks["wrapper_read_only_sandbox_injection"] = "cmd+=(--sandbox read-only)" in selected
    checks["wrapper_shared_cap_guard"] = "QWENDEX_GLOBAL_WORKER_CAP" in selected and "strict native reservation" in selected
    checks["wrapper_depth_cap"] = "agents.max_depth=" in selected and "codex_native_max_depth > 2" in selected
    checks["wrapper_private_prompt_posture"] = "root_agent_usage_hint" in selected and "subagent_usage_hint" in selected
    checks["selector_generation_boundary"] = all(marker in selector_text for marker in ("current.json", "generations", "scripts/qdex"))
    for key, value in checks.items():
        if not value:
            blockers.append(f"{key}_missing")
    return checks, blockers


def _manager_preflight(*, source_root: Path, runtime: Mapping[str, str], generation: Mapping[str, Any]) -> tuple[dict[str, bool], list[str], dict[str, Any]]:
    checks = {
        "manager_preflight_pass": False,
        "policy_read_only": False,
        "policy_worker_cap": False,
        "policy_max_depth": False,
        "policy_strict_reservation": False,
        "policy_leaf_cannot_spawn": False,
        "preflight_argv_prompt_free": False,
    }
    blockers: list[str] = []
    with tempfile.TemporaryDirectory(prefix="qwendex-capability-") as temp:
        temp_root = Path(temp)
        env = dict(runtime)
        env.update(
            {
                "QWENDEX_STATE_DB": str(temp_root / "state.sqlite"),
                "QWENDEX_LEDGER_DB": str(temp_root / "ledger.sqlite"),
                "QWENDEX_PERFORMANCE_DB": str(temp_root / "performance.sqlite"),
                "QWENDEX_META_ROOT": str(temp_root / "meta"),
                "QWENDEX_RESULTS_ROOT": str(temp_root / "results"),
                "QWENDEX_CODEX_STATUS_FILE": str(temp_root / "codex-status.json"),
                "QWENDEX_QDEX_PERMISSION_MODE": "read-only",
                "QWENDEX_GLOBAL_WORKER_CAP": str(EXPECTED_WORKER_CAP),
                "QWENDEX_NATIVE_RESERVATION_MODE": "strict",
                "QWENDEX_LOCAL_SUBAGENTS": "0",
                "QWENDEX_PREFER_LOCAL_QWEN": "0",
                "QWENDEX_MANAGER_TARGET_REPO": str(source_root),
                "QWENDEX_RUNTIME_GENERATION_ID": str(generation.get("generation_id") or ""),
                "QWENDEX_RUNTIME_CONTRACT_SHA256": str(generation.get("runtime_generation_digest") or ""),
                "QWENDEX_HOOK_GENERATION": str(generation.get("generation_id") or ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        # This invokes the same CLI policy resolver without recursing through
        # qdex, so the probe remains model-free and network-free.
        command = [
            sys.executable,
            str(ROOT / "scripts" / "qwendex_cli.py"),
            "manager",
            "preflight",
            "--interactive-prompt-unknown",
            "--dry-run",
            "--json",
            "--mode",
            "manager",
        ]
        try:
            result = subprocess.run(
                command,
                cwd=source_root,
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            payload = json.loads(result.stdout) if result.stdout else {}
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            payload = {}
            blockers.append("manager_preflight_unavailable")
            checks["manager_preflight_pass"] = False
            return checks, blockers, payload
    data = payload.get("data") if isinstance(payload, Mapping) else {}
    data = data if isinstance(data, Mapping) else {}
    policy = data.get("agent_policy") if isinstance(data.get("agent_policy"), Mapping) else data.get("policy_snapshot")
    policy = policy if isinstance(policy, Mapping) else {}
    nested = policy.get("nested_spawn") if isinstance(policy.get("nested_spawn"), Mapping) else {}
    checks["manager_preflight_pass"] = bool(result.returncode == 0 and payload.get("status") == "pass" and data.get("ok") is True)
    checks["policy_read_only"] = str(data.get("qdex_permission_mode") or "") == "read-only"
    checks["policy_worker_cap"] = _int(policy.get("max_workers")) == EXPECTED_WORKER_CAP and _int(policy.get("global_worker_cap")) == EXPECTED_WORKER_CAP
    checks["policy_max_depth"] = _int(policy.get("max_depth")) == EXPECTED_MAX_DEPTH and _int(nested.get("max_depth")) == EXPECTED_MAX_DEPTH
    checks["policy_strict_reservation"] = str(policy.get("native_reservation_mode") or "") == "strict"
    checks["policy_leaf_cannot_spawn"] = nested.get("leaf_can_spawn") is False
    prompt = data.get("prompt") if isinstance(data.get("prompt"), Mapping) else {}
    checks["preflight_argv_prompt_free"] = prompt.get("known") is False and not any(
        str(item).strip().lower() in {"--prompt", "--plan", "-p"}
        for item in list(data.get("argv") or [])
        if isinstance(item, str)
    )
    for key, value in checks.items():
        if not value:
            blockers.append(f"{key}_failed")
    return checks, blockers, dict(data)


def probe(
    *,
    runtime_root: Path,
    selector_path: Path,
    source_root: Path = ROOT,
    output_path: Path | None = None,
    runtime_env: Mapping[str, str] | None = None,
    generation_id: str = "",
) -> dict[str, Any]:
    """Run the bounded no-model capability probe and optionally persist it."""

    receipt = _empty_receipt(status="blocked_external", blockers=[])
    fields, blockers = _generation_fields(runtime_root, generation_id=generation_id)
    if fields is None:
        receipt["blockers"] = sorted(set(blockers))
        receipt["generated_at"] = datetime.now(UTC).isoformat()
        receipt = _seal(receipt)
        _persist(receipt, output_path)
        return receipt
    receipt.update(
        {
            "generation_id": fields["generation_id"],
            "runtime_generation_digest": fields["runtime_generation_digest"],
            "runtime_tree_digest": fields["runtime_tree_digest"],
            "manifest_digest": fields["manifest_digest"],
            "policy_digest": fields["policy_digest"],
        }
    )
    generation_dir = fields["generation_dir"]
    selected_wrapper = generation_dir / "tree" / "scripts" / "qdex"
    selected_digest = _sha256(selected_wrapper) if _regular_file(selected_wrapper) else ""
    selector_digest = _sha256(selector_path) if _regular_file(selector_path) else ""
    receipt["selector_digest"] = selector_digest
    receipt["selector_target_digest"] = selected_digest
    static_checks, static_blockers = _static_wrapper_checks(selected_wrapper, selector_path)
    manager_checks, manager_blockers, manager_data = _manager_preflight(
        source_root=source_root,
        runtime=runtime_env or os.environ,
        generation=fields,
    )
    receipt["checks"] = {**static_checks, **manager_checks}
    receipt["blockers"] = sorted(set(blockers + static_blockers + manager_blockers))
    receipt["status"] = "pass" if not receipt["blockers"] else "blocked_external"
    receipt["generated_at"] = datetime.now(UTC).isoformat()
    receipt["manager_preflight"] = {
        "policy_digest": str(manager_data.get("policy_hash") or "") if isinstance(manager_data, Mapping) else "",
        "status": str(manager_data.get("final_status") or "") if isinstance(manager_data, Mapping) else "",
    }
    receipt = _seal(receipt)
    _persist(receipt, output_path)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, default=Path(os.environ.get("QWENDEX_RUNTIME_ROOT") or ROOT / ".qwendex-dev" / "runtime"))
    parser.add_argument(
        "--selector",
        type=Path,
        default=Path(
            os.environ.get("QWENDEX_QDEX_SELECTOR") or Path.home() / ".local" / "bin" / "qdex"
        ),
    )
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument(
        "--generation",
        default="",
        help="probe this validated generation without changing the active runtime selector",
    )
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.output:
        output = args.output
    elif args.generation:
        output = args.runtime_root / "generations" / args.generation / "qwendex_capability_receipt.json"
    else:
        output = args.runtime_root / "qwendex_capability_receipt.json"
    receipt = probe(
        runtime_root=args.runtime_root,
        selector_path=args.selector,
        source_root=args.source_root,
        output_path=output,
        generation_id=args.generation,
    )
    print(json.dumps(receipt, indent=None if args.json else 2, sort_keys=True))
    return 0 if receipt["status"] == "pass" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
