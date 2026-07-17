#!/usr/bin/env python3
"""Immutable Qwendex runtime generations and shell-safe recovery.

This module deliberately depends only on the Python standard library.  The
normal ``scripts/qwendex runtime`` facade imports it, while the generated
``qwendex-runtime-recovery`` command can execute it directly when Qdex or the
active Qwendex source tree is unusable.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
GENERATION_SCHEMA = "qwendex.runtime_generation.v1"
SELECTION_SCHEMA = "qwendex.runtime_selection.v1"
COMMAND_SCHEMA = "qwendex.cli.v1"
RUNTIME_STATE_SCHEMA_VERSION = 1
RUNTIME_SNAPSHOT_PATHS = (
    ".codex",
    ".github",
    ".gitignore",
    "AGENTS.md",
    "QWENDEX_STARTUP.md",
    "README.md",
    "RELEASE.md",
    "config",
    "docs",
    "llmstack",
    "public",
    "scripts",
    "tests",
)
MANAGER_TERMINAL_STATES = {"blocked", "closed", "failed", "tombstoned"}


class RuntimeContractError(RuntimeError):
    """A fail-closed runtime generation or selector error."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_bytes(path: Path, payload: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Mapping[str, Any], *, mode: int = 0o600) -> None:
    atomic_write_bytes(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n",
        mode=mode,
    )


def stable_envelope(
    *,
    action: str,
    status: str,
    summary: str,
    data: Mapping[str, Any] | None = None,
    artifacts: Iterable[str] = (),
    errors: Iterable[str] = (),
    next_actions: Iterable[str] = (),
) -> dict[str, Any]:
    return {
        "schema_version": COMMAND_SCHEMA,
        "command": "runtime",
        "action": action,
        "status": status,
        "summary": summary,
        "artifacts": list(artifacts),
        "next_actions": list(next_actions),
        "errors": list(errors),
        "data": dict(data or {}),
    }


def run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
    )


def canonical_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def configured_dev_root(env: Mapping[str, str] | None = None) -> Path:
    source = env or os.environ
    return canonical_path(Path(source.get("QWENDEX_DEV_ROOT") or ROOT))


def configured_runtime_root(
    env: Mapping[str, str] | None = None,
    explicit: str | Path = "",
) -> Path:
    source = env or os.environ
    raw = str(explicit or source.get("QWENDEX_RUNTIME_ROOT") or "").strip()
    return canonical_path(Path(raw)) if raw else configured_dev_root(source) / ".qwendex-dev" / "runtime"


def configured_source_root(
    env: Mapping[str, str] | None = None,
    explicit: str | Path = "",
) -> Path:
    source = env or os.environ
    raw = str(explicit or source.get("QWENDEX_RUNTIME_SOURCE_ROOT") or source.get("QWENDEX_DEV_ROOT") or "").strip()
    candidate = canonical_path(Path(raw)) if raw else ROOT
    if not (candidate / "scripts" / "qwendex_cli.py").is_file():
        raise RuntimeContractError(f"Qwendex source root is incomplete: {candidate}")
    return candidate


def state_path(runtime_root: Path) -> Path:
    return runtime_root / "current.json"


def generations_root(runtime_root: Path) -> Path:
    return runtime_root / "generations"


