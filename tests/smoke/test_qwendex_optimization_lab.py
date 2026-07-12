from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


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
