from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"


def load_module(name: str) -> Any:
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_optimization_lab_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git_output(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def make_repository(tmp_path: Path) -> tuple[Path, str, str]:
    repository = tmp_path / "source repository"
    repository.mkdir()
    subprocess.run(["git", "init", str(repository)], check=True, text=True, capture_output=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.email", "lab@example.test"], check=True)
    subprocess.run(["git", "-C", str(repository), "config", "user.name", "Lab Test"], check=True)
    (repository / "tracked.txt").write_text("needle baseline\n", encoding="utf-8")
    (repository / ".hidden.txt").write_text("needle hidden\n", encoding="utf-8")
    (repository / ".gitignore").write_text("generated.txt\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "fixture"], check=True, text=True, capture_output=True)
    return repository, git_output(repository, "rev-parse", "HEAD"), git_output(repository, "rev-parse", "HEAD^{tree}")


def write_full_manifest(tmp_path: Path, repository: Path, commit: str, tree: str) -> Path:
    prompts = {f"task_{index}": f"private prompt {index}" for index in range(12)}
    prompts_path = tmp_path / "prompts.json"
    prompts_path.write_text(json.dumps(prompts), encoding="utf-8")
    strata = ["A_read_only_localization"] * 4 + ["B_diagnosis_documentation"] * 4 + ["C_bounded_implementation"] * 4
    tasks: list[dict[str, Any]] = []
    for index, stratum in enumerate(strata):
        fixture_edit: dict[str, str] | None = None
        validation: list[str] = []
        allowed: list[str] = []
        if stratum == "C_bounded_implementation":
            allowed = [".qwendex-lab-fixture/task.json"]
            validation = ["python3", "-m", "json.tool", ".qwendex-lab-fixture/task.json"]
            fixture_edit = {
                "relative_path": ".qwendex-lab-fixture/task.json",
                "before": '{"state": "before"}\n',
                "after": '{"state": "after"}\n',
            }
        execution: dict[str, Any] = {
            "search": {"pattern": "needle", "mode": "literal", "root": "."},
            "candidate_budget": {"per_file_ranges": 4, "total_ranges": 16, "page_size": 16},
        }
        if fixture_edit:
            execution["fixture_edit"] = fixture_edit
        prompt = prompts[f"task_{index}"]
        tasks.append(
            {
                "id": f"task_{index}",
                "stratum": stratum,
                "repository": "fixture",
                "pair_order": "baseline_first" if index % 2 == 0 else "candidate_first",
                "prompt_digest": "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "private_prompt_ref": f"prompts.json#task_{index}",
                "allowed_write_surface": allowed,
                "expected_relevant_files": ["tracked.txt"],
                "expected_relevant_regions": [{"path": "tracked.txt", "anchor": "needle"}],
                "validation_command": validation,
                "task_success_rubric": {"type": "search_recall", "minimum_file_recall": 1.0, "minimum_region_recall": 1.0},
                "timeout_seconds": 20,
                "tool_call_budget": 6,
                "broad_search_expected": True,
                "candidate_expected": True,
                "execution": execution,
            }
        )
    payload = {
        "schema_version": "qwendex.optimization_lab.workload.v1",
        "workload_id": "fixture-workload",
        "created_at": "2026-07-12T00:00:00Z",
        "frozen": True,
        "seed": 1,
        "execution_mode": "controlled_search_evidence_v1",
        "model_policy": {
            "model_identifier": "controlled-search-evidence-v1",
            "reasoning_effort": "deterministic",
            "manager_mode": "Manager",
            "local_routing_state": "off",
            "permission_mode": "workspace-write",
        },
        "repositories": [
            {
                "id": "fixture",
                "source_path": str(repository),
                "commit": commit,
                "tree_digest": "git:" + tree,
                "fixture_classification": "clean_snapshot",
            },
            {
                "id": "fixture_second",
                "source_path": str(repository),
                "commit": commit,
                "tree_digest": "git:" + tree,
                "fixture_classification": "clean_snapshot",
            },
        ],
        "tasks": tasks,
    }
    manifest = tmp_path / "workload.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    return manifest


def live_supervisor_budget(lab: Any, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema_version": lab.LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION,
        "mode": "test",
        "startup_preflight_seconds": 0.12,
        "first_model_activity_seconds": 0.12,
        "inactivity_seconds": 0.12,
        "hard_wall_seconds": 0.50,
        "graceful_termination_seconds": 0.10,
        "forced_cleanup_seconds": 0.10,
        "pipe_drain_seconds": 0.10,
        "poll_interval_seconds": 0.01,
        "derivation": {"kind": "accelerated_smoke_fixture"},
    }
    value.update(overrides)
    return lab._normalise_live_supervisor_budgets(value)


def run_live_supervisor_fixture(
    lab: Any,
    tmp_path: Path,
    code: str,
    budgets: dict[str, Any],
    *,
    hook_database: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_dir = tmp_path / "raw"
    phase_path = raw_dir / "runtime_phases.log"
    environment = dict(os.environ)
    environment.pop("QWENDEX_PERFORMANCE_DB", None)
    environment.pop("QWENDEX_PERFORMANCE_CAPTURE", None)
    environment["QWENDEX_TEST_PHASES"] = str(phase_path)
    if hook_database is not None:
        environment["QWENDEX_PERFORMANCE_DB"] = str(hook_database)
        environment["QWENDEX_PERFORMANCE_CAPTURE"] = "metadata"
    profile = lab._new_live_runtime_profile(
        run_id="private-test-run",
        task_id="private-test-task",
        variant="baseline",
        attempt="initial",
        candidate_id="baseline_raw_tools",
        repository={"commit": "a" * 40, "tree_digest": "git:" + "b" * 40},
        manifest_digest="sha256:" + "c" * 64,
        model_policy={
            "model_identifier": "test-model",
            "reasoning_effort": "test",
            "local_routing_state": "off",
            "manager_mode": "Manager",
            "permission_mode": "workspace-write",
        },
        budgets=budgets,
    )
    source = (
        SUPERVISOR_PHASE_HELPER + textwrap.dedent(code[len(SUPERVISOR_PHASE_HELPER) :])
        if code.startswith(SUPERVISOR_PHASE_HELPER)
        else textwrap.dedent(code)
    )
    result = lab._supervise_live_subprocess(
        [sys.executable, "-c", source],
        cwd=tmp_path,
        environment=environment,
        raw_dir=raw_dir,
        profile=profile,
        budgets=budgets,
    )
    lab._record_runtime_phase(profile, "runner_complete")
    lab._finalise_live_runtime_profile(profile)
    return result, profile, raw_dir


SUPERVISOR_PHASE_HELPER = """
import json
import os
from pathlib import Path

def phase(value):
    with Path(os.environ["QWENDEX_TEST_PHASES"]).open("a", encoding="utf-8") as handle:
        handle.write(value + "\\n")

def event(event_type, item_type=None, **fields):
    payload = {"type": event_type}
    if item_type:
        payload["item"] = {"type": item_type, **fields}
    print(json.dumps(payload), flush=True)
"""


def test_raw_search_uses_current_worktree_and_preserves_ignore_boundary(tmp_path: Path) -> None:
    search = load_module("qwendex_search")
    repository, _, _ = make_repository(tmp_path)
    (repository / "tracked.txt").write_text("needle modified\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("needle untracked\n", encoding="utf-8")
    (repository / "generated.txt").write_text("needle ignored\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("needle external\n", encoding="utf-8")
    (repository / "external-link.txt").symlink_to(outside)

    default = search.raw_content_search("needle", root=repository, mode="literal")
    paths = {item["path"] for item in default["matches"] if item["kind"] == "match"}

    assert {"tracked.txt", "untracked.txt", ".hidden.txt"}.issubset(paths)
    assert "generated.txt" not in paths
    assert "external-link.txt" not in paths
    assert default["safety"]["external_symlink_denied"] == 1

    included = search.raw_content_search("needle ignored", root=repository, mode="literal", include_ignored=True)
    assert {item["path"] for item in included["matches"] if item["kind"] == "match"} == {"generated.txt"}


def test_workload_validation_and_baseline_capture_are_isolated(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)

    validation = lab.validate_workload(manifest)
    assert validation["status"] == "pass"
    assert validation["workload"]["task_count"] == 12

    baseline = lab.baseline_capture(manifest, output_root=tmp_path / "artifacts")
    artifact_dir = Path(baseline["data"]["artifact_dir"])
    rows = [json.loads(line) for line in (artifact_dir / "06_baseline_runs.jsonl").read_text(encoding="utf-8").splitlines()]

    assert baseline["status"] == "pass"
    assert len(rows) == 12
    assert all(row["status"] == "pass" for row in rows)
    assert not (repository / ".qwendex-lab-fixture").exists()
    assert json.loads((artifact_dir / "13_performance_summary.json").read_text(encoding="utf-8"))["candidate_status"] == "not_applicable_pre_candidate_baseline"
    artifact_manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert all(
        (artifact_dir / item["path"]).is_file()
        and hashlib.sha256((artifact_dir / item["path"]).read_bytes()).hexdigest() == item["sha256"].split(":", 1)[1]
        for item in artifact_manifest["artifacts"]
    )


def test_compaction_is_deterministic_paginated_and_freshness_complete(tmp_path: Path) -> None:
    search = load_module("qwendex_search")
    repository, _, _ = make_repository(tmp_path)
    (repository / "many.txt").write_text("".join(f"needle {index}\n" for index in range(80)), encoding="utf-8")
    raw = search.raw_content_search("needle", root=repository, mode="literal")

    first = search.compact_content_search(raw, pattern="needle", mode="literal", per_file_ranges=8, total_ranges=8, page_size=1)
    second = search.compact_content_search(
        raw,
        pattern="needle",
        mode="literal",
        per_file_ranges=8,
        total_ranges=8,
        page_size=1,
        page_token=first["continuation_token"],
    )
    repeated = search.compact_content_search(
        raw,
        pattern="needle",
        mode="literal",
        per_file_ranges=8,
        total_ranges=8,
        page_size=1,
    )
    duplicate = dict(raw)
    duplicate["matches"] = [*raw["matches"], *raw["matches"]]
    deduplicated = search.compact_content_search(duplicate, pattern="needle", mode="literal", per_file_ranges=8, total_ranges=8, page_size=8)

    assert first["ranges"] == repeated["ranges"]
    assert first["continuation_token"] == repeated["continuation_token"]
    assert first["continuation_token"]
    assert first["ranges"] != second["ranges"]
    assert first["truncated"] is True
    assert deduplicated["retained_range_count"] <= 8
    assert search.freshness_matrix()["status"] == "pass"


def test_v2_preserves_dense_definition_coverage_and_rejects_stale_cursors(tmp_path: Path) -> None:
    search = load_module("qwendex_search")
    repository, _, _ = make_repository(tmp_path)
    source_dir = repository / "path with space" / "unicodé"
    source_dir.mkdir(parents=True)
    dense = source_dir / "dense definitions.py"
    dense.write_text(
        "".join(f"def shared_{index}():\n    return {index}\n\n" for index in range(48))
        + "def required_definition():\n    return 'required'\n",
        encoding="utf-8",
    )
    (repository / "low_density.py").write_text("def required_low_density():\n    return True\n", encoding="utf-8")
    (repository / "references.py").write_text("value = shared_1()\n# reference to required_definition\n", encoding="utf-8")
    (repository / "long.py").write_text("needle-long " + "x" * 20_000 + "\n", encoding="utf-8")
    (repository / "binary.bin").write_bytes(b"\x00needle-binary payload")
    (repository / "generated.txt").write_text("def ignored_definition():\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repository), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repository), "commit", "-m", "v2 adversarial fixture"], check=True, text=True, capture_output=True)
    (repository / "tracked.txt").write_text("def modified_definition():\n", encoding="utf-8")
    (repository / "untracked.py").write_text("def untracked_definition():\n", encoding="utf-8")

    first_payload = search.content_search_payload(
        "def ",
        root=repository,
        mode="regex",
        candidate_id="v2",
        per_file_ranges=4,
        total_ranges=4,
        page_size=1,
    )
    first = first_payload["result"]
    repeated = search.content_search_payload(
        "def ",
        root=repository,
        mode="regex",
        candidate_id="v2",
        per_file_ranges=4,
        total_ranges=4,
        page_size=1,
    )["result"]

    assert first["candidate_id"] == "search_evidence_compaction_v2"
    assert first["query_class"] == "broad_definition"
    assert first["model_evidence"] == repeated["model_evidence"]
    assert first["cursor"] == repeated["cursor"]
    assert first["completeness"]["state"] == "partial_requires_next_cursor"
    assert first["cursor"]
    assert "def " not in first["cursor"]
    assert str(repository) not in first["cursor"]
    assert first["file_inventory_complete"] is True
    assert any(item["path"].endswith("dense definitions.py") for item in first["file_inventory"])
    assert "ranges" not in first

    pages = [first]
    cursor = first["cursor"]
    while cursor:
        next_payload = search.content_search_next_payload(
            "def ",
            root=repository,
            mode="regex",
            cursor=cursor,
            per_file_ranges=4,
            total_ranges=4,
            page_size=1,
        )
        next_result = next_payload["result"]
        pages.append(next_result)
        cursor = next_result["cursor"]
    def evidence_ranges(page: dict[str, Any]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for evidence in page["model_evidence"]:
            location, _, reason = evidence.partition(" — ")
            path, _, span = location.rpartition(":")
            start, _, end = span.partition("-")
            values.append({"path": path, "start_line": int(start), "end_line": int(end), "reason": reason})
        return values

    all_ranges = [item for page in pages for item in evidence_ranges(page)]
    raw_definitions = search.raw_content_search("def ", root=repository, mode="regex")

    def included_anchor(path_suffix: str, anchor: str) -> bool:
        lines = [
            item["line_number"]
            for item in raw_definitions["matches"]
            if item.get("kind") == "match" and str(item.get("path") or "").endswith(path_suffix) and anchor in str(item.get("line_text") or "")
        ]
        return any(
            str(item.get("path") or "").endswith(path_suffix)
            and any(item["start_line"] <= line <= item["end_line"] for line in lines)
            for item in all_ranges
        )

    assert included_anchor("dense definitions.py", "def required_definition")
    assert included_anchor("low_density.py", "def required_low_density")
    assert included_anchor("tracked.txt", "def modified_definition")
    assert included_anchor("untracked.py", "def untracked_definition")
    assert pages[-1]["completeness"]["state"] == "complete"
    assert pages[-1]["cursor"] is None

    stale = search.content_search_payload(
        "def ",
        root=repository,
        mode="regex",
        candidate_id="v2",
        per_file_ranges=4,
        total_ranges=4,
        page_size=1,
    )["result"]
    dense.rename(source_dir / "renamed definitions.py")
    with pytest.raises(search.SearchError, match="stale"):
        search.content_search_next_payload(
            "def ",
            root=repository,
            mode="regex",
            cursor=stale["cursor"],
            per_file_ranges=4,
            total_ranges=4,
            page_size=1,
        )

    binary = search.content_search_payload("needle-binary", root=repository, mode="literal", candidate_id="v2")["result"]
    long_line = search.content_search_payload("needle-long", root=repository, mode="literal", candidate_id="v2")["result"]
    ignored = search.content_search_payload("ignored_definition", root=repository, mode="literal", candidate_id="v2")["result"]
    fallback = search.content_search_payload("def ", root=repository, mode="regex", candidate_id="v2", max_files=1)["result"]
    assert binary["binary_file_count"] + binary["raw_match_count"] >= 1
    assert long_line["model_evidence"]
    assert long_line["model_visible_bytes"] < long_line["raw_output_bytes"]
    assert not any("generated.txt" in item for item in ignored["model_evidence"])
    assert fallback["result_mode"] == "baseline_fallback"
    assert fallback["fallback_count"] == 1


def test_paired_run_isolated_and_compare_validates_artifacts(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)

    paired = lab.paired_run(manifest, candidate_id="search_evidence_compaction_v1", output_root=tmp_path / "paired-artifacts")
    artifact_dir = Path(paired["data"]["artifact_dir"])
    compared = lab.compare_run(artifact_dir)
    gate = json.loads((artifact_dir / "14_gate_decision.json").read_text(encoding="utf-8"))
    environment = json.loads((artifact_dir / "02_environment_lock.json").read_text(encoding="utf-8"))
    performance = json.loads((artifact_dir / "13_performance_summary.json").read_text(encoding="utf-8"))

    assert paired["status"] == "pass"
    assert paired["data"]["attempted_pairs"] == 12
    assert paired["data"]["valid_pairs"] == 12
    assert gate["candidate_decision"] == "hold_for_more_evidence"
    assert gate["hard_gates"]["manager_policy_and_local_routing"] == "pass"
    assert gate["hard_gates"]["privacy_boundary"] == "pass"
    assert compared["status"] == "pass"
    assert compared["data"]["schema_failures"] == 0
    assert environment["started_at"]
    assert environment["completed_at"]
    assert environment["codex_runtime"]["version"]
    assert environment["codex_runtime"]["digest"].startswith("sha256:")
    assert performance["time_to_first_relevant_file_ms"] == "not_observed_controlled_runner"
    assert set(performance["telemetry_instrumentation_overhead_ms"]) == {"p50", "p95"}


def test_v2_paired_run_validates_cursor_coverage_contract(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)

    paired = lab.paired_run(manifest, candidate_id="search_evidence_compaction_v2", output_root=tmp_path / "paired-v2-artifacts")
    artifact_dir = Path(paired["data"]["artifact_dir"])
    gate = json.loads((artifact_dir / "14_gate_decision.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (artifact_dir / "07_candidate_runs.jsonl").read_text(encoding="utf-8").splitlines()]

    assert paired["data"]["valid_pairs"] == 12
    assert gate["hard_gates"]["v2_cursor_coverage_contract"] == "pass"
    assert all(row["candidate_id"] == "search_evidence_compaction_v2" for row in rows)
    assert all(row["retrieval_contract"]["cursor_contract_complete"] for row in rows)


def test_live_workload_schema_and_trace_summary_are_private_metadata_only(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["execution_mode"] = "live_agent_adoption_v2"
    payload["live_contract"] = {
        "runner": "codex_exec_json",
        "conversation_isolation": "fresh_home_per_arm",
        "candidate_instruction_delivery": "scoped_environment_hook",
    }
    task_classes = [
        "narrow_exact_localization",
        "broad_definition_discovery",
        "broad_reference_discovery",
        "documentation_code_verification",
    ]
    for index, task in enumerate(payload["tasks"]):
        task["live"] = {"task_class": task_classes[index % len(task_classes)], "candidate_eligible": index % 2 == 0}
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    validated = lab.validate_workload(manifest)
    events = tmp_path / "events.jsonl"
    events.write_text(
        "\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "scripts/qwendex search content def --candidate v2", "aggregated_output": "{}"}}),
                json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "python3 -m pytest tests", "aggregated_output": "pass"}}),
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 11, "output_tokens": 7}}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    trace = lab._live_trace_summary(events)

    assert validated["status"] == "pass"
    assert trace["candidate_adopted"] is True
    assert trace["candidate_search_calls"] == 1
    assert trace["validation_tool_calls"] == 1
    assert trace["token_usage"] == {"input_tokens": 11, "output_tokens": 7}


def test_live_supervisor_classifies_startup_and_first_model_timeouts(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")

    never_starts, never_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "never-starts",
        """
        import time
        time.sleep(1)
        """,
        live_supervisor_budget(lab, startup_preflight_seconds=0.05),
    )
    assert never_starts["timed_out"] is True
    assert never_profile["termination"]["timeout_classification"] == "timeout_before_preflight_complete"

    no_model, no_model_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "no-model",
        SUPERVISOR_PHASE_HELPER
        + """
        import time
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("thread.started")
        time.sleep(1)
        """,
        live_supervisor_budget(lab, startup_preflight_seconds=0.50, first_model_activity_seconds=0.05, inactivity_seconds=0.50),
    )
    assert no_model["timed_out"] is True
    assert no_model_profile["termination"]["timeout_classification"] == "timeout_before_first_model_event"
    assert no_model_profile["phase_timestamps"]["first_structured_event"] != "not_observed"


def test_live_supervisor_handles_progress_and_stalled_lifecycle_phases(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")

    progress, progress_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "progress",
        SUPERVISOR_PHASE_HELPER
        + """
        import time
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        for _ in range(8):
            event("item.completed", "agent_message")
            time.sleep(0.03)
        """,
        live_supervisor_budget(lab, hard_wall_seconds=0.45, inactivity_seconds=0.08),
    )
    assert progress["timed_out"] is False
    assert progress["returncode"] == 0
    assert progress_profile["phase_durations_ms"]["total_runner_ms"] > 100.0
    assert progress_profile["trusted_progress_event_counts"]["model_or_assistant_event"] == 8

    stalled_tool, stalled_tool_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "stalled-tool",
        SUPERVISOR_PHASE_HELPER
        + """
        import time
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        event("item.started", "command_execution", command="rg test")
        time.sleep(1)
        """,
        live_supervisor_budget(lab, inactivity_seconds=0.05, hard_wall_seconds=0.40),
    )
    assert stalled_tool["timed_out"] is True
    assert stalled_tool_profile["termination"]["timeout_classification"] == "timeout_due_to_inactivity"
    assert stalled_tool_profile["phase_timestamps"]["first_tool_start"] != "not_observed"

    manager_stop, manager_stop_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "manager-stop",
        SUPERVISOR_PHASE_HELPER
        + """
        import time
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        event("item.started", "collab_tool_call", tool_name="manager stop")
        time.sleep(1)
        """,
        live_supervisor_budget(lab, inactivity_seconds=0.05, hard_wall_seconds=0.40),
    )
    assert manager_stop["timed_out"] is True
    assert manager_stop_profile["termination"]["timeout_classification"] == "timeout_during_manager_stop"


def test_live_supervisor_drains_pipes_and_cleans_process_groups(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")

    pipe_hang, pipe_hang_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "pipe-hang",
        SUPERVISOR_PHASE_HELPER
        + """
        import subprocess
        import sys
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        """,
        live_supervisor_budget(lab, pipe_drain_seconds=0.05, graceful_termination_seconds=0.05, forced_cleanup_seconds=0.05),
    )
    assert pipe_hang["timed_out"] is True
    assert pipe_hang_profile["termination"]["timeout_classification"] == "timeout_after_child_exit_pipe_drain"
    assert pipe_hang_profile["termination"]["cleanup_status"] == "pass"

    output, output_profile, output_raw = run_live_supervisor_fixture(
        lab,
        tmp_path / "backpressure",
        SUPERVISOR_PHASE_HELPER
        + """
        import sys
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        sys.stdout.buffer.write(b"x" * (1024 * 1024))
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(b"y" * (1024 * 1024))
        sys.stderr.buffer.flush()
        """,
        live_supervisor_budget(lab, hard_wall_seconds=1.0),
    )
    assert output["timed_out"] is False
    pipe_state = output_profile["process_diagnostics"]["pipe_state"]["streams"]
    assert pipe_state["stdout"]["bytes"] >= 1024 * 1024
    assert pipe_state["stderr"]["bytes"] >= 1024 * 1024
    assert b"x" * 32 not in json.dumps(output_profile, sort_keys=True).encode("utf-8")
    assert (output_raw / "events.jsonl").stat().st_size >= 1024 * 1024

    terminated, terminated_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "group-timeout",
        SUPERVISOR_PHASE_HELPER
        + """
        import subprocess
        import sys
        import time
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        for _ in range(4):
            event("item.completed", "agent_message")
            time.sleep(0.03)
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
        time.sleep(3)
        """,
        live_supervisor_budget(lab, hard_wall_seconds=0.16, inactivity_seconds=0.08, graceful_termination_seconds=0.05, forced_cleanup_seconds=0.05),
    )
    assert terminated["timed_out"] is True
    assert terminated_profile["termination"]["cleanup_status"] == "pass"
    assert terminated_profile["process_diagnostics"]["snapshots"][-1]["non_zombie_process_count"] == 0


def test_live_supervisor_policy_equivalence_and_privacy_contract(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    baseline_policy = live_supervisor_budget(lab)
    candidate_policy = live_supervisor_budget(lab)
    assert baseline_policy["policy_identity"] == candidate_policy["policy_identity"]
    assert json.dumps(baseline_policy, sort_keys=True, separators=(",", ":")) == json.dumps(candidate_policy, sort_keys=True, separators=(",", ":"))

    _, profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "privacy",
        SUPERVISOR_PHASE_HELPER
        + """
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "command_execution", command="QWENDEX_PRIVATE_SENTINEL rg confidential")
        event("item.completed", "agent_message")
        """,
        baseline_policy,
    )
    serialized = json.dumps(profile, sort_keys=True)
    assert "QWENDEX_PRIVATE_SENTINEL" not in serialized
    assert profile["schema_version"] == "qwendex.live_runtime_profile.v1"
    assert profile["privacy"]["metadata_only"] is True
    assert "commands" in lab.live_runtime_profile_contract()["privacy_boundary"]["forbidden_fields"]

    _, wait_profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "collaboration-wait",
        SUPERVISOR_PHASE_HELPER
        + """
        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        event("item.started", "collab_tool_call", tool="wait")
        """,
        baseline_policy,
    )
    assert "collaboration_wait_start" in wait_profile["trusted_progress_event_counts"]
    assert "collaboration_wait_no_completion" in wait_profile["termination"]["contributing_classifications"]


def _create_hook_lifecycle_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE qwendex_performance_events (
              phase TEXT NOT NULL,
              event_kind TEXT NOT NULL,
              tool_family TEXT NOT NULL,
              terminal_classification TEXT NOT NULL
            )
            """
        )


def test_live_supervisor_uses_only_completed_isolated_hook_lifecycle_progress(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    hook_database = tmp_path / "hook-lifecycle.sqlite"
    _create_hook_lifecycle_database(hook_database)
    policy = live_supervisor_budget(
        lab,
        startup_preflight_seconds=0.5,
        first_model_activity_seconds=0.5,
        inactivity_seconds=0.2,
        hard_wall_seconds=0.8,
    )
    result, profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "hook-progress",
        SUPERVISOR_PHASE_HELPER
        + """
        import sqlite3
        import time

        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        for _ in range(2):
            time.sleep(0.1)
            with sqlite3.connect(os.environ["QWENDEX_PERFORMANCE_DB"]) as connection:
                connection.execute(
                    "INSERT INTO qwendex_performance_events VALUES (?, ?, ?, ?)",
                    ("tool", "tool_call", "search", "completed"),
                )
        time.sleep(0.12)
        """,
        policy,
        hook_database=hook_database,
    )

    assert result["timed_out"] is False
    assert profile["hook_lifecycle_event_counts"] == {"tool_completed": 2}
    assert profile["trusted_progress_event_counts"]["hook_tool_completed"] == 2
    assert "allowlisted_hook_lifecycle_counts" in lab.live_runtime_profile_contract()["privacy_boundary"]["safe_diagnostics"]


def test_live_supervisor_does_not_treat_pending_hook_wait_as_progress(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    hook_database = tmp_path / "hook-pending.sqlite"
    _create_hook_lifecycle_database(hook_database)
    policy = live_supervisor_budget(
        lab,
        startup_preflight_seconds=0.5,
        first_model_activity_seconds=0.5,
        inactivity_seconds=0.12,
        hard_wall_seconds=0.8,
    )
    result, profile, _ = run_live_supervisor_fixture(
        lab,
        tmp_path / "hook-pending",
        SUPERVISOR_PHASE_HELPER
        + """
        import sqlite3
        import time

        phase("manager_preflight_start")
        phase("manager_preflight_end")
        phase("codex_process_start")
        event("item.completed", "agent_message")
        time.sleep(0.04)
        with sqlite3.connect(os.environ["QWENDEX_PERFORMANCE_DB"]) as connection:
            connection.execute(
                "INSERT INTO qwendex_performance_events VALUES (?, ?, ?, ?)",
                ("tool", "tool_call", "other", "pending"),
            )
        time.sleep(0.3)
        """,
        policy,
        hook_database=hook_database,
    )

    assert result["timed_out"] is True
    assert profile["hook_lifecycle_event_counts"] == {}
    assert profile["termination"]["timeout_classification"] == "timeout_due_to_inactivity"


def test_hook_lifecycle_reader_handles_late_lower_rowid_commits_and_terminal_updates(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    hook_database = tmp_path / "hook-rowids.sqlite"
    _create_hook_lifecycle_database(hook_database)
    policy = live_supervisor_budget(lab)
    profile = lab._new_live_runtime_profile(
        run_id="private-test-run",
        task_id="private-test-task",
        variant="baseline",
        attempt="initial",
        candidate_id="baseline_raw_tools",
        repository={"commit": "a" * 40, "tree_digest": "git:" + "b" * 40},
        manifest_digest="sha256:" + "c" * 64,
        model_policy={
            "model_identifier": "test-model",
            "reasoning_effort": "test",
            "local_routing_state": "off",
            "manager_mode": "Manager",
            "permission_mode": "workspace-write",
        },
        budgets=policy,
    )
    with sqlite3.connect(hook_database) as connection:
        connection.execute(
            "INSERT INTO qwendex_performance_events (rowid, phase, event_kind, tool_family, terminal_classification) VALUES (?, ?, ?, ?, ?)",
            (10, "subagent", "subagent_start", "collaboration", "observed"),
        )
    row_states: dict[int, str] = {}
    now = float(profile["_runtime_start_monotonic"]) + 1.0
    lab._consume_live_hook_lifecycle(profile, hook_database, row_states=row_states, now=now)

    with sqlite3.connect(hook_database) as connection:
        connection.execute(
            "INSERT INTO qwendex_performance_events (rowid, phase, event_kind, tool_family, terminal_classification) VALUES (?, ?, ?, ?, ?)",
            (1, "tool", "tool_call", "search", "pending"),
        )
    lab._consume_live_hook_lifecycle(profile, hook_database, row_states=row_states, now=now + 1.0)

    with sqlite3.connect(hook_database) as connection:
        connection.execute(
            "UPDATE qwendex_performance_events SET terminal_classification = ? WHERE rowid = ?",
            ("completed", 1),
        )
    lab._consume_live_hook_lifecycle(profile, hook_database, row_states=row_states, now=now + 2.0)

    assert profile["hook_lifecycle_event_counts"] == {"subagent_start": 1, "tool_completed": 1}
    assert row_states == {1: "completed", 10: "observed"}


def test_missing_live_final_message_is_not_a_guard_marker(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")

    assert not lab._contains_live_guard_marker(tmp_path)
    (tmp_path / "last_message.md").write_text("ordinary final text", encoding="utf-8")
    assert not lab._contains_live_guard_marker(tmp_path)
    (tmp_path / "last_message.md").write_text("LOCAL_MODEL_TOOL_CALL_TOO_LARGE", encoding="utf-8")
    assert lab._contains_live_guard_marker(tmp_path)


def test_safe_event_shape_classifies_unclosed_collaboration_wait_without_payload(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "opaque-id",
                    "type": "collab_tool_call",
                    "tool": "wait",
                    "prompt": "QWENDEX_PRIVATE_EVENT_SENTINEL",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    shape = lab._safe_event_shape(events)

    assert shape["unclosed_lifecycle_counts"] == {"collaboration_wait": 1}
    assert "QWENDEX_PRIVATE_EVENT_SENTINEL" not in json.dumps(shape, sort_keys=True)


def test_live_manager_cleanup_accepts_idle_standby_and_rejects_residue() -> None:
    lab = load_module("qwendex_optimization_lab")

    assert lab._live_manager_is_clean({"status": "standby", "agent_count": 0, "stale_count": 0})
    assert lab._live_manager_is_clean({"status": "pass", "agent_count": 0, "stale_count": 0})
    assert not lab._live_manager_is_clean({"status": "standby", "agent_count": 1, "stale_count": 0})
    assert not lab._live_manager_is_clean({"status": "standby", "agent_count": 0, "stale_count": 1})


def test_live_candidate_activation_respects_frozen_task_eligibility() -> None:
    lab = load_module("qwendex_optimization_lab")
    candidate_id = lab.search_module().SEARCH_V2_CANDIDATE_ID

    assert lab._live_candidate_active(
        {"live": {"candidate_eligible": True}},
        variant="candidate",
        candidate_id=candidate_id,
    )
    assert not lab._live_candidate_active(
        {"live": {"candidate_eligible": False}},
        variant="candidate",
        candidate_id=candidate_id,
    )
    assert not lab._live_candidate_active(
        {"live": {"candidate_eligible": True}},
        variant="baseline",
        candidate_id=candidate_id,
    )


def test_live_v2_gates_and_metrics_exclude_ineligible_control_pairs() -> None:
    lab = load_module("qwendex_optimization_lab")
    manager_preflight = {
        "actual_status": "pass",
        "stop_status": "STOP_MANAGER_PREFLIGHT_READY",
        "hook_verified": True,
    }
    manager = {"stale_count": 0}
    eligible_pair = {
        "pair_id": "broad",
        "candidate_eligible": True,
        "candidate_invoked": True,
        "state": "pass",
        "task_success": {"baseline": True, "candidate": True},
        "relevant_file_recall": {"baseline": 1.0, "candidate": 1.0},
        "relevant_region_recall": {"baseline": 1.0, "candidate": 1.0},
        "search_output_bytes": {"reduction": 0.8},
        "search_read_call_ratio": 1.0,
        "total_tool_call_ratio": 1.0,
        "wall_time_ratio": 1.0,
    }
    control_pair = {
        "pair_id": "narrow-control",
        "candidate_eligible": False,
        "candidate_invoked": False,
        "state": "fail",
        "task_success": {"baseline": True, "candidate": False},
        "relevant_file_recall": {"baseline": 1.0, "candidate": 0.0},
        "relevant_region_recall": {"baseline": 1.0, "candidate": 0.0},
        "search_output_bytes": {"reduction": -1.0},
        "search_read_call_ratio": 9.0,
        "total_tool_call_ratio": 9.0,
        "wall_time_ratio": 9.0,
    }
    rows = [
        {
            "candidate_eligible": False,
            "candidate_invoked": False,
            "candidate_adopted": False,
            "fallback_count": 0,
            "pagination_calls": 0,
            "manager_preflight": manager_preflight,
            "manager": manager,
            "guard_marker": False,
            "validation_duration_ms": "not_applicable",
            "telemetry": {},
            "token_usage": "not_observed",
        },
        {
            "candidate_eligible": True,
            "candidate_invoked": True,
            "candidate_adopted": True,
            "fallback_count": 0,
            "pagination_calls": 0,
            "manager_preflight": manager_preflight,
            "manager": manager,
            "guard_marker": False,
            "validation_duration_ms": "not_applicable",
            "telemetry": {},
            "token_usage": "not_observed",
        },
    ]
    performance = lab._live_performance_summary(rows, rows, [eligible_pair, control_pair])
    gate = lab._live_gate_decision(
        baselines=rows,
        candidates=rows,
        pairs=[eligible_pair, control_pair],
        freshness={"status": "pass"},
        privacy={"status": "pass"},
        raw_artifacts_valid=True,
        performance=performance,
    )

    assert performance["search_output_reduction"]["median"] == 0.8
    assert performance["search_read_call_ratio"]["median"] == 1.0
    assert performance["candidate_adoption"] == {
        "eligible_tasks": 1,
        "instruction_delivered_tasks": 1,
        "adopted_tasks": 1,
        "rate": 1.0,
    }
    assert gate["hard_gates"]["live_task_and_validation_noninferior"] == "pass"
    assert gate["hard_gates"]["eligible_v2_instruction_delivery"] == "pass"
    assert gate["control_pair_discordance_count"] == 1


def test_live_discordant_adjudication_requires_reproduced_candidate_failure() -> None:
    lab = load_module("qwendex_optimization_lab")
    initial = {
        "pair_id": "broad",
        "candidate_eligible": True,
        "state": "fail",
        "task_success": {"baseline": True, "candidate": False},
        "relevant_file_recall": {"baseline": 1.0, "candidate": 0.0},
        "relevant_region_recall": {"baseline": 1.0, "candidate": 0.0},
    }
    resolved_rerun = {
        **initial,
        "state": "pass",
        "task_success": {"baseline": True, "candidate": True},
        "relevant_file_recall": {"baseline": 1.0, "candidate": 1.0},
        "relevant_region_recall": {"baseline": 1.0, "candidate": 1.0},
    }
    resolved = {**initial, "adjudication": lab._live_pair_adjudication(initial, resolved_rerun)}
    reproduced = {**initial, "adjudication": lab._live_pair_adjudication(initial, initial)}

    assert lab._live_pair_is_discordant(initial)
    assert resolved["adjudication"]["classification"] == "model_stochastic_behavior"
    assert not resolved["adjudication"]["candidate_failure_reproducible"]
    assert all(
        lab._live_pair_metric_noninferior(resolved, metric)
        for metric in ("relevant_file_recall", "relevant_region_recall", "task_success")
    )
    assert not lab._live_pair_has_reproducible_v2_regression(resolved)
    assert reproduced["adjudication"]["candidate_failure_reproducible"]
    assert lab._live_pair_has_reproducible_v2_regression(reproduced)


def test_live_invalid_sample_precedes_unobservable_hard_gate_decision() -> None:
    lab = load_module("qwendex_optimization_lab")
    blocked_pair = {
        "pair_id": "blocked",
        "candidate_eligible": True,
        "candidate_invoked": True,
        "state": "invalid_pair",
        "task_success": {"baseline": False, "candidate": False},
        "relevant_file_recall": {"baseline": 1.0, "candidate": 1.0},
        "relevant_region_recall": {"baseline": 1.0, "candidate": 1.0},
    }
    gate = lab._live_gate_decision(
        baselines=[{"status": "blocked"}],
        candidates=[{"status": "blocked", "candidate_eligible": True, "candidate_invoked": True}],
        pairs=[blocked_pair],
        freshness={"status": "pass"},
        privacy={"status": "pass"},
        raw_artifacts_valid=True,
        performance={"candidate_adoption": {"rate": "not_observed"}},
    )

    assert gate["candidate_decision"] == "invalid_evaluation"
    assert gate["status"] == "fail"


def test_privacy_scan_does_not_mistake_allowed_telemetry_values_for_raw_queries(tmp_path: Path) -> None:
    lab = load_module("qwendex_optimization_lab")
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    query = str(payload["tasks"][0]["execution"]["search"]["pattern"])
    database = tmp_path / "run" / "isolation" / "task" / "state" / "qwendex-performance.sqlite"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE telemetry (tool_family TEXT, allowed_value TEXT)")
        connection.execute("INSERT INTO telemetry VALUES (?, ?)", ("search", query))

    scan = lab._privacy_scan(tmp_path / "run", manifest, payload)

    assert scan["status"] == "pass"
    assert scan["leak_match_count"] == 0
    assert scan["performance_db_checked"] is True


def test_cli_validates_the_connected_optimization_lab_surface(tmp_path: Path) -> None:
    repository, commit, tree = make_repository(tmp_path)
    manifest = write_full_manifest(tmp_path, repository, commit, tree)
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith("QWENDEX_AGENT_") or key.startswith("QWENDEX_MANAGER_"):
            environment.pop(key)
    result = subprocess.run(
        [str(QWENDEX), "performance", "lab", "validate", "--manifest", str(manifest), "--json"],
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["lab"]["valid"] is True


def test_cli_exposes_only_explicit_default_off_compact_search(tmp_path: Path) -> None:
    repository, _, _ = make_repository(tmp_path)
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("QWENDEX_AGENT_", "QWENDEX_MANAGER_")):
            environment.pop(key)
    content = subprocess.run(
        [str(QWENDEX), "search", "content", "needle", "--root", str(repository), "--literal", "--json"],
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    paths = subprocess.run(
        [str(QWENDEX), "search", "paths", "tracked\\.txt", "--root", str(repository), "--json"],
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    content_payload = json.loads(content.stdout)
    paths_payload = json.loads(paths.stdout)

    assert content.returncode == 0
    assert content_payload["data"]["search"]["activation"] == {
        "active": True,
        "default_state": "off",
        "source": "explicit_direct_command",
    }
    assert content_payload["data"]["search"]["result"]["model_evidence"]
    assert paths.returncode == 0
    assert paths_payload["data"]["search"]["paths"] == ["tracked.txt"]


def test_scoped_candidate_environment_injects_only_the_bounded_instruction() -> None:
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("QWENDEX_AGENT_", "QWENDEX_MANAGER_")):
            environment.pop(key)
    disabled = subprocess.run(
        [str(QWENDEX), "agent", "hook", "SessionStart", "--event-json", "{}", "--json"],
        cwd=ROOT,
        env=environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    enabled_environment = {**environment, "QWENDEX_SEARCH_EVIDENCE_COMPACTION": "1"}
    enabled = subprocess.run(
        [str(QWENDEX), "agent", "hook", "SessionStart", "--event-json", "{}", "--json"],
        cwd=ROOT,
        env=enabled_environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    disabled_context = json.loads(disabled.stdout)["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    enabled_context = json.loads(enabled.stdout)["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]

    assert disabled.returncode == 0
    assert enabled.returncode == 0
    assert "Experimental search compaction is enabled" not in disabled_context
    assert "Experimental search compaction is enabled" in enabled_context
    assert len(enabled_context.encode("utf-8")) - len(disabled_context.encode("utf-8")) < 400

    v2_environment = {
        **environment,
        "QWENDEX_SEARCH_EVIDENCE_COMPACTION": "v2",
        "QWENDEX_SEARCH_COMMAND": "/isolated/live/qwendex",
    }
    v2 = subprocess.run(
        [str(QWENDEX), "agent", "hook", "SessionStart", "--event-json", "{}", "--json"],
        cwd=ROOT,
        env=v2_environment,
        check=False,
        text=True,
        capture_output=True,
        timeout=60,
    )
    v2_context = json.loads(v2.stdout)["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert v2.returncode == 0
    assert "recall-preserving search compaction v2" in v2_context
    assert "/isolated/live/qwendex search content" in v2_context
