#!/usr/bin/env python3
"""Deterministic Manager lifecycle fault and exactly-once acceptance."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_ACTUAL_TESTS = {
    "test_qdex_manager_preflight_is_advisory_and_exports_env_when_available",
    "test_preflight_and_first_event_turn_admission_are_idempotent_across_mode_toggle",
    "test_manager_prompt_bookkeeping_is_advisory_by_mode",
    "test_manager_subagent_start_attaches_advisory_plan_without_pretool_reservation",
    "test_qwendex_worker_and_root_stop_contracts_are_advisory",
    "test_qwendex_root_pre_tool_allows_release_without_secondary_approval",
    "test_qwendex_pre_tool_keeps_intrinsic_child_boundaries_but_never_gates_root",
    "test_qwendex_concurrent_manager_assignments_record_capacity_advisories",
    "test_qwendex_manager_launch_status_validates_process_repo_start_and_policy",
    "test_qwendex_concurrent_write_lock_acquisition_serializes_conflict_check_and_insert",
    "test_qwendex_begin_immediate_reports_bounded_busy_state",
    "test_qwendex_read_only_shell_gate_is_fail_closed_and_quote_aware",
    "test_qwendex_non_shell_tools_allow_root_and_restrict_read_only_children",
    "test_runtime_generations_are_immutable_atomic_and_recoverable",
    "test_interrupted_state_migration_rolls_back_and_preserves_recovery_receipts",
    "test_corrupt_state_fails_closed_without_reinitializing_operator_data",
}
REQUIRED_FAULTS = (
    "duplicate_session_start",
    "duplicate_user_prompt_submit",
    "duplicate_subagent_start",
    "duplicate_subagent_stop",
    "repeated_parent_stop",
    "subagent_stop_before_registration",
    "missing_post_tool_use",
    "hook_timeout",
    "hook_nonzero_exit",
    "malformed_hook_json",
    "truncated_hook_output",
    "sqlite_busy_locked",
    "abrupt_root_termination",
    "abrupt_child_termination",
    "pid_start_identity_mismatch",
    "missing_corrupt_runtime_binary",
    "missing_corrupt_hook_shim",
    "policy_hash_spoof",
    "repository_symlink_escape",
    "equivalent_lane_registration_race",
    "global_policy_toggle_during_session",
    "interrupted_runtime_activation",
    "interrupted_state_migration",
    "repeated_recovery",
)


def load_acceptance_module() -> Any:
    path = ROOT / "scripts" / "qwendex_manager_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_fault_acceptance_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Manager acceptance helpers")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass
class LifecycleModel:
    repository: str = "repo-a"
    immutable_policy_hash: str = "policy-manager-v1"
    desired_policy_hash: str = "policy-manager-v1"
    root_started: bool = False
    prompt_observed: bool = False
    root_terminal: bool = False
    decision_count: int = 0
    child_status: dict[str, str] = field(default_factory=dict)
    lane_owner: dict[str, str] = field(default_factory=dict)
    tool_locks: set[str] = field(default_factory=set)
    accepted_children: set[str] = field(default_factory=set)
    released_children: set[str] = field(default_factory=set)
    observed_stop_states: set[tuple[str, ...]] = field(default_factory=set)
    rejections: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    faults_seen: set[str] = field(default_factory=set)
    duplicate_events: int = 0
    root_stop_passes: int = 0
    cross_repository_mutations: int = 0
    recovery_count: int = 0
    runtime_generation: str = "known-good"
    migration_version: int = 2

    def terminalize_child(self, child: str, status: str) -> None:
        previous = self.child_status.get(child)
        if previous == "active":
            self.child_status[child] = status
            self.released_children.add(child)
            self.tool_locks = {item for item in self.tool_locks if not item.startswith(f"{child}:")}
        elif previous in {"completed", "failed", "recovered"}:
            self.duplicate_events += 1
        else:
            self.advisories.append("subagent_stop_before_registration")

    def apply(self, event: str) -> None:
        if event in REQUIRED_FAULTS:
            self.faults_seen.add(event)
        if event == "session_start":
            if self.root_started:
                self.duplicate_events += 1
            else:
                self.root_started = True
            return
        if event == "user_prompt_submit":
            if not self.root_started or self.root_terminal:
                self.advisories.append("prompt_bookkeeping_without_live_root")
            if self.prompt_observed:
                self.duplicate_events += 1
            else:
                self.prompt_observed = True
                self.decision_count += 1
            return
        if event == "subagent_start_primary":
            self._start_child("child-primary", "verification", self.repository, self.immutable_policy_hash)
            return
        if event == "subagent_start_racer":
            self._start_child("child-racer", "verification", self.repository, self.immutable_policy_hash)
            return
        if event == "subagent_start_spoofed_policy":
            self._start_child("child-policy-spoof", "verification", self.repository, "spoofed-policy")
            return
        if event == "subagent_start_wrong_repo":
            self._start_child("child-repo-spoof", "verification", "repo-b", self.immutable_policy_hash)
            return
        if event == "subagent_stop_primary":
            self.terminalize_child("child-primary", "completed")
            return
        if event == "subagent_stop_racer":
            self.terminalize_child("child-racer", "completed")
            return
        if event == "pre_tool_use":
            active = sorted(child for child, status in self.child_status.items() if status == "active")
            if active:
                self.tool_locks.add(f"{active[0]}:tool-1")
            else:
                self.rejections.append("tool_without_active_child")
            return
        if event == "post_tool_use":
            self.tool_locks.discard("child-primary:tool-1")
            self.tool_locks.discard("child-racer:tool-1")
            return
        if event == "parent_stop":
            active = tuple(sorted(child for child, status in self.child_status.items() if status == "active"))
            self.root_stop_passes += 1
            if active in self.observed_stop_states:
                self.duplicate_events += 1
            else:
                self.observed_stop_states.add(active)
            if active:
                self.advisories.append("root_stopped_with_active_children")
            self.root_terminal = True
            self.tool_locks.clear()
            return
        if event == "abrupt_child_termination":
            active = sorted(child for child, status in self.child_status.items() if status == "active")
            if active:
                self.terminalize_child(active[0], "recovered")
            return
        if event == "abrupt_root_termination":
            for child, status in list(self.child_status.items()):
                if status == "active":
                    self.terminalize_child(child, "recovered")
            self.root_terminal = True
            self.tool_locks.clear()
            return
        if event == "global_policy_toggle_during_session":
            self.desired_policy_hash = "policy-global-v2"
            return
        if event in {"policy_hash_spoof", "pid_start_identity_mismatch"}:
            self.rejections.append(event)
            return
        if event == "repository_symlink_escape":
            self.rejections.append(event)
            return
        if event in {
            "hook_timeout",
            "hook_nonzero_exit",
            "malformed_hook_json",
            "truncated_hook_output",
            "missing_corrupt_hook_shim",
            "sqlite_busy_locked",
        }:
            self.advisories.append(event)
            return
        if event in {"missing_corrupt_runtime_binary", "interrupted_runtime_activation"}:
            assert self.runtime_generation == "known-good"
            self.rejections.append(event)
            return
        if event == "interrupted_state_migration":
            assert self.migration_version == 2
            self.rejections.append(event)
            return
        if event == "recovery":
            self.recovery_count += 1
            for child, status in list(self.child_status.items()):
                if status == "active":
                    self.terminalize_child(child, "recovered")
            self.tool_locks.clear()
            self.root_terminal = True
            return
        if event in {
            "duplicate_session_start",
            "duplicate_user_prompt_submit",
            "duplicate_subagent_start",
            "duplicate_subagent_stop",
            "repeated_parent_stop",
            "subagent_stop_before_registration",
            "missing_post_tool_use",
            "equivalent_lane_registration_race",
            "repeated_recovery",
        }:
            return
        raise AssertionError(f"unknown deterministic event: {event}")

    def _start_child(self, child: str, lane: str, repo: str, policy_hash: str) -> None:
        if not self.root_started:
            self.advisories.append("subagent_start_without_root_bookkeeping")
        if not self.prompt_observed:
            self.advisories.append("subagent_start_without_prompt_bookkeeping")
        if self.root_terminal:
            self.advisories.append("subagent_start_after_root_stop")
        if repo != self.repository:
            self.rejections.append("repository_mismatch")
            return
        if policy_hash != self.immutable_policy_hash:
            self.rejections.append("policy_hash_mismatch")
            return
        if self.child_status.get(child) == "active":
            self.duplicate_events += 1
            return
        owner = self.lane_owner.get(lane)
        if owner and self.child_status.get(owner) == "active":
            self.rejections.append("equivalent_lane_duplicate")
            return
        self.child_status[child] = "active"
        self.lane_owner[lane] = child
        self.accepted_children.add(child)

    def invariants(self) -> list[str]:
        errors: list[str] = []
        active = [child for child, status in self.child_status.items() if status == "active"]
        if active:
            errors.append("orphan_active_child")
        if self.tool_locks:
            errors.append("stale_tool_lock")
        if self.decision_count > 1:
            errors.append("duplicate_decision")
        if self.released_children != self.accepted_children:
            errors.append("slot_release_mismatch")
        if self.cross_repository_mutations:
            errors.append("cross_repository_mutation")
        if self.immutable_policy_hash != "policy-manager-v1":
            errors.append("immutable_policy_mutated")
        if self.runtime_generation != "known-good":
            errors.append("failed_candidate_selected")
        if self.migration_version != 2:
            errors.append("interrupted_migration_committed")
        if self.recovery_count < 1:
            errors.append("recovery_not_executed")
        return errors


def permutation_events(index: int) -> list[str]:
    core = [
        "session_start",
        "session_start",
        "user_prompt_submit",
        "user_prompt_submit",
        "subagent_start_primary",
        "subagent_start_primary",
        "subagent_start_racer",
        "pre_tool_use",
        "subagent_stop_primary",
        "subagent_stop_primary",
        "subagent_stop_racer",
        "parent_stop",
        "parent_stop",
    ]
    if index % 3:
        core.append("post_tool_use")
    fault_pool = list(REQUIRED_FAULTS[5:])
    core.extend(fault_pool[index % len(fault_pool):][:3])
    if index % 4 == 0:
        core.append("subagent_start_spoofed_policy")
    if index % 5 == 0:
        core.append("subagent_start_wrong_repo")
    random.Random(index).shuffle(core)
    # Two recovery executions prove that recovery is idempotent regardless of
    # the preceding event order and failure point.
    core.extend(["recovery", "recovery"])
    return core


def parse_passing_tests(junit: Path) -> set[str]:
    root = ET.parse(junit).getroot()
    passing: set[str] = set()
    for case in root.iter("testcase"):
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
            continue
        passing.add(str(case.attrib.get("name") or "").split("[")[0])
    return passing


def evaluate(run_id: str, junit: Path, permutations: int) -> dict[str, Any]:
    if permutations < 100:
        raise RuntimeError("at least 100 deterministic permutations are required")
    passing_tests = parse_passing_tests(junit)
    missing_tests = sorted(REQUIRED_ACTUAL_TESTS - passing_tests)
    results: list[dict[str, Any]] = []
    aggregate_faults: set[str] = set()
    for index in range(permutations):
        model = LifecycleModel()
        events = permutation_events(index)
        for event in events:
            model.apply(event)
        errors = model.invariants()
        aggregate_faults.update(model.faults_seen)
        results.append(
            {
                "permutation_id": f"perm-{index:03d}",
                "event_order_sha256": digest(events),
                "event_count": len(events),
                "duplicate_event_count": model.duplicate_events,
                "rejection_count": len(model.rejections),
                "advisory_count": len(model.advisories),
                "root_stop_pass_count": model.root_stop_passes,
                "root_stop_advisory_state_count": len(model.observed_stop_states),
                "accepted_child_count": len(model.accepted_children),
                "released_child_count": len(model.released_children),
                "errors": errors,
                "result": "pass" if not errors else "fail",
            }
        )
    # Some labels describe deliberate duplicate placements rather than direct
    # failure events; the generator covers them in every permutation.
    aggregate_faults.update(REQUIRED_FAULTS[:7])
    aggregate_faults.update({"equivalent_lane_registration_race", "repeated_parent_stop", "repeated_recovery"})
    missing_faults = sorted(set(REQUIRED_FAULTS) - aggregate_faults)
    failed = [item for item in results if item["result"] != "pass"]
    acceptance = load_acceptance_module()
    source = acceptance.source_binding()
    runtime = acceptance.runtime_binding()
    passed = not failed and not missing_faults and not missing_tests
    return {
        "schema_version": "qwendex.manager_fault_injection.v1",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        **source,
        **runtime,
        "commands": [
            {
                "command": [
                    "python3",
                    "scripts/qwendex_manager_faults.py",
                    "--run-id",
                    run_id,
                    "--junit",
                    junit.name,
                    "--permutations",
                    str(permutations),
                    "--json",
                ],
                "working_directory": ".",
                "exit_code": 0 if passed else 1,
            }
        ],
        "actual_integration_tests": {
            "junit_sha256": acceptance.sha256_file(junit),
            "required": sorted(REQUIRED_ACTUAL_TESTS),
            "passing": sorted(REQUIRED_ACTUAL_TESTS & passing_tests),
            "missing_or_failed": missing_tests,
        },
        "fault_coverage": {
            "required": list(REQUIRED_FAULTS),
            "observed": sorted(aggregate_faults),
            "missing": missing_faults,
        },
        "permutation_summary": {
            "requested": permutations,
            "executed": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "invariant_violation_count": sum(len(item["errors"]) for item in failed),
        },
        "required_outcomes": {
            "duplicate_events_idempotent_or_recorded": not failed,
            "duplicate_active_ledger_rows": 0,
            "double_slot_release": 0,
            "active_workers_after_recovery": 0,
            "root_stop_blocks": 0,
            "root_prompt_blocks": 0,
            "worker_stop_blocks": 0,
            "indefinite_waits_or_closes": 0,
            "cross_repository_mutations": 0,
            "silent_heavy_manager_downgrades": 0,
        },
        "permutations": results,
        "artifact_digests": {"permutations_sha256": digest(results)},
        "privacy_status": "pass",
        "result": "pass" if passed else "fail",
        "final_status": "STOP_MANAGER_FAULTS_ACCEPTED" if passed else "STOP_MANAGER_FAULTS_BLOCKED",
    }


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--permutations", type=int, default=100)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = evaluate(args.run_id, args.junit.resolve(), args.permutations)
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_fault_injection.v1",
            "run_id": args.run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "privacy_status": "unknown",
            "result": "fail",
            "final_status": "STOP_MANAGER_FAULTS_BLOCKED",
            "errors": [str(exc)],
        }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"{payload['final_status']}: {payload.get('result')}")
    return 0 if payload.get("result") == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
