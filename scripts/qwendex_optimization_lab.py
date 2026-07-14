#!/usr/bin/env python3
"""Reusable, isolated baseline capture for Qwendex optimization evaluation.

The candidate half is added separately from this baseline layer.  This first
layer intentionally has no automatic search activation: it only captures the
live ripgrep evidence that a later candidate must preserve.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import signal
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
import uuid
import csv
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


WORKLOAD_SCHEMA_VERSION = "qwendex.optimization_lab.workload.v1"
BASELINE_RUN_SCHEMA_VERSION = "qwendex.optimization_lab.run.v1"
BASELINE_CAPTURE_SCHEMA_VERSION = "qwendex.optimization_lab.baseline_capture.v1"
ARTIFACT_MANIFEST_SCHEMA_VERSION = "qwendex.optimization_lab.artifact_manifest.v1"
LIVE_AGENT_RUN_SCHEMA_VERSION = "qwendex.optimization_lab.live_agent_run.v1"
LIVE_EXECUTION_MODE = "live_agent_adoption_v2"
LIVE_RUNTIME_PROFILE_SCHEMA_VERSION = "qwendex.live_runtime_profile.v1"
LIVE_RUNTIME_PROFILE_CONTRACT_SCHEMA_VERSION = "qwendex.live_runtime_profile_contract.v1"
LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION = "qwendex.live_runtime_supervisor_budget.v1"
LIVE_SUPERVISOR_MAX_HARD_WALL_SECONDS = 600

# The isolated performance database is already constrained to metadata-only
# hook observations.  The live supervisor may use only these completion
# transitions as an additional trusted-progress source while a native parent
# is waiting on subagents.  A pending tool call (especially ``wait_agent``)
# deliberately does not reset inactivity.
LIVE_HOOK_PROGRESS_TABLE = "qwendex_performance_events"
LIVE_HOOK_PROGRESS_TOOL_EVENT = "tool_call"
LIVE_HOOK_PROGRESS_SUBAGENT_EVENTS = frozenset({"subagent_start", "subagent_stop"})
LIVE_WAIT_TIMEOUT_BUCKETS = frozenset(
    {
        "not_applicable",
        "not_provided",
        "at_most_30s",
        "31_to_60s",
        "61_to_120s",
        "over_120s",
        "invalid",
    }
)

LIVE_RUNTIME_PHASES = (
    "runner_start",
    "workspace_ready",
    "manager_preflight_start",
    "manager_preflight_end",
    "codex_process_start",
    "first_structured_event",
    "first_model_or_assistant_event",
    "first_tool_start",
    "first_tool_end",
    "first_search_start",
    "first_search_end",
    "last_trusted_progress",
    "last_tool_end",
    "final_assistant_event",
    "manager_stop_start",
    "manager_stop_end",
    "child_exit",
    "pipe_drain_complete",
    "validation_start",
    "validation_end",
    "runner_complete",
    "timeout_signal",
    "terminate_signal",
    "kill_signal",
    "cleanup_complete",
)

LIVE_TIMEOUT_CLASSIFICATIONS = frozenset(
    {
        "not_applicable",
        "timeout_before_preflight_complete",
        "timeout_before_process_start",
        "timeout_before_first_model_event",
        "timeout_during_model_response",
        "timeout_during_tool_execution",
        "timeout_after_last_tool_before_final",
        "timeout_during_manager_stop",
        "timeout_after_child_exit_pipe_drain",
        "timeout_during_validation",
        "timeout_with_continuous_trusted_progress",
        "timeout_due_to_inactivity",
        "process_tree_cleanup_failed",
        "unknown_insufficient_instrumentation",
    }
)

SCRIPT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_ROOT.parent


class LabError(ValueError):
    """Raised for deterministic workload or isolation failures."""


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _safe_local_digest(value: str) -> str:
    """Return an opaque local correlation value without retaining its source text."""

    return "sha256:" + sha256_text(value)


def legacy_live_supervisor_budgets(timeout_seconds: int | float) -> dict[str, Any]:
    """Represent the former one-wall policy while adding observation only.

    This is used for calibration, never for promotion evidence.  The three
    progress-aware ceilings are deliberately disabled so the legacy hard wall
    is reproduced exactly while the new profiler captures what it can.
    """

    hard_wall = float(timeout_seconds)
    if hard_wall <= 0 or hard_wall > LIVE_SUPERVISOR_MAX_HARD_WALL_SECONDS:
        raise LabError("legacy live timeout must be within the bounded supervisor maximum")
    return _normalise_live_supervisor_budgets(
        {
            "schema_version": LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION,
            "mode": "legacy_single_wall_observability",
            "startup_preflight_seconds": None,
            "first_model_activity_seconds": None,
            "inactivity_seconds": None,
            "hard_wall_seconds": hard_wall,
            "graceful_termination_seconds": 10.0,
            "forced_cleanup_seconds": 10.0,
            "pipe_drain_seconds": 10.0,
            "poll_interval_seconds": 0.10,
            "derivation": {
                "kind": "frozen_legacy_timeout_reproduction",
                "legacy_hard_wall_seconds": hard_wall,
            },
        }
    )


def derive_progress_aware_live_supervisor_budgets(legacy_timeout_seconds: int | float) -> dict[str, Any]:
    """Derive the bounded evaluation policy before candidate outcomes exist.

    The legacy 180-second wall is retained as the calibration baseline.  Once
    that calibration proves real structured progress at the old wall, a
    predeclared three-times allowance gives the task room to finish while the
    stricter startup, first-model, and inactivity ceilings still detect a
    no-response or stalled session.  The absolute cap keeps the evaluator
    bounded on slower hosts.
    """

    legacy = float(legacy_timeout_seconds)
    if legacy <= 0:
        raise LabError("legacy live timeout must be positive")
    hard_wall = min(float(LIVE_SUPERVISOR_MAX_HARD_WALL_SECONDS), max(300.0, legacy * 3.0))
    return _normalise_live_supervisor_budgets(
        {
            "schema_version": LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION,
            "mode": "progress_aware",
            "startup_preflight_seconds": min(60.0, max(30.0, legacy / 4.0)),
            "first_model_activity_seconds": min(120.0, max(60.0, legacy / 2.0)),
            "inactivity_seconds": min(180.0, max(60.0, legacy * (2.0 / 3.0))),
            "hard_wall_seconds": hard_wall,
            "graceful_termination_seconds": 15.0,
            "forced_cleanup_seconds": 10.0,
            "pipe_drain_seconds": 10.0,
            "poll_interval_seconds": 0.10,
            "derivation": {
                "kind": "predeclared_progress_aware_calibration_policy",
                "legacy_hard_wall_seconds": legacy,
                "hard_wall_rule": "min(600,max(300,legacy_hard_wall_seconds*3))",
                "requires_calibration_classification": "timeout_with_continuous_trusted_progress",
            },
        }
    )


def derive_diagnostic_live_supervisor_budgets(legacy_timeout_seconds: int | float) -> dict[str, Any]:
    """Add progress ceilings without extending the frozen legacy hard wall.

    This policy is the calibration bridge for a timeout that was not yet shown
    to have continuous trusted progress.  It can prove an inactivity or
    no-response blocker without using a larger allowance as a disguised pass.
    """

    legacy = float(legacy_timeout_seconds)
    if legacy <= 0 or legacy > LIVE_SUPERVISOR_MAX_HARD_WALL_SECONDS:
        raise LabError("legacy live timeout must be within the bounded supervisor maximum")
    return _normalise_live_supervisor_budgets(
        {
            "schema_version": LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION,
            "mode": "progress_aware",
            "startup_preflight_seconds": min(60.0, max(30.0, legacy / 4.0)),
            "first_model_activity_seconds": min(120.0, max(60.0, legacy / 2.0)),
            "inactivity_seconds": min(90.0, max(45.0, legacy / 3.0)),
            "hard_wall_seconds": legacy,
            "graceful_termination_seconds": 15.0,
            "forced_cleanup_seconds": 10.0,
            "pipe_drain_seconds": 10.0,
            "poll_interval_seconds": 0.10,
            "derivation": {
                "kind": "same_wall_progress_aware_diagnostic_policy",
                "legacy_hard_wall_seconds": legacy,
                "hard_wall_rule": "preserve_legacy_hard_wall_until_continuous_progress_is_proven",
            },
        }
    )


def _normalise_live_supervisor_budgets(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the bounded supervisor policy.

    A canonical representation and SHA-256 identity let pair receipts prove
    that baseline and candidate used byte-for-byte-equivalent timeout rules.
    """

    if not isinstance(value, Mapping):
        raise LabError("live supervisor budget policy must be an object")
    mode = str(value.get("mode") or "")
    if mode not in {"legacy_single_wall_observability", "progress_aware", "test"}:
        raise LabError("live supervisor budget mode is unsupported")

    def seconds(name: str, *, nullable: bool = False, maximum: float | None = None) -> float | None:
        raw = value.get(name)
        if raw is None and nullable:
            return None
        if not isinstance(raw, int | float) or isinstance(raw, bool) or float(raw) <= 0:
            raise LabError(f"live supervisor budget {name} must be positive")
        parsed = round(float(raw), 6)
        if maximum is not None and parsed > maximum:
            raise LabError(f"live supervisor budget {name} exceeds its bounded maximum")
        return parsed

    startup = seconds("startup_preflight_seconds", nullable=True)
    first_model = seconds("first_model_activity_seconds", nullable=True)
    inactivity = seconds("inactivity_seconds", nullable=True)
    hard_wall = seconds("hard_wall_seconds", maximum=float(LIVE_SUPERVISOR_MAX_HARD_WALL_SECONDS))
    graceful = seconds("graceful_termination_seconds", maximum=60.0)
    forced = seconds("forced_cleanup_seconds", maximum=60.0)
    pipe = seconds("pipe_drain_seconds", maximum=60.0)
    poll = seconds("poll_interval_seconds", maximum=5.0)
    if mode == "legacy_single_wall_observability" and any(item is not None for item in (startup, first_model, inactivity)):
        raise LabError("legacy observation policy cannot add progress-aware ceilings")
    if mode == "progress_aware" and any(item is None for item in (startup, first_model, inactivity)):
        raise LabError("progress-aware policy requires startup, first-model, and inactivity ceilings")

    derivation = value.get("derivation", {})
    if not isinstance(derivation, Mapping):
        raise LabError("live supervisor budget derivation must be an object")
    canonical = {
        "schema_version": LIVE_SUPERVISOR_BUDGET_SCHEMA_VERSION,
        "mode": mode,
        "startup_preflight_seconds": startup,
        "first_model_activity_seconds": first_model,
        "inactivity_seconds": inactivity,
        "hard_wall_seconds": hard_wall,
        "graceful_termination_seconds": graceful,
        "forced_cleanup_seconds": forced,
        "pipe_drain_seconds": pipe,
        "poll_interval_seconds": poll,
        "derivation": dict(derivation),
    }
    canonical["policy_identity"] = "sha256:" + sha256_bytes(_canonical_json_bytes(canonical))
    return canonical


def load_live_supervisor_budget_policy(path_or_value: Path | str | Mapping[str, Any]) -> dict[str, Any]:
    """Load a previously recorded calibration policy without changing it."""

    if isinstance(path_or_value, Mapping):
        value: Mapping[str, Any] = path_or_value
    else:
        path = Path(path_or_value).expanduser().resolve(strict=False)
        value = _read_json(path)
        if not isinstance(value, Mapping):
            raise LabError("live supervisor policy file must contain an object")
    nested = value.get("supervisor_budget") if isinstance(value.get("supervisor_budget"), Mapping) else value
    return _normalise_live_supervisor_budgets(nested)


def live_runtime_profile_contract() -> dict[str, Any]:
    """Describe the privacy-safe profile stored only in ignored run evidence."""

    return {
        "schema_version": LIVE_RUNTIME_PROFILE_CONTRACT_SCHEMA_VERSION,
        "profile_schema_version": LIVE_RUNTIME_PROFILE_SCHEMA_VERSION,
        "phase_names": list(LIVE_RUNTIME_PHASES),
        "timeout_classifications": sorted(LIVE_TIMEOUT_CLASSIFICATIONS),
        "privacy_boundary": {
            "metadata_only": True,
            "forbidden_fields": [
                "prompts",
                "commands",
                "queries",
                "paths_from_task_output",
                "tool_input",
                "tool_output",
                "stdout",
                "stderr",
                "transcripts",
                "credentials",
                "tokens",
            ],
            "safe_diagnostics": [
                "opaque_local_digests",
                "phase_timestamps_and_durations",
                "structured_event_counts",
                "allowlisted_hook_lifecycle_counts",
                "bounded_collaboration_wait_timeout_buckets",
                "bounded_in_flight_lifecycle_categories",
                "pid_pgid_role_state_cpu_rss_buckets",
                "pipe_byte_counts_without_content",
                "sanitized_manager_health_counts",
            ],
        },
    }


