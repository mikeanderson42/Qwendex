from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"


_AMBIENT_QWENDEX_ENV_KEYS = {
    "CODEX_AGENT_USE",
    "CODEX_HOME",
    "QWENDEX_CODEX_STATUS_FILE",
    "QWENDEX_EFFECTIVE_AGENT_USE",
    "QWENDEX_KAVEMAN_ENABLED",
    "QWENDEX_KAVEMAN_DIRECTIVE",
    "QWENDEX_LEDGER_DB",
    "QWENDEX_LOCAL_SUBAGENTS",
    "QWENDEX_ORCHESTRATION_MODE",
    "QWENDEX_OUTPUT_POLICY",
    "QWENDEX_PERFORMANCE_CAPTURE",
    "QWENDEX_PERFORMANCE_DB",
    "QWENDEX_RESULTS_ROOT",
    "QWENDEX_RUN_ID",
    "QWENDEX_STATE_DB",
}


def isolated_qwendex_env(overrides: dict[str, str]) -> dict[str, str]:
    """Do not let a parent Qdex Manager launch affect direct CLI fixtures."""
    environment = dict(os.environ)
    for key in tuple(environment):
        if key in _AMBIENT_QWENDEX_ENV_KEYS or key.startswith(
            ("QWENDEX_AGENT_", "QWENDEX_MANAGER_")
        ):
            environment.pop(key)
    environment.update(overrides)
    return environment