def generation_path(runtime_root: Path, generation_id: str) -> Path:
    if not re.fullmatch(r"rtg-[0-9a-f]{20}", generation_id or ""):
        raise RuntimeContractError(f"invalid runtime generation id: {generation_id!r}")
    candidate = generations_root(runtime_root) / generation_id
    resolved_parent = canonical_path(candidate.parent)
    if resolved_parent != canonical_path(generations_root(runtime_root)):
        raise RuntimeContractError("runtime generation escaped the generation root")
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RuntimeContractError(f"required runtime artifact is missing: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(f"runtime artifact is unreadable: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeContractError(f"runtime artifact must be a JSON object: {path}")
    return payload


def read_selection(runtime_root: Path, *, allow_missing: bool = True) -> dict[str, Any]:
    path = state_path(runtime_root)
    if not path.exists() and allow_missing:
        return {
            "schema_version": SELECTION_SCHEMA,
            "state_schema_version": RUNTIME_STATE_SCHEMA_VERSION,
            "current": "",
            "previous": "",
            "known_good": "",
            "updated_at": "",
            "history": [],
            "last_operation": {},
        }
    payload = read_json(path)
    if payload.get("schema_version") != SELECTION_SCHEMA:
        raise RuntimeContractError(f"unsupported runtime selection schema: {payload.get('schema_version')!r}")
    if int(payload.get("state_schema_version") or 0) != RUNTIME_STATE_SCHEMA_VERSION:
        raise RuntimeContractError("unsupported runtime selector state version")
    for key in ("current", "previous", "known_good"):
        value = str(payload.get(key) or "")
        if value:
            generation_path(runtime_root, value)
    return payload


def git_output(source_root: Path, *args: str, allow_failure: bool = False) -> str:
    result = run_command(["git", "-C", str(source_root), *args], timeout=30)
    if result.returncode and not allow_failure:
        raise RuntimeContractError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def source_metadata(source_root: Path) -> dict[str, Any]:
    commit = git_output(source_root, "rev-parse", "HEAD")
    tree = git_output(source_root, "rev-parse", "HEAD^{tree}")
    branch = git_output(source_root, "branch", "--show-current", allow_failure=True)
    status_result = run_command(
        [
            "git",
            "-C",
            str(source_root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        timeout=30,
    )
    status_lines = status_result.stdout.rstrip("\n").splitlines()
    dirty_paths = sorted(line[3:] for line in status_lines if len(line) > 3)
    return {
        "commit": commit,
        "tree": tree,
        "branch": branch,
        "clean": not status_lines,
        "dirty_paths": dirty_paths,
        "dirty_state": "clean" if not status_lines else "in_scope_candidate",
    }


def path_allowed(relative: str) -> bool:
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return False
    first = path.parts[0] if path.parts else ""
    return any(relative == allowed or first == allowed for allowed in RUNTIME_SNAPSHOT_PATHS)


def runtime_source_files(source_root: Path) -> list[str]:
    output = git_output(
        source_root,
        "ls-files",
        "-co",
        "--exclude-standard",
        "--",
        *RUNTIME_SNAPSHOT_PATHS,
    )
    paths: list[str] = []
    for relative in sorted(set(output.splitlines())):
        if not relative or not path_allowed(relative):
            continue
        source = source_root / relative
        if not source.exists() and not source.is_symlink():
            continue
        if source.is_symlink():
            target = canonical_path(source)
            try:
                target.relative_to(source_root)
            except ValueError as exc:
                raise RuntimeContractError(f"runtime snapshot symlink escapes source root: {relative}") from exc
        if source.is_file() or source.is_symlink():
            paths.append(relative)
    required = {"scripts/qwendex", "scripts/qwendex_cli.py", "scripts/qdex", "config/qwendex/qwendex.json"}
    missing = sorted(required - set(paths))
    if missing:
        raise RuntimeContractError(f"runtime snapshot is missing required files: {', '.join(missing)}")
    return paths


def copy_runtime_tree(source_root: Path, destination: Path, files: list[str]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for relative in files:
        source = source_root / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            resolved = canonical_path(source)
            shutil.copy2(resolved, target)
        else:
            shutil.copy2(source, target)
        mode = target.stat().st_mode
        manifest.append(
            {
                "path": relative,
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
                "executable": bool(mode & stat.S_IXUSR),
            }
        )
    return manifest


def combined_digest(paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def detect_version(source_root: Path) -> str:
    text = (source_root / "scripts" / "qwendex_cli.py").read_text(encoding="utf-8")
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if not match:
        raise RuntimeContractError("cannot locate Qwendex VERSION in qwendex_cli.py")
    return match.group(1)


def load_codex_build_contract(
    *,
    dev_root: Path,
    codex_bin: Path,
    code_mode_host: Path,
) -> dict[str, Any]:
    receipt_path = dev_root / ".qwendex-dev" / "results" / "meta" / "codex_build.json"
    receipt = read_json(receipt_path)
    if receipt.get("schema_version") != "qwendex.dev.codex_build.v1" or receipt.get("status") != "pass":
        raise RuntimeContractError("Codex build receipt is missing or not passing")
    if not codex_bin.is_file() or codex_bin.is_symlink() or not os.access(codex_bin, os.X_OK):
        raise RuntimeContractError(f"patched Codex binary is missing or unsafe: {codex_bin}")
    if not code_mode_host.is_file() or code_mode_host.is_symlink() or not os.access(code_mode_host, os.X_OK):
        raise RuntimeContractError(f"Codex code-mode host is missing or unsafe: {code_mode_host}")
    binary_sha = sha256_file(codex_bin)
    host_sha = sha256_file(code_mode_host)
    if receipt.get("binary_sha256") != binary_sha:
        raise RuntimeContractError("patched Codex binary digest does not match its build receipt")
    host_receipt = receipt.get("code_mode_host")
    if not isinstance(host_receipt, Mapping) or host_receipt.get("binary_sha256") != host_sha:
        raise RuntimeContractError("Codex code-mode host digest does not match its build receipt")
    version_result = run_command([str(codex_bin), "--version"], timeout=30)
    if version_result.returncode:
        raise RuntimeContractError("patched Codex binary did not report a version")
    version_text = (version_result.stdout or version_result.stderr).strip()
    expected_version = str(receipt.get("binary_version") or "").strip()
    if expected_version and version_text != expected_version:
        raise RuntimeContractError("patched Codex binary version drifted from its build receipt")
    return {
        "receipt": str(receipt_path),
        "receipt_sha256": sha256_file(receipt_path),
        "version": version_text,
        "source_commit": str(receipt.get("source_head") or ""),
        "source_ref": str(receipt.get("source_ref") or ""),
        "patch_sha256": str(receipt.get("source_patch_sha256") or ""),
        "binary_sha256": binary_sha,
        "binary_bytes": codex_bin.stat().st_size,
        "code_mode_host_sha256": host_sha,
        "code_mode_host_bytes": code_mode_host.stat().st_size,
    }


def write_codex_runtime(path: Path) -> None:
    text = """#!/usr/bin/env bash
set -euo pipefail
runtime_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
codex="$runtime_dir/bin/codex"
host="$runtime_dir/bin/codex-code-mode-host"
if [[ ! -x "$codex" || ! -x "$host" ]]; then
  printf 'Qwendex runtime generation is missing its pinned Codex pair: %s\n' "$runtime_dir" >&2
  exit 127
fi
exec "$codex" "$@"
"""
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def link_identity_files(codex_home: Path) -> None:
    normal_home = Path.home() / ".codex"
    authentication = normal_home / "auth.json"
    if authentication.is_file() and not (codex_home / authentication.name).exists():
        (codex_home / authentication.name).symlink_to(authentication)
    for name in ("version.json", "installation_id"):
        source = normal_home / name
        target = codex_home / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)


def write_generation_codex_config(dev_root: Path, codex_home: Path) -> None:
    seed = dev_root / ".qwendex-dev" / "codex_home" / "config.toml"
    if seed.is_file():
        shutil.copy2(seed, codex_home / "config.toml")
        return
    (codex_home / "config.toml").write_text(
        'approval_policy = "never"\nsandbox_mode = "workspace-write"\n'
        'suppress_unstable_features_warning = true\n',
        encoding="utf-8",
    )


def generation_runtime_env(
    *,
    dev_root: Path,
    runtime_root: Path,
    generation_dir: Path,
    generation_id: str,
    contract_sha256: str,
) -> dict[str, str]:
    work_root = dev_root / ".qwendex-dev"
    return {
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_ROOT": str(generation_dir / "tree"),
        "QWENDEX_RUNTIME_ROOT": str(runtime_root),
        "QWENDEX_RUNTIME_TREE": str(generation_dir / "tree"),
        "QWENDEX_RUNTIME_GENERATION_DIR": str(generation_dir),
        "QWENDEX_RUNTIME_GENERATION_ID": generation_id,
        "QWENDEX_RUNTIME_CONTRACT_SHA256": contract_sha256,
        "QWENDEX_HOOK_GENERATION": generation_id,
        "QWENDEX_CODEX_RUNTIME": str(generation_dir / "bin" / "codex-runtime"),
        "QWENDEX_CODEX_HOME": str(generation_dir / "codex_home"),
        "CODEX_HOME": str(generation_dir / "codex_home"),
        "QWENDEX_STATE_DB": str(work_root / "state" / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(work_root / "state" / "qwendex-performance.sqlite"),
        "QWENDEX_LEDGER_DB": str(work_root / "state" / "qwendex_ledger.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(work_root / "results" / "qwendex"),
        "QWENDEX_META_ROOT": str(work_root / "results" / "meta"),
        "QWENDEX_CODEX_STATUS_FILE": str(generation_dir / "codex_status.json"),
        "QWENDEX_MODELS_CACHE_FILE": "models_cache.qwendex-runtime.json",
    }


def install_generation_hooks(
    *,
    generation_dir: Path,
    runtime_env: Mapping[str, str],
) -> dict[str, Any]:
    tree = generation_dir / "tree"
    qwendex = tree / "scripts" / "qwendex"
    codex_home = generation_dir / "codex_home"
    environment = os.environ.copy()
    environment.update({key: str(value) for key, value in runtime_env.items()})
    result = run_command(
        [str(qwendex), "agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json"],
        cwd=tree,
        env=environment,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeContractError(result.stderr.strip() or result.stdout.strip() or "managed hook generation failed")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeContractError("managed hook generation did not return JSON") from exc
    hook_path = codex_home / "hooks.json"
    hook_payload = read_json(hook_path)
    commands = canonical_json(hook_payload).decode("utf-8")
    expected_base = str(tree / "scripts" / "qwendex")
    if expected_base not in commands:
        raise RuntimeContractError("managed hooks are not pinned to the candidate runtime tree")
    return {
        "path": str(hook_path),
        "sha256": sha256_file(hook_path),
        "status": payload.get("status"),
        "event_count": len(hook_payload.get("hooks") or {}),
    }


def manifest_digest_payload(manifest: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(manifest)
    payload.pop("manifest_sha256", None)
    return payload


def seal_manifest(path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["manifest_sha256"] = digest_json(manifest_digest_payload(manifest))
    atomic_write_json(path, manifest, mode=0o644)
    return manifest


def verify_manifest_seal(manifest: Mapping[str, Any]) -> bool:
    expected = str(manifest.get("manifest_sha256") or "")
    return bool(expected) and expected == digest_json(manifest_digest_payload(manifest))


def verify_tree_manifest(generation_dir: Path, entries: Iterable[Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    tree = generation_dir / "tree"
    for entry in entries:
        relative = str(entry.get("path") or "")
        if not path_allowed(relative):
            errors.append(f"tree manifest path is outside the runtime allowlist: {relative}")
            continue
        path = tree / relative
        if not path.is_file() or path.is_symlink():
            errors.append(f"runtime tree file is missing or unsafe: {relative}")
            continue
        if path.stat().st_size != int(entry.get("bytes") or -1):
            errors.append(f"runtime tree size mismatch: {relative}")
            continue
        if sha256_file(path) != str(entry.get("sha256") or ""):
            errors.append(f"runtime tree digest mismatch: {relative}")
    return errors


def validate_generation(
    runtime_root: Path,
    generation_id: str,
    *,
    execute_smoke: bool = False,
) -> dict[str, Any]:
    directory = generation_path(runtime_root, generation_id)
    manifest_path = directory / "generation.json"
    manifest = read_json(manifest_path)
    errors: list[str] = []
    if manifest.get("schema_version") != GENERATION_SCHEMA:
        errors.append("unsupported runtime generation schema")
    if manifest.get("generation_id") != generation_id:
        errors.append("runtime generation id does not match its directory")
    if not verify_manifest_seal(manifest):
        errors.append("runtime generation manifest seal is invalid")
    errors.extend(verify_tree_manifest(directory, manifest.get("tree_manifest") or []))
    binary = directory / "bin" / "codex"
    host = directory / "bin" / "codex-code-mode-host"
    runtime = directory / "bin" / "codex-runtime"
    binary_contract = manifest.get("codex") if isinstance(manifest.get("codex"), Mapping) else {}
    for path, key in ((binary, "binary_sha256"), (host, "code_mode_host_sha256")):
        if not path.is_file() or path.is_symlink() or not os.access(path, os.X_OK):
            errors.append(f"runtime executable is missing or unsafe: {path.name}")
        elif sha256_file(path) != str(binary_contract.get(key) or ""):
            errors.append(f"runtime executable digest mismatch: {path.name}")
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        errors.append("runtime binary selector is missing")
    hook = manifest.get("hooks") if isinstance(manifest.get("hooks"), Mapping) else {}
    hook_path = directory / "codex_home" / "hooks.json"
    if not hook_path.is_file() or sha256_file(hook_path) != str(hook.get("sha256") or ""):
        errors.append("runtime hook generation digest mismatch")
    elif str(directory / "tree" / "scripts" / "qwendex") not in hook_path.read_text(encoding="utf-8"):
        errors.append("runtime hook generation is not pinned to its tree")
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), Mapping) else {}
    if digest_json(contract) != str(manifest.get("contract_sha256") or ""):
        errors.append("runtime generation contract digest mismatch")
    if execute_smoke and not errors:
        environment = os.environ.copy()
        environment.update({key: str(value) for key, value in (manifest.get("runtime_env") or {}).items()})
        with tempfile.TemporaryDirectory(prefix="qwendex-runtime-smoke-") as raw_temp:
            temp = Path(raw_temp)
            environment["QWENDEX_STATE_DB"] = str(temp / "state.sqlite")
            environment["QWENDEX_LEDGER_DB"] = str(temp / "ledger.sqlite")
            environment["QWENDEX_RESULTS_ROOT"] = str(temp / "results")
            result = run_command(
                [str(directory / "tree" / "scripts" / "qwendex"), "check", "--json"],
                cwd=directory / "tree",
                env=environment,
                timeout=180,
            )
            try:
                payload = json.loads(result.stdout)
            except json.JSONDecodeError:
                payload = {}
            if result.returncode or payload.get("status") not in {"pass", "warning", "standby"}:
                errors.append("candidate Qwendex smoke check failed")
    return {
        "generation_id": generation_id,
        "path": str(directory),
        "valid": not errors,
        "status": "pass" if not errors else "blocked",
        "errors": errors,
        "manifest": manifest,
    }


def make_tree_read_only(tree: Path) -> None:
    for path in sorted(tree.rglob("*"), reverse=True):
        try:
            mode = path.stat().st_mode
            if path.is_dir():
                path.chmod((mode & 0o555) or 0o555)
            elif path.is_file():
                executable = bool(mode & stat.S_IXUSR)
                path.chmod(0o555 if executable else 0o444)
        except OSError:
            continue
    tree.chmod(0o555)


def remove_generation_directory(directory: Path) -> None:
    """Remove an unreferenced immutable generation during explicit safe prune."""
    for root, directories, files in os.walk(directory):
        root_path = Path(root)
        try:
            root_path.chmod(root_path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        except OSError:
            pass
        for name in directories:
            path = root_path / name
            try:
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            except OSError:
                pass
        for name in files:
            path = root_path / name
            try:
                path.chmod(path.stat().st_mode | stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass
    shutil.rmtree(directory)


def build_generation(
    *,
    source_root: Path,
    runtime_root: Path,
    dev_root: Path,
    codex_bin: Path,
    code_mode_host: Path,
) -> dict[str, Any]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    generations_root(runtime_root).mkdir(parents=True, exist_ok=True)
    source = source_metadata(source_root)
    codex = load_codex_build_contract(
        dev_root=dev_root,
        codex_bin=codex_bin,
        code_mode_host=code_mode_host,
    )
    files = runtime_source_files(source_root)
    staging = Path(tempfile.mkdtemp(prefix=".runtime-tree-", dir=runtime_root))
    try:
        tree = staging / "tree"
        tree.mkdir(parents=True)
        tree_manifest = copy_runtime_tree(source_root, tree, files)
        tree_digest = digest_json(tree_manifest)
        config_dir = tree / "config" / "qwendex"
        config_digest = combined_digest(
            path for path in config_dir.glob("*.json") if path.name != "qwendex.schema.json"
        )
        schema_digest = sha256_file(config_dir / "qwendex.schema.json")
        contract = {
            "schema_version": "qwendex.runtime_contract.v1",
            "qwendex_version": detect_version(source_root),
            "source_commit": source["commit"],
            "source_tree": source["tree"],
            "runtime_source_sha256": tree_digest,
            "codex_version": codex["version"],
            "codex_source_commit": codex["source_commit"],
            "codex_patch_sha256": codex["patch_sha256"],
            "patched_binary_sha256": codex["binary_sha256"],
            "code_mode_host_sha256": codex["code_mode_host_sha256"],
            "config_sha256": config_digest,
            "schema_sha256": schema_digest,
            "state_schema_version": 3,
        }
        contract_sha = digest_json(contract)
        generation_id = f"rtg-{contract_sha[:20]}"
        directory = generation_path(runtime_root, generation_id)
        if directory.exists():
            existing = validate_generation(runtime_root, generation_id, execute_smoke=False)
            if existing["valid"]:
                return existing["manifest"]
            raise RuntimeContractError(
                f"existing candidate generation is invalid and was preserved for diagnosis: {directory}"
            )
        directory.mkdir(parents=True)
        os.replace(tree, directory / "tree")
        (directory / "bin").mkdir()
        shutil.copy2(codex_bin, directory / "bin" / "codex")
        shutil.copy2(code_mode_host, directory / "bin" / "codex-code-mode-host")
        (directory / "bin" / "codex").chmod(0o555)
        (directory / "bin" / "codex-code-mode-host").chmod(0o555)
        write_codex_runtime(directory / "bin" / "codex-runtime")
        codex_home = directory / "codex_home"
        codex_home.mkdir()
        write_generation_codex_config(dev_root, codex_home)
        link_identity_files(codex_home)
        runtime_env = generation_runtime_env(
            dev_root=dev_root,
            runtime_root=runtime_root,
            generation_dir=directory,
            generation_id=generation_id,
            contract_sha256=contract_sha,
        )
        hooks = install_generation_hooks(generation_dir=directory, runtime_env=runtime_env)
        manifest: dict[str, Any] = {
            "schema_version": GENERATION_SCHEMA,
            "generation_id": generation_id,
            "generated_at": utc_now(),
            "validated_at": "",
            "status": "candidate",
            "result": "pending",
            "source": source,
            "contract": contract,
            "contract_sha256": contract_sha,
            "runtime_generation": generation_id,
            "hook_generation": generation_id,
            "tree_manifest": tree_manifest,
            "tree_manifest_sha256": tree_digest,
            "codex": codex,
            "config_digest": config_digest,
            "schema_digest": schema_digest,
            "hooks": hooks,
            "runtime_env": runtime_env,
            "commands": [
                "scripts/qwendex runtime build --json",
                f"scripts/qwendex runtime activate --candidate {generation_id} --json",
            ],
            "privacy_status": "pass",
            "artifact_digests": {
                "codex_build_receipt": codex["receipt_sha256"],
                "runtime_tree": tree_digest,
                "hook_config": hooks["sha256"],
            },
            "validation": {},
        }
        seal_manifest(directory / "generation.json", manifest)
        validation = validate_generation(runtime_root, generation_id, execute_smoke=True)
        manifest["validated_at"] = utc_now()
        manifest["status"] = "validated" if validation["valid"] else "invalid"
        manifest["result"] = "pass" if validation["valid"] else "blocked"
        manifest["validation"] = {
            "status": validation["status"],
            "errors": validation["errors"],
            "command": "candidate source and hook smoke validation",
        }
        seal_manifest(directory / "generation.json", manifest)
        if not validation["valid"]:
            raise RuntimeContractError(
                "candidate generation failed validation: " + "; ".join(validation["errors"])
            )
        make_tree_read_only(directory / "tree")
        fsync_directory(directory)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


class RuntimeLock:
    def __init__(self, runtime_root: Path, *, timeout_seconds: float = 10.0) -> None:
        self.path = runtime_root / "activation.lock"
        self.handle: Any = None
        self.timeout_seconds = timeout_seconds

    def __enter__(self) -> "RuntimeLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= deadline:
                    self.handle.close()
                    self.handle = None
                    raise RuntimeContractError(
                        f"runtime activation lock remained busy for {self.timeout_seconds:.1f}s"
                    ) from exc
                time.sleep(0.05)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def update_current_symlink(runtime_root: Path, generation_id: str) -> None:
    link = runtime_root / "current"
    temporary = runtime_root / f".current.{uuid.uuid4().hex}.tmp"
    target = Path("generations") / generation_id
    os.symlink(target, temporary)
    os.replace(temporary, link)
    fsync_directory(runtime_root)


def write_selection(runtime_root: Path, payload: dict[str, Any]) -> None:
    failure_point = os.environ.get("QWENDEX_RUNTIME_FAIL_ACTIVATION_AT", "")
    if failure_point == "before_selector_replace":
        raise RuntimeContractError("fault injection interrupted runtime activation before selector replacement")
    atomic_write_json(state_path(runtime_root), payload, mode=0o600)
    if failure_point == "after_selector_replace":
        raise RuntimeContractError("fault injection interrupted runtime activation after selector replacement")
    update_current_symlink(runtime_root, str(payload.get("current") or ""))


def restore_selection_after_failure(
    runtime_root: Path,
    selection: Mapping[str, Any],
    *,
    existed: bool,
) -> None:
    """Restore the prior selector without consulting fault-injection hooks."""
    if existed:
        atomic_write_json(state_path(runtime_root), selection, mode=0o600)
    else:
        state_path(runtime_root).unlink(missing_ok=True)
    current = str(selection.get("current") or "")
    if current:
        update_current_symlink(runtime_root, current)
    else:
        (runtime_root / "current").unlink(missing_ok=True)
        fsync_directory(runtime_root)


def activate_generation(runtime_root: Path, generation_id: str) -> dict[str, Any]:
    with RuntimeLock(runtime_root):
        validation = validate_generation(runtime_root, generation_id, execute_smoke=True)
        if not validation["valid"] or validation["manifest"].get("status") != "validated":
            raise RuntimeContractError(
                "new sessions cannot activate an unvalidated runtime candidate: "
                + "; ".join(validation["errors"])
            )
        selection = read_selection(runtime_root)
        current = str(selection.get("current") or "")
        if current == generation_id:
            update_current_symlink(runtime_root, generation_id)
            return selection
        now = utc_now()
        history = list(selection.get("history") or [])
        history.append({"operation": "activate", "from": current, "to": generation_id, "at": now})
        updated = {
            "schema_version": SELECTION_SCHEMA,
            "state_schema_version": RUNTIME_STATE_SCHEMA_VERSION,
            "current": generation_id,
            "previous": current,
            "known_good": current or generation_id,
            "updated_at": now,
            "history": history[-100:],
            "last_operation": {"operation": "activate", "from": current, "to": generation_id, "at": now},
        }
        selection_existed = state_path(runtime_root).is_file()
        try:
            write_selection(runtime_root, updated)
        except Exception:
            restore_selection_after_failure(
                runtime_root,
                selection,
                existed=selection_existed,
            )
            raise
        return updated


def rollback_generation(runtime_root: Path) -> dict[str, Any]:
    with RuntimeLock(runtime_root):
        selection = read_selection(runtime_root, allow_missing=False)
        current = str(selection.get("current") or "")
        last = selection.get("last_operation") if isinstance(selection.get("last_operation"), Mapping) else {}
        if last.get("operation") == "rollback" and current == str(last.get("to") or ""):
            update_current_symlink(runtime_root, current)
            return selection
        target = str(selection.get("known_good") or selection.get("previous") or "")
        if not target or target == current:
            raise RuntimeContractError("no distinct known-good runtime generation is available for rollback")
        validation = validate_generation(runtime_root, target, execute_smoke=False)
        if not validation["valid"] or validation["manifest"].get("status") != "validated":
            raise RuntimeContractError("known-good rollback generation failed integrity validation")
        now = utc_now()
        history = list(selection.get("history") or [])
        history.append({"operation": "rollback", "from": current, "to": target, "at": now})
        updated = {
            "schema_version": SELECTION_SCHEMA,
            "state_schema_version": RUNTIME_STATE_SCHEMA_VERSION,
            "current": target,
            "previous": current,
            "known_good": target,
            "updated_at": now,
            "history": history[-100:],
            "last_operation": {"operation": "rollback", "from": current, "to": target, "at": now},
        }
        try:
            write_selection(runtime_root, updated)
        except Exception:
            restore_selection_after_failure(runtime_root, selection, existed=True)
            raise
        return updated


def manager_active_generation_refs(state_db: Path) -> tuple[set[str], list[str]]:
    if not state_db.exists():
        return set(), []
    refs: set[str] = set()
    errors: list[str] = []
    try:
        connection = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(qwendex_manager_decisions)")}
        if "runtime_generation" in columns:
            rows = connection.execute(
                "SELECT DISTINCT runtime_generation, final_status FROM qwendex_manager_decisions "
                "WHERE runtime_generation <> ''"
            ).fetchall()
            for row in rows:
                if str(row["final_status"] or "") not in MANAGER_TERMINAL_STATES:
                    refs.add(str(row["runtime_generation"]))
        agent_columns = {row["name"] for row in connection.execute("PRAGMA table_info(qwendex_agent_sessions)")}
        if "runtime_generation" in agent_columns:
            rows = connection.execute(
                "SELECT DISTINCT runtime_generation, status FROM qwendex_agent_sessions "
                "WHERE runtime_generation <> ''"
            ).fetchall()
            for row in rows:
                if str(row["status"] or "") not in MANAGER_TERMINAL_STATES | {"completed", "waived"}:
                    refs.add(str(row["runtime_generation"]))
        connection.close()
    except sqlite3.Error as exc:
        errors.append(str(exc))
    return refs, errors


def list_generations(runtime_root: Path) -> list[dict[str, Any]]:
    selection = read_selection(runtime_root)
    current = str(selection.get("current") or "")
    previous = str(selection.get("previous") or "")
    known_good = str(selection.get("known_good") or "")
    items: list[dict[str, Any]] = []
    root = generations_root(runtime_root)
    if not root.exists():
        return items
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not re.fullmatch(r"rtg-[0-9a-f]{20}", directory.name):
            continue
        try:
            validation = validate_generation(runtime_root, directory.name, execute_smoke=False)
            manifest = validation["manifest"]
            items.append(
                {
                    "generation_id": directory.name,
                    "path": str(directory),
                    "status": manifest.get("status"),
                    "result": manifest.get("result"),
                    "valid": validation["valid"],
                    "errors": validation["errors"],
                    "current": directory.name == current,
                    "previous": directory.name == previous,
                    "known_good": directory.name == known_good,
                    "generated_at": manifest.get("generated_at"),
                    "validated_at": manifest.get("validated_at"),
                    "source_commit": (manifest.get("source") or {}).get("commit"),
                    "dirty_state": (manifest.get("source") or {}).get("dirty_state"),
                    "contract_sha256": manifest.get("contract_sha256"),
                    "binary_sha256": (manifest.get("codex") or {}).get("binary_sha256"),
                    "patch_sha256": (manifest.get("codex") or {}).get("patch_sha256"),
                    "hook_generation": manifest.get("hook_generation"),
                }
            )
        except RuntimeContractError as exc:
            items.append(
                {
                    "generation_id": directory.name,
                    "path": str(directory),
                    "status": "invalid",
                    "result": "blocked",
                    "valid": False,
                    "errors": [str(exc)],
                    "current": directory.name == current,
                    "previous": directory.name == previous,
                    "known_good": directory.name == known_good,
                }
            )
    return items


def prune_generations(runtime_root: Path, *, state_db: Path) -> dict[str, Any]:
    with RuntimeLock(runtime_root):
        selection = read_selection(runtime_root)
        active_refs, state_errors = manager_active_generation_refs(state_db)
        if state_errors:
            raise RuntimeContractError(
                "safe pruning requires readable Manager state: " + "; ".join(state_errors)
            )
        retained = {
            str(selection.get("current") or ""),
            str(selection.get("previous") or ""),
            str(selection.get("known_good") or ""),
            *active_refs,
        }
        retained.discard("")
        removed: list[str] = []
        skipped: list[dict[str, Any]] = []
        for item in list_generations(runtime_root):
            generation_id = item["generation_id"]
            if generation_id in retained:
                skipped.append({"generation_id": generation_id, "reason": "selected_or_active"})
                continue
            if not item.get("valid"):
                skipped.append({"generation_id": generation_id, "reason": "invalid_preserved_for_diagnosis"})
                continue
            remove_generation_directory(generation_path(runtime_root, generation_id))
            removed.append(generation_id)
        return {
            "removed": removed,
            "retained": sorted(retained),
            "active_session_refs": sorted(active_refs),
            "skipped": skipped,
        }


def runtime_status(runtime_root: Path, *, state_db: Path) -> dict[str, Any]:
    selection = read_selection(runtime_root)
    items = list_generations(runtime_root)
    current = str(selection.get("current") or "")
    current_item = next((item for item in items if item["generation_id"] == current), None)
    active_refs, state_errors = manager_active_generation_refs(state_db)
    pinned = str(os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or "")
    return {
        "schema_version": "qwendex.runtime_status.v1",
        "runtime_root": str(runtime_root),
        "selection": selection,
        "current_generation": current_item,
        "generation_count": len(items),
        "validated_generation_count": sum(1 for item in items if item.get("valid") and item.get("status") == "validated"),
        "active_session_generation_refs": sorted(active_refs),
        "state_errors": state_errors,
        "process_pinned_generation": pinned,
        "process_matches_current": not pinned or pinned == current,
        "stock_codex_recovery": "codex",
        "shell_recovery": str(configured_dev_root() / ".qwendex-dev" / "bin" / "qwendex-runtime-recovery"),
    }


def build_command(args: argparse.Namespace) -> dict[str, Any]:
    runtime_root = configured_runtime_root(explicit=getattr(args, "runtime_root", ""))
    source_root = configured_source_root(explicit=getattr(args, "source_root", ""))
    dev_root = configured_dev_root()
    codex_raw = str(getattr(args, "codex_bin", "") or os.environ.get("QWENDEX_DEV_CODEX_BIN") or "").strip()
    codex_bin = canonical_path(Path(codex_raw)) if codex_raw else dev_root / ".qwendex-dev" / "codex-build" / "bin" / "codex"
    host_raw = str(getattr(args, "code_mode_host", "") or "").strip()
    code_mode_host = canonical_path(Path(host_raw)) if host_raw else codex_bin.parent / "codex-code-mode-host"
    with RuntimeLock(runtime_root):
        manifest = build_generation(
            source_root=source_root,
            runtime_root=runtime_root,
            dev_root=dev_root,
            codex_bin=codex_bin,
            code_mode_host=code_mode_host,
        )
    generation_id = str(manifest["generation_id"])
    return stable_envelope(
        action="build",
        status="pass",
        summary=f"Built and validated runtime candidate {generation_id}.",
        data={"runtime_generation": manifest},
        artifacts=[str(generation_path(runtime_root, generation_id) / "generation.json")],
        next_actions=[f"scripts/qwendex runtime activate --candidate {generation_id} --json"],
    )


def command(args: argparse.Namespace) -> dict[str, Any]:
    action = str(getattr(args, "action", "status") or "status")
    runtime_root = configured_runtime_root(explicit=getattr(args, "runtime_root", ""))
    dev_root = configured_dev_root()
    state_db = Path(os.environ.get("QWENDEX_STATE_DB") or dev_root / ".qwendex-dev" / "state" / "qwendex.sqlite")
    try:
        if action == "build":
            return build_command(args)
        if action == "status":
            data = runtime_status(runtime_root, state_db=state_db)
            current = data.get("current_generation")
            ready = bool(current and current.get("valid") and current.get("status") == "validated")
            return stable_envelope(
                action=action,
                status="pass" if ready else "blocked",
                summary=(
                    f"Runtime generation {current['generation_id']} is selected and valid."
                    if ready
                    else "No validated runtime generation is selected."
                ),
                data=data,
                next_actions=[] if ready else ["scripts/qwendex runtime build --json"],
            )
        if action == "generations":
            items = list_generations(runtime_root)
            return stable_envelope(
                action=action,
                status="pass",
                summary=f"Loaded {len(items)} runtime generations.",
                data={"runtime_root": str(runtime_root), "generations": items, "selection": read_selection(runtime_root)},
            )
        if action == "activate":
            candidate = str(getattr(args, "candidate", "") or "")
            if not candidate:
                raise RuntimeContractError("runtime activate requires --candidate <id>")
            selection = activate_generation(runtime_root, candidate)
            return stable_envelope(
                action=action,
                status="pass",
                summary=f"Activated runtime generation {candidate} for new sessions.",
                data={"selection": selection, "active_sessions_remain_pinned": True},
                artifacts=[str(state_path(runtime_root))],
            )
        if action == "rollback":
            selection = rollback_generation(runtime_root)
            return stable_envelope(
                action=action,
                status="pass",
                summary=f"Rolled back new sessions to {selection['current']}.",
                data={"selection": selection, "stock_codex_unchanged": True},
                artifacts=[str(state_path(runtime_root))],
            )
        if action == "prune":
            if not bool(getattr(args, "safe", False)):
                raise RuntimeContractError("runtime prune requires --safe")
            data = prune_generations(runtime_root, state_db=state_db)
            return stable_envelope(
                action=action,
                status="pass",
                summary=f"Safely pruned {len(data['removed'])} unreferenced runtime generations.",
                data=data,
            )
        raise RuntimeContractError(f"unknown runtime action: {action}")
    except RuntimeContractError as exc:
        return stable_envelope(
            action=action,
            status="blocked",
            summary=str(exc),
            errors=[str(exc)],
            next_actions=["Use stock codex or qwendex-runtime-recovery rollback from a shell."],
        )


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description="Qwendex immutable runtime generation recovery")
    cli.add_argument("action", choices=["status", "generations", "build", "activate", "rollback", "prune"], nargs="?", default="status")
    cli.add_argument("--candidate", default="")
    cli.add_argument("--source-root", default="")
    cli.add_argument("--runtime-root", default="")
    cli.add_argument("--codex-bin", default="")
    cli.add_argument("--code-mode-host", default="")
    cli.add_argument("--safe", action="store_true")
    cli.add_argument("--json", action="store_true")
    return cli


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    payload = command(args)
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"status: {payload['status']}")
        print(payload["summary"])
    return 0 if payload.get("status") in {"pass", "ready", "warning", "standby"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