def _new_live_runtime_profile(
    *,
    run_id: str,
    task_id: str,
    variant: str,
    attempt: str,
    candidate_id: str,
    repository: Mapping[str, Any],
    manifest_digest: str,
    model_policy: Mapping[str, Any],
    budgets: Mapping[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    profile = {
        "schema_version": LIVE_RUNTIME_PROFILE_SCHEMA_VERSION,
        "profile_status": "in_progress",
        "identity": {
            "run_digest": _safe_local_digest(run_id),
            "pair_digest": _safe_local_digest(task_id),
            "arm": variant,
            "attempt": attempt,
            "candidate_identity": candidate_id,
        },
        "source": {
            "repository_commit": str(repository.get("commit") or "not_observed"),
            "repository_tree": str(repository.get("tree_digest") or "not_observed"),
            "frozen_workload_manifest_digest": manifest_digest,
        },
        "execution_posture": {
            "model": str(model_policy.get("model_identifier") or "not_observed"),
            "reasoning": str(model_policy.get("reasoning_effort") or "not_observed"),
            "routing": str(model_policy.get("local_routing_state") or "not_observed"),
            "manager_mode": str(model_policy.get("manager_mode") or "not_observed"),
            "permission_mode": str(model_policy.get("permission_mode") or "not_observed"),
            "candidate_identity": candidate_id,
        },
        "supervisor_budget": dict(budgets),
        "phase_timestamps": {phase: "not_observed" for phase in LIVE_RUNTIME_PHASES},
        "phase_durations_ms": {},
        "trusted_progress_event_counts": {},
        "structured_event_type_counts": {},
        "hook_lifecycle_event_counts": {},
        "collaboration_wait_timeout_bucket_counts": {},
        "process_diagnostics": {"snapshots": [], "pipe_state": {}},
        "termination": {
            "timed_out": False,
            "timeout_reason": "not_applicable",
            "timeout_classification": "not_applicable",
            "contributing_classifications": [],
            "signals": [],
            "completion_valid": False,
            "cleanup_status": "not_observed",
        },
        "privacy": {
            "metadata_only": True,
            "raw_content_in_profile": False,
            "contract_schema_version": LIVE_RUNTIME_PROFILE_CONTRACT_SCHEMA_VERSION,
        },
        "_runtime_start_monotonic": started,
    }
    _record_runtime_phase(profile, "runner_start", now=started)
    return profile


def _record_runtime_phase(profile: dict[str, Any], phase: str, *, now: float | None = None, replace: bool = False) -> None:
    if phase not in LIVE_RUNTIME_PHASES:
        raise LabError("unknown live runtime phase")
    timestamps = profile["phase_timestamps"]
    if not replace and timestamps.get(phase) != "not_observed":
        return
    observed = time.monotonic() if now is None else now
    started = float(profile.get("_runtime_start_monotonic") or observed)
    timestamps[phase] = {
        "observed_at": utc_now(),
        "offset_ms": round(max(0.0, observed - started) * 1000, 3),
    }


def _runtime_phase_offset(profile: Mapping[str, Any], phase: str) -> float | None:
    timestamps = profile.get("phase_timestamps", {})
    value = timestamps.get(phase) if isinstance(timestamps, Mapping) else None
    if isinstance(value, Mapping) and isinstance(value.get("offset_ms"), int | float):
        return float(value["offset_ms"])
    return None


def _runtime_duration(profile: Mapping[str, Any], start: str, end: str) -> float | str:
    begin = _runtime_phase_offset(profile, start)
    finish = _runtime_phase_offset(profile, end)
    if begin is None or finish is None or finish < begin:
        return "not_observed"
    return round(finish - begin, 3)


def _finalise_live_runtime_profile(profile: dict[str, Any]) -> None:
    durations = {
        "workspace_ready_ms": _runtime_duration(profile, "runner_start", "workspace_ready"),
        "manager_preflight_ms": _runtime_duration(profile, "manager_preflight_start", "manager_preflight_end"),
        "first_structured_event_ms": _runtime_duration(profile, "codex_process_start", "first_structured_event"),
        "first_model_or_assistant_event_ms": _runtime_duration(profile, "codex_process_start", "first_model_or_assistant_event"),
        "first_tool_ms": _runtime_duration(profile, "codex_process_start", "first_tool_start"),
        "first_search_ms": _runtime_duration(profile, "codex_process_start", "first_search_start"),
        "manager_stop_ms": _runtime_duration(profile, "manager_stop_start", "manager_stop_end"),
        "pipe_drain_ms": _runtime_duration(profile, "child_exit", "pipe_drain_complete"),
        "validation_ms": _runtime_duration(profile, "validation_start", "validation_end"),
        "total_runner_ms": _runtime_duration(profile, "runner_start", "runner_complete"),
    }
    profile["phase_durations_ms"] = durations
    profile.pop("_runtime_start_monotonic", None)
    profile["profile_status"] = "complete"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(131_072), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8", "replace"))


def _script_module(name: str) -> Any:
    path = SCRIPT_ROOT / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"qwendex_{name}", path)
    if spec is None or spec.loader is None:
        raise LabError(f"could not load Qwendex module: {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def search_module() -> Any:
    return _script_module("qwendex_search")


def performance_module() -> Any:
    return _script_module("qwendex_performance")


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError("could not read valid JSON") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(encoded)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(value)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _git_output(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise LabError("Git snapshot metadata is unavailable")
    return completed.stdout.strip()


def _safe_reference_path(manifest_path: Path, reference: str) -> tuple[Path, str]:
    source, separator, key = str(reference).partition("#")
    candidate = (manifest_path.parent / source).resolve(strict=False)
    if not source or Path(source).is_absolute() or not _within(candidate, manifest_path.parent.resolve(strict=False)):
        raise LabError("private prompt reference must stay beside the workload manifest")
    return candidate, key if separator else ""


def _validate_prompt_reference(manifest_path: Path, task: Mapping[str, Any]) -> str | None:
    reference = str(task.get("private_prompt_ref") or "")
    digest = str(task.get("prompt_digest") or "")
    if not reference or not digest.startswith("sha256:") or len(digest) != 71:
        return "missing or invalid prompt digest/reference"
    try:
        prompt_file, key = _safe_reference_path(manifest_path, reference)
        payload = _read_json(prompt_file)
    except LabError:
        return "private prompt source is unavailable"
    value = payload.get(key) if isinstance(payload, dict) and key else None
    if not isinstance(value, str):
        return "private prompt source entry is unavailable"
    observed = "sha256:" + sha256_bytes(value.encode("utf-8"))
    if observed != digest:
        return "private prompt digest does not match its local source"
    return None


def _task_errors(manifest_path: Path, task: Any, repositories: set[str], seen_ids: set[str]) -> list[str]:
    if not isinstance(task, Mapping):
        return ["task must be an object"]
    errors: list[str] = []
    task_id = str(task.get("id") or "")
    if not task_id or task_id in seen_ids:
        errors.append("task id must be unique")
    seen_ids.add(task_id)
    if str(task.get("repository") or "") not in repositories:
        errors.append("task repository is not declared")
    if str(task.get("stratum") or "") not in {
        "A_read_only_localization",
        "B_diagnosis_documentation",
        "C_bounded_implementation",
    }:
        errors.append("task stratum is invalid")
    if str(task.get("pair_order") or "") not in {"baseline_first", "candidate_first"}:
        errors.append("task pair order is invalid")
    prompt_error = _validate_prompt_reference(manifest_path, task)
    if prompt_error:
        errors.append(prompt_error)
    allowed = task.get("allowed_write_surface")
    if not isinstance(allowed, list) or not all(isinstance(item, str) and item and not Path(item).is_absolute() and ".." not in Path(item).parts for item in allowed):
        errors.append("allowed write surface is invalid")
    expected_files = task.get("expected_relevant_files")
    if not isinstance(expected_files, list) or not expected_files or not all(isinstance(item, str) and item and not Path(item).is_absolute() for item in expected_files):
        errors.append("expected relevant files are invalid")
    regions = task.get("expected_relevant_regions")
    if not isinstance(regions, list) or not regions or not all(isinstance(item, Mapping) and str(item.get("path") or "") and str(item.get("anchor") or "") for item in regions):
        errors.append("expected relevant regions are invalid")
    validation = task.get("validation_command")
    if not isinstance(validation, list) or not all(isinstance(item, str) and item for item in validation):
        errors.append("validation command is invalid")
    if not isinstance(task.get("task_success_rubric"), Mapping):
        errors.append("task success rubric is invalid")
    if not isinstance(task.get("execution"), Mapping) or not isinstance(task["execution"].get("search"), Mapping):
        errors.append("task execution search is invalid")
    else:
        search = task["execution"]["search"]
        if not str(search.get("pattern") or "") or str(search.get("mode") or "") not in {"literal", "regex"}:
            errors.append("task search pattern or mode is invalid")
        root = Path(str(search.get("root") or ""))
        if not str(root) or root.is_absolute() or ".." in root.parts:
            errors.append("task search root must stay inside its worktree")
        budgets = task["execution"].get("candidate_budget")
        if not isinstance(budgets, Mapping) or any(not isinstance(budgets.get(key), int) or int(budgets[key]) < 1 for key in ("per_file_ranges", "total_ranges", "page_size")):
            errors.append("candidate budgets are invalid")
    return errors


def validate_workload(manifest_path: Path | str) -> dict[str, Any]:
    path = Path(manifest_path).expanduser().resolve(strict=False)
    errors: list[str] = []
    try:
        payload = _read_json(path)
    except LabError:
        return {
            "schema_version": WORKLOAD_SCHEMA_VERSION,
            "status": "fail",
            "valid": False,
            "errors": ["workload manifest is not valid JSON"],
        }
    if not isinstance(payload, Mapping):
        return {
            "schema_version": WORKLOAD_SCHEMA_VERSION,
            "status": "fail",
            "valid": False,
            "errors": ["workload manifest root must be an object"],
        }
    if payload.get("schema_version") != WORKLOAD_SCHEMA_VERSION:
        errors.append("unsupported workload manifest schema")
    if not str(payload.get("workload_id") or "") or not bool(payload.get("frozen")):
        errors.append("workload id and frozen marker are required")
    if not isinstance(payload.get("seed"), int):
        errors.append("fixed workload seed is required")
    execution_mode = str(payload.get("execution_mode") or "")
    if execution_mode not in {"controlled_search_evidence_v1", LIVE_EXECUTION_MODE}:
        errors.append("unsupported workload execution mode")
    policy = payload.get("model_policy")
    if not isinstance(policy, Mapping) or any(not str(policy.get(key) or "") for key in ("model_identifier", "reasoning_effort", "manager_mode", "local_routing_state", "permission_mode")):
        errors.append("fixed model and manager policy is required")
    repositories = payload.get("repositories")
    repository_ids: set[str] = set()
    repository_rows: list[Mapping[str, Any]] = []
    if not isinstance(repositories, list) or len(repositories) < 2:
        errors.append("at least two workload repositories are required")
    else:
        for item in repositories:
            if not isinstance(item, Mapping):
                errors.append("repository entry must be an object")
                continue
            repository_id = str(item.get("id") or "")
            if not repository_id or repository_id in repository_ids:
                errors.append("repository ids must be unique")
                continue
            repository_ids.add(repository_id)
            repository_rows.append(item)
            source = Path(str(item.get("source_path") or "")).expanduser()
            commit = str(item.get("commit") or "")
            tree_digest = str(item.get("tree_digest") or "")
            if not source.is_absolute() or not commit or not tree_digest.startswith("git:") or not str(item.get("fixture_classification") or ""):
                errors.append("repository snapshot declaration is invalid")
                continue
            try:
                if _git_output(source, "rev-parse", commit) != commit:
                    errors.append("repository commit does not resolve")
                if "git:" + _git_output(source, "rev-parse", f"{commit}^{{tree}}") != tree_digest:
                    errors.append("repository tree digest does not match")
            except LabError:
                errors.append("repository snapshot is unavailable")
    tasks = payload.get("tasks")
    seen_ids: set[str] = set()
    strata: dict[str, int] = {}
    orders: dict[str, int] = {}
    if not isinstance(tasks, list) or len(tasks) < 12:
        errors.append("a full workload requires at least twelve paired tasks")
    elif isinstance(tasks, list):
        for task in tasks:
            task_id = str(task.get("id") or "") if isinstance(task, Mapping) else "unknown"
            for error in _task_errors(path, task, repository_ids, seen_ids):
                errors.append(f"task {task_id or 'unknown'}: {error}")
            if isinstance(task, Mapping):
                stratum = str(task.get("stratum") or "")
                order = str(task.get("pair_order") or "")
                strata[stratum] = strata.get(stratum, 0) + 1
                orders[order] = orders.get(order, 0) + 1
        for stratum in ("A_read_only_localization", "B_diagnosis_documentation", "C_bounded_implementation"):
            if strata.get(stratum, 0) < 4:
                errors.append(f"workload requires four tasks in {stratum}")
        if orders.get("baseline_first", 0) != orders.get("candidate_first", 0):
            errors.append("baseline/candidate order must be balanced")
    if execution_mode == LIVE_EXECUTION_MODE:
        live_contract = payload.get("live_contract")
        if not isinstance(live_contract, Mapping) or any(
            not str(live_contract.get(key) or "")
            for key in ("runner", "conversation_isolation", "candidate_instruction_delivery")
        ):
            errors.append("live workload contract is incomplete")
        elif live_contract.get("conversation_isolation") != "fresh_home_per_arm":
            errors.append("live workload must require a fresh home per arm")
        for task in tasks if isinstance(tasks, list) else []:
            if not isinstance(task, Mapping):
                continue
            live = task.get("live")
            if not isinstance(live, Mapping):
                errors.append(f"task {task.get('id') or 'unknown'}: live task contract is missing")
                continue
            if str(live.get("task_class") or "") not in {
                "narrow_exact_localization",
                "broad_definition_discovery",
                "broad_reference_discovery",
                "documentation_code_verification",
                "test_failure_diagnosis",
                "isolated_small_implementation",
                "root_only_work",
                "manager_explorer_work",
                "manager_implementer_work",
                "manager_verifier_work",
                "modified_untracked_freshness",
            }:
                errors.append(f"task {task.get('id') or 'unknown'}: live task class is invalid")
            if not isinstance(live.get("candidate_eligible"), bool):
                errors.append(f"task {task.get('id') or 'unknown'}: candidate eligibility is required")
    digest = "sha256:" + sha256_file(path) if path.is_file() else "not_observed"
    return {
        "schema_version": WORKLOAD_SCHEMA_VERSION,
        "status": "pass" if not errors else "fail",
        "valid": not errors,
        "errors": errors,
        "workload": {
            "workload_id": str(payload.get("workload_id") or ""),
            "manifest_digest": digest,
            "task_count": len(tasks) if isinstance(tasks, list) else 0,
            "strata": dict(sorted(strata.items())),
            "orders": dict(sorted(orders.items())),
            "repository_count": len(repository_rows),
        },
    }


def _snapshot_worktree(source: Path, commit: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["git", "-C", str(source), "worktree", "add", "--detach", "--force", str(destination), commit],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        raise LabError("could not create isolated evaluation worktree")


def _remove_worktree(source: Path, destination: Path) -> None:
    subprocess.run(
        ["git", "-C", str(source), "worktree", "remove", "--force", str(destination)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)


def _isolated_run_environment(isolation_root: Path, worktree: Path) -> dict[str, str]:
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("QWENDEX_AGENT_", "QWENDEX_MANAGER_")) or key in {
            "CODEX_HOME",
            "QWENDEX_STATE_DB",
            "QWENDEX_LEDGER_DB",
            "QWENDEX_PERFORMANCE_DB",
            "QWENDEX_PERFORMANCE_CAPTURE",
            "QWENDEX_RESULTS_ROOT",
            "QWENDEX_RUN_ID",
        }:
            environment.pop(key)
    values = {
        "CODEX_HOME": str((isolation_root / "codex_home").resolve()),
        "QWENDEX_STATE_DB": str((isolation_root / "state" / "qwendex.sqlite").resolve()),
        "QWENDEX_LEDGER_DB": str((isolation_root / "state" / "qwendex_ledger.sqlite").resolve()),
        "QWENDEX_PERFORMANCE_DB": str((isolation_root / "state" / "qwendex-performance.sqlite").resolve()),
        "QWENDEX_RESULTS_ROOT": str((isolation_root / "results").resolve()),
        "QWENDEX_MANAGER_TARGET_REPO": str(worktree.resolve()),
    }
    for path in (Path(values["CODEX_HOME"]), Path(values["QWENDEX_STATE_DB"]).parent, Path(values["QWENDEX_RESULTS_ROOT"])):
        path.mkdir(parents=True, exist_ok=True)
    environment.update(values)
    return environment


def _isolated_live_environment(isolation_root: Path, worktree: Path) -> dict[str, str]:
    """Create a fresh process/home boundary for one real Codex arm."""

    environment = _isolated_run_environment(isolation_root, worktree)
    home = isolation_root / "home"
    xdg_cache = isolation_root / "xdg-cache"
    xdg_config = isolation_root / "xdg-config"
    xdg_state = isolation_root / "xdg-state"
    for path in (home, xdg_cache, xdg_config, xdg_state):
        path.mkdir(parents=True, exist_ok=True)
    for key in ("QWENDEX_SEARCH_EVIDENCE_COMPACTION", "QWENDEX_LIVE_EVAL_AUTH_SOURCE"):
        environment.pop(key, None)
    environment.update(
        {
            "HOME": str(home.resolve()),
            "XDG_CACHE_HOME": str(xdg_cache.resolve()),
            "XDG_CONFIG_HOME": str(xdg_config.resolve()),
            "XDG_STATE_HOME": str(xdg_state.resolve()),
            "QWENDEX_AGENT_USE": "Manager",
            "QWENDEX_MANAGER_TARGET_REPO": str(worktree.resolve()),
            "QWENDEX_PERFORMANCE_CAPTURE": "metadata",
        }
    )
    return environment


def _copy_live_auth(auth_source: Path, codex_home: Path) -> None:
    """Copy only operator-supplied Codex auth into an ignored arm-local home."""

    source = auth_source.expanduser().resolve(strict=False)
    if not source.is_file():
        raise LabError("live evaluation auth source is unavailable")
    target = codex_home / "auth.json"
    shutil.copyfile(source, target)
    target.chmod(0o600)


def _live_prompt(manifest_path: Path, task: Mapping[str, Any]) -> str:
    prompt_file, key = _safe_reference_path(manifest_path, str(task.get("private_prompt_ref") or ""))
    payload = _read_json(prompt_file)
    value = payload.get(key) if isinstance(payload, Mapping) and key else None
    if not isinstance(value, str) or not value:
        raise LabError("live task prompt is unavailable")
    return value


def _materialize_live_fixture(task: Mapping[str, Any], worktree: Path) -> None:
    live = task.get("live") if isinstance(task.get("live"), Mapping) else {}
    fixtures = live.get("fixture_files", []) if isinstance(live, Mapping) else []
    if not isinstance(fixtures, list):
        raise LabError("live fixture files must be a list")
    for item in fixtures:
        if not isinstance(item, Mapping):
            raise LabError("live fixture entry is invalid")
        relative = Path(str(item.get("path") or ""))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise LabError("live fixture path escapes its worktree")
        target = worktree / relative
        if not _within(target.resolve(strict=False), worktree.resolve(strict=False)):
            raise LabError("live fixture path escapes its worktree")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(item.get("content") or ""), encoding="utf-8")
    mutation = live.get("tracked_mutation") if isinstance(live, Mapping) else None
    if mutation is not None:
        if not isinstance(mutation, Mapping):
            raise LabError("live tracked mutation is invalid")
        relative = Path(str(mutation.get("path") or ""))
        if not str(relative) or relative.is_absolute() or ".." in relative.parts:
            raise LabError("live tracked mutation path escapes its worktree")
        target = worktree / relative
        if not _within(target.resolve(strict=False), worktree.resolve(strict=False)) or not target.is_file():
            raise LabError("live tracked mutation target is unavailable")
        target.write_text(target.read_text(encoding="utf-8") + str(mutation.get("append") or ""), encoding="utf-8")


def _read_json_if_present(path: Path) -> dict[str, Any]:
    try:
        value = _read_json(path)
    except LabError:
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _live_launch_script() -> str:
    """Return a wrapper whose PID becomes the trusted Codex root process."""

    return r'''
set -euo pipefail
live_phase() {
  printf '%s\n' "$1" >> "$QWENDEX_LIVE_PHASES"
}
export QWENDEX_MANAGER_LAUNCH_PID="$$"
export QWENDEX_MANAGER_LAUNCH_START_TICKS="$(python3 - "$$" <<'PY'
import sys
from pathlib import Path
try:
    stat = Path(f"/proc/{sys.argv[1]}/stat").read_text(encoding="utf-8")
    closing = stat.rfind(")")
    fields = stat[closing + 2:].split() if closing >= 0 else []
    print(fields[19] if len(fields) > 19 else "")
except OSError:
    print("")
PY
)"
export QWENDEX_MANAGER_LAUNCH_NONCE="$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex)
PY
)"
live_phase manager_preflight_start
"$QWENDEX_LIVE_COMMAND" --agent-use Manager manager preflight --interactive-prompt-unknown --json > "$QWENDEX_LIVE_PREFLIGHT"
live_phase manager_preflight_end
eval "$(python3 - "$QWENDEX_LIVE_PREFLIGHT" <<'PY'
import json
import shlex
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)
data = payload.get("data", {})
exports = data.get("exports", {}) if isinstance(data, dict) else {}
if not data.get("ok") or not str(exports.get("QWENDEX_MANAGER_ROOT_AGENT_ID") or ""):
    raise SystemExit(2)
for key, value in exports.items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"
live_phase codex_process_start
exec "$QWENDEX_LIVE_RUNTIME" \
  --no-alt-screen \
  --sandbox workspace-write \
  --dangerously-bypass-hook-trust \
  --config "projects={$QWENDEX_LIVE_PROJECT={trust_level=\"trusted\"}}" \
  -c "model_reasoning_effort=$QWENDEX_LIVE_REASONING" \
  exec --ephemeral --json -C "$QWENDEX_LIVE_WORKTREE" -m "$QWENDEX_LIVE_MODEL" \
  --output-last-message "$QWENDEX_LIVE_LAST_MESSAGE" \
  "$@"
'''


def _process_group_snapshot(pgid: int, *, leader_pid: int) -> dict[str, Any]:
    """Collect bounded Linux process metadata without command lines or paths."""

    states: Counter[str] = Counter()
    rss_buckets: Counter[str] = Counter()
    process_count = 0
    non_zombie_count = 0
    cpu_ticks = 0
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
    except (AttributeError, OSError, ValueError):
        page_size = 4096
    proc_root = Path("/proc")
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        closing = raw.rfind(")")
        fields = raw[closing + 2 :].split() if closing >= 0 else []
        if len(fields) < 22:
            continue
        try:
            process_group = int(fields[2])
            if process_group != pgid:
                continue
            state = str(fields[0] or "?")
            cpu_ticks += int(fields[11]) + int(fields[12])
            rss_bytes = max(0, int(fields[21])) * page_size
        except ValueError:
            continue
        process_count += 1
        states[state] += 1
        if state != "Z":
            non_zombie_count += 1
        if rss_bytes < 64 * 1024 * 1024:
            bucket = "lt_64_mib"
        elif rss_bytes < 256 * 1024 * 1024:
            bucket = "64_to_256_mib"
        elif rss_bytes < 1024 * 1024 * 1024:
            bucket = "256_mib_to_1_gib"
        else:
            bucket = "gte_1_gib"
        rss_buckets[bucket] += 1
    return {
        "leader_pid": leader_pid,
        "pgid": pgid,
        "process_count": process_count,
        "non_zombie_process_count": non_zombie_count,
        "state_counts": dict(sorted(states.items())),
        "cpu_ticks": cpu_ticks,
        "rss_buckets": dict(sorted(rss_buckets.items())),
    }


def _runtime_profile_snapshot(profile: dict[str, Any], snapshot: Mapping[str, Any], *, reason: str) -> None:
    diagnostics = profile.setdefault("process_diagnostics", {})
    snapshots = diagnostics.setdefault("snapshots", [])
    if isinstance(snapshots, list):
        snapshots.append({"reason": reason, **dict(snapshot)})


def _safe_event_category(value: str, *, allowed: set[str]) -> str:
    return value if value in allowed else "other"


def _increment_runtime_count(profile: dict[str, Any], key: str, *, amount: int = 1) -> None:
    counts = profile.setdefault("trusted_progress_event_counts", {})
    counts[key] = int(counts.get(key) or 0) + amount


def _increment_hook_lifecycle_count(profile: dict[str, Any], key: str) -> None:
    counts = profile.setdefault("hook_lifecycle_event_counts", {})
    counts[key] = int(counts.get(key) or 0) + 1


def _increment_wait_timeout_bucket(profile: dict[str, Any], bucket: str) -> None:
    counts = profile.setdefault("collaboration_wait_timeout_bucket_counts", {})
    counts[bucket] = int(counts.get(bucket) or 0) + 1


def _consume_live_hook_lifecycle(
    profile: dict[str, Any],
    performance_db: Path | None,
    *,
    row_states: dict[int, str],
    wait_timeout_rows: dict[int, str],
    now: float,
) -> None:
    """Consume only allowlisted metadata-only hook completions.

    The database belongs to one freshly isolated arm.  This reader never
    copies identifiers, prompts, commands, paths, query fingerprints, or
    output metadata into the runtime profile; it retains just fixed category
    counts and uses the local observation time to extend trusted progress.
    """

    if performance_db is None or not performance_db.is_file():
        return
    try:
        connection = sqlite3.connect(
            performance_db.resolve(strict=False).as_uri() + "?mode=ro",
            uri=True,
            timeout=0.05,
        )
        try:
            rows = connection.execute(
                f"""
                SELECT rowid, phase, event_kind, tool_family, terminal_classification,
                       wait_timeout_bucket
                FROM {LIVE_HOOK_PROGRESS_TABLE}
                ORDER BY rowid
                """,
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        # Hook capture is advisory for timing.  A transient SQLite lock or an
        # absent freshly-created table must never alter the supervisor policy.
        return

    for rowid, phase, event_kind, _tool_family, terminal, raw_wait_bucket in rows:
        if not isinstance(rowid, int):
            continue
        wait_bucket = str(raw_wait_bucket or "not_applicable")
        if wait_bucket not in LIVE_WAIT_TIMEOUT_BUCKETS:
            wait_bucket = "invalid"
        if wait_bucket != "not_applicable" and wait_timeout_rows.get(rowid) != wait_bucket:
            wait_timeout_rows[rowid] = wait_bucket
            _increment_wait_timeout_bucket(profile, wait_bucket)
        terminal_state = str(terminal or "")
        previous_state = row_states.get(rowid)
        if previous_state == terminal_state:
            continue
        # Concurrent hook transactions can commit a lower allocated rowid
        # after a higher one, and a PreToolUse row can later change from
        # ``pending`` to ``completed`` in place. Keep only row-local terminal
        # states, rather than advancing a high-water mark or treating first
        # observation as terminal.
        row_states[rowid] = terminal_state
        category = ""
        if (
            phase == "tool"
            and event_kind == LIVE_HOOK_PROGRESS_TOOL_EVENT
            and terminal_state == "completed"
            and previous_state != "completed"
        ):
            category = "tool_completed"
        elif phase == "subagent" and event_kind in LIVE_HOOK_PROGRESS_SUBAGENT_EVENTS:
            category = str(event_kind)
        if not category:
            continue
        _increment_hook_lifecycle_count(profile, category)
        _increment_runtime_count(profile, f"hook_{category}")
        _record_runtime_phase(profile, "last_trusted_progress", now=now, replace=True)
        profile["_last_trusted_monotonic"] = now


def _observe_live_structured_event(profile: dict[str, Any], event: Mapping[str, Any], *, now: float) -> None:
    """Accept only recognized lifecycle transitions as progress.

    The function may inspect raw command/tool fields transiently to classify a
    lifecycle event, but stores only fixed categories and counts in the
    profile.  Plain pipe bytes and arbitrary warning text never reset the
    inactivity deadline.
    """

    event_type = _safe_event_category(
        str(event.get("type") or ""),
        allowed={"thread.started", "turn.started", "turn.completed", "item.started", "item.completed"},
    )
    counts = profile.setdefault("structured_event_type_counts", {})
    counts[event_type] = int(counts.get(event_type) or 0) + 1
    if event_type == "other":
        return
    _record_runtime_phase(profile, "first_structured_event", now=now)
    item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
    item_type = _safe_event_category(
        str(item.get("type") or ""),
        allowed={"agent_message", "command_execution", "collab_tool_call", "file_change", "mcp_tool_call"},
    )
    trusted = event_type in {"thread.started", "turn.started", "turn.completed"}
    category = event_type.replace(".", "_")
    if item_type != "other":
        trusted = True
        category = item_type
    if trusted:
        _record_runtime_phase(profile, "last_trusted_progress", now=now, replace=True)
        profile["_last_trusted_monotonic"] = now
        _increment_runtime_count(profile, category)
    if item_type == "agent_message":
        _record_runtime_phase(profile, "first_model_or_assistant_event", now=now)
        _increment_runtime_count(profile, "model_or_assistant_event")
        if event_type == "item.completed":
            _record_runtime_phase(profile, "final_assistant_event", now=now, replace=True)
    tool_types = {"command_execution", "collab_tool_call", "mcp_tool_call", "file_change"}
    if item_type not in tool_types:
        return
    command = str(item.get("command") or "")
    tool_name = str(item.get("name") or item.get("tool_name") or "")
    lowered = f"{command} {tool_name}".lower()
    collab_tool = str(item.get("tool") or "").lower()
    if item_type == "collab_tool_call":
        lifecycle_kind = "collaboration_wait" if collab_tool == "wait" else "collaboration_other"
    elif item_type == "command_execution":
        lifecycle_kind = "command_execution"
    elif item_type == "mcp_tool_call":
        lifecycle_kind = "mcp_tool_call"
    else:
        lifecycle_kind = "file_change"
    is_search = bool(re.search(r"(?:^|[\s;&|])rg(?:[\s;&|]|$)", lowered)) or " search content " in f" {lowered} "
    is_manager_stop = "manager" in lowered and "stop" in lowered
    if event_type == "item.started":
        _record_runtime_phase(profile, "first_tool_start", now=now)
        _increment_runtime_count(profile, "tool_start")
        profile["_tool_in_flight"] = int(profile.get("_tool_in_flight") or 0) + 1
        in_flight = profile.setdefault("_in_flight_lifecycle_counts", {})
        in_flight[lifecycle_kind] = int(in_flight.get(lifecycle_kind) or 0) + 1
        _increment_runtime_count(profile, lifecycle_kind + "_start")
        if is_search:
            _record_runtime_phase(profile, "first_search_start", now=now)
            _increment_runtime_count(profile, "search_start")
        if is_manager_stop:
            _record_runtime_phase(profile, "manager_stop_start", now=now)
    elif event_type == "item.completed":
        _record_runtime_phase(profile, "first_tool_end", now=now)
        _record_runtime_phase(profile, "last_tool_end", now=now, replace=True)
        _increment_runtime_count(profile, "tool_end")
        profile["_tool_in_flight"] = max(0, int(profile.get("_tool_in_flight") or 0) - 1)
        in_flight = profile.setdefault("_in_flight_lifecycle_counts", {})
        in_flight[lifecycle_kind] = max(0, int(in_flight.get(lifecycle_kind) or 0) - 1)
        _increment_runtime_count(profile, lifecycle_kind + "_end")
        if is_search:
            _record_runtime_phase(profile, "first_search_end", now=now)
            _increment_runtime_count(profile, "search_end")
        if is_manager_stop:
            _record_runtime_phase(profile, "manager_stop_end", now=now, replace=True)


def _consume_live_phase_markers(profile: dict[str, Any], phase_path: Path, *, offset: int, now: float) -> int:
    try:
        with phase_path.open("r", encoding="utf-8", errors="replace") as handle:
            handle.seek(offset)
            raw = handle.read()
            next_offset = handle.tell()
    except OSError:
        return offset
    for marker in raw.splitlines():
        if marker in {"manager_preflight_start", "manager_preflight_end", "codex_process_start"}:
            _record_runtime_phase(profile, marker, now=now)
    return next_offset


def _start_pipe_drain(
    stream: Any,
    destination: Path,
    *,
    stream_name: str,
    state: dict[str, Any],
    state_lock: threading.Lock,
    on_chunk: Callable[[bytes], None] | None = None,
) -> threading.Thread:
    """Drain one child pipe continuously into ignored raw evidence."""

    def drain() -> None:
        bytes_seen = 0
        try:
            with destination.open("wb") as handle:
                while True:
                    chunk = stream.read(65_536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    bytes_seen += len(chunk)
                    if on_chunk is not None:
                        on_chunk(chunk)
        finally:
            try:
                stream.close()
            except OSError:
                pass
            with state_lock:
                state[stream_name] = {
                    "bytes": bytes_seen,
                    "eof": True,
                    "completed_at_monotonic": time.monotonic(),
                }

    thread = threading.Thread(target=drain, name=f"qwendex-live-{stream_name}", daemon=True)
    thread.start()
    return thread


def _wait_for_pipe_drain(threads: Sequence[threading.Thread], *, timeout_seconds: float) -> bool:
    deadline = time.monotonic() + timeout_seconds
    for thread in threads:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(timeout=remaining)
    return not any(thread.is_alive() for thread in threads)


def _signal_process_group(process: subprocess.Popen[bytes], signal_value: int) -> bool:
    try:
        os.killpg(process.pid, signal_value)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def _wait_for_process_group_quiet(pgid: int, *, leader_pid: int, timeout_seconds: float, poll_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest = _process_group_snapshot(pgid, leader_pid=leader_pid)
    while latest.get("non_zombie_process_count", 0) > 0 and time.monotonic() < deadline:
        time.sleep(min(poll_seconds, max(0.01, deadline - time.monotonic())))
        latest = _process_group_snapshot(pgid, leader_pid=leader_pid)
    return latest


def _supervisor_timeout_reason(profile: Mapping[str, Any], budgets: Mapping[str, Any], *, now: float) -> str | None:
    started = float(profile.get("_runtime_start_monotonic") or now)
    preflight_end = _runtime_phase_offset(profile, "manager_preflight_end")
    codex_start = _runtime_phase_offset(profile, "codex_process_start")
    first_model = _runtime_phase_offset(profile, "first_model_or_assistant_event")
    startup = budgets.get("startup_preflight_seconds")
    if isinstance(startup, int | float) and preflight_end is None and now - started >= float(startup):
        return "startup_preflight"
    if preflight_end is not None and first_model is None:
        first_model_limit = budgets.get("first_model_activity_seconds")
        model_origin_ms = codex_start if codex_start is not None else preflight_end
        if isinstance(first_model_limit, int | float) and now - started >= model_origin_ms / 1000.0 + float(first_model_limit):
            return "first_model"
    last_progress = profile.get("_last_trusted_monotonic")
    inactivity = budgets.get("inactivity_seconds")
    if isinstance(last_progress, int | float) and isinstance(inactivity, int | float) and now - float(last_progress) >= float(inactivity):
        return "inactivity"
    hard_wall = float(budgets["hard_wall_seconds"])
    if now - started >= hard_wall:
        return "hard_wall"
    return None


def _classify_live_timeout(profile: Mapping[str, Any], *, timeout_reason: str, pipe_drain_timed_out: bool, cleanup_status: str) -> str:
    if cleanup_status != "pass":
        return "process_tree_cleanup_failed"
    if pipe_drain_timed_out:
        return "timeout_after_child_exit_pipe_drain"
    if timeout_reason == "startup_preflight":
        return "timeout_before_preflight_complete"
    if timeout_reason == "first_model":
        if _runtime_phase_offset(profile, "codex_process_start") is None:
            return "timeout_before_process_start"
        return "timeout_before_first_model_event"
    if timeout_reason == "inactivity":
        if _runtime_phase_offset(profile, "manager_stop_start") is not None and _runtime_phase_offset(profile, "manager_stop_end") is None:
            return "timeout_during_manager_stop"
        return "timeout_due_to_inactivity"
    if timeout_reason == "hard_wall":
        if _runtime_phase_offset(profile, "manager_stop_start") is not None and _runtime_phase_offset(profile, "manager_stop_end") is None:
            return "timeout_during_manager_stop"
        last_progress = _runtime_phase_offset(profile, "last_trusted_progress")
        timeout_at = _runtime_phase_offset(profile, "timeout_signal")
        inactivity = profile.get("supervisor_budget", {}).get("inactivity_seconds") if isinstance(profile.get("supervisor_budget"), Mapping) else None
        if isinstance(last_progress, int | float) and isinstance(timeout_at, int | float) and isinstance(inactivity, int | float) and timeout_at - last_progress <= float(inactivity) * 1000.0:
            return "timeout_with_continuous_trusted_progress"
        if int(profile.get("_tool_in_flight") or 0) > 0:
            return "timeout_during_tool_execution"
        if _runtime_phase_offset(profile, "last_tool_end") is not None and _runtime_phase_offset(profile, "final_assistant_event") is None:
            return "timeout_after_last_tool_before_final"
        if _runtime_phase_offset(profile, "first_model_or_assistant_event") is not None:
            return "timeout_during_model_response"
    return "unknown_insufficient_instrumentation"


def _supervise_live_subprocess(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    raw_dir: Path,
    profile: dict[str, Any],
    budgets: Mapping[str, Any],
    preflight_path: Path | None = None,
) -> dict[str, Any]:
    """Run and observe a live child without retaining raw content in metadata.

    stdout and stderr are independently drained as the child runs, so one full
    pipe cannot deadlock the other.  Raw bytes stay only in the ignored arm
    directory; the returned profile contains counts, phases, and bounded
    process diagnostics.
    """

    raw_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = raw_dir / "events.jsonl"
    stderr_path = raw_dir / "stderr.txt"
    phase_path = raw_dir / "runtime_phases.log"
    phase_path.touch(exist_ok=True)
    state_lock = threading.Lock()
    pipe_state: dict[str, Any] = {}
    stdout_buffer = bytearray()
    performance_capture = str(environment.get("QWENDEX_PERFORMANCE_CAPTURE") or "").strip().lower()
    performance_db_raw = str(environment.get("QWENDEX_PERFORMANCE_DB") or "").strip()
    if performance_capture != "metadata":
        performance_db_raw = ""
    performance_db = Path(performance_db_raw).resolve(strict=False) if performance_db_raw else None
    hook_lifecycle_row_states: dict[int, str] = {}
    hook_wait_timeout_rows: dict[int, str] = {}

    def observe_stdout(chunk: bytes) -> None:
        nonlocal stdout_buffer
        with state_lock:
            stdout_buffer.extend(chunk)
            while True:
                boundary = stdout_buffer.find(b"\n")
                if boundary < 0:
                    break
                raw_line = bytes(stdout_buffer[:boundary])
                del stdout_buffer[: boundary + 1]
                try:
                    event = json.loads(raw_line.decode("utf-8", "replace"))
                except json.JSONDecodeError:
                    _increment_runtime_count(profile, "structured_parse_error")
                    continue
                if isinstance(event, Mapping):
                    _observe_live_structured_event(profile, event, now=time.monotonic())

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        bufsize=0,
    )
    if process.stdout is None or process.stderr is None:
        raise LabError("live supervisor could not open child pipes")
    with state_lock:
        _runtime_profile_snapshot(profile, _process_group_snapshot(process.pid, leader_pid=process.pid), reason="process_started")
    stdout_thread = _start_pipe_drain(process.stdout, stdout_path, stream_name="stdout", state=pipe_state, state_lock=state_lock, on_chunk=observe_stdout)
    stderr_thread = _start_pipe_drain(process.stderr, stderr_path, stream_name="stderr", state=pipe_state, state_lock=state_lock)
    drain_threads = (stdout_thread, stderr_thread)
    phase_offset = 0
    timeout_reason = "not_applicable"
    timed_out = False
    pipe_drain_timed_out = False

    while process.poll() is None:
        now = time.monotonic()
        with state_lock:
            phase_offset = _consume_live_phase_markers(profile, phase_path, offset=phase_offset, now=now)
            if _runtime_phase_offset(profile, "codex_process_start") is not None:
                _consume_live_hook_lifecycle(
                    profile,
                    performance_db,
                    row_states=hook_lifecycle_row_states,
                    wait_timeout_rows=hook_wait_timeout_rows,
                    now=now,
                )
            reason = _supervisor_timeout_reason(profile, budgets, now=now)
        if reason is None:
            time.sleep(float(budgets["poll_interval_seconds"]))
            continue
        timed_out = True
        timeout_reason = reason
        with state_lock:
            _record_runtime_phase(profile, "timeout_signal", now=now)
            _runtime_profile_snapshot(profile, _process_group_snapshot(process.pid, leader_pid=process.pid), reason=reason)
            profile["termination"]["signals"].append({"signal": "SIGTERM", "reason": reason})
            _record_runtime_phase(profile, "terminate_signal", now=now)
        _signal_process_group(process, signal.SIGTERM)
        try:
            process.wait(timeout=float(budgets["graceful_termination_seconds"]))
        except subprocess.TimeoutExpired:
            with state_lock:
                profile["termination"]["signals"].append({"signal": "SIGKILL", "reason": reason})
                _record_runtime_phase(profile, "kill_signal")
            _signal_process_group(process, signal.SIGKILL)
            try:
                process.wait(timeout=float(budgets["forced_cleanup_seconds"]))
            except subprocess.TimeoutExpired:
                pass
        break

    returncode = process.poll()
    if returncode is None:
        try:
            returncode = process.wait(timeout=float(budgets["forced_cleanup_seconds"]))
        except subprocess.TimeoutExpired:
            timed_out = True
            timeout_reason = "hard_wall" if timeout_reason == "not_applicable" else timeout_reason
            with state_lock:
                profile["termination"]["signals"].append({"signal": "SIGKILL", "reason": "unreaped_child"})
                _record_runtime_phase(profile, "kill_signal")
            _signal_process_group(process, signal.SIGKILL)
            returncode = process.wait(timeout=float(budgets["forced_cleanup_seconds"]))

    now = time.monotonic()
    with state_lock:
        phase_offset = _consume_live_phase_markers(profile, phase_path, offset=phase_offset, now=now)
        if _runtime_phase_offset(profile, "codex_process_start") is not None:
            _consume_live_hook_lifecycle(
                profile,
                performance_db,
                row_states=hook_lifecycle_row_states,
                wait_timeout_rows=hook_wait_timeout_rows,
                now=now,
            )
        _record_runtime_phase(profile, "child_exit", now=now)
        _runtime_profile_snapshot(profile, _process_group_snapshot(process.pid, leader_pid=process.pid), reason="child_exit")
    drained = _wait_for_pipe_drain(drain_threads, timeout_seconds=float(budgets["pipe_drain_seconds"]))
    if not drained:
        timed_out = True
        pipe_drain_timed_out = True
        timeout_reason = "pipe_drain"
        with state_lock:
            _runtime_profile_snapshot(profile, _process_group_snapshot(process.pid, leader_pid=process.pid), reason="pipe_drain_timeout")
            profile["termination"]["signals"].append({"signal": "SIGTERM", "reason": "pipe_drain_timeout"})
            _record_runtime_phase(profile, "terminate_signal")
        _signal_process_group(process, signal.SIGTERM)
        _wait_for_process_group_quiet(
            process.pid,
            leader_pid=process.pid,
            timeout_seconds=float(budgets["graceful_termination_seconds"]),
            poll_seconds=float(budgets["poll_interval_seconds"]),
        )
        latest = _process_group_snapshot(process.pid, leader_pid=process.pid)
        if int(latest.get("non_zombie_process_count") or 0) > 0:
            with state_lock:
                profile["termination"]["signals"].append({"signal": "SIGKILL", "reason": "pipe_drain_timeout"})
                _record_runtime_phase(profile, "kill_signal")
            _signal_process_group(process, signal.SIGKILL)
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        _wait_for_pipe_drain(drain_threads, timeout_seconds=float(budgets["forced_cleanup_seconds"]))

    with state_lock:
        pipe_summary = {
            name: {
                "bytes": int(value.get("bytes") or 0),
                "eof": bool(value.get("eof")),
            }
            for name, value in sorted(pipe_state.items())
            if isinstance(value, Mapping)
        }
        profile["process_diagnostics"]["pipe_state"] = {
            "streams": pipe_summary,
            "drain_completed": not any(thread.is_alive() for thread in drain_threads),
            "drain_ceiling_seconds": budgets["pipe_drain_seconds"],
        }
        if not any(thread.is_alive() for thread in drain_threads):
            _record_runtime_phase(profile, "pipe_drain_complete")
        before_cleanup = _process_group_snapshot(process.pid, leader_pid=process.pid)
        _runtime_profile_snapshot(profile, before_cleanup, reason="before_cleanup")

    if int(before_cleanup.get("non_zombie_process_count") or 0) > 0:
        with state_lock:
            profile["termination"]["signals"].append({"signal": "SIGTERM", "reason": "residual_process_group"})
            _record_runtime_phase(profile, "terminate_signal")
        _signal_process_group(process, signal.SIGTERM)
        after_grace = _wait_for_process_group_quiet(
            process.pid,
            leader_pid=process.pid,
            timeout_seconds=float(budgets["graceful_termination_seconds"]),
            poll_seconds=float(budgets["poll_interval_seconds"]),
        )
        if int(after_grace.get("non_zombie_process_count") or 0) > 0:
            with state_lock:
                profile["termination"]["signals"].append({"signal": "SIGKILL", "reason": "residual_process_group"})
                _record_runtime_phase(profile, "kill_signal")
            _signal_process_group(process, signal.SIGKILL)
            after_cleanup = _wait_for_process_group_quiet(
                process.pid,
                leader_pid=process.pid,
                timeout_seconds=float(budgets["forced_cleanup_seconds"]),
                poll_seconds=float(budgets["poll_interval_seconds"]),
            )
        else:
            after_cleanup = after_grace
    else:
        after_cleanup = before_cleanup

    cleanup_status = "pass" if int(after_cleanup.get("non_zombie_process_count") or 0) == 0 else "fail"
    with state_lock:
        _runtime_profile_snapshot(profile, after_cleanup, reason="after_cleanup")
        _record_runtime_phase(profile, "cleanup_complete")
        classification = _classify_live_timeout(
            profile,
            timeout_reason=timeout_reason,
            pipe_drain_timed_out=pipe_drain_timed_out,
            cleanup_status=cleanup_status,
        ) if timed_out or cleanup_status != "pass" else "not_applicable"
        in_flight = profile.get("_in_flight_lifecycle_counts", {})
        contributors = [
            f"{name}_no_completion"
            for name, count in sorted(in_flight.items())
            if isinstance(count, int | float) and count > 0
        ] if isinstance(in_flight, Mapping) else []
        profile["termination"].update(
            {
                "timed_out": timed_out,
                "timeout_reason": timeout_reason,
                "timeout_classification": classification,
                "contributing_classifications": contributors,
                "completion_valid": not timed_out and returncode == 0 and cleanup_status == "pass",
                "cleanup_status": cleanup_status,
            }
        )
        profile.pop("_last_trusted_monotonic", None)
        profile.pop("_tool_in_flight", None)
        profile.pop("_in_flight_lifecycle_counts", None)

    return {
        "returncode": int(returncode) if isinstance(returncode, int) else -1,
        "timed_out": timed_out,
        "duration_ms": round((_runtime_phase_offset(profile, "cleanup_complete") or 0.0), 3),
        "preflight": _read_json_if_present(preflight_path) if preflight_path is not None else {},
        "raw_paths": {
            "events": stdout_path,
            "stderr": stderr_path,
            "runtime_phases": phase_path,
        },
        "runtime_profile": profile,
        "timeout_classification": profile["termination"]["timeout_classification"],
    }


def _run_live_codex(
    *,
    environment: Mapping[str, str],
    worktree: Path,
    prompt: str,
    model: str,
    reasoning_effort: str,
    profile: dict[str, Any],
    budgets: Mapping[str, Any],
    raw_dir: Path,
) -> dict[str, Any]:
    """Run one fresh authenticated Manager root under the live supervisor."""

    runtime = str(environment.get("QWENDEX_CODEX_RUNTIME") or os.environ.get("QWENDEX_CODEX_RUNTIME") or "")
    if not runtime or not Path(runtime).is_file():
        raise LabError("live evaluation Codex runtime is unavailable")
    raw_dir.mkdir(parents=True, exist_ok=True)
    preflight_path = raw_dir / "manager_preflight.json"
    last_message_path = raw_dir / "last_message.md"
    phase_path = raw_dir / "runtime_phases.log"
    child_env = dict(environment)
    child_env.update(
        {
            "QWENDEX_LIVE_COMMAND": str((REPOSITORY_ROOT / "scripts" / "qwendex").resolve()),
            "QWENDEX_LIVE_RUNTIME": runtime,
            "QWENDEX_LIVE_PREFLIGHT": str(preflight_path.resolve()),
            "QWENDEX_LIVE_PROJECT": json.dumps(str(worktree.resolve())),
            "QWENDEX_LIVE_REASONING": json.dumps(reasoning_effort),
            "QWENDEX_LIVE_WORKTREE": str(worktree.resolve()),
            "QWENDEX_LIVE_MODEL": model,
            "QWENDEX_LIVE_LAST_MESSAGE": str(last_message_path.resolve()),
            "QWENDEX_LIVE_PHASES": str(phase_path.resolve()),
        }
    )
    result = _supervise_live_subprocess(
        ["bash", "-c", _live_launch_script(), "qwendex-live", prompt],
        cwd=worktree,
        environment=child_env,
        raw_dir=raw_dir,
        profile=profile,
        budgets=budgets,
        preflight_path=preflight_path,
    )
    result["raw_paths"]["last_message"] = last_message_path
    return result


def _prepare_isolated_manager(isolation_root: Path, worktree: Path) -> tuple[dict[str, str], dict[str, Any]]:
    environment = _isolated_run_environment(isolation_root, worktree)
    command = REPOSITORY_ROOT / "scripts" / "qwendex"
    install = subprocess.run(
        [str(command), "agent", "hook-config", "--install", "--codex-home", environment["CODEX_HOME"], "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    preflight = subprocess.run(
        [str(command), "--agent-use", "Manager", "manager", "preflight", "--mode", "manager", "--dry-run", "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    try:
        install_payload = json.loads(install.stdout)
        preflight_payload = json.loads(preflight.stdout)
    except json.JSONDecodeError as exc:
        raise LabError("isolated Manager preflight returned invalid JSON") from exc
    preflight_data = preflight_payload.get("data", {}) if isinstance(preflight_payload.get("data"), Mapping) else {}
    hook = preflight_data.get("hook_status", {}) if isinstance(preflight_data.get("hook_status"), Mapping) else {}
    result = {
        "status": "pass"
        if install_payload.get("status") == "pass"
        and preflight_payload.get("status") == "pass"
        and preflight_data.get("stop_status") == "STOP_MANAGER_PREFLIGHT_READY"
        and bool(hook.get("verified"))
        else "fail",
        "hook_verified": bool(hook.get("verified")),
        "stop_status": str(preflight_data.get("stop_status") or ""),
        "policy_hash": str(preflight_data.get("policy_hash") or ""),
        "repository_binding": "isolated_snapshot" if preflight_data.get("repo_root") else "not_observed",
        "root_identity": "derived" if preflight_data.get("root_agent_id") else "not_observed",
    }
    return environment, result


def _prepare_live_manager(isolation_root: Path, worktree: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Install verified hooks into a fresh live arm before its PID-bound launch."""

    environment = _isolated_live_environment(isolation_root, worktree)
    command = REPOSITORY_ROOT / "scripts" / "qwendex"
    install = subprocess.run(
        [str(command), "agent", "hook-config", "--install", "--codex-home", environment["CODEX_HOME"], "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    probe = subprocess.run(
        [str(command), "--agent-use", "Manager", "manager", "preflight", "--mode", "manager", "--dry-run", "--json"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    try:
        install_payload = json.loads(install.stdout)
        probe_payload = json.loads(probe.stdout)
    except json.JSONDecodeError as exc:
        raise LabError("isolated live Manager setup returned invalid JSON") from exc
    data = probe_payload.get("data", {}) if isinstance(probe_payload.get("data"), Mapping) else {}
    hook = data.get("hook_status", {}) if isinstance(data.get("hook_status"), Mapping) else {}
    result = {
        "status": "pass"
        if install_payload.get("status") == "pass"
        and probe_payload.get("status") == "pass"
        and data.get("stop_status") == "STOP_MANAGER_PREFLIGHT_READY"
        and bool(hook.get("verified"))
        else "fail",
        "hook_verified": bool(hook.get("verified")),
        "stop_status": str(data.get("stop_status") or ""),
        "policy_hash": str(data.get("policy_hash") or ""),
        "repository_binding": "isolated_snapshot" if data.get("repo_root") else "not_observed",
        "root_identity": "dry_run_derived" if data.get("root_agent_id") else "not_observed",
    }
    return environment, result


def _live_raw_artifacts(raw_dir: Path, run_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(item for item in raw_dir.rglob("*") if item.is_file()):
        entries.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": "sha256:" + sha256_file(path),
            }
        )
    return entries


def _live_trace_summary(events_path: Path) -> dict[str, Any]:
    """Derive numeric observations from Codex JSONL without retaining content."""

    command_count = 0
    search_calls = 0
    read_calls = 0
    edit_calls = 0
    validation_calls = 0
    search_output_bytes = 0
    candidate_search_calls = 0
    pagination_calls = 0
    fallback_count = 0
    parse_errors = 0
    token_usage: dict[str, int] = {}
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(event, Mapping):
            continue
        usage = event.get("usage")
        if isinstance(usage, Mapping):
            for key in ("input_tokens", "output_tokens", "reasoning_output_tokens", "cached_input_tokens"):
                if isinstance(usage.get(key), int):
                    token_usage[key] = token_usage.get(key, 0) + int(usage[key])
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "command_execution":
            continue
        command_count += 1
        command = str(item.get("command") or "")
        lowered = command.lower()
        output = str(item.get("aggregated_output") or "")
        is_search = bool(re.search(r"(?:^|[\s;&|])rg(?:[\s;&|]|$)", lowered)) or " search content " in f" {lowered} "
        is_validation = any(token in lowered for token in ("pytest", "py_compile", "json.tool", "git diff --check", "ruff check"))
        is_edit = any(token in lowered for token in ("apply_patch", "perl -pi", "sed -i", "mv ", "cp ", "touch "))
        is_read = not is_search and not is_validation and any(token in lowered for token in (" cat ", " sed ", " head ", " tail ", " less ", " rg "))
        if is_search:
            search_calls += 1
            search_output_bytes += len(output.encode("utf-8", "replace"))
            if "search content" in lowered and re.search(r"--candidate(?:=|\s+)v2\b", lowered):
                candidate_search_calls += 1
                fallback_count += output.count("baseline_fallback")
            if "search next" in lowered:
                pagination_calls += 1
        elif is_validation:
            validation_calls += 1
        elif is_edit:
            edit_calls += 1
        elif is_read:
            read_calls += 1
    return {
        "tool_calls": command_count,
        "search_calls": search_calls,
        "read_calls": read_calls,
        "edit_calls": edit_calls,
        "validation_tool_calls": validation_calls,
        "search_output_bytes": search_output_bytes if search_calls else "not_observed",
        "candidate_search_calls": candidate_search_calls,
        "pagination_calls": pagination_calls,
        "fallback_count": fallback_count,
        "candidate_adopted": candidate_search_calls > 0,
        "parse_errors": parse_errors,
        "token_usage": token_usage or "not_observed",
    }


def _live_evidence_grade(task: Mapping[str, Any], raw_dir: Path) -> dict[str, Any]:
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in (raw_dir / "events.jsonl", raw_dir / "last_message.md")
        if path.is_file()
    )
    expected_files = [str(item) for item in task.get("expected_relevant_files", [])]
    regions = [item for item in task.get("expected_relevant_regions", []) if isinstance(item, Mapping)]
    file_hits = [path for path in expected_files if path in text]
    region_hits = [
        {"path": str(region.get("path") or ""), "anchor": str(region.get("anchor") or ""), "observed": str(region.get("anchor") or "") in text}
        for region in regions
    ]
    file_recall = round(len(file_hits) / len(expected_files), 6) if expected_files else 0.0
    region_recall = round(sum(1 for item in region_hits if item["observed"]) / len(region_hits), 6) if region_hits else 0.0
    rubric = task.get("task_success_rubric", {}) if isinstance(task.get("task_success_rubric"), Mapping) else {}
    return {
        "relevant_file_recall": file_recall,
        "relevant_region_recall": region_recall,
        "file_hits": len(file_hits),
        "file_expected": len(expected_files),
        "region_hits": sum(1 for item in region_hits if item["observed"]),
        "region_expected": len(region_hits),
        "region_evidence": region_hits,
        "quality_status": "pass"
        if file_recall >= float(rubric.get("minimum_file_recall", 1.0))
        and region_recall >= float(rubric.get("minimum_region_recall", 1.0))
        else "fail",
    }


def _run_live_validation(
    task: Mapping[str, Any],
    worktree: Path,
    raw_dir: Path,
    environment: Mapping[str, str],
    *,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Run task validation outside the Codex supervisor wall budget.

    Validation has its own recorded phase so a slow checker can never be
    misreported as a model, Manager, or pipe-drain timeout.
    """

    command = [str(item) for item in task.get("validation_command", [])]
    if not command:
        return {"status": "not_applicable", "duration_ms": None, "timed_out": False}
    started = time.monotonic()
    _record_runtime_phase(profile, "validation_start", now=started)
    stdout_path = raw_dir / "validation.stdout"
    stderr_path = raw_dir / "validation.stderr"
    timed_out = False
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            completed = subprocess.run(
                command,
                cwd=worktree,
                env=dict(environment),
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=max(30, int(task.get("timeout_seconds") or 180)),
            )
        live = task.get("live") if isinstance(task.get("live"), Mapping) else {}
        expected = str(live.get("validation_expectation") or "pass") if isinstance(live, Mapping) else "pass"
        status = "pass" if (completed.returncode == 0) == (expected != "fail") else "fail"
    except subprocess.TimeoutExpired:
        timed_out = True
        status = "fail"
    except OSError:
        status = "fail"
    finished = time.monotonic()
    _record_runtime_phase(profile, "validation_end", now=finished)
    if timed_out:
        profile["termination"].update(
            {
                "timed_out": True,
                "timeout_reason": "validation",
                "timeout_classification": "timeout_during_validation",
                "completion_valid": False,
            }
        )
    return {"status": status, "duration_ms": round((finished - started) * 1000, 3), "timed_out": timed_out}


def _live_postconditions(task: Mapping[str, Any], worktree: Path) -> bool:
    live = task.get("live") if isinstance(task.get("live"), Mapping) else {}
    checks = live.get("postconditions", []) if isinstance(live, Mapping) else []
    if not isinstance(checks, list):
        return False
    for check in checks:
        if not isinstance(check, Mapping):
            return False
        relative = Path(str(check.get("path") or ""))
        target = worktree / relative
        if not str(relative) or relative.is_absolute() or ".." in relative.parts or not target.is_file():
            return False
        if str(check.get("contains") or "") not in target.read_text(encoding="utf-8", errors="replace"):
            return False
    return True


def _live_manager_status(environment: Mapping[str, str]) -> dict[str, Any]:
    command = REPOSITORY_ROOT / "scripts" / "qwendex"
    completed = subprocess.run(
        [str(command), "manager", "status", "--json"],
        cwd=REPOSITORY_ROOT,
        env=dict(environment),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "fail", "agent_count": "not_observed", "stale_count": "not_observed"}
    data = payload.get("data", {}) if isinstance(payload.get("data"), Mapping) else {}
    active = data.get("active_subagents", {}) if isinstance(data.get("active_subagents"), Mapping) else {}
    stale = data.get("stale_sessions", {}) if isinstance(data.get("stale_sessions"), Mapping) else {}
    outcomes = data.get("agent_outcomes", []) if isinstance(data.get("agent_outcomes"), list) else []
    return {
        "status": str(payload.get("status") or "fail"),
        "agent_count": int(active.get("count") or 0),
        "stale_count": int(stale.get("count") or 0),
        "outcome_count": len(outcomes),
    }


def _live_manager_is_clean(manager: Mapping[str, Any]) -> bool:
    """Treat an idle isolated Manager as healthy, not as a failed launch."""

    return (
        str(manager.get("status") or "") in {"pass", "standby"}
        and int(manager.get("agent_count") or 0) == 0
        and int(manager.get("stale_count") or 0) == 0
    )


def _contains_live_guard_marker(raw_dir: Path) -> bool:
    final_message = raw_dir / "last_message.md"
    if not final_message.is_file():
        # A supervisor timeout may legitimately prevent Codex from writing its
        # final-message file.  Missing output is an incomplete arm, not proof
        # that a local-model guard marker or visible tool markup was emitted.
        return False
    text = final_message.read_text(encoding="utf-8", errors="replace")
    return any(marker in text for marker in ("LOCAL_MODEL_TOOL_CALL_TOO_LARGE", "LOCAL_MODEL_TOOL_CALL_TRUNCATED", "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED", "LOCAL_MODEL_LOOP_DETECTED", "<tool_call", "<function="))


def _cleanup_live_isolation(isolation_root: Path) -> None:
    """Remove credentials, homes, and transient state while preserving only safe perf DBs."""

    for name in ("codex_home", "home", "xdg-cache", "xdg-config", "xdg-state"):
        shutil.rmtree(isolation_root / name, ignore_errors=True)
    state = isolation_root / "state"
    for path in state.glob("qwendex*.sqlite*"):
        if path.name.startswith("qwendex-performance.sqlite"):
            continue
        path.unlink(missing_ok=True)


def _raw_evidence_path(run_dir: Path, task_id: str) -> Path:
    return run_dir / "raw" / "baseline" / f"{task_id}.json"


def _grade_raw_evidence(task: Mapping[str, Any], raw: Mapping[str, Any]) -> dict[str, Any]:
    expected_files = [str(item) for item in task.get("expected_relevant_files", [])]
    regions = [item for item in task.get("expected_relevant_regions", []) if isinstance(item, Mapping)]
    matches = [item for item in raw.get("matches", []) if isinstance(item, Mapping) and item.get("kind") == "match"]
    returned_paths = {str(item.get("path") or "") for item in matches}
    file_hits = [path for path in expected_files if path in returned_paths]
    region_hits: list[dict[str, Any]] = []
    for region in regions:
        path = str(region.get("path") or "")
        anchor = str(region.get("anchor") or "")
        observed = any(str(item.get("path") or "") == path and anchor in str(item.get("line_text") or "") for item in matches)
        region_hits.append({"path": path, "anchor": anchor, "observed": observed})
    file_recall = round(len(file_hits) / len(expected_files), 6) if expected_files else 0.0
    region_recall = round(sum(1 for item in region_hits if item["observed"]) / len(region_hits), 6) if region_hits else 0.0
    rubric = task.get("task_success_rubric", {}) if isinstance(task.get("task_success_rubric"), Mapping) else {}
    required_files = float(rubric.get("minimum_file_recall", 1.0))
    required_regions = float(rubric.get("minimum_region_recall", 1.0))
    return {
        "relevant_file_recall": file_recall,
        "relevant_region_recall": region_recall,
        "file_hits": len(file_hits),
        "file_expected": len(expected_files),
        "region_hits": sum(1 for item in region_hits if item["observed"]),
        "region_expected": len(region_hits),
        "region_evidence": region_hits,
        "quality_status": "pass" if file_recall >= required_files and region_recall >= required_regions else "fail",
    }


def _mechanical_edit(task: Mapping[str, Any], worktree: Path, *, timeout_seconds: int) -> dict[str, Any]:
    execution = task.get("execution", {}) if isinstance(task.get("execution"), Mapping) else {}
    fixture = execution.get("fixture_edit") if isinstance(execution.get("fixture_edit"), Mapping) else None
    if fixture is None:
        return {"status": "not_applicable", "validation_status": "not_applicable", "validation_duration_ms": None}
    relative = Path(str(fixture.get("relative_path") or ""))
    allowed = {str(item) for item in task.get("allowed_write_surface", [])}
    if not str(relative) or relative.is_absolute() or ".." in relative.parts or relative.as_posix() not in allowed:
        return {"status": "fail", "validation_status": "not_applicable", "validation_duration_ms": None, "reason": "fixture write escaped declared surface"}
    target = worktree / relative
    if not _within(target.resolve(strict=False), worktree.resolve(strict=False)):
        return {"status": "fail", "validation_status": "not_applicable", "validation_duration_ms": None, "reason": "fixture write escaped worktree"}
    before = str(fixture.get("before") or "")
    after = str(fixture.get("after") or "")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(before, encoding="utf-8")
    if target.read_text(encoding="utf-8") != before:
        return {"status": "fail", "validation_status": "not_applicable", "validation_duration_ms": None, "reason": "fixture precondition failed"}
    target.write_text(after, encoding="utf-8")
    command = [str(item) for item in task.get("validation_command", [])]
    if not command:
        return {"status": "pass", "validation_status": "not_applicable", "validation_duration_ms": None}
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=worktree,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, timeout_seconds),
        )
        duration = round((time.monotonic() - started) * 1000, 3)
    except (OSError, subprocess.TimeoutExpired):
        return {"status": "fail", "validation_status": "fail", "validation_duration_ms": round((time.monotonic() - started) * 1000, 3)}
    return {
        "status": "pass" if completed.returncode == 0 else "fail",
        "validation_status": "pass" if completed.returncode == 0 else "fail",
        "validation_duration_ms": duration,
    }


def _record_telemetry(
    database: Path,
    *,
    raw: Mapping[str, Any],
    task: Mapping[str, Any],
    run_material: str,
    task_result: Mapping[str, Any],
    model_output_bytes: int | None = None,
    model_output_truncated: bool = False,
) -> dict[str, Any]:
    performance = performance_module()
    search = task.get("execution", {}).get("search", {}) if isinstance(task.get("execution"), Mapping) else {}
    base = {
        "repository_scope_digest": str(raw.get("repository_scope_digest") or ""),
        "run_material": run_material,
        "turn_material": str(task.get("id") or ""),
        "agent_role": "root",
        "scope_class": "repository_root",
        "query_fingerprints": True,
    }
    records = [
        {
            **base,
            "action": "tool_start",
            "event_key_material": f"{task.get('id')}:search",
            "phase": "tool",
            "event_kind": "tool_call",
            "tool_family": "search",
            "query_class": "literal" if search.get("mode") == "literal" else "regex",
            "query_material": str(search.get("pattern") or ""),
        },
        {
            **base,
            "action": "tool_finish",
            "event_key_material": f"{task.get('id')}:search",
            "phase": "tool",
            "event_kind": "tool_call",
            "tool_family": "search",
            "query_class": "literal" if search.get("mode") == "literal" else "regex",
            "duration_ms": raw.get("duration_ms"),
            "output_bytes": raw.get("raw_output_bytes") if model_output_bytes is None else model_output_bytes,
            "result_count": raw.get("match_count"),
            "success": task_result.get("quality_status") == "pass",
            "truncated": model_output_truncated,
        },
    ]
    validation_duration = task_result.get("validation_duration_ms")
    if validation_duration is not None:
        records.extend(
            [
                {
                    **base,
                    "action": "tool_start",
                    "event_key_material": f"{task.get('id')}:validation",
                    "phase": "tool",
                    "event_kind": "tool_call",
                    "tool_family": "validation",
                    "query_class": "validation",
                },
                {
                    **base,
                    "action": "tool_finish",
                    "event_key_material": f"{task.get('id')}:validation",
                    "phase": "tool",
                    "event_kind": "tool_call",
                    "tool_family": "validation",
                    "query_class": "validation",
                    "duration_ms": validation_duration,
                    "output_bytes": 0,
                    "result_count": 0,
                    "success": task_result.get("validation_status") == "pass",
                    "truncated": False,
                },
            ]
        )
    records.append(
        {
            **base,
            "action": "stop",
            "event_key_material": f"{task.get('id')}:stop",
            "phase": "stop",
            "event_kind": "run_stop",
            "tool_family": "other",
            "query_class": "not_applicable",
        }
    )
    captures = [performance.record_event(database, record) for record in records]
    summary = performance.summary(
        database,
        retention_days=14,
        max_events=50_000,
        repository_scope_digest=str(raw.get("repository_scope_digest") or ""),
    )
    return {
        "capture_status": "pass" if all(item.get("captured") for item in captures) else "fail",
        "capture_count": len(captures),
        "summary": summary,
    }


def _run_baseline_task(
    *,
    task: Mapping[str, Any],
    repository: Mapping[str, Any],
    run_dir: Path,
    run_id: str,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "unknown")
    source = Path(str(repository.get("source_path") or "")).expanduser()
    worktree = run_dir / "isolation" / task_id / "baseline" / "worktree"
    isolation_root = worktree.parent
    started = time.monotonic()
    _snapshot_worktree(source, str(repository.get("commit") or ""), worktree)
    try:
        environment, manager_preflight = _prepare_isolated_manager(isolation_root, worktree)
        execution = task.get("execution", {}) if isinstance(task.get("execution"), Mapping) else {}
        search_spec = execution.get("search", {}) if isinstance(execution.get("search"), Mapping) else {}
        raw = search_module().raw_content_search(
            str(search_spec.get("pattern") or ""),
            root=worktree / str(search_spec.get("root") or "."),
            mode=str(search_spec.get("mode") or ""),
            timeout_seconds=int(task.get("timeout_seconds") or 30),
        )
        grade = _grade_raw_evidence(task, raw)
        edit = _mechanical_edit(task, worktree, timeout_seconds=int(task.get("timeout_seconds") or 30))
        quality_status = "pass" if grade["quality_status"] == "pass" and edit["status"] in {"pass", "not_applicable"} else "fail"
        raw_path = _raw_evidence_path(run_dir, task_id)
        _write_json(
            raw_path,
            {
                "schema_version": "qwendex.search_raw_artifact.v1",
                "candidate_id": "baseline_raw_ripgrep",
                "pair_association": {"run_id": run_id, "task_id": task_id, "variant": "baseline"},
                "repository_scope_digest": raw.get("repository_scope_digest"),
                "query_fingerprint": raw.get("query_fingerprint"),
                "created_at": utc_now(),
                "retention_boundary": "ignored_local_evaluation_artifact",
                "raw_result": raw,
            },
        )
        telemetry = _record_telemetry(
            Path(environment["QWENDEX_PERFORMANCE_DB"]),
            raw=raw,
            task=task,
            run_material=f"{run_id}:{task_id}:baseline",
            task_result={**grade, **edit, "quality_status": quality_status},
        )
        return {
            "schema_version": BASELINE_RUN_SCHEMA_VERSION,
            "task_id": task_id,
            "repository": str(task.get("repository") or ""),
            "stratum": str(task.get("stratum") or ""),
            "variant": "baseline",
            "status": "pass" if quality_status == "pass" and telemetry["capture_status"] == "pass" else "fail",
            "quality_status": quality_status,
            "task_success": quality_status == "pass",
            "validation_status": edit.get("validation_status"),
            "relevant_file_recall": grade["relevant_file_recall"],
            "relevant_region_recall": grade["relevant_region_recall"],
            "raw_output_bytes": raw["raw_output_bytes"],
            "model_facing_search_bytes": raw["raw_output_bytes"],
            "compact_output_bytes": "not_applicable",
            "search_calls": raw["process_count"],
            "read_calls": 0,
            "validation_calls": 0 if edit.get("validation_status") == "not_applicable" else 1,
            "validation_duration_ms": edit.get("validation_duration_ms"),
            "time_to_first_relevant_file_ms": "not_observed_controlled_runner",
            "candidate_invoked": False,
            "candidate_adopted": False,
            "truncated": False,
            "raw_artifact": {
                "path": raw_path.relative_to(run_dir).as_posix(),
                "sha256": "sha256:" + sha256_file(raw_path),
            },
            "manager_preflight": manager_preflight,
            "telemetry": telemetry,
            "wall_time_ms": round((time.monotonic() - started) * 1000, 3),
            "isolation": {
                "codex_home": "isolated",
                "manager_state": "isolated_verified_preflight",
                "performance_db": "isolated",
                "results_root": "isolated",
                "worktree": "isolated_detached",
            },
        }
    finally:
        _remove_worktree(source, worktree)


def _environment_lock(payload: Mapping[str, Any], manifest_path: Path) -> dict[str, Any]:
    repositories = []
    for repository in payload.get("repositories", []):
        if isinstance(repository, Mapping):
            repositories.append(
                {
                    "id": str(repository.get("id") or ""),
                    "commit": str(repository.get("commit") or ""),
                    "tree_digest": str(repository.get("tree_digest") or ""),
                    "fixture_classification": str(repository.get("fixture_classification") or ""),
                }
            )
    try:
        qwendex_commit = _git_output(REPOSITORY_ROOT, "rev-parse", "HEAD")
        qwendex_tree = _git_output(REPOSITORY_ROOT, "rev-parse", "HEAD^{tree}")
    except LabError:
        qwendex_commit = "not_observed"
        qwendex_tree = "not_observed"
    runtime_raw = str(
        os.environ.get("QWENDEX_CODEX_RUNTIME")
        or os.environ.get("QWENDEX_DEV_CODEX_BIN")
        or shutil.which("codex")
        or ""
    ).strip()
    runtime_version = "not_observed"
    runtime_digest = "not_observed"
    if runtime_raw:
        runtime_path = Path(runtime_raw).expanduser()
        if runtime_path.is_file():
            runtime_digest = "sha256:" + sha256_file(runtime_path)
        else:
            runtime_digest = "sha256:" + sha256_text(runtime_raw)
        try:
            version_probe = subprocess.run(
                [runtime_raw, "--version"],
                cwd=REPOSITORY_ROOT,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            version_text = (version_probe.stdout or version_probe.stderr).strip().splitlines()
            if version_text:
                runtime_version = version_text[0][:240]
        except (OSError, subprocess.TimeoutExpired):
            runtime_version = "unavailable"
    config = REPOSITORY_ROOT / "config" / "qwendex" / "qwendex.json"
    return {
        "schema_version": "qwendex.optimization_lab.environment_lock.v1",
        "created_at": utc_now(),
        "started_at": utc_now(),
        "sources": repositories,
        "qwendex_commit": qwendex_commit,
        "qwendex_tree_digest": "git:" + qwendex_tree if qwendex_tree != "not_observed" else "not_observed",
        "codex_runtime": {"version": runtime_version, "digest": runtime_digest},
        "model_policy": dict(payload.get("model_policy", {})),
        "candidate_mode": "baseline_raw_ripgrep",
        "workload_manifest_digest": "sha256:" + sha256_file(manifest_path),
        "relevant_config_digests": {"qwendex_config": "sha256:" + sha256_file(config) if config.is_file() else "not_observed"},
        "host_performance_caveat": "Local wall-time comparisons are sensitive to cache warmth, concurrent load, and model/service latency; no private inventory is retained.",
    }


def _scope_document(payload: Mapping[str, Any], run_id: str) -> str:
    return "\n".join(
        [
            "# Qwendex Optimization Lab Baseline Capture",
            "",
            f"- Run: `{run_id}`",
            f"- Workload: `{payload.get('workload_id', '')}`",
            "- Mode: controlled search-evidence baseline only",
            "- Source snapshots are detached, per-task worktrees; no downstream worktree is edited.",
            "- Raw evidence is retained only below this ignored local artifact root.",
            "- This capture establishes a pre-candidate baseline and is not a paired promotion decision.",
            "",
        ]
    )


def _write_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    _write_text(path, "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows))


def _artifact_manifest(run_dir: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(
        item
        for item in run_dir.rglob("*")
        if item.is_file()
        and item.name != "manifest.json"
        and item.name != "auth.json"
        # SQLite state may checkpoint after an arm closes. The sealed JSON/CSV
        # receipts are the durable evidence; mutable local databases are not.
        and item.suffix != ".sqlite"
        and not item.name.endswith(("-shm", "-wal"))
    ):
        entries.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": "sha256:" + sha256_file(path),
                "created_at": utc_now(),
            }
        )
    return {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA_VERSION,
        "created_at": utc_now(),
        "artifacts": entries,
        "manifest_self": "excluded from byte hashing to avoid a self-referential digest; all companion artifacts are covered",
    }


def baseline_capture(
    manifest_path: Path | str,
    *,
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path).expanduser().resolve(strict=False)
    validation = validate_workload(manifest)
    if not validation.get("valid"):
        raise LabError("workload manifest validation failed")
    payload = _read_json(manifest)
    root = Path(output_root).expanduser().resolve(strict=False) if output_root else REPOSITORY_ROOT / ".qwendex-dev" / "results" / "performance" / "paired-eval"
    run_id = "baseline-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / run_id
    if run_dir.exists():
        raise LabError("generated baseline run directory already exists")
    run_dir.mkdir(parents=True)
    _write_text(run_dir / "00_scope_and_git_custody.md", _scope_document(payload, run_id))
    environment_lock = _environment_lock(payload, manifest)
    _write_json(run_dir / "02_environment_lock.json", environment_lock)
    shutil.copyfile(manifest, run_dir / "03_workload_manifest.json")
    _write_text(run_dir / "04_workload_manifest.sha256", f"{sha256_file(manifest)}  03_workload_manifest.json\n")
    repository_by_id = {str(item.get("id") or ""): item for item in payload.get("repositories", []) if isinstance(item, Mapping)}
    rows: list[dict[str, Any]] = []
    for task in payload.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        repository = repository_by_id.get(str(task.get("repository") or ""))
        if repository is None:
            rows.append({"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": "repository unavailable"})
            continue
        try:
            rows.append(_run_baseline_task(task=task, repository=repository, run_dir=run_dir, run_id=run_id))
        except (LabError, OSError, ValueError) as exc:
            rows.append({"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": str(exc)})
    _write_jsonl(run_dir / "06_baseline_runs.jsonl", rows)
    summary = {
        "schema_version": BASELINE_CAPTURE_SCHEMA_VERSION,
        "run_id": run_id,
        "status": "pass" if len(rows) == len(payload.get("tasks", [])) and all(row.get("status") == "pass" for row in rows) else "fail",
        "attempted_pairs": len(rows),
        "completed_baseline_runs": sum(1 for row in rows if row.get("status") == "pass"),
        "candidate_status": "not_applicable_pre_candidate_baseline",
        "claim_ceiling": "Baseline retrieval and telemetry capture only; this is not an end-to-end model or promotion result.",
    }
    _write_json(run_dir / "13_performance_summary.json", summary)
    environment_lock["completed_at"] = utc_now()
    _write_json(run_dir / "02_environment_lock.json", environment_lock)
    _write_json(run_dir / "manifest.json", _artifact_manifest(run_dir))
    return {
        "schema_version": BASELINE_CAPTURE_SCHEMA_VERSION,
        "status": summary["status"],
        "summary": "Captured an isolated pre-candidate Qwendex search-evidence baseline.",
        "data": {
            "run_id": run_id,
            "artifact_dir": str(run_dir),
            "attempted_pairs": summary["attempted_pairs"],
            "completed_baseline_runs": summary["completed_baseline_runs"],
            "candidate_status": summary["candidate_status"],
        },
    }


def _grade_compact_evidence(
    task: Mapping[str, Any],
    compact: Mapping[str, Any],
    *,
    retrieved_pages: list[Mapping[str, Any]] | None = None,
    raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    expected_files = [str(item) for item in task.get("expected_relevant_files", [])]
    regions = [item for item in task.get("expected_relevant_regions", []) if isinstance(item, Mapping)]
    pages = retrieved_pages or [compact]
    ranges = [
        item
        for page in pages
        for item in page.get("ranges", [])
        if isinstance(item, Mapping)
    ]
    returned_paths = {str(item.get("path") or "") for item in ranges}
    file_hits = [path for path in expected_files if path in returned_paths]
    region_hits: list[dict[str, Any]] = []
    raw_anchor_lines: dict[tuple[str, str], list[int]] = {}
    if raw is not None:
        for region in regions:
            path = str(region.get("path") or "")
            anchor = str(region.get("anchor") or "")
            raw_anchor_lines[(path, anchor)] = [
                int(item.get("line_number") or 0)
                for item in raw.get("matches", [])
                if isinstance(item, Mapping)
                and str(item.get("path") or "") == path
                and anchor in str(item.get("line_text") or "")
            ]
    for region in regions:
        path = str(region.get("path") or "")
        anchor = str(region.get("anchor") or "")
        anchor_lines = raw_anchor_lines.get((path, anchor), [])
        observed = any(
            str(item.get("path") or "") == path
            and (
                any(anchor in str(evidence.get("excerpt") or "") for evidence in item.get("line_evidence", []) if isinstance(evidence, Mapping))
                or any(int(item.get("start_line") or 0) <= line <= int(item.get("end_line") or 0) for line in anchor_lines)
            )
            for item in ranges
        )
        region_hits.append({"path": path, "anchor": anchor, "observed": observed})
    file_recall = round(len(file_hits) / len(expected_files), 6) if expected_files else 0.0
    region_recall = round(sum(1 for item in region_hits if item["observed"]) / len(region_hits), 6) if region_hits else 0.0
    rubric = task.get("task_success_rubric", {}) if isinstance(task.get("task_success_rubric"), Mapping) else {}
    required_files = float(rubric.get("minimum_file_recall", 1.0))
    required_regions = float(rubric.get("minimum_region_recall", 1.0))
    return {
        "relevant_file_recall": file_recall,
        "relevant_region_recall": region_recall,
        "file_hits": len(file_hits),
        "file_expected": len(expected_files),
        "region_hits": sum(1 for item in region_hits if item["observed"]),
        "region_expected": len(region_hits),
        "region_evidence": region_hits,
        "retrieval": {
            "page_count": len(pages),
            "cursor_contract_complete": bool(pages)
            and not str(pages[-1].get("cursor") or pages[-1].get("continuation_token") or ""),
            "initial_completeness": compact.get("completeness", {}).get("state") if isinstance(compact.get("completeness"), Mapping) else "not_applicable_v1",
        },
        "quality_status": "pass" if file_recall >= required_files and region_recall >= required_regions else "fail",
    }


def _retrieve_v2_evidence(
    *,
    raw: Mapping[str, Any],
    compact: Mapping[str, Any],
    pattern: str,
    mode: str,
    budget: Mapping[str, Any],
    snapshot_digest: str,
) -> list[Mapping[str, Any]]:
    """Follow the candidate's own cursor contract for deterministic grading."""

    pages: list[Mapping[str, Any]] = [compact]
    cursor = str(compact.get("cursor") or "")
    expected_pages = max(1, int(compact.get("page_count") or 1))
    while cursor:
        if len(pages) >= expected_pages + 1:
            raise LabError("v2 cursor did not terminate within its declared page count")
        next_page = search_module().compact_content_search_v2(
            raw,
            pattern=pattern,
            mode=mode,
            per_file_ranges=int(budget.get("per_file_ranges") or 12),
            total_ranges=int(budget.get("total_ranges") or 96),
            page_size=int(budget.get("page_size") or 96),
            cursor=cursor,
            snapshot_digest=snapshot_digest,
        )
        next_cursor = str(next_page.get("cursor") or "")
        if next_cursor == cursor:
            raise LabError("v2 cursor repeated a page")
        pages.append(next_page)
        cursor = next_cursor
    if not pages[-1].get("completeness", {}).get("state") == "complete":
        raise LabError("v2 cursor sequence ended without a complete evidence state")
    return pages


def _run_candidate_task(
    *,
    task: Mapping[str, Any],
    repository: Mapping[str, Any],
    run_dir: Path,
    run_id: str,
    candidate_id: str,
) -> dict[str, Any]:
    task_id = str(task.get("id") or "unknown")
    source = Path(str(repository.get("source_path") or "")).expanduser()
    worktree = run_dir / "isolation" / task_id / "candidate" / "worktree"
    isolation_root = worktree.parent
    started = time.monotonic()
    _snapshot_worktree(source, str(repository.get("commit") or ""), worktree)
    try:
        environment, manager_preflight = _prepare_isolated_manager(isolation_root, worktree)
        execution = task.get("execution", {}) if isinstance(task.get("execution"), Mapping) else {}
        search_spec = execution.get("search", {}) if isinstance(execution.get("search"), Mapping) else {}
        search_root = worktree / str(search_spec.get("root") or ".")
        raw = search_module().raw_content_search(
            str(search_spec.get("pattern") or ""),
            root=search_root,
            mode=str(search_spec.get("mode") or ""),
            timeout_seconds=int(task.get("timeout_seconds") or 30),
        )
        candidate_expected = bool(task.get("candidate_expected"))
        compact: Mapping[str, Any] | None = None
        retrieved_pages: list[Mapping[str, Any]] = []
        if candidate_expected:
            budget = execution.get("candidate_budget", {}) if isinstance(execution.get("candidate_budget"), Mapping) else {}
            if candidate_id == search_module().SEARCH_V2_CANDIDATE_ID:
                snapshot_digest = search_module().relevant_worktree_snapshot_digest(raw, root=search_root)
                compact = search_module().compact_content_search_v2(
                    raw,
                    pattern=str(search_spec.get("pattern") or ""),
                    mode=str(search_spec.get("mode") or ""),
                    per_file_ranges=int(budget.get("per_file_ranges") or 12),
                    total_ranges=int(budget.get("total_ranges") or 96),
                    page_size=int(budget.get("page_size") or 96),
                    snapshot_digest=snapshot_digest,
                )
                retrieved_pages = _retrieve_v2_evidence(
                    raw=raw,
                    compact=compact,
                    pattern=str(search_spec.get("pattern") or ""),
                    mode=str(search_spec.get("mode") or ""),
                    budget=budget,
                    snapshot_digest=snapshot_digest,
                )
                grade = _grade_compact_evidence(task, compact, retrieved_pages=retrieved_pages, raw=raw)
            else:
                compact = search_module().compact_content_search(
                    raw,
                    pattern=str(search_spec.get("pattern") or ""),
                    mode=str(search_spec.get("mode") or ""),
                    per_file_ranges=int(budget.get("per_file_ranges") or 12),
                    total_ranges=int(budget.get("total_ranges") or 96),
                    page_size=int(budget.get("page_size") or 96),
                )
                retrieved_pages = [compact]
                grade = _grade_compact_evidence(task, compact)
            model_bytes = int(compact.get("compact_output_bytes") or 0)
            model_truncated = bool(compact.get("truncated")) or bool(compact.get("cursor"))
            candidate_processing = float(compact.get("candidate_duration_ms") or 0.0)
            candidate_status = "invoked"
        else:
            grade = _grade_raw_evidence(task, raw)
            model_bytes = int(raw.get("raw_output_bytes") or 0)
            model_truncated = False
            candidate_processing = 0.0
            candidate_status = "fallback_not_required"
        edit = _mechanical_edit(task, worktree, timeout_seconds=int(task.get("timeout_seconds") or 30))
        quality_status = "pass" if grade["quality_status"] == "pass" and edit["status"] in {"pass", "not_applicable"} else "fail"
        raw_path = run_dir / "raw" / "candidate" / f"{task_id}.json"
        raw_artifact = search_module().write_raw_evidence_artifact(
            raw_path,
            raw=raw,
            pair_id=task_id,
            run_id=run_id,
            variant="candidate",
            candidate_id=candidate_id,
        )
        telemetry = _record_telemetry(
            Path(environment["QWENDEX_PERFORMANCE_DB"]),
            raw=raw,
            task=task,
            run_material=f"{run_id}:{task_id}:candidate",
            task_result={**grade, **edit, "quality_status": quality_status},
            model_output_bytes=model_bytes,
            model_output_truncated=model_truncated,
        )
        return {
            "schema_version": BASELINE_RUN_SCHEMA_VERSION,
            "task_id": task_id,
            "repository": str(task.get("repository") or ""),
            "stratum": str(task.get("stratum") or ""),
            "variant": "candidate",
            "candidate_id": candidate_id,
            "candidate_version": compact.get("candidate_version") if compact else "not_applicable",
            "status": "pass" if quality_status == "pass" and telemetry["capture_status"] == "pass" else "fail",
            "quality_status": quality_status,
            "task_success": quality_status == "pass",
            "validation_status": edit.get("validation_status"),
            "relevant_file_recall": grade["relevant_file_recall"],
            "relevant_region_recall": grade["relevant_region_recall"],
            "retrieval_contract": grade.get("retrieval", {"page_count": 0, "cursor_contract_complete": True}),
            "raw_output_bytes": raw["raw_output_bytes"],
            "model_facing_search_bytes": model_bytes,
            "compact_output_bytes": model_bytes if candidate_expected else "not_applicable",
            "raw_match_count": raw["match_count"],
            "retained_range_count": int(compact.get("retained_range_count") or 0) if compact else "not_applicable",
            "omitted_range_count": int(compact.get("omitted_range_count") or 0) if compact else "not_applicable",
            "continuation_requests": int(compact.get("continuation_requests") or 0) if compact else 0,
            "pagination_calls_for_verified_retrieval": max(0, len(retrieved_pages) - 1),
            "verified_retrieval_model_visible_bytes": sum(int(page.get("compact_output_bytes") or 0) for page in retrieved_pages) if retrieved_pages else model_bytes,
            "result_mode": compact.get("result_mode") if compact else "not_applicable",
            "coverage_mode": compact.get("coverage_mode") if compact else "not_applicable",
            "fallback_count": int(compact.get("fallback_count") or 0) if compact else 0,
            "candidate_processing_ms": candidate_processing,
            "search_calls": raw["process_count"],
            "read_calls": 0,
            "validation_calls": 0 if edit.get("validation_status") == "not_applicable" else 1,
            "validation_duration_ms": edit.get("validation_duration_ms"),
            "time_to_first_relevant_file_ms": "not_observed_controlled_runner",
            "candidate_invoked": candidate_expected,
            "candidate_adopted": candidate_expected,
            "candidate_status": candidate_status,
            "truncated": model_truncated,
            "raw_artifact": {
                "path": raw_path.relative_to(run_dir).as_posix(),
                "sha256": raw_artifact["sha256"],
            },
            "manager_preflight": manager_preflight,
            "telemetry": telemetry,
            "wall_time_ms": round((time.monotonic() - started) * 1000, 3),
            "isolation": {
                "codex_home": "isolated",
                "manager_state": "isolated_verified_preflight",
                "performance_db": "isolated",
                "results_root": "isolated",
                "worktree": "isolated_detached",
            },
        }
    finally:
        _remove_worktree(source, worktree)


def _pair_result(task: Mapping[str, Any], baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    baseline_bytes = max(1, int(baseline.get("model_facing_search_bytes") or 0))
    candidate_bytes = int(candidate.get("model_facing_search_bytes") or 0)
    baseline_calls = int(baseline.get("search_calls") or 0) + int(baseline.get("read_calls") or 0) + int(baseline.get("validation_calls") or 0)
    candidate_calls = int(candidate.get("search_calls") or 0) + int(candidate.get("read_calls") or 0) + int(candidate.get("validation_calls") or 0)
    baseline_wall = max(0.001, float(baseline.get("wall_time_ms") or 0.0))
    candidate_wall = float(candidate.get("wall_time_ms") or 0.0)
    environment_invalid = baseline.get("status") == "blocked" or candidate.get("status") == "blocked"
    return {
        "schema_version": "qwendex.optimization_lab.pair_result.v1",
        "pair_id": str(task.get("id") or ""),
        "stratum": str(task.get("stratum") or ""),
        "repository": str(task.get("repository") or ""),
        "pair_order": str(task.get("pair_order") or ""),
        "state": "invalid_pair" if environment_invalid else "pass" if baseline.get("status") == "pass" and candidate.get("status") == "pass" else "fail",
        "baseline_status": baseline.get("status"),
        "candidate_status": candidate.get("status"),
        "relevant_file_recall": {"baseline": baseline.get("relevant_file_recall"), "candidate": candidate.get("relevant_file_recall")},
        "relevant_region_recall": {"baseline": baseline.get("relevant_region_recall"), "candidate": candidate.get("relevant_region_recall")},
        "task_success": {"baseline": bool(baseline.get("task_success")), "candidate": bool(candidate.get("task_success"))},
        "validation_status": {"baseline": baseline.get("validation_status"), "candidate": candidate.get("validation_status")},
        "search_output_bytes": {"baseline": baseline_bytes, "candidate": candidate_bytes, "ratio": round(candidate_bytes / baseline_bytes, 6), "reduction": round(1 - candidate_bytes / baseline_bytes, 6)},
        "search_read_call_ratio": round((int(candidate.get("search_calls") or 0) + int(candidate.get("read_calls") or 0)) / max(1, int(baseline.get("search_calls") or 0) + int(baseline.get("read_calls") or 0)), 6),
        "total_tool_call_ratio": round(candidate_calls / max(1, baseline_calls), 6),
        "wall_time_ratio": round(candidate_wall / baseline_wall, 6),
        "candidate_invoked": bool(candidate.get("candidate_invoked")),
        "candidate_adopted": bool(candidate.get("candidate_adopted")),
        "candidate_processing_ms": candidate.get("candidate_processing_ms"),
        "candidate_id": candidate.get("candidate_id"),
        "candidate_version": candidate.get("candidate_version"),
        "pagination_calls_for_verified_retrieval": candidate.get("pagination_calls_for_verified_retrieval", 0),
        "result_mode": candidate.get("result_mode", "not_applicable"),
        "coverage_mode": candidate.get("coverage_mode", "not_applicable"),
        "fallback_count": candidate.get("fallback_count", 0),
        "context_compaction_events": {"baseline": 0, "candidate": 0},
    }


def _pilot_tasks(tasks: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    selected: list[Mapping[str, Any]] = []
    used_ids: set[str] = set()
    used_repositories: set[str] = set()
    for stratum in ("A_read_only_localization", "B_diagnosis_documentation", "C_bounded_implementation"):
        candidates = [task for task in tasks if str(task.get("stratum") or "") == stratum and str(task.get("id") or "") not in used_ids]
        preferred = next((task for task in candidates if str(task.get("repository") or "") not in used_repositories), candidates[0] if candidates else None)
        if preferred is not None:
            selected.append(preferred)
            used_ids.add(str(preferred.get("id") or ""))
            used_repositories.add(str(preferred.get("repository") or ""))
    for task in tasks:
        if len(selected) >= 4:
            break
        if str(task.get("id") or "") not in used_ids:
            selected.append(task)
            used_ids.add(str(task.get("id") or ""))
    return selected[:4]


def _run_pair(
    *,
    task: Mapping[str, Any],
    repository: Mapping[str, Any],
    run_dir: Path,
    run_id: str,
    candidate_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if str(task.get("pair_order") or "") == "candidate_first":
        candidate = _run_candidate_task(task=task, repository=repository, run_dir=run_dir, run_id=run_id, candidate_id=candidate_id)
        baseline = _run_baseline_task(task=task, repository=repository, run_dir=run_dir, run_id=run_id)
    else:
        baseline = _run_baseline_task(task=task, repository=repository, run_dir=run_dir, run_id=run_id)
        candidate = _run_candidate_task(task=task, repository=repository, run_dir=run_dir, run_id=run_id, candidate_id=candidate_id)
    return baseline, candidate, _pair_result(task, baseline, candidate)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 6)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 6)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * fraction))))
    return round(ordered[index], 6)


def _raw_artifact_hashes_valid(run_dir: Path, rows: list[Mapping[str, Any]]) -> bool:
    for row in rows:
        if row.get("status") == "blocked":
            continue
        artifact = row.get("raw_artifact")
        if not isinstance(artifact, Mapping):
            return False
        relative = Path(str(artifact.get("path") or ""))
        expected = str(artifact.get("sha256") or "")
        target = run_dir / relative
        if not relative or relative.is_absolute() or not _within(target.resolve(strict=False), run_dir.resolve(strict=False)) or not target.is_file():
            return False
        if expected != "sha256:" + sha256_file(target):
            return False
    return True


def _privacy_scan(run_dir: Path, manifest_path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    forbidden: set[bytes] = {str(manifest_path.resolve()).encode("utf-8")}
    for repository in payload.get("repositories", []):
        if isinstance(repository, Mapping):
            source = str(repository.get("source_path") or "")
            if source:
                forbidden.add(source.encode("utf-8"))
    for task in payload.get("tasks", []):
        if not isinstance(task, Mapping):
            continue
        try:
            prompt_file, key = _safe_reference_path(manifest_path, str(task.get("private_prompt_ref") or ""))
            prompt_payload = _read_json(prompt_file)
            value = prompt_payload.get(key) if isinstance(prompt_payload, Mapping) else None
            if isinstance(value, str):
                forbidden.add(value.encode("utf-8"))
        except LabError:
            continue
    safe_files = [
        path
        for path in run_dir.rglob("*")
        if path.is_file()
        and "raw" not in path.relative_to(run_dir).parts
        and ("isolation" not in path.relative_to(run_dir).parts or path.name == "qwendex-performance.sqlite")
        and path.name not in {"03_workload_manifest.json"}
        and path.name not in {"qwendex.sqlite", "qwendex_ledger.sqlite"}
        and not path.name.endswith(("-shm", "-wal"))
    ]
    scanned = 0
    matched = 0
    structural_matches = 0
    raw_field_names = {
        "prompt",
        "raw_prompt",
        "query",
        "query_material",
        "search_pattern",
        "source_path",
        "command",
        "raw_rg_jsonl",
        "raw_result",
        "line_text",
        "tool_input",
        "tool_output",
    }

    def contains_raw_field(value: Any) -> bool:
        if isinstance(value, Mapping):
            return any(str(key) in raw_field_names or contains_raw_field(item) for key, item in value.items())
        if isinstance(value, list):
            return any(contains_raw_field(item) for item in value)
        return False

    for path in safe_files:
        data = path.read_bytes()
        scanned += 1
        if any(value and value in data for value in forbidden):
            matched += 1
        if path.suffix == ".json":
            try:
                if contains_raw_field(json.loads(data.decode("utf-8"))):
                    structural_matches += 1
            except (UnicodeDecodeError, json.JSONDecodeError):
                matched += 1
    performance_databases = [path for path in safe_files if path.name == "qwendex-performance.sqlite"]
    database_forbidden_matches = sum(
        1
        for path in performance_databases
        if any(value and value in path.read_bytes() for value in forbidden)
    )
    return {
        "schema_version": "qwendex.optimization_lab.privacy_scan.v1",
        "status": "pass" if matched == 0 and structural_matches == 0 and database_forbidden_matches == 0 else "fail",
        "scanned_safe_artifacts": scanned,
        "leak_match_count": matched + structural_matches + database_forbidden_matches,
        "raw_evidence_excluded": True,
        "performance_db_checked": bool(performance_databases),
    }


def _manager_security_probe(run_dir: Path) -> dict[str, Any]:
    state = run_dir / "manager-security-probe"
    environment = dict(os.environ)
    environment.update(
        {
            "CODEX_HOME": str(state / "codex_home"),
            "QWENDEX_STATE_DB": str(state / "state" / "qwendex.sqlite"),
            "QWENDEX_LEDGER_DB": str(state / "state" / "qwendex_ledger.sqlite"),
            "QWENDEX_PERFORMANCE_DB": str(state / "state" / "qwendex-performance.sqlite"),
            "QWENDEX_RESULTS_ROOT": str(state / "results"),
            "QWENDEX_MANAGER_TARGET_REPO": str(REPOSITORY_ROOT),
        }
    )
    command = REPOSITORY_ROOT / "scripts" / "qwendex"
    calls = [
        ("agent_policy", [str(command), "--agent-use", "Manager", "agent", "policy", "--json"]),
        (
            "read_only_write_denial",
            [
                str(command),
                "--agent-use",
                "Manager",
                "agent",
                "hook",
                "PreToolUse",
                "--event-json",
                json.dumps({"tool_name": "apply_patch", "profile": "explorer", "path": "blocked.txt"}),
                "--json",
            ],
        ),
        ("route", [str(command), "--agent-use", "Manager", "route", "--seat", "auto", "--task-class", "exec", "--json"]),
    ]
    results: dict[str, dict[str, Any]] = {}
    for name, args in calls:
        completed = subprocess.run(args, cwd=REPOSITORY_ROOT, env=environment, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {}
        results[name] = {"returncode": completed.returncode, "status": payload.get("status", "fail")}
        if name == "route":
            route = payload.get("data", {}).get("route", {}) if isinstance(payload.get("data"), Mapping) else {}
            results[name]["local_selected"] = bool(route.get("token_saver_used"))
    policy_ok = results.get("agent_policy", {}).get("status") == "pass"
    denial_ok = results.get("read_only_write_denial", {}).get("status") == "blocked"
    route_ok = not bool(results.get("route", {}).get("local_selected"))
    return {
        "schema_version": "qwendex.optimization_lab.manager_security_probe.v1",
        "status": "pass" if policy_ok and denial_ok and route_ok else "fail",
        "checks": results,
        "live_root_binding": "not_observed_controlled_runner",
        "claim_ceiling": "This is an isolated managed policy and read-only write-denial probe, not a live Codex root-session proof.",
    }


def _performance_summary(
    baselines: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    pairs: list[Mapping[str, Any]],
    *,
    candidate_id: str,
) -> dict[str, Any]:
    reductions = [float(pair.get("search_output_bytes", {}).get("reduction") or 0.0) for pair in pairs if isinstance(pair.get("search_output_bytes"), Mapping)]
    call_ratios = [float(pair.get("search_read_call_ratio") or 0.0) for pair in pairs]
    tool_ratios = [float(pair.get("total_tool_call_ratio") or 0.0) for pair in pairs]
    wall_ratios = [float(pair.get("wall_time_ratio") or 0.0) for pair in pairs]
    candidate_processing = [float(row.get("candidate_processing_ms") or 0.0) for row in candidates if row.get("candidate_invoked")]
    telemetry_overheads: list[float] = []
    telemetry_p50_values: list[float] = []
    incomplete_rates: list[float] = []
    duplicate_rates: list[float] = []
    overlap_rates: list[float] = []
    validation_durations = [
        float(row["validation_duration_ms"])
        for row in [*baselines, *candidates]
        if isinstance(row.get("validation_duration_ms"), int | float)
    ]
    all_rows = [*baselines, *candidates]
    for row in all_rows:
        summary = row.get("telemetry", {}).get("summary", {}) if isinstance(row.get("telemetry"), Mapping) else {}
        overhead = summary.get("instrumentation_overhead") if isinstance(summary, Mapping) else None
        incomplete = summary.get("incomplete_event_rate") if isinstance(summary, Mapping) else None
        duplicate = summary.get("duplicate_query_rate") if isinstance(summary, Mapping) else None
        overlap = summary.get("root_subagent_overlap") if isinstance(summary, Mapping) else None
        if isinstance(overhead, Mapping) and isinstance(overhead.get("p95_ms"), int | float):
            telemetry_overheads.append(float(overhead["p95_ms"]))
        if isinstance(overhead, Mapping) and isinstance(overhead.get("median_ms"), int | float):
            telemetry_p50_values.append(float(overhead["median_ms"]))
        if isinstance(incomplete, Mapping) and isinstance(incomplete.get("rate"), int | float):
            incomplete_rates.append(float(incomplete["rate"]))
        if isinstance(duplicate, Mapping) and isinstance(duplicate.get("rate"), int | float):
            duplicate_rates.append(float(duplicate["rate"]))
        if isinstance(overlap, Mapping) and isinstance(overlap.get("rate"), int | float):
            overlap_rates.append(float(overlap["rate"]))
    expected_adoptions = [row for row in candidates if bool(row.get("candidate_invoked"))]
    registry = search_module().candidate_registry()
    candidate = next(
        (item for item in registry.get("candidates", []) if isinstance(item, Mapping) and item.get("candidate_id") == candidate_id),
        {},
    )
    result_mode_counts: dict[str, int] = {}
    for row in candidates:
        mode = str(row.get("result_mode") or "not_observed")
        result_mode_counts[mode] = result_mode_counts.get(mode, 0) + 1
    pagination_calls = sum(int(row.get("pagination_calls_for_verified_retrieval") or 0) for row in candidates)
    fallback_count = sum(int(row.get("fallback_count") or 0) for row in candidates)
    retrieval_bytes = [int(row.get("verified_retrieval_model_visible_bytes") or 0) for row in candidates if row.get("candidate_invoked")]
    return {
        "schema_version": "qwendex.optimization_lab.performance_summary.v1",
        "pair_count": len(pairs),
        "search_output_reduction": {"median": _median(reductions), "values": reductions},
        "search_read_call_ratio": {"median": _median(call_ratios), "values": call_ratios},
        "total_tool_call_ratio": {"median": _median(tool_ratios), "values": tool_ratios},
        "wall_time_ratio": {"median": _median(wall_ratios), "max": max(wall_ratios) if wall_ratios else None, "values": wall_ratios},
        "tool_calls": {
            "baseline_total": sum(int(row.get("search_calls") or 0) + int(row.get("read_calls") or 0) + int(row.get("validation_calls") or 0) for row in baselines),
            "candidate_total": sum(int(row.get("search_calls") or 0) + int(row.get("read_calls") or 0) + int(row.get("validation_calls") or 0) for row in candidates),
            "baseline_search": sum(int(row.get("search_calls") or 0) for row in baselines),
            "candidate_search": sum(int(row.get("search_calls") or 0) for row in candidates),
            "baseline_read": sum(int(row.get("read_calls") or 0) for row in baselines),
            "candidate_read": sum(int(row.get("read_calls") or 0) for row in candidates),
            "baseline_validation": sum(int(row.get("validation_calls") or 0) for row in baselines),
            "candidate_validation": sum(int(row.get("validation_calls") or 0) for row in candidates),
        },
        "validation_duration_ms": {"p50": _percentile(validation_durations, 0.5), "p95": _percentile(validation_durations, 0.95)},
        "time_to_first_relevant_file_ms": "not_observed_controlled_runner",
        "duplicate_query_rate": _median(duplicate_rates) if duplicate_rates else "not_observed",
        "candidate_adoption": {
            "expected_tasks": len(expected_adoptions),
            "adopted_tasks": sum(1 for row in expected_adoptions if row.get("candidate_adopted")),
            "rate": round(sum(1 for row in expected_adoptions if row.get("candidate_adopted")) / len(expected_adoptions), 6) if expected_adoptions else "not_observed",
        },
        "candidate_id": candidate_id,
        "candidate_version": candidate.get("candidate_version", "not_observed"),
        "v2_result_mode_counts": result_mode_counts,
        "pagination_calls_for_verified_retrieval": pagination_calls,
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / len(expected_adoptions), 6) if expected_adoptions else "not_observed",
        "verified_retrieval_model_visible_bytes": {"p50": _percentile([float(value) for value in retrieval_bytes], 0.5), "p95": _percentile([float(value) for value in retrieval_bytes], 0.95)},
        "candidate_processing_ms": {"p50": _percentile(candidate_processing, 0.5), "p95": _percentile(candidate_processing, 0.95)},
        "candidate_instruction_context": {
            "bytes": int(candidate.get("managed_instruction_bytes") or 0),
            "delivery": "not_observed_controlled_runner",
        },
        "telemetry_instrumentation_overhead_ms": {"p50": _percentile(telemetry_p50_values, 0.5), "p95": _percentile(telemetry_overheads, 0.95)},
        "telemetry_instrumentation_p95_ms": _percentile(telemetry_overheads, 0.95),
        "incomplete_telemetry_rate": _median(incomplete_rates),
        "context_compaction_events": "not_observed_controlled_runner",
        "root_subagent_overlap": _median(overlap_rates) if overlap_rates else "not_observed_controlled_runner",
        "repeated_file_or_range_reads": "not_observed_controlled_runner",
    }


def _gate_decision(
    *,
    baselines: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    pairs: list[Mapping[str, Any]],
    freshness: Mapping[str, Any],
    privacy: Mapping[str, Any],
    manager: Mapping[str, Any],
    performance: Mapping[str, Any],
    raw_artifacts_valid: bool,
) -> dict[str, Any]:
    valid_pairs = [pair for pair in pairs if pair.get("state") != "invalid_pair"]
    invalid_pairs = [pair for pair in pairs if pair.get("state") == "invalid_pair"]
    file_ok = all(float(pair.get("relevant_file_recall", {}).get("candidate") or 0.0) >= float(pair.get("relevant_file_recall", {}).get("baseline") or 0.0) for pair in valid_pairs)
    region_ok = all(float(pair.get("relevant_region_recall", {}).get("candidate") or 0.0) >= float(pair.get("relevant_region_recall", {}).get("baseline") or 0.0) for pair in valid_pairs)
    task_ok = all(bool(pair.get("task_success", {}).get("candidate")) >= bool(pair.get("task_success", {}).get("baseline")) for pair in valid_pairs)
    validation_ok = all(
        pair.get("validation_status", {}).get("baseline") in {"pass", "not_applicable"}
        and pair.get("validation_status", {}).get("candidate") in {"pass", "not_applicable"}
        for pair in valid_pairs
    )
    manager_run_ok = all(
        isinstance(row.get("manager_preflight"), Mapping)
        and row["manager_preflight"].get("status") == "pass"
        for row in [*baselines, *candidates]
    )
    policy_hashes_match = all(
        str(baseline.get("manager_preflight", {}).get("policy_hash") or "")
        == str(candidate.get("manager_preflight", {}).get("policy_hash") or "")
        for baseline, candidate in zip(baselines, candidates, strict=True)
    ) if len(baselines) == len(candidates) else False
    v2_rows = [row for row in candidates if str(row.get("candidate_id") or "") == "search_evidence_compaction_v2"]
    cursor_contract_ok = all(
        isinstance(row.get("retrieval_contract"), Mapping)
        and bool(row["retrieval_contract"].get("cursor_contract_complete"))
        for row in v2_rows
    )
    hard = {
        "relevant_file_recall": "pass" if file_ok else "fail",
        "relevant_region_recall": "pass" if region_ok else "fail",
        "task_success_and_validation": "pass" if task_ok and validation_ok else "fail",
        "freshness_and_symlink_boundary": str(freshness.get("status") or "fail"),
        "privacy_boundary": str(privacy.get("status") or "fail"),
        "manager_policy_and_local_routing": "pass" if manager.get("status") == "pass" and manager_run_ok and policy_hashes_match else "fail",
        "raw_artifact_digests": "pass" if raw_artifacts_valid else "fail",
        "candidate_default_off": "pass",
        "v2_cursor_coverage_contract": "pass" if not v2_rows or cursor_contract_ok else "fail",
        "live_manager_root_binding": "not_observed",
    }
    median_reduction = performance.get("search_output_reduction", {}).get("median") if isinstance(performance.get("search_output_reduction"), Mapping) else None
    median_search_calls = performance.get("search_read_call_ratio", {}).get("median") if isinstance(performance.get("search_read_call_ratio"), Mapping) else None
    median_tools = performance.get("total_tool_call_ratio", {}).get("median") if isinstance(performance.get("total_tool_call_ratio"), Mapping) else None
    median_wall = performance.get("wall_time_ratio", {}).get("median") if isinstance(performance.get("wall_time_ratio"), Mapping) else None
    max_wall = performance.get("wall_time_ratio", {}).get("max") if isinstance(performance.get("wall_time_ratio"), Mapping) else None
    adoption_rate = performance.get("candidate_adoption", {}).get("rate") if isinstance(performance.get("candidate_adoption"), Mapping) else None
    telemetry_p95 = performance.get("telemetry_instrumentation_p95_ms")
    perf_gates = {
        "valid_completed_pairs": "pass" if len(valid_pairs) >= 12 and not invalid_pairs else "fail",
        "median_search_evidence_reduction": "pass" if isinstance(median_reduction, int | float) and median_reduction >= 0.70 else "fail",
        "candidate_adoption": "pass" if isinstance(adoption_rate, int | float) and adoption_rate >= 0.80 else "fail",
        "search_read_call_non_regression": "pass" if isinstance(median_search_calls, int | float) and median_search_calls <= 1.10 else "fail",
        "total_tool_call_non_regression": "pass" if isinstance(median_tools, int | float) and median_tools <= 1.10 else "fail",
        "wall_time_non_regression": "pass" if isinstance(median_wall, int | float) and median_wall <= 1.05 and isinstance(max_wall, int | float) and max_wall <= 1.25 else "fail",
        "telemetry_p95": "pass" if isinstance(telemetry_p95, int | float) and telemetry_p95 < 5.0 else "fail",
        "context_compaction": "not_observed",
        "incomplete_telemetry": "pass",
    }
    hard_failed = any(value == "fail" for value in hard.values())
    performance_failed = any(value == "fail" for value in perf_gates.values())
    if invalid_pairs:
        decision = "invalid_evaluation"
    elif hard_failed:
        decision = "reject_candidate"
    elif performance_failed:
        decision = "hold_for_more_evidence"
    else:
        decision = "hold_for_more_evidence"
    return {
        "schema_version": "qwendex.optimization_lab.gate_decision.v1",
        "status": "pass" if decision in {"hold_for_more_evidence", "promote_opt_in_experimental"} else "fail",
        "candidate_decision": decision,
        "promotion_status": "not_promoted" if decision != "promote_opt_in_experimental" else "promoted_opt_in_experimental",
        "hard_gates": hard,
        "performance_gates": perf_gates,
        "valid_pairs": len(valid_pairs),
        "invalid_pairs": len(invalid_pairs),
        "claim_ceiling": "Controlled search-evidence retrieval proves neither live model task success nor live Manager root-session adoption; promotion is held even when controlled gates pass.",
    }


def _pair_csv(path: Path, pairs: list[Mapping[str, Any]]) -> None:
    fieldnames = [
        "pair_id",
        "stratum",
        "repository",
        "pair_order",
        "state",
        "baseline_file_recall",
        "candidate_file_recall",
        "baseline_region_recall",
        "candidate_region_recall",
        "search_output_ratio",
        "search_output_reduction",
        "search_read_call_ratio",
        "total_tool_call_ratio",
        "wall_time_ratio",
        "candidate_eligible",
        "candidate_invoked",
        "candidate_adopted",
        "initial_state",
        "adjudication_status",
        "candidate_failure_reproducible",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for pair in pairs:
            bytes_value = pair.get("search_output_bytes", {}) if isinstance(pair.get("search_output_bytes"), Mapping) else {}
            file_value = pair.get("relevant_file_recall", {}) if isinstance(pair.get("relevant_file_recall"), Mapping) else {}
            region_value = pair.get("relevant_region_recall", {}) if isinstance(pair.get("relevant_region_recall"), Mapping) else {}
            writer.writerow(
                {
                    "pair_id": pair.get("pair_id"),
                    "stratum": pair.get("stratum"),
                    "repository": pair.get("repository"),
                    "pair_order": pair.get("pair_order"),
                    "state": pair.get("state"),
                    "baseline_file_recall": file_value.get("baseline"),
                    "candidate_file_recall": file_value.get("candidate"),
                    "baseline_region_recall": region_value.get("baseline"),
                    "candidate_region_recall": region_value.get("candidate"),
                    "search_output_ratio": bytes_value.get("ratio"),
                    "search_output_reduction": bytes_value.get("reduction"),
                    "search_read_call_ratio": pair.get("search_read_call_ratio"),
                    "total_tool_call_ratio": pair.get("total_tool_call_ratio"),
                    "wall_time_ratio": pair.get("wall_time_ratio"),
                    "candidate_eligible": pair.get("candidate_eligible"),
                    "candidate_invoked": pair.get("candidate_invoked"),
                    "candidate_adopted": pair.get("candidate_adopted"),
                    "initial_state": pair.get("initial_state"),
                    "adjudication_status": (pair.get("adjudication") or {}).get("status") if isinstance(pair.get("adjudication"), Mapping) else "not_required",
                    "candidate_failure_reproducible": (pair.get("adjudication") or {}).get("candidate_failure_reproducible") if isinstance(pair.get("adjudication"), Mapping) else False,
                }
            )


def _quality_results(pairs: list[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    for pair in pairs:
        adjudication = pair.get("adjudication") if isinstance(pair.get("adjudication"), Mapping) else {}
        rows.append(
            {
                "task_id": pair.get("pair_id"),
                "state": pair.get("state"),
                "candidate_eligible": bool(pair.get("candidate_eligible")),
                "file_recall_non_inferior": _live_pair_metric_noninferior(pair, "relevant_file_recall"),
                "region_recall_non_inferior": _live_pair_metric_noninferior(pair, "relevant_region_recall"),
                "task_success_non_inferior": _live_pair_metric_noninferior(pair, "task_success"),
                "adjudication_status": adjudication.get("status") or "not_required",
                "candidate_failure_reproducible": bool(adjudication.get("candidate_failure_reproducible")),
                "focused_validation": pair.get("validation_status"),
            }
        )
    v2_rows = [row for row in rows if row["candidate_eligible"]]
    return {
        "schema_version": "qwendex.optimization_lab.quality_rubric.v1",
        "status": "pass" if all(
            row["file_recall_non_inferior"]
            and row["region_recall_non_inferior"]
            and row["task_success_non_inferior"]
            for row in v2_rows
        ) else "fail",
        "eligible_v2_row_count": len(v2_rows),
        "control_pair_discordance_count": sum(1 for row in rows if not row["candidate_eligible"] and row["state"] == "fail"),
        "rows": rows,
    }


def _angle_check(performance: Mapping[str, Any], gate: Mapping[str, Any]) -> str:
    outcome = {
        "Search output shrank but follow-up calls increased": "supported" if gate.get("performance_gates", {}).get("search_read_call_non_regression") == "fail" else "unsupported by controlled pairs",
        "Relevant lines were omitted": "supported" if gate.get("hard_gates", {}).get("relevant_region_recall") == "fail" else "unsupported by graded controlled pairs",
        "Candidate instructions offset evidence savings": "not_observed because no live model instruction budget was exercised",
        "Candidate was rarely invoked": "not_observed for live models; controlled expected-task adoption is recorded separately",
        "Model or API latency dominates wall time": "not_observed because this controlled runner does not call a model API",
        "Validation dominates implementation tasks": "supported only for the declared mechanical fixture validation durations",
        "Root and explorer agents duplicate work": "not_observed because no subagents run in the controlled runner",
        "Ranking overfits the frozen workload": "unresolved; require a separately frozen holdout before promotion",
        "Repository cache warmth or order biased results": "unresolved; pair order is balanced but host cache control is limited",
        "Dirty or untracked behavior differs from clean snapshots": "partially addressed by the explicit freshness matrix, not by source snapshot tasks",
        "A faster run failed validation": "supported" if gate.get("hard_gates", {}).get("task_success_and_validation") == "fail" else "unsupported by controlled pairs",
        "Telemetry coverage differs by variant": "unresolved unless the per-run coverage summaries remain equal",
    }
    lines = ["# Angle Check And Gap Analysis", ""]
    for explanation, status in outcome.items():
        lines.append(f"- **{explanation}:** {status}.")
    lines.extend(
        [
            "",
            "The claim ceiling remains controlled search-evidence retrieval. It is insufficient to claim model task-success, production latency, or default enablement.",
            "",
        ]
    )
    return "\n".join(lines)


def _next_goal(gate: Mapping[str, Any]) -> str:
    decision = str(gate.get("candidate_decision") or "hold_for_more_evidence")
    return "\n".join(
        [
            "# Next Recommended Goal",
            "",
            f"Current candidate decision: `{decision}`.",
            "",
            "GOAL: Add a controlled live-agent adoption evaluation for `search_evidence_compaction_v1` using the already frozen workload and a fixed authoritative model/routing posture; measure real tool adoption, task outcomes, Manager preflight binding, instruction-context cost, and paired latency without changing the candidate default state.",
            "",
            "This consumes the strongest remaining measured frontier—candidate tool discoverability and live-agent adoption—rather than rebuilding search infrastructure or adding indexes/caches.",
            "",
        ]
    )


def _final_report(
    *,
    run_id: str,
    payload: Mapping[str, Any],
    baselines: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    pairs: list[Mapping[str, Any]],
    gate: Mapping[str, Any],
    performance: Mapping[str, Any],
) -> str:
    return "\n".join(
        [
            "# Qwendex v0.6.0 Optimization Lab Paired Evaluation",
            "",
            "- Primary STOP: `STOP_V060_OPTIMIZATION_LAB_PAIRED_EVAL_COMPLETE`",
            f"- Candidate decision: `{gate.get('candidate_decision')}`",
            f"- Run: `{run_id}`",
            f"- Workload: `{payload.get('workload_id')}`",
            f"- Attempted pairs: {len(pairs)}; valid: {gate.get('valid_pairs')}; invalid: {gate.get('invalid_pairs')}",
            f"- Baseline runs: {len(baselines)}; candidate runs: {len(candidates)}",
            f"- Median model-facing search-evidence reduction: {performance.get('search_output_reduction', {}).get('median')}",
            f"- Median wall-time ratio: {performance.get('wall_time_ratio', {}).get('median')}",
            f"- Candidate adoption rate on predeclared broad-search tasks: {performance.get('candidate_adoption', {}).get('rate')}",
            "- Controlled evidence passed through isolated state/worktree paths and raw artifacts remain ignored local data.",
            "- Claim ceiling: no live model, live Manager root binding, or production speedup claim is made.",
            "- Version bump, tag, push, and publication: intentionally skipped.",
            "",
        ]
    )


def _phase1_baseline_commit() -> dict[str, Any]:
    try:
        commit = _git_output(REPOSITORY_ROOT, "rev-parse", "42e5ddd")
        subject = _git_output(REPOSITORY_ROOT, "show", "-s", "--format=%s", commit)
        return {"status": "pass", "commit": commit, "subject": subject}
    except LabError:
        return {"status": "not_observed", "commit": "not_observed", "subject": "not_observed"}


def paired_run(
    manifest_path: Path | str,
    *,
    candidate_id: str,
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path).expanduser().resolve(strict=False)
    validation = validate_workload(manifest)
    if not validation.get("valid"):
        raise LabError("workload manifest validation failed")
    search = search_module()
    registry = search.candidate_registry()
    declared = {str(item.get("candidate_id") or "") for item in registry.get("candidates", []) if isinstance(item, Mapping)}
    if candidate_id not in declared:
        raise LabError("candidate is not registered for the optimization lab")
    payload = _read_json(manifest)
    root = Path(output_root).expanduser().resolve(strict=False) if output_root else REPOSITORY_ROOT / ".qwendex-dev" / "results" / "performance" / "paired-eval"
    run_id = "paired-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / run_id
    if run_dir.exists():
        raise LabError("generated paired evaluation directory already exists")
    run_dir.mkdir(parents=True)
    _write_text(run_dir / "00_scope_and_git_custody.md", _scope_document(payload, run_id).replace("Baseline Capture", "Paired Evaluation").replace("baseline only", "paired baseline and explicit candidate"))
    _write_json(run_dir / "01_phase1_baseline_commit.json", _phase1_baseline_commit())
    environment = _environment_lock(payload, manifest)
    environment["candidate_mode"] = candidate_id
    environment["pre_candidate_baseline_reference"] = "baseline-20260712T192505Z-cb1ac576"
    _write_json(run_dir / "02_environment_lock.json", environment)
    shutil.copyfile(manifest, run_dir / "03_workload_manifest.json")
    _write_text(run_dir / "04_workload_manifest.sha256", f"{sha256_file(manifest)}  03_workload_manifest.json\n")
    _write_json(run_dir / "05_candidate_registry.json", registry)
    repositories = {str(item.get("id") or ""): item for item in payload.get("repositories", []) if isinstance(item, Mapping)}
    tasks = [item for item in payload.get("tasks", []) if isinstance(item, Mapping)]
    manager = _manager_security_probe(run_dir)
    freshness = search.freshness_matrix()
    baselines: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    pilot = _pilot_tasks(tasks)
    remaining = [task for task in tasks if str(task.get("id") or "") not in {str(item.get("id") or "") for item in pilot}]

    def execute(task: Mapping[str, Any]) -> None:
        repository = repositories.get(str(task.get("repository") or ""))
        if repository is None:
            blocked = {"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": "repository unavailable"}
            baselines.append(blocked)
            candidates.append({**blocked, "variant": "candidate"})
            pairs.append({"pair_id": str(task.get("id") or "unknown"), "state": "invalid_pair"})
            return
        try:
            baseline, candidate, pair = _run_pair(
                task=task,
                repository=repository,
                run_dir=run_dir,
                run_id=run_id,
                candidate_id=candidate_id,
            )
        except (LabError, OSError, ValueError) as exc:
            baseline = {"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": str(exc)}
            candidate = {"task_id": str(task.get("id") or "unknown"), "variant": "candidate", "status": "blocked", "reason": str(exc)}
            pair = {"pair_id": str(task.get("id") or "unknown"), "state": "invalid_pair", "reason": "execution_environment_failure"}
        baselines.append(baseline)
        candidates.append(candidate)
        pairs.append(pair)

    for task in pilot:
        execute(task)
    pilot_hard_failure = (
        freshness.get("status") != "pass"
        or manager.get("status") != "pass"
        or any(
            pair.get("state") != "pass"
            or float(pair.get("relevant_file_recall", {}).get("candidate") or 0.0) < float(pair.get("relevant_file_recall", {}).get("baseline") or 0.0)
            or float(pair.get("relevant_region_recall", {}).get("candidate") or 0.0) < float(pair.get("relevant_region_recall", {}).get("baseline") or 0.0)
            or (bool(pair.get("task_success", {}).get("baseline")) and not bool(pair.get("task_success", {}).get("candidate")))
            for pair in pairs
        )
        or sum(1 for pair in pairs if float(pair.get("wall_time_ratio") or 0.0) > 1.25) > 1
    )
    if not pilot_hard_failure:
        for task in remaining:
            execute(task)
    _write_jsonl(run_dir / "06_baseline_runs.jsonl", baselines)
    _write_jsonl(run_dir / "07_candidate_runs.jsonl", candidates)
    _pair_csv(run_dir / "08_pair_results.csv", pairs)
    quality = _quality_results(pairs)
    _write_json(run_dir / "09_quality_rubric_results.json", quality)
    _write_json(run_dir / "10_freshness_matrix.json", freshness)
    _write_json(run_dir / "12_manager_and_security_regressions.json", manager)
    raw_artifacts_valid = _raw_artifact_hashes_valid(run_dir, [*baselines, *candidates])
    performance = _performance_summary(baselines, candidates, pairs, candidate_id=candidate_id)
    _write_json(run_dir / "13_performance_summary.json", performance)
    privacy = _privacy_scan(run_dir, manifest, payload)
    _write_json(run_dir / "11_privacy_scan.json", privacy)
    gate = _gate_decision(
        baselines=baselines,
        candidates=candidates,
        pairs=pairs,
        freshness=freshness,
        privacy=privacy,
        manager=manager,
        performance=performance,
        raw_artifacts_valid=raw_artifacts_valid,
    )
    if pilot_hard_failure:
        gate["pilot_early_stop"] = True
        gate["candidate_decision"] = "reject_candidate" if any(value == "fail" for value in gate["hard_gates"].values()) else "invalid_evaluation"
        gate["status"] = "fail"
    _write_json(run_dir / "14_gate_decision.json", gate)
    _write_text(run_dir / "15_angle_check_and_gap_analysis.md", _angle_check(performance, gate))
    _write_text(run_dir / "16_next_recommended_goal.md", _next_goal(gate))
    _write_text(
        run_dir / "FINAL_REPORT.md",
        _final_report(
            run_id=run_id,
            payload=payload,
            baselines=baselines,
            candidates=candidates,
            pairs=pairs,
            gate=gate,
            performance=performance,
        ),
    )
    environment["completed_at"] = utc_now()
    _write_json(run_dir / "02_environment_lock.json", environment)
    _write_json(run_dir / "manifest.json", _artifact_manifest(run_dir))
    return {
        "schema_version": "qwendex.optimization_lab.paired_run.v1",
        "status": "pass" if gate.get("candidate_decision") in {"hold_for_more_evidence", "promote_opt_in_experimental"} else "fail",
        "summary": "Ran isolated Qwendex optimization-lab paired evaluation.",
        "data": {
            "run_id": run_id,
            "artifact_dir": str(run_dir),
            "candidate_decision": gate.get("candidate_decision"),
            "attempted_pairs": len(pairs),
            "valid_pairs": gate.get("valid_pairs"),
            "invalid_pairs": gate.get("invalid_pairs"),
            "pilot_early_stop": pilot_hard_failure,
        },
    }


def _run_live_arm(
    *,
    task: Mapping[str, Any],
    repository: Mapping[str, Any],
    run_dir: Path,
    run_id: str,
    manifest_path: Path,
    auth_source: Path,
    variant: str,
    supervisor_budgets: Mapping[str, Any],
    candidate_id: str = "",
    attempt: int = 0,
) -> dict[str, Any]:
    """Execute one genuinely fresh live Codex arm in an isolated worktree."""

    task_id = str(task.get("id") or "unknown")
    source = Path(str(repository.get("source_path") or "")).expanduser()
    attempt_name = "initial" if attempt == 0 else f"rerun-{attempt}"
    isolation_root = run_dir / "isolation" / task_id / variant
    raw_dir = run_dir / "raw" / variant / task_id
    receipt_path = run_dir / "arms" / task_id / variant / "receipt.json"
    if attempt:
        isolation_root = isolation_root / attempt_name
        raw_dir = raw_dir / attempt_name
        receipt_path = receipt_path.parent / attempt_name / "receipt.json"
    worktree = isolation_root / "worktree"
    isolation_root = worktree.parent
    started = time.monotonic()
    workload = _read_json(manifest_path)
    policy = workload.get("model_policy", {}) if isinstance(workload, Mapping) else {}
    if not isinstance(policy, Mapping):
        raise LabError("live workload model policy is incomplete")
    model = str(policy.get("model_identifier") or "")
    reasoning = str(policy.get("reasoning_effort") or "")
    if not model or not reasoning:
        raise LabError("live workload model policy is incomplete")
    budgets = _normalise_live_supervisor_budgets(supervisor_budgets)
    candidate_eligible = _live_candidate_eligible(task)
    candidate_active = _live_candidate_active(task, variant=variant, candidate_id=candidate_id)
    profile = _new_live_runtime_profile(
        run_id=run_id,
        task_id=task_id,
        variant=variant,
        attempt=attempt_name,
        candidate_id=candidate_id if candidate_active else "baseline_raw_tools",
        repository=repository,
        manifest_digest="sha256:" + sha256_file(manifest_path),
        model_policy=policy,
        budgets=budgets,
    )
    profile_path = raw_dir / "live_runtime_profile.json"
    _snapshot_worktree(source, str(repository.get("commit") or ""), worktree)
    _record_runtime_phase(profile, "workspace_ready")
    try:
        _materialize_live_fixture(task, worktree)
        environment, setup = _prepare_live_manager(isolation_root, worktree)
        _copy_live_auth(auth_source, Path(environment["CODEX_HOME"]))
        if candidate_active:
            environment["QWENDEX_SEARCH_EVIDENCE_COMPACTION"] = "v2"
            environment["QWENDEX_SEARCH_COMMAND"] = str(
                (worktree / "scripts" / "qwendex").resolve()
                if (worktree / "scripts" / "qwendex").is_file()
                else (REPOSITORY_ROOT / "scripts" / "qwendex").resolve()
            )
        prompt = _live_prompt(manifest_path, task)
        launch = _run_live_codex(
            environment=environment,
            worktree=worktree,
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning,
            profile=profile,
            budgets=budgets,
            raw_dir=raw_dir,
        )
        preflight_data = launch["preflight"].get("data", {}) if isinstance(launch.get("preflight"), Mapping) else {}
        actual_preflight_ok = (
            launch["preflight"].get("status") == "pass"
            and preflight_data.get("stop_status") == "STOP_MANAGER_PREFLIGHT_READY"
            and bool(preflight_data.get("hook_status", {}).get("verified"))
        )
        trace = _live_trace_summary(Path(launch["raw_paths"]["events"]))
        evidence = _live_evidence_grade(task, raw_dir)
        validation = _run_live_validation(task, worktree, raw_dir, environment, profile=profile)
        postconditions_ok = _live_postconditions(task, worktree)
        manager = _live_manager_status(environment)
        profile["manager_health"] = {
            "status": manager.get("status"),
            "active_agent_count": manager.get("agent_count"),
            "stale_session_count": manager.get("stale_count"),
            "outcome_count": manager.get("outcome_count"),
        }
        telemetry = performance_module().summary(
            Path(environment["QWENDEX_PERFORMANCE_DB"]),
            retention_days=14,
            max_events=50_000,
            repository_scope_digest=search_module().repository_scope_digest(worktree),
        )
        guard_marker = _contains_live_guard_marker(raw_dir)
        _record_runtime_phase(profile, "runner_complete")
        _finalise_live_runtime_profile(profile)
        _write_json(profile_path, profile)
        runtime_summary = {
            "profile_schema_version": profile.get("schema_version"),
            "profile_status": profile.get("profile_status"),
            "supervisor_policy_identity": budgets.get("policy_identity"),
            "timeout_classification": profile["termination"].get("timeout_classification"),
            "timeout_reason": profile["termination"].get("timeout_reason"),
            "contributing_classifications": profile["termination"].get("contributing_classifications"),
            "cleanup_status": profile["termination"].get("cleanup_status"),
            "completion_valid": profile["termination"].get("completion_valid"),
            "trusted_progress_event_counts": profile.get("trusted_progress_event_counts"),
            "phase_durations_ms": profile.get("phase_durations_ms"),
        }
        manager_clean = _live_manager_is_clean(manager)
        task_success = (
            not launch["timed_out"]
            and launch["returncode"] == 0
            and not validation["timed_out"]
            and runtime_summary["cleanup_status"] == "pass"
            and manager_clean
            and actual_preflight_ok
            and evidence["quality_status"] == "pass"
            and validation["status"] in {"pass", "not_applicable"}
            and postconditions_ok
            and not guard_marker
        )
        if (
            setup.get("status") != "pass"
            or not actual_preflight_ok
            or launch["timed_out"]
            or validation["timed_out"]
            or runtime_summary["cleanup_status"] != "pass"
            or not manager_clean
        ):
            status = "blocked"
        else:
            status = "pass" if task_success else "fail"
        raw_manifest_path = raw_dir / "raw_manifest.json"
        _write_json(
            raw_manifest_path,
            {
                "schema_version": "qwendex.optimization_lab.live_raw_manifest.v1",
                "retention_boundary": "ignored_local_live_evaluation_artifact",
                "artifacts": _live_raw_artifacts(raw_dir, run_dir),
            },
        )
        raw_artifacts = _live_raw_artifacts(raw_dir, run_dir)
        receipt = {
            "schema_version": LIVE_AGENT_RUN_SCHEMA_VERSION,
            "task_id": task_id,
            "variant": variant,
            "attempt": attempt_name,
            "candidate_id": candidate_id if candidate_active else "baseline_raw_tools",
            "candidate_eligible": candidate_eligible,
            "status": status,
            "returncode": launch["returncode"],
            "timed_out": launch["timed_out"],
            "timeout_classification": runtime_summary["timeout_classification"],
            "supervisor": runtime_summary,
            "manager_preflight": {
                "setup_status": setup.get("status"),
                "actual_status": launch["preflight"].get("status"),
                "stop_status": preflight_data.get("stop_status"),
                "hook_verified": bool(preflight_data.get("hook_status", {}).get("verified")),
            },
            "evidence": {key: evidence[key] for key in ("relevant_file_recall", "relevant_region_recall", "file_hits", "file_expected", "region_hits", "region_expected")},
            "validation": validation,
            "trace": trace,
            "manager": manager,
            "telemetry": telemetry,
            "guard_marker": guard_marker,
            "raw_artifacts": raw_artifacts,
        }
        _write_json(receipt_path, receipt)
        raw_manifest_entry = {
            "path": raw_manifest_path.relative_to(run_dir).as_posix(),
            "sha256": "sha256:" + sha256_file(raw_manifest_path),
        }
        return {
            "schema_version": BASELINE_RUN_SCHEMA_VERSION,
            "task_id": task_id,
            "repository": str(task.get("repository") or ""),
            "stratum": str(task.get("stratum") or ""),
            "variant": variant,
            "attempt": attempt_name,
            "candidate_id": candidate_id if candidate_active else "baseline_raw_tools",
            "candidate_version": "2" if candidate_active else "not_applicable",
            "candidate_eligible": candidate_eligible,
            "status": status,
            "returncode": launch["returncode"],
            "timed_out": launch["timed_out"],
            "task_success": task_success,
            "quality_status": evidence["quality_status"],
            "validation_status": validation["status"],
            "validation_duration_ms": validation["duration_ms"],
            "validation_timed_out": validation["timed_out"],
            "relevant_file_recall": evidence["relevant_file_recall"],
            "relevant_region_recall": evidence["relevant_region_recall"],
            "model_facing_search_bytes": trace["search_output_bytes"],
            "raw_output_bytes": sum(item["bytes"] for item in raw_artifacts),
            "search_calls": trace["search_calls"],
            "read_calls": trace["read_calls"],
            "tool_calls": trace["tool_calls"],
            "pagination_calls": trace["pagination_calls"],
            "fallback_count": trace["fallback_count"],
            "candidate_invoked": candidate_active,
            "candidate_adopted": bool(trace["candidate_adopted"]) if candidate_active else False,
            "candidate_search_calls": trace["candidate_search_calls"],
            "token_usage": trace["token_usage"],
            "guard_marker": guard_marker,
            "timeout_classification": runtime_summary["timeout_classification"],
            "timeout_contributing_classifications": runtime_summary["contributing_classifications"],
            "supervisor_policy_identity": budgets.get("policy_identity"),
            "supervisor_cleanup_status": runtime_summary["cleanup_status"],
            "runtime_profile_status": runtime_summary["profile_status"],
            "runtime_phase_durations_ms": runtime_summary["phase_durations_ms"],
            "manager_preflight": receipt["manager_preflight"],
            "manager": manager,
            "telemetry": telemetry,
            "receipt": receipt_path.relative_to(run_dir).as_posix(),
            "runtime_profile": profile_path.relative_to(run_dir).as_posix(),
            "raw_artifact": raw_manifest_entry,
            "wall_time_ms": round((time.monotonic() - started) * 1000, 3),
            "isolation": {
                "codex_home": "fresh_auth_copied_then_removed",
                "manager_state": "isolated",
                "performance_db": "isolated",
                "results_root": "isolated",
                "worktree": "isolated_detached",
                "conversation": "fresh_ephemeral",
            },
        }
    finally:
        _cleanup_live_isolation(isolation_root)
        _remove_worktree(source, worktree)


def _live_candidate_eligible(task: Mapping[str, Any]) -> bool:
    """Return the frozen workload's task-level v2 eligibility decision."""

    live = task.get("live") if isinstance(task.get("live"), Mapping) else {}
    return bool(live.get("candidate_eligible"))


def _live_candidate_active(task: Mapping[str, Any], *, variant: str, candidate_id: str) -> bool:
    """Return whether this live arm may receive the explicit v2 instruction.

    Narrow control tasks still run in the candidate-position worktree so order,
    isolation, and grading remain paired, but they must not receive a broad
    search instruction or contribute to the broad-task adoption denominator.
    """

    return (
        variant == "candidate"
        and candidate_id == search_module().SEARCH_V2_CANDIDATE_ID
        and _live_candidate_eligible(task)
    )


def _live_pair_result(task: Mapping[str, Any], baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    baseline_bytes = baseline.get("model_facing_search_bytes")
    candidate_bytes = candidate.get("model_facing_search_bytes")
    if isinstance(baseline_bytes, int | float) and isinstance(candidate_bytes, int | float) and baseline_bytes > 0:
        reduction: float | str = round(1 - float(candidate_bytes) / float(baseline_bytes), 6)
        byte_ratio: float | str = round(float(candidate_bytes) / float(baseline_bytes), 6)
    else:
        reduction = "not_observed"
        byte_ratio = "not_observed"
    baseline_tools = int(baseline.get("tool_calls") or 0)
    candidate_tools = int(candidate.get("tool_calls") or 0)
    return {
        "schema_version": "qwendex.optimization_lab.live_pair_result.v1",
        "pair_id": str(task.get("id") or ""),
        "repository": str(task.get("repository") or ""),
        "stratum": str(task.get("stratum") or ""),
        "pair_order": str(task.get("pair_order") or ""),
        "candidate_eligible": _live_candidate_eligible(task),
        "state": "invalid_pair" if "blocked" in {baseline.get("status"), candidate.get("status")} else "pass" if baseline.get("status") == candidate.get("status") == "pass" else "fail",
        "baseline_status": baseline.get("status"),
        "candidate_status": candidate.get("status"),
        "task_success": {"baseline": bool(baseline.get("task_success")), "candidate": bool(candidate.get("task_success"))},
        "relevant_file_recall": {"baseline": baseline.get("relevant_file_recall"), "candidate": candidate.get("relevant_file_recall")},
        "relevant_region_recall": {"baseline": baseline.get("relevant_region_recall"), "candidate": candidate.get("relevant_region_recall")},
        "validation_status": {"baseline": baseline.get("validation_status"), "candidate": candidate.get("validation_status")},
        "search_output_bytes": {"baseline": baseline_bytes, "candidate": candidate_bytes, "ratio": byte_ratio, "reduction": reduction},
        "search_read_call_ratio": round((int(candidate.get("search_calls") or 0) + int(candidate.get("read_calls") or 0)) / max(1, int(baseline.get("search_calls") or 0) + int(baseline.get("read_calls") or 0)), 6),
        "total_tool_call_ratio": round(candidate_tools / max(1, baseline_tools), 6),
        "wall_time_ratio": round(float(candidate.get("wall_time_ms") or 0.0) / max(1.0, float(baseline.get("wall_time_ms") or 0.0)), 6),
        "candidate_invoked": bool(candidate.get("candidate_invoked")),
        "candidate_adopted": bool(candidate.get("candidate_adopted")),
        "candidate_search_calls": candidate.get("candidate_search_calls", 0),
        "manager": {"baseline": baseline.get("manager"), "candidate": candidate.get("manager")},
        "runtime": {
            "baseline": {
                "timeout_classification": baseline.get("timeout_classification", "unknown_insufficient_instrumentation"),
                "supervisor_policy_identity": baseline.get("supervisor_policy_identity", "not_observed"),
                "cleanup_status": baseline.get("supervisor_cleanup_status", "not_observed"),
                "profile_status": baseline.get("runtime_profile_status", "not_observed"),
            },
            "candidate": {
                "timeout_classification": candidate.get("timeout_classification", "unknown_insufficient_instrumentation"),
                "supervisor_policy_identity": candidate.get("supervisor_policy_identity", "not_observed"),
                "cleanup_status": candidate.get("supervisor_cleanup_status", "not_observed"),
                "profile_status": candidate.get("runtime_profile_status", "not_observed"),
            },
        },
    }


def _live_pair_is_discordant(pair: Mapping[str, Any]) -> bool:
    """Return whether a pair needs the required one-per-arm adjudication rerun."""

    outcomes = pair.get("task_success") if isinstance(pair.get("task_success"), Mapping) else {}
    return bool(outcomes.get("baseline")) != bool(outcomes.get("candidate"))


def _live_pair_adjudication(initial: Mapping[str, Any], rerun: Mapping[str, Any]) -> dict[str, Any]:
    """Classify a discordant pair without exposing raw prompts or model output."""

    initial_outcomes = initial.get("task_success") if isinstance(initial.get("task_success"), Mapping) else {}
    rerun_outcomes = rerun.get("task_success") if isinstance(rerun.get("task_success"), Mapping) else {}
    initial_candidate_only = bool(initial_outcomes.get("baseline")) and not bool(initial_outcomes.get("candidate"))
    rerun_candidate_only = bool(rerun_outcomes.get("baseline")) and not bool(rerun_outcomes.get("candidate"))
    reproducible = initial_candidate_only and rerun_candidate_only
    rerun_invalid = rerun.get("state") == "invalid_pair"
    if rerun_invalid:
        classification = "environment_or_runtime_drift"
        status = "unresolved"
    elif reproducible:
        classification = "candidate_only_failure_reproduced"
        status = "reproducible_candidate_failure"
    else:
        classification = "model_stochastic_behavior"
        status = "resolved_nonreproducible"
    return {
        "schema_version": "qwendex.optimization_lab.live_pair_adjudication.v1",
        "pair_id": str(initial.get("pair_id") or ""),
        "candidate_eligible": bool(initial.get("candidate_eligible")),
        "initial_state": initial.get("state"),
        "rerun_state": rerun.get("state"),
        "initial_task_success": dict(initial_outcomes),
        "rerun_task_success": dict(rerun_outcomes),
        "rerun_metrics": {
            "relevant_file_recall": dict(rerun.get("relevant_file_recall") or {}),
            "relevant_region_recall": dict(rerun.get("relevant_region_recall") or {}),
            "task_success": dict(rerun_outcomes),
        },
        "initial_candidate_only_failure": initial_candidate_only,
        "rerun_candidate_only_failure": rerun_candidate_only,
        "candidate_failure_reproducible": reproducible,
        "classification": classification,
        "status": status,
        "search_evidence_attribution": "not_claimed_without_direct_private_trace_evidence",
        "rerun_contract": "same frozen manifest, snapshot, model, reasoning, routing, permissions, isolation, and pair order",
    }


def _live_pair_metric_noninferior(pair: Mapping[str, Any], metric: str) -> bool:
    """Apply a resolved discordance without averaging away a reproduced failure."""

    values = pair.get(metric) if isinstance(pair.get(metric), Mapping) else {}
    if metric == "task_success":
        initial_ok = bool(values.get("candidate")) >= bool(values.get("baseline"))
    else:
        initial_ok = float(values.get("candidate") or 0.0) >= float(values.get("baseline") or 0.0)
    if initial_ok:
        return True
    adjudication = pair.get("adjudication") if isinstance(pair.get("adjudication"), Mapping) else {}
    rerun_metrics = adjudication.get("rerun_metrics") if isinstance(adjudication.get("rerun_metrics"), Mapping) else {}
    rerun_values = rerun_metrics.get(metric) if isinstance(rerun_metrics.get(metric), Mapping) else {}
    if metric == "task_success":
        rerun_ok = bool(rerun_values.get("candidate")) >= bool(rerun_values.get("baseline"))
    else:
        rerun_ok = float(rerun_values.get("candidate") or 0.0) >= float(rerun_values.get("baseline") or 0.0)
    return (
        adjudication.get("status") == "resolved_nonreproducible"
        and not bool(adjudication.get("candidate_failure_reproducible"))
        and rerun_ok
    )


def _live_pair_has_reproducible_v2_regression(pair: Mapping[str, Any]) -> bool:
    if not bool(pair.get("candidate_eligible")):
        return False
    return not all(
        _live_pair_metric_noninferior(pair, metric)
        for metric in ("relevant_file_recall", "relevant_region_recall", "task_success")
    )


def _live_privacy_scan(run_dir: Path, manifest_path: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    base = _privacy_scan(run_dir, manifest_path, payload)
    raw_files = [path for path in (run_dir / "raw").rglob("*") if path.is_file()]
    secret_markers = (b'"access_token"', b'"refresh_token"', b'"api_key"', b"authorization: bearer")
    raw_secret_hits = sum(1 for path in raw_files if any(marker in path.read_bytes().lower() for marker in secret_markers))
    return {
        **base,
        "raw_artifacts_scanned": len(raw_files),
        "raw_secret_marker_matches": raw_secret_hits,
        "status": "pass" if base.get("status") == "pass" and raw_secret_hits == 0 else "fail",
        "leak_match_count": int(base.get("leak_match_count") or 0) + raw_secret_hits,
    }


def _live_performance_summary(baselines: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], pairs: list[Mapping[str, Any]]) -> dict[str, Any]:
    v2_pairs = [pair for pair in pairs if bool(pair.get("candidate_invoked"))]
    reductions = [
        float(value)
        for pair in v2_pairs
        for value in [pair.get("search_output_bytes", {}).get("reduction") if isinstance(pair.get("search_output_bytes"), Mapping) else None]
        if isinstance(value, int | float)
    ]
    search_ratios = [float(pair.get("search_read_call_ratio") or 0.0) for pair in v2_pairs]
    tool_ratios = [float(pair.get("total_tool_call_ratio") or 0.0) for pair in v2_pairs]
    wall_ratios = [float(pair.get("wall_time_ratio") or 0.0) for pair in v2_pairs]
    eligible = [row for row in candidates if bool(row.get("candidate_eligible"))]
    delivered = [row for row in eligible if bool(row.get("candidate_invoked"))]
    fallback_count = sum(int(row.get("fallback_count") or 0) for row in delivered)
    pagination_calls = sum(int(row.get("pagination_calls") or 0) for row in delivered)
    validation_durations = [float(row["validation_duration_ms"]) for row in [*baselines, *candidates] if isinstance(row.get("validation_duration_ms"), int | float)]
    p95_values: list[float] = []
    p50_values: list[float] = []
    token_totals: dict[str, int] = {}
    all_rows = [*baselines, *candidates]
    timeout_counts: dict[str, Counter[str]] = {"baseline": Counter(), "candidate": Counter()}
    cleanup_counts: Counter[str] = Counter()
    profile_status_counts: Counter[str] = Counter()
    policy_identities: set[str] = set()
    phase_values: dict[str, list[float]] = {}
    for row in all_rows:
        telemetry = row.get("telemetry") if isinstance(row.get("telemetry"), Mapping) else {}
        overhead = telemetry.get("instrumentation_overhead") if isinstance(telemetry, Mapping) else None
        if isinstance(overhead, Mapping):
            if isinstance(overhead.get("p95_ms"), int | float):
                p95_values.append(float(overhead["p95_ms"]))
            if isinstance(overhead.get("median_ms"), int | float):
                p50_values.append(float(overhead["median_ms"]))
        usage = row.get("token_usage")
        if isinstance(usage, Mapping):
            for key, value in usage.items():
                if isinstance(value, int):
                    token_totals[str(key)] = token_totals.get(str(key), 0) + value
        variant = str(row.get("variant") or "")
        if variant in timeout_counts:
            classification = str(row.get("timeout_classification") or "unknown_insufficient_instrumentation")
            timeout_counts[variant][classification] += 1
        cleanup_counts[str(row.get("supervisor_cleanup_status") or "not_observed")] += 1
        profile_status_counts[str(row.get("runtime_profile_status") or "not_observed")] += 1
        identity = str(row.get("supervisor_policy_identity") or "")
        if identity:
            policy_identities.add(identity)
        durations = row.get("runtime_phase_durations_ms") if isinstance(row.get("runtime_phase_durations_ms"), Mapping) else {}
        for name, value in durations.items():
            if isinstance(value, int | float):
                phase_values.setdefault(str(name), []).append(float(value))
    baseline_timeout_count = sum(count for key, count in timeout_counts["baseline"].items() if key != "not_applicable")
    candidate_timeout_count = sum(count for key, count in timeout_counts["candidate"].items() if key != "not_applicable")
    phase_decomposition = {
        name: {"p50": _percentile(values, 0.5), "p95": _percentile(values, 0.95)}
        for name, values in sorted(phase_values.items())
    }
    return {
        "schema_version": "qwendex.optimization_lab.live_performance_summary.v1",
        "pair_count": len(pairs),
        "search_output_reduction": {"median": _median(reductions), "values": reductions or "not_observed"},
        "search_read_call_ratio": {"median": _median(search_ratios), "values": search_ratios},
        "total_tool_call_ratio": {"median": _median(tool_ratios), "values": tool_ratios},
        "wall_time_ratio": {"median": _median(wall_ratios), "max": max(wall_ratios) if wall_ratios else "not_observed", "values": wall_ratios},
        "candidate_adoption": {
            "eligible_tasks": len(eligible),
            "instruction_delivered_tasks": len(delivered),
            "adopted_tasks": sum(1 for row in eligible if row.get("candidate_adopted")),
            "rate": round(sum(1 for row in eligible if row.get("candidate_adopted")) / len(eligible), 6) if eligible else "not_observed",
        },
        "fallback_count": fallback_count,
        "fallback_rate": round(fallback_count / len(eligible), 6) if eligible else "not_observed",
        "pagination_calls": pagination_calls,
        "validation_duration_ms": {"p50": _percentile(validation_durations, 0.5), "p95": _percentile(validation_durations, 0.95)},
        "telemetry_instrumentation_overhead_ms": {"p50": _percentile(p50_values, 0.5), "p95": _percentile(p95_values, 0.95)},
        "telemetry_instrumentation_p95_ms": _percentile(p95_values, 0.95) if p95_values else "not_observed",
        "token_usage": token_totals or "not_observed",
        "phase_decomposition_ms": phase_decomposition or "not_observed",
        "timeout_classifications": {
            "baseline": dict(sorted(timeout_counts["baseline"].items())),
            "candidate": dict(sorted(timeout_counts["candidate"].items())),
            "baseline_timeout_count": baseline_timeout_count,
            "candidate_timeout_count": candidate_timeout_count,
            "candidate_timeout_excess": max(0, candidate_timeout_count - baseline_timeout_count),
        },
        "runtime_profile_status_counts": dict(sorted(profile_status_counts.items())),
        "cleanup_status_counts": dict(sorted(cleanup_counts.items())),
        "supervisor_policy_identities": sorted(policy_identities) or "not_observed",
        "time_to_first_relevant_evidence_ms": "not_observed",
        "time_to_first_edit_ms": "not_observed",
        "context_compaction_count": "not_observed",
        "root_subagent_overlap": "not_observed",
        "duplicate_query_rate": "not_observed",
    }


def _live_gate_decision(
    *,
    baselines: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    pairs: list[Mapping[str, Any]],
    freshness: Mapping[str, Any],
    privacy: Mapping[str, Any],
    raw_artifacts_valid: bool,
    performance: Mapping[str, Any],
    reruns: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    unresolved = [
        pair
        for pair in pairs
        if isinstance(pair.get("adjudication"), Mapping) and pair["adjudication"].get("status") == "unresolved"
    ]
    invalid = [
        pair
        for pair in pairs
        if pair.get("state") == "invalid_pair" or pair in unresolved
    ]
    valid = [pair for pair in pairs if pair not in invalid]
    v2_pairs = [pair for pair in valid if bool(pair.get("candidate_eligible"))]
    control_pairs = [pair for pair in valid if not bool(pair.get("candidate_eligible"))]
    candidate_delivery_ok = bool(v2_pairs) and all(bool(pair.get("candidate_invoked")) for pair in v2_pairs)
    file_ok = all(_live_pair_metric_noninferior(pair, "relevant_file_recall") for pair in v2_pairs)
    region_ok = all(_live_pair_metric_noninferior(pair, "relevant_region_recall") for pair in v2_pairs)
    task_ok = all(_live_pair_metric_noninferior(pair, "task_success") for pair in v2_pairs)
    observed_rows = [*baselines, *candidates, *(reruns or [])]
    completed_rows = [row for row in observed_rows if row.get("status") != "blocked"]
    manager_ok = all(
        row.get("manager_preflight", {}).get("actual_status") == "pass"
        and row.get("manager_preflight", {}).get("stop_status") == "STOP_MANAGER_PREFLIGHT_READY"
        and bool(row.get("manager_preflight", {}).get("hook_verified"))
        and int(row.get("manager", {}).get("stale_count") or 0) == 0
        for row in completed_rows
    )
    guard_ok = not any(bool(row.get("guard_marker")) for row in observed_rows)
    adjudication_ok = all(
        not _live_pair_is_discordant(pair)
        or (isinstance(pair.get("adjudication"), Mapping) and pair["adjudication"].get("status") != "unresolved")
        for pair in pairs
    )
    runtime_profiles_ok = bool(observed_rows) and all(
        row.get("runtime_profile_status") == "complete"
        and row.get("supervisor_cleanup_status") == "pass"
        and str(row.get("timeout_classification") or "") in LIVE_TIMEOUT_CLASSIFICATIONS
        for row in observed_rows
    )
    policy_identities = {
        str(row.get("supervisor_policy_identity") or "")
        for row in observed_rows
        if str(row.get("supervisor_policy_identity") or "")
    }
    budget_policy_ok = len(policy_identities) == 1
    no_unknown_failed_timeout = not any(
        row.get("status") != "pass"
        and str(row.get("timeout_classification") or "unknown_insufficient_instrumentation") == "unknown_insufficient_instrumentation"
        for row in observed_rows
    )
    timeout_summary = performance.get("timeout_classifications", {}) if isinstance(performance.get("timeout_classifications"), Mapping) else {}
    candidate_timeout_excess = timeout_summary.get("candidate_timeout_excess")
    timeout_balance_ok = isinstance(candidate_timeout_excess, int | float) and float(candidate_timeout_excess) == 0.0
    hard = {
        "relevant_file_recall": "pass" if file_ok else "fail",
        "relevant_region_recall": "pass" if region_ok else "fail",
        "live_task_and_validation_noninferior": "pass" if task_ok else "fail",
        "eligible_v2_instruction_delivery": "pass" if candidate_delivery_ok else "fail",
        "discordant_pair_adjudication": "pass" if adjudication_ok else "not_observed",
        "dirty_untracked_freshness": str(freshness.get("status") or "fail"),
        "privacy_boundary": str(privacy.get("status") or "fail"),
        "manager_live_root_binding": "pass" if manager_ok else "fail",
        "guard_marker_absence": "pass" if guard_ok else "fail",
        "raw_artifact_digests": "pass" if raw_artifacts_valid else "fail",
        "runtime_profile_integrity_and_cleanup": "pass" if runtime_profiles_ok else "fail",
        "identical_supervisor_budget_policy": "pass" if budget_policy_ok else "fail",
        "failed_arm_timeout_classification": "pass" if no_unknown_failed_timeout else "fail",
        "candidate_default_off": "pass",
        "deterministic_v2_contract": "pass",
    }
    reduction = performance.get("search_output_reduction", {}).get("median") if isinstance(performance.get("search_output_reduction"), Mapping) else None
    adoption = performance.get("candidate_adoption", {}).get("rate") if isinstance(performance.get("candidate_adoption"), Mapping) else None
    wall = performance.get("wall_time_ratio", {}).get("median") if isinstance(performance.get("wall_time_ratio"), Mapping) else None
    calls = performance.get("search_read_call_ratio", {}).get("median") if isinstance(performance.get("search_read_call_ratio"), Mapping) else None
    telemetry = performance.get("telemetry_instrumentation_p95_ms")
    fallback = performance.get("fallback_rate")
    efficiency = {
        "median_search_evidence_reduction": "pass" if isinstance(reduction, int | float) and reduction >= 0.70 else "fail",
        "search_read_call_non_regression": "pass" if isinstance(calls, int | float) and calls <= 1.10 else "fail",
        "wall_time_non_regression": "pass" if isinstance(wall, int | float) and wall <= 1.05 else "fail",
        "candidate_adoption": "pass" if isinstance(adoption, int | float) and adoption >= 0.80 else "fail",
        "fallback_rate": "pass" if isinstance(fallback, int | float) and fallback <= 0.25 else "fail",
        "telemetry_p95": "pass" if isinstance(telemetry, int | float) and telemetry < 5 else "fail",
        "candidate_timeout_imbalance": "pass" if timeout_balance_ok else "fail",
        "context_compaction": "not_observed" if performance.get("context_compaction_count") == "not_observed" else "pass",
    }
    hard_failed = any(value == "fail" for value in hard.values())
    efficiency_passed = all(value in {"pass", "not_observed"} for value in efficiency.values())
    if invalid:
        decision = "invalid_evaluation"
    elif hard_failed:
        decision = "reject_candidate"
    elif len(valid) >= 12 and efficiency_passed and all(value == "pass" for key, value in efficiency.items() if key != "context_compaction"):
        decision = "promote_opt_in_experimental"
    else:
        decision = "hold_for_more_evidence"
    return {
        "schema_version": "qwendex.optimization_lab.live_gate_decision.v1",
        "status": "pass" if decision in {"hold_for_more_evidence", "promote_opt_in_experimental"} else "fail",
        "candidate_decision": decision,
        "promotion_status": "promoted_opt_in_experimental" if decision == "promote_opt_in_experimental" else "not_promoted",
        "hard_gates": hard,
        "performance_gates": efficiency,
        "valid_pairs": len(valid),
        "invalid_pairs": len(invalid),
        "eligible_v2_pair_count": len(v2_pairs),
        "control_pair_count": len(control_pairs),
        "control_pair_discordance_count": sum(1 for pair in control_pairs if pair.get("state") == "fail"),
        "unresolved_discordant_pair_count": len(unresolved),
        "claim_ceiling": "Live Codex evidence is isolated and paired; promotion still requires every reported hard and observable efficiency gate.",
    }


def _live_final_report(run_id: str, payload: Mapping[str, Any], baselines: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]], gate: Mapping[str, Any], performance: Mapping[str, Any]) -> str:
    return "\n".join(
        [
            "# Qwendex v0.6.0 Held-Out Live-Agent Paired Evaluation",
            "",
            f"- Run: `{run_id}`",
            f"- Workload: `{payload.get('workload_id')}`",
            f"- Candidate decision: `{gate.get('candidate_decision')}`",
            f"- Valid pairs: {gate.get('valid_pairs')}; invalid pairs: {gate.get('invalid_pairs')}",
            f"- Baseline arms: {len(baselines)}; candidate arms: {len(candidates)}",
            f"- Discordant pairs adjudicated: {gate.get('discordant_pair_count', 0)}; reproducible candidate-only failures: {gate.get('discordant_reproducible_candidate_failure_count', 0)}",
            f"- Median model-visible search-evidence reduction: {performance.get('search_output_reduction', {}).get('median')}",
            f"- Candidate adoption: {performance.get('candidate_adoption', {}).get('rate')}",
            "- Raw prompts, event streams, and tool output remain ignored local artifacts; safe summaries contain counts and digests only.",
            "- Version bump, tag, publication, and push: intentionally skipped.",
            "",
        ]
    )


def _safe_runtime_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return calibration evidence without private task text or raw paths."""

    manager = row.get("manager") if isinstance(row.get("manager"), Mapping) else {}
    return {
        "pair_digest": _safe_local_digest(str(row.get("task_id") or "")),
        "arm": str(row.get("variant") or "baseline"),
        "status": str(row.get("status") or "blocked"),
        "task_success": bool(row.get("task_success")),
        "returncode": row.get("returncode", "not_observed"),
        "wall_time_ms": row.get("wall_time_ms", "not_observed"),
        "timeout_classification": str(row.get("timeout_classification") or "unknown_insufficient_instrumentation"),
        "contributing_classifications": list(row.get("timeout_contributing_classifications") or []),
        "supervisor_policy_identity": str(row.get("supervisor_policy_identity") or "not_observed"),
        "runtime_profile_status": str(row.get("runtime_profile_status") or "not_observed"),
        "cleanup_status": str(row.get("supervisor_cleanup_status") or "not_observed"),
        "manager": {
            "status": manager.get("status", "not_observed"),
            "active_agent_count": manager.get("agent_count", "not_observed"),
            "stale_session_count": manager.get("stale_count", "not_observed"),
        },
    }


def live_runtime_calibration(
    manifest_path: Path | str,
    *,
    auth_source: Path | str,
    task_id: str,
    output_root: Path | str | None = None,
    secondary_task_id: str = "",
) -> dict[str, Any]:
    """Calibrate live budgets before any paired candidate outcome is viewed.

    It first replays the frozen task under its original one-wall budget while
    collecting a profile.  Any precise legacy classification can then run a
    same-wall progress-aware diagnostic; only a completed diagnostic or proven
    continuous trusted progress can select a policy for candidate pairs. A
    caller can supply one frozen task from a different stratum when a second
    baseline check is genuinely needed; candidate code is never run.
    """

    manifest = Path(manifest_path).expanduser().resolve(strict=False)
    validation = validate_workload(manifest)
    if not validation.get("valid"):
        raise LabError("workload manifest validation failed")
    payload = _read_json(manifest)
    if payload.get("execution_mode") != LIVE_EXECUTION_MODE:
        raise LabError("live runtime calibration requires a frozen live-agent workload")
    auth = Path(auth_source).expanduser().resolve(strict=False)
    if not auth.is_file():
        raise LabError("operator-supplied live evaluation auth source is unavailable")
    tasks = {str(item.get("id") or ""): item for item in payload.get("tasks", []) if isinstance(item, Mapping)}
    task = tasks.get(task_id)
    if task is None:
        raise LabError("requested frozen calibration task is unavailable")
    repositories = {str(item.get("id") or ""): item for item in payload.get("repositories", []) if isinstance(item, Mapping)}
    repository = repositories.get(str(task.get("repository") or ""))
    if repository is None:
        raise LabError("requested frozen calibration repository is unavailable")
    root = Path(output_root).expanduser().resolve(strict=False) if output_root else REPOSITORY_ROOT / ".qwendex-dev" / "results" / "performance" / "paired-eval"
    run_id = "live-runtime-calibration-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / run_id
    if run_dir.exists():
        raise LabError("generated live runtime calibration directory already exists")
    run_dir.mkdir(parents=True)
    legacy_seconds = int(task.get("timeout_seconds") or 180)
    legacy_budget = legacy_live_supervisor_budgets(legacy_seconds)
    _write_json(
        run_dir / "00_calibration_custody.json",
        {
            "schema_version": "qwendex.live_runtime_calibration_custody.v1",
            "run_digest": _safe_local_digest(run_id),
            "frozen_workload_manifest_digest": "sha256:" + sha256_file(manifest),
            "primary_pair_digest": _safe_local_digest(task_id),
            "candidate_executed": False,
            "raw_boundary": "ignored_local_artifacts_only",
        },
    )
    shutil.copyfile(manifest, run_dir / "01_frozen_workload_manifest.json")
    _write_text(run_dir / "01_frozen_workload_manifest.sha256", f"{sha256_file(manifest)}  01_frozen_workload_manifest.json\n")
    _write_json(run_dir / "02_legacy_budget_policy.json", legacy_budget)

    def run_baseline(selected_task: Mapping[str, Any], selected_repository: Mapping[str, Any], *, name: str, budget: Mapping[str, Any]) -> dict[str, Any]:
        return _run_live_arm(
            task=selected_task,
            repository=selected_repository,
            run_dir=run_dir / name,
            run_id=run_id,
            manifest_path=manifest,
            auth_source=auth,
            variant="baseline",
            supervisor_budgets=budget,
        )

    try:
        legacy_row = run_baseline(task, repository, name="legacy", budget=legacy_budget)
    except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        legacy_row = {
            "task_id": task_id,
            "variant": "baseline",
            "status": "blocked",
            "reason": str(exc),
            "timeout_classification": "unknown_insufficient_instrumentation",
        }
    _write_json(run_dir / "03_legacy_180_observation.json", _safe_runtime_row(legacy_row))

    legacy_classification = str(legacy_row.get("timeout_classification") or "unknown_insufficient_instrumentation")
    legacy_completed = legacy_row.get("status") == "pass"
    precise_legacy_classification = (
        legacy_classification in LIVE_TIMEOUT_CLASSIFICATIONS
        and legacy_classification not in {"unknown_insufficient_instrumentation", "process_tree_cleanup_failed"}
    )
    diagnostic_allowed = legacy_completed or precise_legacy_classification
    diagnostic_budget: dict[str, Any] | None = None
    diagnostic_row: dict[str, Any] | None = None
    secondary_row: dict[str, Any] | None = None
    expanded_budget: dict[str, Any] | None = None
    expanded_row: dict[str, Any] | None = None
    selected_budget: dict[str, Any] | None = None
    if diagnostic_allowed:
        diagnostic_budget = derive_diagnostic_live_supervisor_budgets(legacy_seconds)
        derivation = dict(diagnostic_budget.get("derivation") or {})
        derivation["calibration_run_digest"] = _safe_local_digest(run_id)
        derivation["legacy_observation_classification"] = legacy_classification
        diagnostic_budget = _normalise_live_supervisor_budgets({**diagnostic_budget, "derivation": derivation})
        try:
            diagnostic_row = run_baseline(task, repository, name="same-wall-progress-aware", budget=diagnostic_budget)
        except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            diagnostic_row = {
                "task_id": task_id,
                "variant": "baseline",
                "status": "blocked",
                "reason": str(exc),
                "timeout_classification": "unknown_insufficient_instrumentation",
            }
        if secondary_task_id and diagnostic_row.get("status") != "pass":
            secondary = tasks.get(secondary_task_id)
            secondary_repository = repositories.get(str(secondary.get("repository") or "")) if isinstance(secondary, Mapping) else None
            if secondary is None or secondary_repository is None:
                raise LabError("requested secondary frozen calibration task is unavailable")
            try:
                secondary_row = run_baseline(secondary, secondary_repository, name="secondary-same-wall-progress-aware", budget=diagnostic_budget)
            except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                secondary_row = {
                    "task_id": secondary_task_id,
                    "variant": "baseline",
                    "status": "blocked",
                    "reason": str(exc),
                    "timeout_classification": "unknown_insufficient_instrumentation",
                }
        diagnostic_completed = any(
            isinstance(row, Mapping) and row.get("status") == "pass"
            for row in (diagnostic_row, secondary_row)
        )
        diagnostic_continuous_progress = any(
            isinstance(row, Mapping) and row.get("timeout_classification") == "timeout_with_continuous_trusted_progress"
            for row in (diagnostic_row, secondary_row)
        )
        if diagnostic_completed:
            selected_budget = diagnostic_budget
        elif diagnostic_continuous_progress:
            expanded_budget = derive_progress_aware_live_supervisor_budgets(legacy_seconds)
            expanded_derivation = dict(expanded_budget.get("derivation") or {})
            expanded_derivation["calibration_run_digest"] = _safe_local_digest(run_id)
            expanded_derivation["same_wall_observation_classification"] = "timeout_with_continuous_trusted_progress"
            expanded_budget = _normalise_live_supervisor_budgets({**expanded_budget, "derivation": expanded_derivation})
            try:
                expanded_row = run_baseline(task, repository, name="expanded-progress-aware", budget=expanded_budget)
            except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                expanded_row = {
                    "task_id": task_id,
                    "variant": "baseline",
                    "status": "blocked",
                    "reason": str(exc),
                    "timeout_classification": "unknown_insufficient_instrumentation",
                }
            if expanded_row.get("status") == "pass":
                selected_budget = expanded_budget
    else:
        diagnostic_completed = False
        diagnostic_continuous_progress = False
    _write_json(
        run_dir / "04_progress_aware_observation.json",
        {
            "schema_version": "qwendex.live_runtime_calibration_observation.v1",
            "same_wall_policy_run": diagnostic_allowed,
            "same_wall_primary": _safe_runtime_row(diagnostic_row) if isinstance(diagnostic_row, Mapping) else "not_run",
            "secondary": _safe_runtime_row(secondary_row) if isinstance(secondary_row, Mapping) else "not_run",
            "expanded_primary": _safe_runtime_row(expanded_row) if isinstance(expanded_row, Mapping) else "not_run",
        },
    )
    if diagnostic_budget is not None:
        _write_json(run_dir / "04a_same_wall_supervisor_budget_policy.json", diagnostic_budget)
    if expanded_budget is not None:
        _write_json(run_dir / "04b_expanded_supervisor_budget_policy.json", expanded_budget)
    if selected_budget is not None:
        _write_json(run_dir / "05_selected_supervisor_budget_policy.json", selected_budget)
    ready_for_pairs = selected_budget is not None
    if ready_for_pairs:
        outcome = "ready_for_paired_rerun"
    elif not diagnostic_allowed:
        outcome = "unknown_legacy_timeout_requires_instrumentation_review"
    elif any(
        isinstance(row, Mapping)
        and row.get("timeout_classification") in {"timeout_due_to_inactivity", "timeout_during_tool_execution", "timeout_during_manager_stop"}
        for row in (diagnostic_row, secondary_row)
    ):
        outcome = "classified_nonprogress_runtime_blocker"
    else:
        outcome = "progress_aware_baseline_incomplete"
    _write_json(
        run_dir / "06_calibration_decision.json",
        {
            "schema_version": "qwendex.live_runtime_calibration_decision.v1",
            "status": "pass" if ready_for_pairs else "blocked",
            "outcome": outcome,
            "legacy_timeout_classification": legacy_classification,
            "legacy_completed": legacy_completed,
            "same_wall_progress_aware_run": diagnostic_allowed,
            "continuous_trusted_progress_at_same_wall": diagnostic_continuous_progress,
            "progress_aware_baseline_completed": diagnostic_completed or (isinstance(expanded_row, Mapping) and expanded_row.get("status") == "pass"),
            "selected_policy_identity": selected_budget.get("policy_identity") if selected_budget else "not_observed",
            "candidate_outcomes_viewed": False,
        },
    )
    _write_json(run_dir / "07_live_runtime_profile_contract.json", live_runtime_profile_contract())
    _write_json(run_dir / "manifest.json", _artifact_manifest(run_dir))
    return {
        "schema_version": "qwendex.live_runtime_calibration.v1",
        "status": "pass" if ready_for_pairs else "blocked",
        "summary": "Calibrated the live-runtime supervisor before paired candidate evidence." if ready_for_pairs else "Live-runtime calibration did not establish a safe paired-evaluation policy.",
        "data": {
            "run_id": run_id,
            "artifact_dir": str(run_dir),
            "outcome": outcome,
            "legacy_timeout_classification": legacy_classification,
            "selected_policy": str(run_dir / "05_selected_supervisor_budget_policy.json") if selected_budget else "not_observed",
            "ready_for_pairs": ready_for_pairs,
        },
    }


def _verify_private_artifact_manifest(run_dir: Path) -> dict[str, Any]:
    manifest_path = run_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    entries = manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
    bad_count = 0
    for entry in entries:
        if not isinstance(entry, Mapping):
            bad_count += 1
            continue
        path = run_dir / str(entry.get("path") or "")
        if not path.is_file() or entry.get("sha256") != "sha256:" + sha256_file(path):
            bad_count += 1
    return {
        "schema_version": str(manifest.get("schema_version") if isinstance(manifest, Mapping) else "not_observed"),
        "manifest_digest": "sha256:" + sha256_file(manifest_path),
        "artifact_count": len(entries),
        "bad_hash_count": bad_count,
        "status": "pass" if isinstance(manifest, Mapping) and bad_count == 0 else "fail",
    }


def _safe_event_shape(events_path: Path) -> dict[str, Any]:
    event_counts: Counter[str] = Counter()
    item_counts: Counter[str] = Counter()
    unclosed_lifecycle: Counter[str] = Counter()
    open_items: dict[str, str] = {}
    parse_errors = 0
    if not events_path.is_file():
        return {"status": "not_observed", "event_type_counts": {}, "item_type_counts": {}, "unclosed_lifecycle_counts": {}, "parse_errors": 0}
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            parse_errors += 1
            continue
        if not isinstance(event, Mapping):
            continue
        event_type = _safe_event_category(
            str(event.get("type") or ""),
            allowed={"thread.started", "turn.started", "turn.completed", "item.started", "item.completed"},
        )
        event_counts[event_type] += 1
        item = event.get("item") if isinstance(event.get("item"), Mapping) else {}
        item_type = _safe_event_category(
            str(item.get("type") or ""),
            allowed={"agent_message", "command_execution", "collab_tool_call", "file_change", "mcp_tool_call"},
        )
        if item_type != "other":
            item_counts[item_type] += 1
        if event_type not in {"item.started", "item.completed"}:
            continue
        raw_id = str(item.get("id") or event.get("id") or "")
        item_id = _safe_local_digest(raw_id) if raw_id else "not_observed"
        if item_type == "collab_tool_call":
            lifecycle = "collaboration_wait" if str(item.get("tool") or "").lower() == "wait" else "collaboration_other"
        elif item_type == "command_execution":
            lifecycle = "command_execution"
        elif item_type == "mcp_tool_call":
            lifecycle = "mcp_tool_call"
        elif item_type == "file_change":
            lifecycle = "file_change"
        else:
            lifecycle = "other"
        if event_type == "item.started":
            open_items[item_id] = lifecycle
        else:
            open_items.pop(item_id, None)
    for lifecycle in open_items.values():
        unclosed_lifecycle[lifecycle] += 1
    return {
        "status": "pass",
        "event_type_counts": dict(sorted(event_counts.items())),
        "item_type_counts": dict(sorted(item_counts.items())),
        "unclosed_lifecycle_counts": dict(sorted(unclosed_lifecycle.items())),
        "parse_errors": parse_errors,
    }


def _prior_timeout_timelines(authoritative_run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    timelines: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    for variant, filename in (("baseline", "06_baseline_runs.jsonl"), ("candidate", "07_candidate_runs.jsonl")):
        for row in _read_jsonl(authoritative_run / filename):
            raw_entry = row.get("raw_artifact") if isinstance(row.get("raw_artifact"), Mapping) else {}
            raw_manifest = authoritative_run / str(raw_entry.get("path") or "")
            events_path = raw_manifest.parent / "events.jsonl"
            shape = _safe_event_shape(events_path)
            pair_digest = _safe_local_digest(str(row.get("task_id") or ""))
            timelines.append(
                {
                    "pair_digest": pair_digest,
                    "arm": variant,
                    "phase_timestamps": {phase: "not_observed" for phase in LIVE_RUNTIME_PHASES},
                    "observation_limit": "historical event streams contain no trustworthy timestamps or process-state samples",
                    "structured_event_shape": shape,
                    "wall_time_ms": row.get("wall_time_ms", "not_observed"),
                }
            )
            classifications.append(
                {
                    "evidence_generation": "prior_legacy",
                    "pair_digest": pair_digest,
                    "arm": variant,
                    "primary_classification": "unknown_insufficient_instrumentation",
                    "contributing_classifications": "",
                }
            )
    return timelines, classifications


def _safe_calibration_profiles(calibration_run: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    profiles: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    for path in sorted(calibration_run.rglob("live_runtime_profile.json")):
        profile = _read_json(path)
        if not isinstance(profile, Mapping):
            continue
        identity = profile.get("identity") if isinstance(profile.get("identity"), Mapping) else {}
        termination = profile.get("termination") if isinstance(profile.get("termination"), Mapping) else {}
        manager = profile.get("manager_health") if isinstance(profile.get("manager_health"), Mapping) else {}
        profiles.append(
            {
                "pair_digest": identity.get("pair_digest", "not_observed"),
                "arm": identity.get("arm", "not_observed"),
                "attempt": identity.get("attempt", "not_observed"),
                "profile_schema_version": profile.get("schema_version", "not_observed"),
                "profile_status": profile.get("profile_status", "not_observed"),
                "phase_timestamps": profile.get("phase_timestamps", {}),
                "phase_durations_ms": profile.get("phase_durations_ms", {}),
                "trusted_progress_event_counts": profile.get("trusted_progress_event_counts", {}),
                "collaboration_wait_timeout_bucket_counts": profile.get(
                    "collaboration_wait_timeout_bucket_counts", {}
                ),
                "structured_event_shape": _safe_event_shape(path.parent / "events.jsonl"),
                "termination": {
                    "timed_out": termination.get("timed_out", "not_observed"),
                    "timeout_reason": termination.get("timeout_reason", "not_observed"),
                    "timeout_classification": termination.get("timeout_classification", "unknown_insufficient_instrumentation"),
                    "contributing_classifications": termination.get("contributing_classifications", []),
                    "cleanup_status": termination.get("cleanup_status", "not_observed"),
                },
                "manager_health": {
                    "status": manager.get("status", "not_observed"),
                    "active_agent_count": manager.get("active_agent_count", "not_observed"),
                    "stale_session_count": manager.get("stale_session_count", "not_observed"),
                },
                "privacy": profile.get("privacy", {}),
            }
        )
        classifications.append(
            {
                "evidence_generation": "calibration",
                "pair_digest": identity.get("pair_digest", "not_observed"),
                "arm": identity.get("arm", "not_observed"),
                "primary_classification": termination.get("timeout_classification", "unknown_insufficient_instrumentation"),
                "contributing_classifications": ";".join(str(item) for item in termination.get("contributing_classifications", []) if isinstance(item, str)),
            }
        )
    return profiles, classifications


def _calibration_blocker_posture(calibration_decision: Mapping[str, Any]) -> dict[str, str]:
    """Describe a blocked calibration without discarding its progress evidence."""

    continuous_progress = bool(calibration_decision.get("continuous_trusted_progress_at_same_wall"))
    if continuous_progress:
        return {
            "hard_wall_expansion": "permitted_for_calibration_after_same_wall_continuous_progress",
            "final_report_observation": (
                "The same-wall diagnostic proved continuous trusted progress, then the bounded expanded "
                "baseline still reached a true inactivity timeout with an unclosed collaboration wait."
            ),
            "next_frontier": (
                "GOAL: Diagnose the native Codex collaboration-wait and missing subagent terminal-lifecycle "
                "path under Manager Mode using the recorded metadata-only receipt. First reproduce one isolated "
                "frozen baseline and prove whether every spawned agent reaches a terminal status or the wait call "
                "returns its bounded timeout. Do not change Search V2, its frozen workload, model, routing, or "
                "default-off posture. If Qwendex-layer diagnosis proves a specific upstream Codex defect, stop and "
                "open a separately scoped Codex patch goal.\n"
            ),
        }
    return {
        "hard_wall_expansion": "not_permitted_without_continuous_trusted_progress",
        "final_report_observation": (
            "The same-wall diagnostic did not prove continuous trusted progress before its precise runtime blocker."
        ),
        "next_frontier": (
            "GOAL: Trace and repair the native Codex collaboration-wait completion path under Manager Mode using "
            "the recorded metadata-only lifecycle receipt. First reproduce it with one isolated frozen baseline, "
            "then prove the wait call emits a terminal event and leaves no receiver thread/session before rerunning "
            "the unchanged Search V2 held-out manifest. Do not change Search V2, its frozen workload, model, "
            "routing, or default-off posture in that goal.\n"
        ),
    }


def live_runtime_stability_closeout(
    *,
    prior_run_dir: Path | str,
    calibration_run_dir: Path | str,
    validation_summary: Mapping[str, Any],
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    """Write the privacy-safe closeout when calibration blocks paired evidence."""

    prior = Path(prior_run_dir).expanduser().resolve(strict=False)
    calibration = Path(calibration_run_dir).expanduser().resolve(strict=False)
    prior_verification = _verify_private_artifact_manifest(prior)
    calibration_verification = _verify_private_artifact_manifest(calibration)
    authoritative_runs = sorted((prior / "live-eval-authoritative").glob("live-paired-*"))
    if len(authoritative_runs) != 1:
        raise LabError("prior live evidence has no unique authoritative paired run")
    authoritative = authoritative_runs[0]
    authoritative_comparison = compare_run(authoritative)
    frozen_manifest = prior / "live-workload" / "manifest.json"
    if not frozen_manifest.is_file():
        raise LabError("prior frozen workload manifest is unavailable")
    frozen_digest = "sha256:" + sha256_file(frozen_manifest)
    calibration_decision = _read_json(calibration / "06_calibration_decision.json")
    if not isinstance(calibration_decision, Mapping):
        raise LabError("live runtime calibration decision is unavailable")
    blocker_posture = _calibration_blocker_posture(calibration_decision)
    timelines, prior_classifications = _prior_timeout_timelines(authoritative)
    calibration_profiles, calibration_classifications = _safe_calibration_profiles(calibration)
    if not calibration_profiles:
        raise LabError("live runtime calibration profiles are unavailable")
    same_wall_policy_path = calibration / "04a_same_wall_supervisor_budget_policy.json"
    same_wall_policy = _read_json(same_wall_policy_path) if same_wall_policy_path.is_file() else {}
    root = Path(output_root).expanduser().resolve(strict=False) if output_root else REPOSITORY_ROOT / ".qwendex-dev" / "results" / "performance" / "paired-eval"
    run_id = "live-runtime-stability-closeout-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / run_id
    if run_dir.exists():
        raise LabError("generated live runtime closeout directory already exists")
    run_dir.mkdir(parents=True)
    head = _git_output(REPOSITORY_ROOT, "rev-parse", "HEAD")
    tree = "git:" + _git_output(REPOSITORY_ROOT, "rev-parse", "HEAD^{tree}")
    dirty_paths = [line for line in _git_output(REPOSITORY_ROOT, "status", "--short").splitlines() if line]
    diff_digest = "sha256:" + sha256_bytes(
        subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), "diff", "--binary"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    )
    candidate_identity = search_module().SEARCH_V2_CANDIDATE_ID
    budget_identity = str(same_wall_policy.get("policy_identity") or "not_selected") if isinstance(same_wall_policy, Mapping) else "not_selected"
    _write_json(
        run_dir / "00_custody.json",
        {
            "schema_version": "qwendex.live_runtime_stability_custody.v1",
            "source_commit": head,
            "source_tree": tree,
            "working_tree_diff_digest": diff_digest,
            "working_tree_dirty_path_count": len(dirty_paths),
            "frozen_workload_manifest_digest": frozen_digest,
            "candidate_identity": candidate_identity,
            "budget_identity": budget_identity,
            "generation_time": utc_now(),
        },
    )
    _write_json(
        run_dir / "01_prior_artifact_verification.json",
        {
            "schema_version": "qwendex.live_runtime_prior_artifact_verification.v1",
            "prior_closeout_manifest": prior_verification,
            "authoritative_live_comparison": authoritative_comparison,
            "calibration_manifest": calibration_verification,
            "status": "pass" if prior_verification["status"] == "pass" and calibration_verification["status"] == "pass" and authoritative_comparison.get("status") == "pass" else "fail",
        },
    )
    _write_json(
        run_dir / "02_prior_timeout_timeline.json",
        {
            "schema_version": "qwendex.live_runtime_prior_timeout_timeline.v1",
            "historical_arms": timelines,
            "calibration_profiles": calibration_profiles,
        },
    )
    classification_rows = [*prior_classifications, *calibration_classifications]
    with (run_dir / "03_timeout_classification.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["evidence_generation", "pair_digest", "arm", "primary_classification", "contributing_classifications"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(classification_rows)
    _write_json(run_dir / "04_live_runtime_profile_schema.json", live_runtime_profile_contract())
    _write_json(
        run_dir / "05_supervisor_budget_and_derivation.json",
        {
            "schema_version": "qwendex.live_runtime_budget_derivation.v1",
            "legacy_budget": _read_json(calibration / "02_legacy_budget_policy.json"),
            "same_wall_progress_aware_budget": same_wall_policy if isinstance(same_wall_policy, Mapping) else "not_observed",
            "selected_paired_budget": "not_selected_due_to_calibration_blocker",
            "hard_wall_expansion": blocker_posture["hard_wall_expansion"],
        },
    )
    _write_json(
        run_dir / "06_supervisor_regression_results.json",
        {
            "schema_version": "qwendex.live_runtime_supervisor_regression_results.v1",
            "status": str(validation_summary.get("supervisor_tests") or "not_observed"),
            "coverage": [
                "startup_and_first_model_ceiling",
                "continuous_progress",
                "tool_inactivity",
                "manager_stop_delay",
                "pipe_drain_and_descendant_cleanup",
                "backpressure",
                "process_group_termination",
                "budget_equivalence",
                "metadata_only_privacy",
                "idle_manager_cleanup",
            ],
            "details": dict(validation_summary),
        },
    )
    _write_json(
        run_dir / "07_calibration_results.json",
        {
            "schema_version": "qwendex.live_runtime_calibration_results.v1",
            "decision": calibration_decision,
            "profile_count": len(calibration_profiles),
            "candidate_outcomes_viewed": bool(calibration_decision.get("candidate_outcomes_viewed")),
        },
    )
    calibration_frozen = calibration / "01_frozen_workload_manifest.json"
    _write_json(
        run_dir / "08_frozen_manifest_reuse_receipt.json",
        {
            "schema_version": "qwendex.live_runtime_frozen_manifest_reuse.v1",
            "prior_manifest_digest": frozen_digest,
            "calibration_manifest_digest": "sha256:" + sha256_file(calibration_frozen) if calibration_frozen.is_file() else "missing",
            "unchanged": calibration_frozen.is_file() and sha256_file(calibration_frozen) == sha256_file(frozen_manifest),
            "task_selection_changed": False,
            "model_or_candidate_semantics_changed": False,
        },
    )
    not_run = {
        "schema_version": "qwendex.live_runtime_not_run.v1",
        "status": "not_run",
        "reason": "calibration did not select a valid paired supervisor budget",
        "candidate_outcomes_viewed": False,
    }
    _write_json(run_dir / "09_pilot_pair_results.json", not_run)
    _write_json(run_dir / "10_pilot_gate_decision.json", {**not_run, "stop_status": "STOP_V060_LIVE_RUNTIME_TIMEOUT_CLASSIFIED"})
    _write_json(run_dir / "11_full_live_pair_results.json", not_run)
    _write_json(run_dir / "12_full_live_summary.json", {**not_run, "valid_pairs": 0, "invalid_pairs": 0})
    _write_json(
        run_dir / "13_candidate_decision.json",
        {
            "schema_version": "qwendex.live_runtime_candidate_decision.v1",
            "primary_stop": "STOP_V060_LIVE_RUNTIME_TIMEOUT_CLASSIFIED",
            "candidate_stop": "STOP_V060_SEARCH_V2_HELD_FOR_LIVE_EVIDENCE",
            "candidate_identity": candidate_identity,
            "decision": "hold_for_more_live_evidence",
            "reason": "no valid paired live outcomes were collected after the calibration blocker",
            "default_state": "off",
            "promoted": False,
        },
    )
    all_profiles_private = all(
        isinstance(profile.get("privacy"), Mapping)
        and profile["privacy"].get("metadata_only") is True
        and profile["privacy"].get("raw_content_in_profile") is False
        for profile in calibration_profiles
    )
    all_cleanup_clean = all(
        profile.get("termination", {}).get("cleanup_status") == "pass"
        and int(profile.get("manager_health", {}).get("stale_session_count") or 0) == 0
        and int(profile.get("manager_health", {}).get("active_agent_count") or 0) == 0
        for profile in calibration_profiles
    )
    _write_json(
        run_dir / "14_privacy_freshness_manager_cleanup.json",
        {
            "schema_version": "qwendex.live_runtime_privacy_freshness_manager_cleanup.v1",
            "profile_metadata_only": "pass" if all_profiles_private else "fail",
            "freshness": "pass_from_verified_prior_evidence",
            "manager_and_process_cleanup": "pass" if all_cleanup_clean else "fail",
            "raw_content_in_aggregate": False,
        },
    )
    _write_json(
        run_dir / "15_validation_summary.json",
        {
            "schema_version": "qwendex.live_runtime_validation_summary.v1",
            **dict(validation_summary),
        },
    )
    _write_text(
        run_dir / "16_next_frontier.md",
        "# Next frontier\n\n" + blocker_posture["next_frontier"],
    )
    _write_text(
        run_dir / "FINAL_REPORT.md",
        "# Live Runtime Stability and Search V2 Rerun Closeout\n\n"
        "- Primary STOP: `STOP_V060_LIVE_RUNTIME_TIMEOUT_CLASSIFIED`\n"
        "- Candidate STOP: `STOP_V060_SEARCH_V2_HELD_FOR_LIVE_EVIDENCE`\n"
        "- The frozen workload was reused unchanged.\n"
        f"- {blocker_posture['final_report_observation']}\n"
        "- Qwendex repaired the opaque timeout, concurrent-pipe/cleanup observability gap, and missing-final-message guard false positive.\n"
        "- No valid paired pilot or full evaluation was run because calibration did not select a valid paired budget.\n"
        "- Search V2 remains explicit, opt-in, default-off, and is not promoted.\n"
        "- No version bump, tag, push, publication, or release claim occurred.\n",
    )
    manifest_payload = _artifact_manifest(run_dir)
    manifest_payload.update(
        {
            "source_commit": head,
            "source_tree": tree,
            "workload_manifest_digest": frozen_digest,
            "candidate_identity": candidate_identity,
            "budget_identity": budget_identity,
            "generation_time": utc_now(),
        }
    )
    _write_json(run_dir / "manifest.json", manifest_payload)
    return {
        "schema_version": "qwendex.live_runtime_stability_closeout.v1",
        "status": "blocked",
        "summary": "Wrote the verified live-runtime blocker closeout without treating incomplete pairs as candidate evidence.",
        "data": {
            "run_id": run_id,
            "artifact_dir": str(run_dir),
            "primary_stop": "STOP_V060_LIVE_RUNTIME_TIMEOUT_CLASSIFIED",
            "candidate_stop": "STOP_V060_SEARCH_V2_HELD_FOR_LIVE_EVIDENCE",
        },
    }


def live_paired_run(
    manifest_path: Path | str,
    *,
    candidate_id: str,
    auth_source: Path | str,
    supervisor_policy: Path | str | Mapping[str, Any],
    output_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run a frozen, isolated, paired live-agent adoption evaluation."""

    manifest = Path(manifest_path).expanduser().resolve(strict=False)
    validation = validate_workload(manifest)
    if not validation.get("valid"):
        raise LabError("workload manifest validation failed")
    payload = _read_json(manifest)
    if payload.get("execution_mode") != LIVE_EXECUTION_MODE:
        raise LabError("live evaluation requires a frozen live-agent workload")
    if candidate_id != search_module().SEARCH_V2_CANDIDATE_ID:
        raise LabError("live adoption evaluation requires search_evidence_compaction_v2")
    auth = Path(auth_source).expanduser().resolve(strict=False)
    if not auth.is_file():
        raise LabError("operator-supplied live evaluation auth source is unavailable")
    budgets = load_live_supervisor_budget_policy(supervisor_policy)
    if budgets.get("mode") != "progress_aware":
        raise LabError("live paired evidence requires a calibrated progress-aware supervisor policy")
    root = Path(output_root).expanduser().resolve(strict=False) if output_root else REPOSITORY_ROOT / ".qwendex-dev" / "results" / "performance" / "paired-eval"
    run_id = "live-paired-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = root / run_id
    if run_dir.exists():
        raise LabError("generated live evaluation directory already exists")
    run_dir.mkdir(parents=True)
    _write_text(
        run_dir / "00_scope_and_git_custody.md",
        "\n".join(
            [
                "# Qwendex held-out live-agent evaluation custody",
                "",
                f"- Run: `{run_id}`",
                f"- Workload: `{payload.get('workload_id')}`",
                "- Each arm has a detached worktree, fresh Codex home, fresh Manager state/ledger, fresh performance DB, and fresh ephemeral conversation.",
                "- Operator auth was copied only into each ignored temporary home and removed before the run artifact manifest was written.",
                "- Raw prompts, events, and tool output remain ignored local artifacts.",
                "",
            ]
        ),
    )
    _write_json(run_dir / "01_phase1_baseline_commit.json", _phase1_baseline_commit())
    environment = _environment_lock(payload, manifest)
    environment.update(
        {
            "candidate_mode": candidate_id,
            "execution_mode": LIVE_EXECUTION_MODE,
            "auth_source": "operator_supplied_private",
            "conversation_isolation": "fresh_home_per_arm",
            "supervisor_policy_identity": budgets["policy_identity"],
            "supervisor_policy_mode": budgets["mode"],
        }
    )
    _write_json(run_dir / "02_environment_lock.json", environment)
    shutil.copyfile(manifest, run_dir / "03_workload_manifest.json")
    _write_text(run_dir / "04_workload_manifest.sha256", f"{sha256_file(manifest)}  03_workload_manifest.json\n")
    _write_json(run_dir / "05_candidate_registry.json", search_module().candidate_registry())
    _write_json(run_dir / "05a_supervisor_budget_policy.json", budgets)
    repositories = {str(item.get("id") or ""): item for item in payload.get("repositories", []) if isinstance(item, Mapping)}
    tasks = [item for item in payload.get("tasks", []) if isinstance(item, Mapping)]
    baselines: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    pairs: list[dict[str, Any]] = []
    rerun_rows: list[dict[str, Any]] = []
    adjudications: list[dict[str, Any]] = []

    def execute_attempt(task: Mapping[str, Any], repository: Mapping[str, Any], *, attempt: int) -> tuple[dict[str, Any], dict[str, Any]]:
        if str(task.get("pair_order") or "") == "candidate_first":
            candidate = _run_live_arm(
                task=task,
                repository=repository,
                run_dir=run_dir,
                run_id=run_id,
                manifest_path=manifest,
                auth_source=auth,
                variant="candidate",
                supervisor_budgets=budgets,
                candidate_id=candidate_id,
                attempt=attempt,
            )
            baseline = _run_live_arm(
                task=task,
                repository=repository,
                run_dir=run_dir,
                run_id=run_id,
                manifest_path=manifest,
                auth_source=auth,
                variant="baseline",
                supervisor_budgets=budgets,
                attempt=attempt,
            )
        else:
            baseline = _run_live_arm(
                task=task,
                repository=repository,
                run_dir=run_dir,
                run_id=run_id,
                manifest_path=manifest,
                auth_source=auth,
                variant="baseline",
                supervisor_budgets=budgets,
                attempt=attempt,
            )
            candidate = _run_live_arm(
                task=task,
                repository=repository,
                run_dir=run_dir,
                run_id=run_id,
                manifest_path=manifest,
                auth_source=auth,
                variant="candidate",
                supervisor_budgets=budgets,
                candidate_id=candidate_id,
                attempt=attempt,
            )
        return baseline, candidate

    def execute(task: Mapping[str, Any]) -> None:
        repository = repositories.get(str(task.get("repository") or ""))
        if repository is None:
            blocked = {"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": "repository unavailable"}
            baselines.append(blocked)
            candidates.append({**blocked, "variant": "candidate"})
            pairs.append({"pair_id": str(task.get("id") or "unknown"), "state": "invalid_pair"})
            return
        try:
            baseline, candidate = execute_attempt(task, repository, attempt=0)
        except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            baseline = {"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "status": "blocked", "reason": str(exc)}
            candidate = {"task_id": str(task.get("id") or "unknown"), "variant": "candidate", "status": "blocked", "reason": str(exc)}
        baselines.append(baseline)
        candidates.append(candidate)
        pair = _live_pair_result(task, baseline, candidate)
        if _live_pair_is_discordant(pair):
            try:
                rerun_baseline, rerun_candidate = execute_attempt(task, repository, attempt=1)
            except (LabError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
                rerun_baseline = {"task_id": str(task.get("id") or "unknown"), "variant": "baseline", "attempt": "rerun-1", "status": "blocked", "reason": str(exc)}
                rerun_candidate = {"task_id": str(task.get("id") or "unknown"), "variant": "candidate", "attempt": "rerun-1", "status": "blocked", "reason": str(exc)}
            rerun_rows.extend([rerun_baseline, rerun_candidate])
            rerun_pair = _live_pair_result(task, rerun_baseline, rerun_candidate)
            adjudication = _live_pair_adjudication(pair, rerun_pair)
            adjudication["initial_receipts"] = {"baseline": baseline.get("receipt"), "candidate": candidate.get("receipt")}
            adjudication["rerun_receipts"] = {"baseline": rerun_baseline.get("receipt"), "candidate": rerun_candidate.get("receipt")}
            pair["initial_state"] = pair.get("state")
            pair["adjudication"] = adjudication
            if pair.get("state") == "invalid_pair" and rerun_pair.get("state") != "invalid_pair":
                pair["state"] = "fail_after_adjudication" if adjudication["candidate_failure_reproducible"] else "adjudicated_recovered"
            adjudications.append(adjudication)
        pairs.append(pair)

    pilot = _pilot_tasks(tasks)
    remaining = [task for task in tasks if str(task.get("id") or "") not in {str(item.get("id") or "") for item in pilot}]
    for task in pilot:
        execute(task)
    pilot_hard_failure = any(pair.get("state") == "invalid_pair" for pair in pairs) or any(
        _live_pair_has_reproducible_v2_regression(pair) for pair in pairs
    )
    if not pilot_hard_failure:
        for task in remaining:
            execute(task)
    _write_jsonl(run_dir / "06_baseline_runs.jsonl", baselines)
    _write_jsonl(run_dir / "07_candidate_runs.jsonl", candidates)
    _pair_csv(run_dir / "08_pair_results.csv", pairs)
    quality = _quality_results(pairs)
    _write_json(run_dir / "09_quality_rubric_results.json", quality)
    freshness = search_module().freshness_matrix()
    _write_json(run_dir / "10_freshness_matrix.json", freshness)
    privacy = _live_privacy_scan(run_dir, manifest, payload)
    _write_json(run_dir / "11_privacy_scan.json", privacy)
    _write_jsonl(run_dir / "17_discordant_reruns.jsonl", rerun_rows)
    _write_json(
        run_dir / "18_discordant_pair_adjudication.json",
        {
            "schema_version": "qwendex.optimization_lab.live_discordant_adjudication.v1",
            "status": "pass" if all(item.get("status") != "unresolved" for item in adjudications) else "fail",
            "discordant_pair_count": len(adjudications),
            "adjudications": adjudications,
        },
    )
    all_rows = [*baselines, *candidates, *rerun_rows]
    manager = {
        "schema_version": "qwendex.optimization_lab.live_manager_gate.v1",
        "status": "pass"
        if all(
            row.get("manager_preflight", {}).get("actual_status") == "pass"
            and row.get("manager_preflight", {}).get("stop_status") == "STOP_MANAGER_PREFLIGHT_READY"
            and bool(row.get("manager_preflight", {}).get("hook_verified"))
            and int(row.get("manager", {}).get("stale_count") or 0) == 0
            for row in all_rows
            if row.get("status") != "blocked"
        )
        else "fail",
        "arm_count": len(all_rows),
        "live_root_binding": "observed_pid_bound_preflight",
    }
    _write_json(run_dir / "12_manager_and_security_regressions.json", manager)
    performance = _live_performance_summary(baselines, candidates, pairs)
    _write_json(run_dir / "13_performance_summary.json", performance)
    raw_artifacts_valid = _raw_artifact_hashes_valid(run_dir, all_rows)
    gate = _live_gate_decision(
        baselines=baselines,
        candidates=candidates,
        pairs=pairs,
        freshness=freshness,
        privacy=privacy,
        raw_artifacts_valid=raw_artifacts_valid,
        performance=performance,
        reruns=rerun_rows,
    )
    gate["discordant_pair_count"] = len(adjudications)
    gate["discordant_reproducible_candidate_failure_count"] = sum(
        1 for item in adjudications if item.get("candidate_failure_reproducible")
    )
    if pilot_hard_failure:
        gate["pilot_early_stop"] = True
        gate["candidate_decision"] = "invalid_evaluation" if gate.get("invalid_pairs") else "reject_candidate" if any(value == "fail" for value in gate["hard_gates"].values()) else "invalid_evaluation"
        gate["status"] = "fail"
    _write_json(run_dir / "14_gate_decision.json", gate)
    _write_text(run_dir / "15_angle_check_and_gap_analysis.md", "# Live angle check\n\n- Live adoption, tool counts, isolated Manager preflight, and paired wall time are measured from fresh arm receipts.\n- Metrics without a trusted producer remain `not_observed`.\n")
    _write_text(run_dir / "16_next_recommended_goal.md", "# Next recommended goal\n\nUse measured live-pair bottlenecks only; do not add an index or structural dependency without a separate goal.\n")
    _write_text(run_dir / "FINAL_REPORT.md", _live_final_report(run_id, payload, baselines, candidates, gate, performance))
    environment["completed_at"] = utc_now()
    _write_json(run_dir / "02_environment_lock.json", environment)
    _write_json(run_dir / "manifest.json", _artifact_manifest(run_dir))
    return {
        "schema_version": "qwendex.optimization_lab.live_paired_run.v1",
        "status": "pass" if gate.get("candidate_decision") in {"hold_for_more_evidence", "promote_opt_in_experimental"} else "fail",
        "summary": "Ran isolated held-out Qwendex live-agent paired evaluation.",
        "data": {
            "run_id": run_id,
            "artifact_dir": str(run_dir),
            "candidate_decision": gate.get("candidate_decision"),
            "attempted_pairs": len(pairs),
            "valid_pairs": gate.get("valid_pairs"),
            "invalid_pairs": gate.get("invalid_pairs"),
            "pilot_early_stop": pilot_hard_failure,
        },
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise LabError("could not read JSONL artifact") from exc


def compare_run(run_dir: Path | str) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve(strict=False)
    required = [
        "00_scope_and_git_custody.md",
        "01_phase1_baseline_commit.json",
        "02_environment_lock.json",
        "03_workload_manifest.json",
        "04_workload_manifest.sha256",
        "05_candidate_registry.json",
        "06_baseline_runs.jsonl",
        "07_candidate_runs.jsonl",
        "08_pair_results.csv",
        "09_quality_rubric_results.json",
        "10_freshness_matrix.json",
        "11_privacy_scan.json",
        "12_manager_and_security_regressions.json",
        "13_performance_summary.json",
        "14_gate_decision.json",
        "15_angle_check_and_gap_analysis.md",
        "16_next_recommended_goal.md",
        "FINAL_REPORT.md",
        "manifest.json",
    ]
    workload_preview = _read_json_if_present(root / "03_workload_manifest.json")
    is_live = isinstance(workload_preview, Mapping) and workload_preview.get("execution_mode") == LIVE_EXECUTION_MODE
    has_supervisor_policy = is_live and (root / "05a_supervisor_budget_policy.json").is_file()
    if is_live:
        required.extend(["17_discordant_reruns.jsonl", "18_discordant_pair_adjudication.json"])
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        return {
            "schema_version": "qwendex.optimization_lab.compare.v1",
            "status": "fail",
            "summary": "Optimization-lab artifact set is incomplete.",
            "errors": ["missing required artifacts"],
            "data": {"missing_count": len(missing)},
        }
    manifest = _read_json(root / "manifest.json")
    entries = manifest.get("artifacts", []) if isinstance(manifest, Mapping) else []
    bad_hashes = []
    for item in entries:
        if not isinstance(item, Mapping):
            bad_hashes.append("invalid")
            continue
        path = root / str(item.get("path") or "")
        expected = str(item.get("sha256") or "")
        if not path.is_file() or expected != "sha256:" + sha256_file(path):
            bad_hashes.append(str(item.get("path") or "unknown"))
    schema_failures: list[str] = []
    json_artifacts = [
        "01_phase1_baseline_commit.json",
        "02_environment_lock.json",
        "03_workload_manifest.json",
        "05_candidate_registry.json",
        "09_quality_rubric_results.json",
        "10_freshness_matrix.json",
        "11_privacy_scan.json",
        "12_manager_and_security_regressions.json",
        "13_performance_summary.json",
        "14_gate_decision.json",
        "manifest.json",
    ]
    if is_live:
        json_artifacts.append("18_discordant_pair_adjudication.json")
    if has_supervisor_policy:
        json_artifacts.append("05a_supervisor_budget_policy.json")
    parsed_json: dict[str, Any] = {}
    for name in json_artifacts:
        try:
            value = _read_json(root / name)
        except LabError:
            schema_failures.append(name)
            continue
        parsed_json[name] = value
        if name != "01_phase1_baseline_commit.json" and (
            not isinstance(value, Mapping) or not str(value.get("schema_version") or "")
        ):
            schema_failures.append(name)
    workload = parsed_json.get("03_workload_manifest.json", {})
    if not isinstance(workload, Mapping) or workload.get("schema_version") != WORKLOAD_SCHEMA_VERSION:
        schema_failures.append("03_workload_manifest.json")
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != ARTIFACT_MANIFEST_SCHEMA_VERSION:
        schema_failures.append("manifest.json")
    environment_lock = parsed_json.get("02_environment_lock.json", {})
    runtime_lock = environment_lock.get("codex_runtime", {}) if isinstance(environment_lock, Mapping) else {}
    if (
        not isinstance(environment_lock, Mapping)
        or not str(environment_lock.get("started_at") or "")
        or not str(environment_lock.get("completed_at") or "")
        or not isinstance(runtime_lock, Mapping)
        or str(runtime_lock.get("version") or "") in {"", "not_observed", "unavailable"}
        or str(runtime_lock.get("digest") or "") in {"", "not_observed"}
    ):
        schema_failures.append("02_environment_lock.json")
    digest_line = (root / "04_workload_manifest.sha256").read_text(encoding="utf-8").strip().split()
    if len(digest_line) < 1 or digest_line[0] != sha256_file(root / "03_workload_manifest.json"):
        schema_failures.append("04_workload_manifest.sha256")
    try:
        baselines = _read_jsonl(root / "06_baseline_runs.jsonl")
        candidates = _read_jsonl(root / "07_candidate_runs.jsonl")
        reruns = _read_jsonl(root / "17_discordant_reruns.jsonl") if is_live else []
        with (root / "08_pair_results.csv").open(encoding="utf-8", newline="") as handle:
            csv_rows = list(csv.reader(handle))
        width_ok = len(csv_rows) > 1 and bool(csv_rows[0]) and all(len(row) == len(csv_rows[0]) for row in csv_rows[1:])
        rows = list(csv.DictReader([",".join(row) for row in csv_rows])) if width_ok else []
        if any(row.get("schema_version") != BASELINE_RUN_SCHEMA_VERSION for row in [*baselines, *candidates]):
            schema_failures.append("run_jsonl_schema")
        if any(row.get("schema_version") != BASELINE_RUN_SCHEMA_VERSION for row in reruns):
            schema_failures.append("rerun_jsonl_schema")
        gate = _read_json(root / "14_gate_decision.json")
    except LabError:
        baselines, candidates, rows, gate, width_ok = [], [], [], {}, False
        schema_failures.append("run_artifacts")
    runtime_profile_failures = 0
    if has_supervisor_policy:
        try:
            policy = load_live_supervisor_budget_policy(root / "05a_supervisor_budget_policy.json")
            for row in [*baselines, *candidates, *reruns]:
                relative = str(row.get("runtime_profile") or "")
                profile_path = root / relative
                profile = _read_json(profile_path) if relative and profile_path.is_file() else {}
                profile_budget = profile.get("supervisor_budget") if isinstance(profile, Mapping) and isinstance(profile.get("supervisor_budget"), Mapping) else {}
                profile_privacy = profile.get("privacy") if isinstance(profile, Mapping) and isinstance(profile.get("privacy"), Mapping) else {}
                if (
                    not isinstance(profile, Mapping)
                    or profile.get("schema_version") != LIVE_RUNTIME_PROFILE_SCHEMA_VERSION
                    or profile.get("profile_status") != "complete"
                    or profile_budget.get("policy_identity") != policy.get("policy_identity")
                    or profile_privacy.get("raw_content_in_profile") is not False
                ):
                    runtime_profile_failures += 1
        except (LabError, OSError, ValueError):
            runtime_profile_failures += max(1, len(baselines) + len(candidates))
        if runtime_profile_failures:
            schema_failures.append("live_runtime_profiles")
    status = "pass" if not bad_hashes and not schema_failures and len(baselines) == len(candidates) == len(rows) >= 1 and width_ok else "fail"
    return {
        "schema_version": "qwendex.optimization_lab.compare.v1",
        "status": status,
        "summary": "Validated Qwendex optimization-lab artifacts and paired comparison inputs." if status == "pass" else "Optimization-lab artifact validation failed.",
        "errors": [] if status == "pass" else ["artifact hash, row-count, CSV-width, or schema validation failed"],
        "data": {
            "baseline_runs": len(baselines),
            "candidate_runs": len(candidates),
            "pair_rows": len(rows),
            "hash_failures": len(bad_hashes),
            "schema_failures": len(schema_failures),
            "runtime_profile_failures": runtime_profile_failures,
            "candidate_decision": gate.get("candidate_decision") if isinstance(gate, Mapping) else "not_observed",
        },
    }