def load_module(name: str) -> Any:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_performance_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_qwendex(*args: str, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = subprocess.run(
        [str(QWENDEX), *args],
        cwd=ROOT,
        env=isolated_qwendex_env(env),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result, json.loads(result.stdout)


def repository_scope(path: Path) -> str:
    return "sha256:" + hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def database_bytes(path: Path) -> bytes:
    return b"".join(
        candidate.read_bytes()
        for candidate in sorted(path.parent.glob(path.name + "*"))
        if candidate.is_file()
    )


def performance_record(
    scope: str,
    *,
    action: str,
    event_key: str,
    raw_path: str,
    raw_prompt: str,
    role: str = "root",
    tool_family: str = "other",
    query_class: str = "not_applicable",
    query_material: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "action": action,
        "repository_scope_digest": scope,
        "run_material": f"run:{raw_path}",
        "manager_launch_material": f"launch:{raw_path}",
        "turn_material": raw_prompt,
        "event_key_material": event_key,
        "agent_role": role,
        "phase": "tool" if action.startswith("tool_") else "session",
        "event_kind": "tool_call" if action.startswith("tool_") else "prompt_submit",
        "tool_family": tool_family,
        "query_class": query_class,
        "scope_class": "repository_root",
        "input_size_bucket": "33-128" if query_material else "none",
        "query_material": query_material,
        "query_fingerprints": True,
        **extra,
    }


def test_performance_storage_persists_only_safe_aggregate_metadata(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    raw_query = "performance-private-query-sentinel"
    raw_path = "/performance/private/path/sentinel"
    raw_prompt = "performance-private-prompt-sentinel"
    raw_output = "performance-private-output-sentinel"
    scope = repository_scope(tmp_path / "repository")

    rejected = performance.record_event(
        database,
        {"repository_scope_digest": raw_path, "query_material": raw_query},
    )
    assert rejected == {"captured": False, "reason": "missing_or_invalid_repository_scope"}
    assert not database.exists()

    records = [
        performance_record(
            scope,
            action="lifecycle",
            event_key="prompt",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
        ),
        performance_record(
            scope,
            action="tool_start",
            event_key="root-search",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            tool_family="search",
            query_class="literal",
            query_material=raw_query,
        ),
        performance_record(
            scope,
            action="tool_finish",
            event_key="root-search",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            tool_family="search",
            query_class="literal",
            output_bytes=len(raw_output.encode("utf-8")),
            result_count=3,
            success=True,
            truncated=False,
        ),
        performance_record(
            scope,
            action="tool_start",
            event_key="worker-search",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            role="worker",
            tool_family="search",
            query_class="literal",
            query_material=raw_query,
        ),
        performance_record(
            scope,
            action="tool_finish",
            event_key="worker-search",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            role="worker",
            tool_family="search",
            query_class="literal",
            output_bytes=0,
            result_count=0,
            success=True,
            truncated=False,
        ),
        performance_record(
            scope,
            action="tool_start",
            event_key="edit",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            tool_family="edit",
        ),
        performance_record(
            scope,
            action="tool_finish",
            event_key="edit",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            tool_family="edit",
            output_bytes=0,
            result_count=0,
            success=True,
            truncated=False,
        ),
        performance_record(
            scope,
            action="stop",
            event_key="stop",
            raw_path=raw_path,
            raw_prompt=raw_prompt,
            phase="stop",
            event_kind="run_stop",
        ),
    ]
    captured = [performance.record_event(database, record) for record in records]
    assert all(result["captured"] is True for result in captured)

    first_summary = performance.summary(
        database,
        retention_days=14,
        max_events=50_000,
        repository_scope_digest=scope,
    )
    second_summary = performance.summary(
        database,
        retention_days=14,
        max_events=50_000,
        repository_scope_digest=scope,
    )
    run_summaries = performance.runs(database, limit=20, repository_scope_digest=scope)
    storage_status = performance.status(database)

    assert first_summary == second_summary
    assert first_summary["schema_version"] == performance.SUMMARY_SCHEMA_VERSION
    assert first_summary["runs_observed"] == 1
    assert first_summary["tool_calls_by_family"] == {"edit": 1, "search": 2}
    assert first_summary["search_read_calls_per_run"]["search_total"] == 2
    assert first_summary["search_output_bytes"]["total"] == len(raw_output.encode("utf-8"))
    assert first_summary["duplicate_query_rate"] == {
        "observed_queries": 2,
        "duplicate_queries": 1,
        "rate": 0.5,
    }
    assert first_summary["root_subagent_overlap"] == {
        "observed_query_fingerprints": 1,
        "overlap_count": 1,
        "rate": 1.0,
    }
    assert first_summary["time_to_first_edit"]["observed"] == 1
    assert first_summary["telemetry_coverage"]["rate"] == 1.0
    assert first_summary["incomplete_event_rate"]["rate"] == 0.0
    assert run_summaries[0]["terminal_classification"] == "stopped"
    assert "run_id" not in run_summaries[0]
    assert storage_status["storage"] == "local_sqlite"
    assert "database_path" not in storage_status

    public_payload = json.dumps(
        {"summary": first_summary, "runs": run_summaries, "status": storage_status},
        sort_keys=True,
    )
    persisted = database_bytes(database)
    for raw_value in (raw_query, raw_path, raw_prompt, raw_output):
        assert raw_value not in public_payload
        assert raw_value.encode("utf-8") not in persisted


def test_performance_maintenance_expires_old_events_and_runs(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    scope = repository_scope(tmp_path / "repository")
    old = performance_record(
        scope,
        action="lifecycle",
        event_key="old-prompt",
        raw_path="/performance/old-path",
        raw_prompt="performance-old-prompt",
        started_at="2000-01-01T00:00:00.000Z",
    )

    assert performance.record_event(database, old)["captured"] is True
    maintenance = performance.maintain(database, retention_days=1, max_events=1)

    assert maintenance == {
        "classified_incomplete": 0,
        "expired_events": 1,
        "max_event_trimmed": 0,
    }
    assert performance.runs(database, limit=20, repository_scope_digest=scope) == []
    assert performance.summary(
        database,
        retention_days=1,
        max_events=1,
        repository_scope_digest=scope,
    )["runs_observed"] == 0


def test_performance_maintenance_trims_the_configured_event_bound(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    scope = repository_scope(tmp_path / "repository")

    for index in range(3):
        record = performance_record(
            scope,
            action="lifecycle",
            event_key=f"event-{index}",
            raw_path=f"/performance/max-events-{index}",
            raw_prompt=f"performance-max-events-{index}",
        )
        assert performance.record_event(database, record)["captured"] is True

    maintenance = performance.maintain(database, retention_days=3650, max_events=1)

    assert maintenance == {
        "classified_incomplete": 0,
        "expired_events": 0,
        "max_event_trimmed": 2,
    }
    assert performance.status(database)["event_count"] == 1


def test_stop_classifies_a_missing_post_tool_event_as_incomplete(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    scope = repository_scope(tmp_path / "repository")
    start = performance_record(
        scope,
        action="tool_start",
        event_key="missing-post",
        raw_path="/performance/missing-post",
        raw_prompt="performance-missing-post",
        tool_family="read",
        query_class="read",
    )
    stop = performance_record(
        scope,
        action="stop",
        event_key="stop",
        raw_path="/performance/missing-post",
        raw_prompt="performance-missing-post",
        phase="stop",
        event_kind="run_stop",
    )

    assert performance.record_event(database, start)["captured"] is True
    assert performance.record_event(database, stop)["captured"] is True
    with sqlite3.connect(database) as conn:
        terminal = conn.execute(
            "SELECT terminal_classification FROM qwendex_performance_events "
            "WHERE event_kind = 'tool_call'"
        ).fetchone()[0]
    aggregate = performance.summary(
        database,
        retention_days=14,
        max_events=50_000,
        repository_scope_digest=scope,
    )

    assert terminal == "aborted_or_incomplete"
    assert aggregate["telemetry_coverage"] == {
        "tool_events": 1,
        "complete_or_classified": 1,
        "rate": 1.0,
    }
    assert aggregate["incomplete_event_rate"] == {
        "tool_events": 1,
        "incomplete_events": 0,
        "rate": 0.0,
    }


def test_performance_cli_defaults_are_inert_and_benchmark_is_isolated(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    database = tmp_path / "qwendex-performance.sqlite"
    env = {
        "HOME": str(home),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(database),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "QWENDEX_MANAGER_MODE": "off",
    }

    status_result, status = run_qwendex("performance", "status", "--json", env=env)
    summary_result, summary = run_qwendex("performance", "summary", "--json", env=env)
    runs_result, runs = run_qwendex("performance", "runs", "--json", env=env)
    purge_result, purge = run_qwendex("performance", "purge", "--json", env=env)
    benchmark_result, benchmark = run_qwendex(
        "performance", "benchmark", "--suite", "exploration", "--json", env=env
    )

    assert status_result.returncode == 0
    assert status["data"]["capture"] == "off"
    assert status["data"]["telemetry"]["database_exists"] is False
    assert summary_result.returncode == 0
    assert summary["data"]["summary"]["runs_observed"] == 0
    assert summary["data"]["summary"]["instrumentation_overhead"] == "not_observed"
    assert runs_result.returncode == 0
    assert runs["data"]["runs"] == []
    assert purge_result.returncode == 1
    assert purge["status"] == "blocked"
    assert "explicit approval required" in purge["errors"]
    assert benchmark_result.returncode == 0
    assert benchmark["data"]["benchmark"]["status"] == "pass"
    assert benchmark["data"]["benchmark"]["execution"] == "synthetic_isolated"
    assert benchmark["data"]["benchmark"]["event_coverage"]["rate"] == 1.0
    assert benchmark["data"]["benchmark"]["instrumentation_overhead"]["observed"] == 5
    assert benchmark["data"]["benchmark"]["aggregate_summary"]["status"] == "pass"
    assert benchmark["data"]["benchmark"]["privacy_scan"]["status"] == "pass"
    assert not database.exists()
    assert not (tmp_path / "qwendex.sqlite").exists()
    assert not (tmp_path / "results").exists()


def test_performance_purge_requires_approval_and_clears_local_data(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    scope = repository_scope(tmp_path / "repository")
    record = performance_record(
        scope,
        action="lifecycle",
        event_key="purge",
        raw_path="/performance/purge",
        raw_prompt="performance-purge",
    )
    assert performance.record_event(database, record)["captured"] is True
    env = {
        "HOME": str(tmp_path / "home"),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(database),
        "QWENDEX_MANAGER_MODE": "off",
    }

    result, payload = run_qwendex("performance", "purge", "--approve", "--json", env=env)

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["purge"] == {"purged_events": 1, "purged_runs": 1}
    assert performance.status(database)["event_count"] == 0
    assert performance.status(database)["run_count"] == 0


def test_performance_cli_defaults_to_the_canonical_repository_scope(tmp_path: Path) -> None:
    performance = load_module("qwendex_performance")
    database = tmp_path / "qwendex-performance.sqlite"
    repository_a = tmp_path / "repository-a"
    repository_b = tmp_path / "repository-b"
    repository_a.mkdir()
    repository_b.mkdir()
    scope_a = repository_scope(repository_a)
    scope_b = repository_scope(repository_b)
    for scope, event_key in ((scope_a, "a"), (scope_b, "b")):
        record = performance_record(
            scope,
            action="lifecycle",
            event_key=event_key,
            raw_path=f"/performance/{event_key}",
            raw_prompt=f"performance-{event_key}",
        )
        assert performance.record_event(database, record)["captured"] is True

    env = {
        "HOME": str(tmp_path / "home"),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(database),
        "QWENDEX_MANAGER_TARGET_REPO": str(repository_a),
        "QWENDEX_MANAGER_MODE": "off",
    }
    summary_result, summary = run_qwendex("performance", "summary", "--json", env=env)
    explicit_result, explicit_summary = run_qwendex(
        "performance",
        "summary",
        "--repo-root",
        str(repository_b),
        "--json",
        env=env,
    )
    runs_result, runs = run_qwendex("performance", "runs", "--json", env=env)

    assert summary_result.returncode == 0
    assert summary["data"]["summary"]["repository_scope_digest"] == scope_a
    assert summary["data"]["summary"]["runs_observed"] == 1
    assert explicit_result.returncode == 0
    assert explicit_summary["data"]["summary"]["repository_scope_digest"] == scope_b
    assert explicit_summary["data"]["summary"]["runs_observed"] == 1
    assert runs_result.returncode == 0
    assert len(runs["data"]["runs"]) == 1
    assert runs["data"]["runs"][0]["repository_scope_digest"] == scope_a


def test_metadata_hook_capture_preserves_gate_outcomes_and_excludes_raw_values(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    database = tmp_path / "qwendex-performance.sqlite"
    raw_prompt = "performance-hook-private-prompt"
    raw_query = "performance-hook-private-query"
    raw_output = "performance-hook-private-output"
    raw_path = "/performance/hook/private/path"
    env = {
        "HOME": str(home),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(database),
        "QWENDEX_PERFORMANCE_CAPTURE": "metadata",
        "QWENDEX_MANAGER_MODE": "off",
        "QWENDEX_RUN_ID": "performance-hook-run",
    }
    common = {
        "session_id": "performance-hook-session",
        "turn_id": "performance-hook-turn",
        "cwd": str(ROOT),
    }
    events = [
        ("UserPromptSubmit", {**common, "prompt": raw_prompt}),
        (
            "PreToolUse",
            {
                **common,
                "tool_name": "exec_command",
                "tool_use_id": "performance-search",
                "tool_input": {"cmd": f"rg -F {raw_query} {raw_path}"},
            },
        ),
        (
            "PostToolUse",
            {
                **common,
                "tool_name": "exec_command",
                "tool_use_id": "performance-search",
                "tool_input": {"cmd": f"rg -F {raw_query} {raw_path}"},
                "tool_output": raw_output,
                "success": True,
            },
        ),
        ("PreCompact", {**common, "reason": raw_prompt}),
        ("PostCompact", {**common, "reason": raw_prompt}),
        ("SubagentStart", {**common, "agent_id": "worker-1"}),
        (
            "SubagentStop",
            {
                **common,
                "agent_id": "worker-1",
                "last_assistant_message": "FINAL_REPORT\nstatus: completed\nValidation: passed",
            },
        ),
        ("Stop", {**common, "last_assistant_message": raw_prompt}),
    ]

    payloads: list[dict[str, Any]] = []
    for event_name, event in events:
        result, payload = run_qwendex(
            "agent",
            "hook",
            event_name,
            "--event-json",
            json.dumps(event),
            "--json",
            env=env,
        )
        assert result.returncode == 0, result.stderr or result.stdout
        assert payload["status"] == "pass"
        capture = payload["data"]["performance_capture"]
        assert capture["enabled"] is True
        assert capture["captured"] is True
        assert raw_prompt not in json.dumps(capture, sort_keys=True)
        assert raw_query not in json.dumps(capture, sort_keys=True)
        assert raw_output not in json.dumps(capture, sort_keys=True)
        payloads.append(payload)

    summary_result, summary = run_qwendex("performance", "summary", "--json", env=env)
    assert summary_result.returncode == 0
    aggregate = summary["data"]["summary"]
    assert aggregate["runs_observed"] == 1
    assert aggregate["tool_calls_by_family"] == {"search": 1}
    assert aggregate["search_output_bytes"]["total"] == len(raw_output.encode("utf-8"))
    assert aggregate["compaction_event_count"] == 2
    assert aggregate["telemetry_coverage"]["rate"] == 1.0

    performance = load_module("qwendex_performance")
    public_output = json.dumps(
        {"summary": aggregate, "runs": performance.runs(database, limit=20)},
        sort_keys=True,
    )
    persisted = database_bytes(database)
    for raw_value in (raw_prompt, raw_query, raw_output, raw_path):
        assert raw_value not in public_output
        assert raw_value.encode("utf-8") not in persisted
    assert all(payload["data"]["hook_result"] != {"decision": "block"} for payload in payloads)


def test_blocked_hook_does_not_create_telemetry_event(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    database = tmp_path / "qwendex-performance.sqlite"
    env = {
        "HOME": str(home),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_PERFORMANCE_DB": str(database),
        "QWENDEX_PERFORMANCE_CAPTURE": "metadata",
        "QWENDEX_MANAGER_MODE": "off",
    }

    result, payload = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps(
            {
                "tool_name": "exec_command",
                "profile": "explorer",
                "command": "rm -f private-file",
            }
        ),
        "--json",
        env=env,
    )

    assert result.returncode == 1
    assert payload["status"] == "blocked"
    assert payload["data"]["performance_capture"] == {
        "enabled": True,
        "capture": "metadata",
        "captured": False,
        "reason": "hook_blocked",
    }
    assert not database.exists()


def test_runtime_validator_accepts_an_older_v1_config_without_performance() -> None:
    qwendex = load_module("qwendex_cli")
    old_config = json.loads(
        (ROOT / "config" / "qwendex" / "qwendex.json").read_text(encoding="utf-8")
    )
    old_config.pop("performance")

    assert qwendex.validate_qwendex_config(old_config) == []


def test_managed_hook_environment_uses_the_separate_development_database(tmp_path: Path) -> None:
    qwendex = load_module("qwendex_cli")
    codex_home = tmp_path / "qwendex-dev" / ".qwendex-dev" / "codex_home"
    runtime_env = qwendex.managed_hook_runtime_env(
        {"QWENDEX_ROOT": str(ROOT)},
        codex_home=codex_home,
    )
    hook_config = qwendex.managed_agent_hook_config(runtime_env=runtime_env)

    expected_database = str(codex_home.parent / "state" / "qwendex-performance.sqlite")
    assert runtime_env["QWENDEX_PERFORMANCE_DB"] == expected_database
    for entries in hook_config["hooks"].values():
        command = entries[0]["hooks"][0]["command"]
        assert f"QWENDEX_PERFORMANCE_DB={expected_database}" in command
