from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"


def run_qwendex(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(env)
    return subprocess.run(
        [str(QWENDEX), *args],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )


def legacy_state(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            PRAGMA user_version = 0;
            CREATE TABLE qwendex_manager_settings (
              key TEXT PRIMARY KEY,
              value_json TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            INSERT INTO qwendex_manager_settings
              (key, value_json, updated_at)
            VALUES ('selected_mode', '"medium"', '2026-01-01T00:00:00Z');
            """
        )


def state_env(tmp_path: Path, state_db: Path) -> dict[str, str]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "CODEX_HOME": str(tmp_path / "codex-home"),
    }


def test_state_schema_v3_migration_is_backed_up_transactional_and_idempotent(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    legacy_state(state_db)
    env = state_env(tmp_path, state_db)

    first = run_qwendex("manager", "status", "--json", env=env)
    assert first.returncode == 0, first.stderr or first.stdout
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute(
            "SELECT value_json FROM qwendex_manager_settings WHERE key = 'selected_mode'"
        ).fetchone()[0] == '"medium"'
        migration = conn.execute(
            "SELECT from_version, to_version, status, backup_path FROM qwendex_state_migrations"
        ).fetchone()
        assert migration[:3] == (0, 3, "pass")
        assert Path(migration[3]).is_file()

    backups = sorted((tmp_path / "migrations" / state_db.name).glob("state-v0-to-v3-*.sqlite"))
    assert len(backups) == 1
    second = run_qwendex("manager", "status", "--json", env=env)
    assert second.returncode == 0, second.stderr or second.stdout
    assert sorted((tmp_path / "migrations" / state_db.name).glob("state-v0-to-v3-*.sqlite")) == backups
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM qwendex_state_migrations").fetchone()[0] == 1


def test_interrupted_state_migration_rolls_back_and_preserves_recovery_receipts(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    legacy_state(state_db)
    env = {
        **state_env(tmp_path, state_db),
        "QWENDEX_STATE_MIGRATION_FAIL_AT": "before_commit",
    }

    interrupted = run_qwendex("manager", "status", "--json", env=env)
    assert interrupted.returncode == 1
    payload = json.loads(interrupted.stdout)
    assert payload["status"] == "fail"
    assert "injected state migration failure" in payload["summary"]
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute(
            "SELECT value_json FROM qwendex_manager_settings WHERE key = 'selected_mode'"
        ).fetchone()[0] == '"medium"'
        assert conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'qwendex_state_migrations'"
        ).fetchone()[0] == 0

    migration_dir = tmp_path / "migrations" / state_db.name
    assert len(list(migration_dir.glob("state-v0-to-v3-*.sqlite"))) == 1
    failures = list(migration_dir.glob("migration-failed-*.json"))
    assert len(failures) == 1
    failure = json.loads(failures[0].read_text(encoding="utf-8"))
    assert failure["status"] == "blocked"
    assert failure["from_version"] == 0
    assert failure["target_version"] == 3

    retry_env = dict(env)
    retry_env.pop("QWENDEX_STATE_MIGRATION_FAIL_AT")
    retried = run_qwendex("manager", "status", "--json", env=retry_env)
    assert retried.returncode == 0, retried.stderr or retried.stdout
    with sqlite3.connect(state_db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        assert conn.execute(
            "SELECT value_json FROM qwendex_manager_settings WHERE key = 'selected_mode'"
        ).fetchone()[0] == '"medium"'


def test_state_schema_v2_upgrade_adds_qdex_permission_columns(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = state_env(tmp_path, state_db)
    initial = run_qwendex("manager", "status", "--json", env=env)
    assert initial.returncode == 0, initial.stderr or initial.stdout

    with sqlite3.connect(state_db) as conn:
        conn.execute("ALTER TABLE qwendex_manager_decisions DROP COLUMN qdex_permission_mode")
        conn.execute("ALTER TABLE qwendex_manager_decisions DROP COLUMN qdex_permission_source")
        conn.execute("PRAGMA user_version = 2")

    upgraded = run_qwendex("manager", "status", "--json", env=env)
    assert upgraded.returncode == 0, upgraded.stderr or upgraded.stdout
    with sqlite3.connect(state_db) as conn:
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(qwendex_manager_decisions)")
        }
        assert {"qdex_permission_mode", "qdex_permission_source"} <= columns
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        migration = conn.execute(
            "SELECT from_version, to_version, status, backup_path "
            "FROM qwendex_state_migrations ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        assert migration[:3] == (2, 3, "pass")
        assert Path(migration[3]).is_file()


def test_corrupt_state_fails_closed_without_reinitializing_operator_data(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    original = b"not-a-sqlite-database\x00operator-state"
    state_db.write_bytes(original)
    result = run_qwendex("manager", "status", "--json", env=state_env(tmp_path, state_db))
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert state_db.read_bytes() == original
    failures = list((tmp_path / "migrations" / state_db.name).glob("migration-failed-*.json"))
    assert len(failures) == 1


def test_manager_accept_profiles_are_first_class_and_do_not_touch_state_on_dispatch(tmp_path):
    state_db = tmp_path / "must-not-exist.sqlite"
    occupied = tmp_path / "acceptance" / "manager-production" / "dispatch-live-check" / "live"
    occupied.mkdir(parents=True)
    (occupied / "existing-run.json").write_text("{}\n", encoding="utf-8")
    result = run_qwendex(
        "manager",
        "accept",
        "--profile",
        "live",
        "--run-id",
        "dispatch-live-check",
        "--results-root",
        str(tmp_path / "acceptance"),
        "--json",
        env={"QWENDEX_STATE_DB": str(state_db)},
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["data"]["acceptance_profile"] == "live"
    assert payload["data"]["final_status"] == "STOP_MANAGER_ACCEPT_LIVE_BLOCKED"
    assert not state_db.exists()


def test_production_acceptance_dispatches_executable_profiles_and_install_contract(tmp_path, monkeypatch):
    acceptance_path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_acceptance_test", acceptance_path)
    assert spec is not None and spec.loader is not None
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    monkeypatch.setattr(
        acceptance,
        "production_profile",
        lambda run_id, results_root: {
            "acceptance_profile": "production",
            "run_id": run_id,
            "results_root": str(results_root),
            "result": "pass",
        },
    )
    dispatched = acceptance.run_profile("production", "production-dispatch", tmp_path)
    assert dispatched["acceptance_profile"] == "production"
    assert dispatched["result"] == "pass"

    install_source = (ROOT / "scripts" / "qwendex_manager_install_acceptance.py").read_text(
        encoding="utf-8"
    )
    for required in (
        "fresh_pinned_codex_build",
        "fresh_offline_acceptance",
        "fresh_install_non_ultra_live_manager",
        "upgrade_v0_5_7",
        "upgrade_old_dependency_install",
        "upgrade_old_hook_install",
        "upgrade_old_hook_verify",
        "upgrade_historical_evidence_classification",
        "rollback_shell_recovery",
        "rollback_injected_activation_failure",
        "rollback_stock_codex_recovery",
        "normal_codex_isolation_receipt.json",
    ):
        assert required in install_source


def test_install_acceptance_uses_canonical_runtime_validation_without_legacy_flag(
    tmp_path, monkeypatch
):
    install_path = ROOT / "scripts" / "qwendex_manager_install_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_install_runtime_test", install_path)
    assert spec is not None and spec.loader is not None
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    generation_id = "rtg-" + "1" * 20
    runtime_root = tmp_path / ".qwendex-dev" / "runtime"
    generation_root = runtime_root / "generations" / generation_id
    generation_root.mkdir(parents=True)
    (tmp_path / ".qwendex-dev" / "bin").mkdir(parents=True)
    (runtime_root / "current.json").write_text(
        json.dumps({"current": generation_id}),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "qwendex.runtime_generation.v1",
        "generation_id": generation_id,
        "status": "validated",
        "result": "pass",
    }
    (generation_root / "generation.json").write_text(json.dumps(manifest), encoding="utf-8")
    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "status": "pass",
                    "data": {
                        "current_generation": {
                            "generation_id": generation_id,
                            "status": "validated",
                            "valid": True,
                        }
                    },
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(install.subprocess, "run", fake_run)

    assert install.selected_manifest(tmp_path) == manifest
    assert observed["command"] == [
        str(tmp_path / ".qwendex-dev" / "bin" / "qwendex-runtime-recovery"),
        "status",
        "--runtime-root",
        str(runtime_root),
        "--json",
    ]
    assert "validated" not in manifest
    assert install.manifest_is_canonically_validated(manifest, generation_id)
    assert not install.manifest_is_canonically_validated(
        {**manifest, "result": "fail"}, generation_id
    )

    def invalid_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command,
            1,
            stdout=json.dumps({"status": "blocked", "data": {}}),
            stderr="",
        )

    monkeypatch.setattr(install.subprocess, "run", invalid_run)
    with pytest.raises(install.InstallAcceptanceError, match="no validated selected runtime"):
        install.selected_manifest(tmp_path)


def test_upgrade_fixture_bootstraps_legacy_dependencies_and_hooks_without_system_writes(tmp_path):
    install_path = ROOT / "scripts" / "qwendex_manager_install_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_install_upgrade_test", install_path)
    assert spec is not None and spec.loader is not None
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    source = tmp_path / "legacy-source"
    assert install.legacy_dependency_install_command(source) == [
        str(source / "scripts" / "qwendex_install_deps"),
        "--install",
        "--no-system",
        "--json",
    ]
    for action in ("--install", "--verify"):
        assert install.legacy_hook_command(source, "/isolated/codex-home", action) == [
            str(source / "scripts" / "qwendex"),
            "agent",
            "hook-config",
            action,
            "--codex-home",
            "/isolated/codex-home",
            "--json",
        ]
    with pytest.raises(install.InstallAcceptanceError, match="unsupported legacy hook action"):
        install.legacy_hook_command(source, "/isolated/codex-home", "--force")
    assert install.legacy_codex_home(
        {"QWENDEX_CODEX_HOME": "/legacy/codex-home"}
    ) == "/legacy/codex-home"
    assert install.legacy_codex_home(
        {"CODEX_HOME": "/candidate/codex-home"}
    ) == "/candidate/codex-home"
    with pytest.raises(install.InstallAcceptanceError, match="no managed Codex home"):
        install.legacy_codex_home({})


def test_manager_acceptance_artifact_contract_requires_all_provenance_fields():
    acceptance_path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_artifact_contract_test", acceptance_path)
    assert spec is not None and spec.loader is not None
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    payload = {name: "value" for name in acceptance.REQUIRED_ARTIFACT_FIELDS}
    payload.update(
        {
            "commands": [],
            "artifact_digests": {},
            "result": "pass",
            "privacy_status": "pass",
        }
    )
    assert acceptance.artifact_contract_errors(payload) == []
    del payload["artifact_digests"]
    assert acceptance.artifact_contract_errors(payload) == ["missing:artifact_digests"]


def test_install_acceptance_treats_empty_manager_standby_as_healthy():
    install_path = ROOT / "scripts" / "qwendex_manager_install_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_install_status_test", install_path)
    assert spec is not None and spec.loader is not None
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    healthy = {
        "status": "standby",
        "errors": [],
        "data": {"mode": "manager", "write_safety": {"status": "ready"}},
    }
    install.require_healthy_manager_status(healthy, "upgraded Manager status")
    for invalid in (
        {**healthy, "status": "blocked"},
        {**healthy, "errors": ["failure"]},
        {**healthy, "data": {"mode": "off", "write_safety": {"status": "ready"}}},
        {**healthy, "data": {"mode": "manager", "write_safety": {"status": "blocked"}}},
    ):
        with pytest.raises(install.InstallAcceptanceError, match="healthy Manager status"):
            install.require_healthy_manager_status(invalid, "upgraded Manager status")


def test_live_invariants_resolve_failed_worker_with_visible_waiver_without_hiding_duplicates():
    live_path = ROOT / "scripts" / "qwendex_manager_live.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_live_invariant_test", live_path)
    assert spec is not None and spec.loader is not None
    live = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(live)

    waived_lane = {
        "repository_alias": "manager-ultra",
        "task_id": "verify-task",
        "lane": "verification",
        "required": True,
    }
    state = {
        "decisions": [],
        "agents": [
            {**waived_lane, "agent_id": "verifier", "status": "failed"},
            {**waived_lane, "agent_id": "waiver-receipt", "status": "waived"},
        ],
    }
    summary = live.invariant_summary(state, [])
    assert summary["duplicate_equivalent_lanes"] == 0
    assert summary["unresolved_suggested_lanes"] == 0
    assert summary["suggested_lane_count"] == 1
    assert summary["suggested_lane_observed_count"] == 1
    assert summary["waived_suggested_lane_count"] == 1
    assert summary["suggested_lane_observation_rate"] == 1.0

    state["agents"].extend(
        [
            {
                "repository_alias": "manager-heavy",
                "task_id": "review-task",
                "lane": "review",
                "required": False,
                "agent_id": agent_id,
                "status": "completed",
            }
            for agent_id in ("reviewer-a", "reviewer-b")
        ]
    )
    assert live.invariant_summary(state, [])["duplicate_equivalent_lanes"] == 1


def test_manager_acceptance_pytest_environment_drops_parent_generation_binding():
    acceptance_path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_pytest_environment_test", acceptance_path)
    assert spec is not None and spec.loader is not None
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    isolated = acceptance.isolated_pytest_environment(
        {
            "HOME": "/isolated/home",
            "QWENDEX_STATE_DB": "/isolated/state.sqlite",
            "QWENDEX_RUNTIME_GENERATION_REQUIRED": "1",
            "QWENDEX_RUNTIME_GENERATION_ID": "rtg-parent",
            "QWENDEX_RUNTIME_ROOT": "/parent/runtime",
            "QWENDEX_ROOT": "/parent/generation/tree",
            "QWENDEX_DEV_ROOT": "/parent/dev",
            "QWENDEX_CODEX_HOME": "/parent/codex-home",
            "QWENDEX_HOOK_GENERATION": "rtg-parent",
        }
    )
    assert isolated == {"HOME": "/isolated/home"}


def test_manager_acceptance_sanitizes_embedded_workspace_paths_and_rejects_private_paths(tmp_path):
    acceptance_path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_privacy_test", acceptance_path)
    assert spec is not None and spec.loader is not None
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)

    command = acceptance.relative_command(
        [
            "python3",
            f"--junitxml={ROOT / '.qwendex-dev' / 'results' / 'pytest.xml'}",
            str(ROOT / "tests" / "smoke"),
        ]
    )
    assert command == [
        "python3",
        "--junitxml=.qwendex-dev/results/pytest.xml",
        "tests/smoke",
    ]
    assert acceptance.public_artifact_path(ROOT / "docs" / "validation" / "summary.json") == (
        "docs/validation/summary.json"
    )
    assert acceptance.public_artifact_path(tmp_path / "outside" / "summary.json") == "summary.json"

    private_artifact = tmp_path / "summary.json"
    private_workspace = "/" + "/".join(("home", "alice", "private", "repo"))
    private_artifact.write_text(
        json.dumps({"working_directory": private_workspace}) + "\n", encoding="utf-8"
    )
    privacy = acceptance.scan_privacy([private_artifact])
    assert privacy["status"] == "fail"
    assert privacy["failures"] == [
        {"artifact": "summary.json", "reason": "private_absolute_path"}
    ]


def test_manager_evidence_distinguishes_current_history_debt_stale_and_quarantine(tmp_path):
    results = tmp_path / "results"
    current_run = "current-run-001"
    generation = "rtg-11111111111111111111"
    source_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    config_digest = hashlib.sha256(
        (ROOT / "config" / "qwendex" / "qwendex.json").read_bytes()
    ).hexdigest()
    schema_digest = hashlib.sha256(
        (ROOT / "config" / "qwendex" / "qwendex.schema.json").read_bytes()
    ).hexdigest()

    def write_summary(run_id: str, profile: str, *, result: str, commit: str, runtime: str) -> None:
        path = (
            results
            / "manager-production"
            / run_id
            / profile
            / f"manager_accept_{profile}_summary.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "schema_version": f"qwendex.manager_accept_{profile}.v1",
                    "run_id": run_id,
                    "acceptance_profile": profile,
                    "source_commit": commit,
                    "config_digest": config_digest,
                    "schema_digest": schema_digest,
                    "runtime_generation": runtime,
                    "hook_generation": runtime,
                    "state_schema_version": 2,
                    "privacy_status": "pass",
                    "result": result,
                    "final_status": "fixture",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    write_summary(current_run, "offline", result="pass", commit=source_commit, runtime=generation)
    write_summary("historical-run", "offline", result="pass", commit="1" * 40, runtime=generation)
    write_summary("debt-run-001", "live", result="fail", commit=source_commit, runtime=generation)
    write_summary("stale-run-001", "production", result="pass", commit=source_commit, runtime="")
    quarantine = results / "manager-production" / "quarantine" / "rejected.json"
    quarantine.parent.mkdir(parents=True)
    quarantine.write_text('{"status":"quarantined"}\n', encoding="utf-8")

    state_db = tmp_path / "must-not-exist.sqlite"
    shown = run_qwendex(
        "manager",
        "evidence",
        "--run-id",
        current_run,
        "--results-root",
        str(results),
        "--json",
        env={
            "QWENDEX_STATE_DB": str(state_db),
            "QWENDEX_RUNTIME_GENERATION_ID": generation,
            "QWENDEX_HOOK_GENERATION": generation,
        },
    )
    assert shown.returncode == 0, shown.stderr or shown.stdout
    data = json.loads(shown.stdout)["data"]
    assert data["counts"] == {
        "current_acceptance_evidence": 1,
        "historical_accepted_evidence": 1,
        "historical_validation_debt": 1,
        "stale_or_unbound_artifacts": 1,
        "quarantined_artifacts": 1,
    }
    assert data["ambiguous_latest_selection"] is False
    assert not state_db.exists()


def test_qdex_isolated_home_leaves_normal_codex_home_byte_for_byte_unchanged(tmp_path):
    home = tmp_path / "home"
    normal_home = home / ".codex"
    normal_home.mkdir(parents=True)
    (normal_home / "config.toml").write_text('model = "normal-decoy"\n', encoding="utf-8")
    (normal_home / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    (normal_home / "auth.json").write_text('{"auth":"normal-decoy"}\n', encoding="utf-8")
    (normal_home / "version.json").write_text('{"latest":"0.144.4"}\n', encoding="utf-8")
    (normal_home / "installation_id").write_text("normal-installation\n", encoding="utf-8")
    (normal_home / "sentinel.bin").write_bytes(b"normal-codex-home-must-not-change\x00")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"${1:-}\" == \"--version\" ]]; then printf 'codex-cli 0.144.4\\n'; fi\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    host = fake_bin / "codex-code-mode-host"
    host.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    host.chmod(0o755)
    dev_root = tmp_path / "qwendex-dev"
    repo = tmp_path / "repo"
    repo.mkdir()

    def snapshot() -> dict[str, str]:
        return {
            path.relative_to(normal_home).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(normal_home.rglob("*"))
            if path.is_file()
        }

    before = snapshot()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        "QWENDEX_MAIN_CODEX_BIN": str(fake_codex),
        "QWENDEX_DEV_CODEX_BIN": str(fake_codex),
    }
    sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout
    qdex = subprocess.run(
        [str(home / ".local" / "bin" / "qdex"), "--repo", str(repo), "--json"],
        cwd=repo,
        env={
            **env,
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
            "QWENDEX_MANAGER_UNHOOKED_REASON": "isolated security fixture",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert qdex.returncode == 0, qdex.stderr or qdex.stdout
    payload = json.loads(qdex.stdout)
    isolated_codex_home = dev_root / ".qwendex-dev" / "codex_home"
    assert payload["codex_home"] == str(dev_root / ".qwendex-dev" / "codex_home")
    assert payload["codex_home"] != str(normal_home)
    assert (isolated_codex_home / "auth.json").is_symlink()
    assert (isolated_codex_home / "auth.json").resolve() == (normal_home / "auth.json").resolve()
    assert not (isolated_codex_home / "version.json").is_symlink()
    assert (isolated_codex_home / "version.json").read_bytes() == (
        normal_home / "version.json"
    ).read_bytes()
    assert snapshot() == before


def test_actual_normal_home_snapshot_ignores_volatile_auth_and_version_cache(tmp_path):
    install_path = ROOT / "scripts" / "qwendex_manager_install_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_manager_install_snapshot_test", install_path)
    assert spec is not None and spec.loader is not None
    install = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(install)

    normal = tmp_path / ".codex"
    normal.mkdir()
    for name in ("config.toml", "hooks.json", "installation_id", "auth.json", "version.json"):
        (normal / name).write_text(f"{name}:before\n", encoding="utf-8")
    before = install.LIVE.static_normal_home_snapshot(tmp_path)
    assert sorted(before) == ["config.toml", "hooks.json", "installation_id"]
    (normal / "auth.json").write_text("auth:after\n", encoding="utf-8")
    (normal / "version.json").write_text("version:after\n", encoding="utf-8")
    assert install.LIVE.static_normal_home_snapshot(tmp_path) == before
    (normal / "config.toml").write_text("config:after\n", encoding="utf-8")
    assert install.LIVE.static_normal_home_snapshot(tmp_path) != before
