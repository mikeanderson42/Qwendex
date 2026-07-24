from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load_runtime():
    path = ROOT / "scripts" / "qwendex_runtime.py"
    spec = importlib.util.spec_from_file_location("qwendex_runtime_generation_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNTIME = load_runtime()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        list(args),
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    return result.stdout.strip()


def copy_candidate_source(destination: Path) -> None:
    shutil.copytree(
        ROOT,
        destination,
        ignore=shutil.ignore_patterns(
            ".git",
            ".qwendex-dev",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
            "results",
        ),
    )
    run("git", "init", "-b", "candidate", cwd=destination)
    run("git", "add", ".", cwd=destination)
    run(
        "git",
        "-c",
        "user.name=Qwendex Test",
        "-c",
        "user.email=qwendex-test@example.invalid",
        "commit",
        "-m",
        "candidate fixture",
        cwd=destination,
    )


def write_pinned_codex_fixture(dev_root: Path) -> tuple[Path, Path]:
    binary_root = dev_root / ".qwendex-dev" / "codex-build" / "bin"
    binary_root.mkdir(parents=True)
    codex = binary_root / "codex"
    codex.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"--version\" ]]; then\n"
        "  printf 'codex-cli 0.145.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    codex.chmod(0o755)
    host = binary_root / "codex-code-mode-host"
    host.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    host.chmod(0o755)
    receipt = {
        "schema_version": "qwendex.dev.codex_build.v1",
        "status": "pass",
        "source_head": "1" * 40,
        "source_ref": "rust-v0.145.0",
        "source_patch_sha256": "2" * 64,
        "binary_sha256": sha256_file(codex),
        "binary_version": "codex-cli 0.145.0",
        "code_mode_host": {"binary_sha256": sha256_file(host)},
    }
    receipt_path = dev_root / ".qwendex-dev" / "results" / "meta" / "codex_build.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(json.dumps(receipt, sort_keys=True) + "\n", encoding="utf-8")
    codex_home = dev_root / ".qwendex-dev" / "codex_home"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(
        'approval_policy = "never"\nsandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )
    return codex, host


def build_candidate(source: Path, runtime_root: Path, codex: Path, host: Path) -> dict:
    return RUNTIME.build_generation(
        source_root=source,
        runtime_root=runtime_root,
        dev_root=source,
        codex_bin=codex,
        code_mode_host=host,
    )


def test_runtime_generations_are_immutable_atomic_and_recoverable(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    copy_candidate_source(source)
    codex, host = write_pinned_codex_fixture(source)
    runtime_root = source / ".qwendex-dev" / "runtime"

    first = build_candidate(source, runtime_root, codex, host)
    first_id = first["generation_id"]
    first_dir = runtime_root / "generations" / first_id
    first_qwendex_sha = sha256_file(first_dir / "tree" / "scripts" / "qwendex_cli.py")
    first_hook = (first_dir / "codex_home" / "hooks.json").read_text(encoding="utf-8")
    assert first["status"] == "validated"
    assert first["contract"]["state_schema_version"] == 3
    assert first_id in first_hook
    assert RUNTIME.validate_generation(runtime_root, first_id)["valid"] is True

    selected = RUNTIME.activate_generation(runtime_root, first_id)
    assert selected["current"] == first_id
    assert (runtime_root / "current").resolve() == first_dir.resolve()

    with (source / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("\nRuntime generation two fixture.\n")
    second = build_candidate(source, runtime_root, codex, host)
    second_id = second["generation_id"]
    assert second_id != first_id
    assert sha256_file(first_dir / "tree" / "scripts" / "qwendex_cli.py") == first_qwendex_sha
    assert first_id in (first_dir / "codex_home" / "hooks.json").read_text(encoding="utf-8")

    for failure_point in ("before_selector_replace", "after_selector_replace"):
        monkeypatch.setenv("QWENDEX_RUNTIME_FAIL_ACTIVATION_AT", failure_point)
        with pytest.raises(RUNTIME.RuntimeContractError, match="fault injection"):
            RUNTIME.activate_generation(runtime_root, second_id)
        after_failure = RUNTIME.read_selection(runtime_root, allow_missing=False)
        assert after_failure["current"] == first_id
        assert (runtime_root / "current").resolve() == first_dir.resolve()
    monkeypatch.delenv("QWENDEX_RUNTIME_FAIL_ACTIVATION_AT")

    selected = RUNTIME.activate_generation(runtime_root, second_id)
    assert selected["current"] == second_id
    assert selected["known_good"] == first_id
    rolled_back = RUNTIME.rollback_generation(runtime_root)
    assert rolled_back["current"] == first_id
    history_length = len(rolled_back["history"])
    assert RUNTIME.rollback_generation(runtime_root)["history"] == rolled_back["history"]
    assert len(RUNTIME.read_selection(runtime_root)["history"]) == history_length

    with (source / "README.md").open("a", encoding="utf-8") as handle:
        handle.write("\nUnreferenced runtime generation fixture.\n")
    third = build_candidate(source, runtime_root, codex, host)
    third_id = third["generation_id"]
    pruned = RUNTIME.prune_generations(
        runtime_root,
        state_db=source / ".qwendex-dev" / "state" / "missing.sqlite",
    )
    assert pruned["removed"] == [third_id]
    assert first_id in pruned["retained"]
    assert second_id in pruned["retained"]
    assert not (runtime_root / "generations" / third_id).exists()


def test_runtime_generation_excludes_operator_qdex_permission_config(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    copy_candidate_source(source)
    codex, host = write_pinned_codex_fixture(source)
    runtime_root = source / ".qwendex-dev" / "runtime"
    operator_config = home / ".config" / "qwendex" / "qdex.json"
    operator_config.parent.mkdir(parents=True)
    operator_config.write_text('{"permission_mode": "yolo"}\n', encoding="utf-8")

    first = build_candidate(source, runtime_root, codex, host)
    entries = {item["path"] for item in first["tree_manifest"]}
    assert ".config/qwendex/qdex.json" not in entries
    assert not any(path.endswith("/qdex.json") for path in entries)
    assert str(operator_config) not in json.dumps(first, sort_keys=True)

    operator_config.write_text('{"permission_mode": "workspace-write"}\n', encoding="utf-8")
    second = build_candidate(source, runtime_root, codex, host)
    assert second["generation_id"] == first["generation_id"]
    assert second["config_digest"] == first["config_digest"]


def test_qdex_top_level_discards_an_inherited_stale_runtime_pin(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    copy_candidate_source(source)
    qdex = source / "scripts" / "qdex"
    marker = "pinned_runtime_generation_id=\"${QWENDEX_RUNTIME_GENERATION_ID:-}\"\n"
    test_exit = (
        'if [[ "${QDEX_SELECTOR_TEST:-0}" == "1" ]]; then\n'
        '  printf \'%s\\n\' "${QWENDEX_RUNTIME_GENERATION_ID:-}"\n'
        "  exit 0\n"
        "fi\n\n"
    )
    qdex.write_text(
        qdex.read_text(encoding="utf-8").replace(marker, test_exit + marker, 1),
        encoding="utf-8",
    )
    codex, host = write_pinned_codex_fixture(source)
    runtime_root = source / ".qwendex-dev" / "runtime"
    generation = build_candidate(source, runtime_root, codex, host)
    RUNTIME.activate_generation(runtime_root, generation["generation_id"])

    stale_tree = tmp_path / "stale-runtime" / "tree"
    result = subprocess.run(
        [str(ROOT / "scripts" / "qdex")],
        cwd=source,
        env={
            **os.environ,
            "QWENDEX_DEV_ROOT": str(source),
            "QWENDEX_RUNTIME_PINNED": "1",
            "QWENDEX_RUNTIME_TREE": str(stale_tree),
            "QWENDEX_RUNTIME_GENERATION_ID": "rtg-stale",
            "QWENDEX_CODEX_HOME": str(tmp_path / "stale-home"),
            "QWENDEX_CODEX_RUNTIME": str(tmp_path / "stale-codex"),
            "QDEX_SELECTOR_TEST": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert result.stdout.strip() == generation["generation_id"]


def test_safe_prune_reads_live_decision_and_child_generation_refs(tmp_path):
    state_db = tmp_path / "state.sqlite"
    with sqlite3.connect(state_db) as conn:
        conn.executescript(
            """
            CREATE TABLE qwendex_manager_decisions (
              runtime_generation TEXT NOT NULL,
              final_status TEXT NOT NULL
            );
            CREATE TABLE qwendex_agent_sessions (
              runtime_generation TEXT NOT NULL,
              status TEXT NOT NULL
            );
            INSERT INTO qwendex_manager_decisions VALUES ('rtg-11111111111111111111', 'preflight_ready');
            INSERT INTO qwendex_manager_decisions VALUES ('rtg-22222222222222222222', 'closed');
            INSERT INTO qwendex_agent_sessions VALUES ('rtg-33333333333333333333', 'active');
            INSERT INTO qwendex_agent_sessions VALUES ('rtg-44444444444444444444', 'completed');
            """
        )
    refs, errors = RUNTIME.manager_active_generation_refs(state_db)
    assert errors == []
    assert refs == {"rtg-11111111111111111111", "rtg-33333333333333333333"}


def test_runtime_lock_wait_is_bounded(tmp_path):
    runtime_root = tmp_path / "runtime"
    with RUNTIME.RuntimeLock(runtime_root):
        with pytest.raises(RUNTIME.RuntimeContractError, match="remained busy"):
            with RUNTIME.RuntimeLock(runtime_root, timeout_seconds=0.05):
                raise AssertionError("nested lock unexpectedly acquired")


def test_runtime_shares_auth_but_copies_version_cache_and_installation_identity(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    normal_home = home / ".codex"
    normal_home.mkdir(parents=True)
    authentication = normal_home / "auth.json"
    version = normal_home / "version.json"
    installation = normal_home / "installation_id"
    authentication.write_text('{"auth":"fixture"}\n', encoding="utf-8")
    version.write_text('{"latest":"0.145.0"}\n', encoding="utf-8")
    installation.write_text("installation-fixture\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    codex_home = tmp_path / "generation" / "codex_home"
    codex_home.mkdir(parents=True)
    RUNTIME.link_identity_files(codex_home)

    assert (codex_home / "auth.json").is_symlink()
    assert (codex_home / "auth.json").resolve() == authentication.resolve()
    assert not (codex_home / "version.json").is_symlink()
    assert (codex_home / "version.json").read_bytes() == version.read_bytes()
    assert not (codex_home / "installation_id").is_symlink()
    assert (codex_home / "installation_id").read_bytes() == installation.read_bytes()
    (codex_home / "version.json").write_text('{"latest":"fixture-new"}\n', encoding="utf-8")
    assert version.read_text(encoding="utf-8") == '{"latest":"0.145.0"}\n'


def test_corrupt_selector_fails_closed(tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    (runtime_root / "current.json").write_text('{"schema_version":"wrong"}\n', encoding="utf-8")
    with pytest.raises(RUNTIME.RuntimeContractError, match="unsupported runtime selection schema"):
        RUNTIME.read_selection(runtime_root, allow_missing=False)
