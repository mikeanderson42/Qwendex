#!/usr/bin/env python3
"""Evaluate the tracked synthetic Manager routing corpus."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "config" / "qwendex" / "manager-routing-corpus.json"
AUTHORITY_CLASSES = {"security_or_protocol", "release_or_publish", "live_acceptance"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_qwendex() -> Any:
    path = ROOT / "scripts" / "qwendex_cli.py"
    spec = importlib.util.spec_from_file_location("qwendex_routing_eval_cli", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load Qwendex classifier: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def runtime_contract() -> dict[str, Any]:
    generation_dir = Path(str(os.environ.get("QWENDEX_RUNTIME_GENERATION_DIR") or ""))
    manifest: dict[str, Any] = {}
    if generation_dir.is_dir():
        try:
            loaded = json.loads((generation_dir / "generation.json").read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
        except (OSError, json.JSONDecodeError):
            manifest = {}
    codex = manifest.get("codex") if isinstance(manifest.get("codex"), Mapping) else {}
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), Mapping) else {}
    return {
        "runtime_generation": str(os.environ.get("QWENDEX_RUNTIME_GENERATION_ID") or manifest.get("generation_id") or ""),
        "hook_generation": str(os.environ.get("QWENDEX_HOOK_GENERATION") or manifest.get("hook_generation") or ""),
        "codex_version": str(codex.get("version") or contract.get("codex_version") or ""),
        "patch_digest": str(codex.get("patch_sha256") or contract.get("codex_patch_sha256") or ""),
        "binary_digest": str(codex.get("binary_sha256") or contract.get("patched_binary_sha256") or ""),
        "state_schema_version": int(contract.get("state_schema_version") or 2),
    }


def privacy_failures(corpus: Mapping[str, Any]) -> list[str]:
    text = json.dumps(corpus, sort_keys=True)
    failures: list[str] = []
    checks = {
        "private_home_path": re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
        "credential_marker": re.compile(r"(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}"),
        "raw_transcript_field": re.compile(r'"(?:transcript|raw_prompt|raw_output)"\s*:'),
    }
    for name, pattern in checks.items():
        if pattern.search(text):
            failures.append(name)
    return failures


def evaluate(corpus_path: Path, run_id: str) -> dict[str, Any]:
    qwendex = load_qwendex()
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    cases = corpus.get("cases") if isinstance(corpus, Mapping) else None
    if corpus.get("schema_version") != "qwendex.manager_routing_corpus.v1" or not isinstance(cases, list):
        raise RuntimeError("unsupported Manager routing corpus")
    if len(cases) < 60:
        raise RuntimeError(f"routing corpus has {len(cases)} cases; at least 60 are required")

    config = copy.deepcopy(qwendex.DEFAULT_CONFIG)
    policy = qwendex.resolve_agent_policy(config, cli_agent_use="auto", selected_manager_mode="auto")
    local_status = {
        "enabled": True,
        "available": True,
        "usable": True,
        "local_enabled": True,
        "local_available": True,
        "local_usable": True,
        "local_state": "ready",
        "model": str(config["routing"]["local_model"]),
    }
    results: list[dict[str, Any]] = []
    exact_count = 0
    authority_total = 0
    authority_passed = 0
    for case in cases:
        prompt = str(case.get("prompt") or "")
        expected = str(case.get("expected_class") or "")
        accepted = {expected, *[str(item) for item in case.get("accepted_equivalents", [])]}
        actual = qwendex.classify_manager_turn(prompt)
        effective = qwendex.effective_manager_turn_mode("auto", actual)
        plan = qwendex.build_agent_team_plan(
            config,
            prompt=prompt,
            task_id=str(case.get("id") or "routing-case"),
            agent_policy=policy,
            local_status=local_status,
            repo_root=str(ROOT),
        )
        class_pass = actual in accepted
        mode_pass = effective == str(case.get("expected_auto_mode") or "")
        if class_pass:
            exact_count += 1
        local_assignments = [
            item
            for item in plan.get("assignments", [])
            if (item.get("routing") or {}).get("token_saver_used")
            or (item.get("routing") or {}).get("selected_model") == config["routing"]["local_model"]
        ]
        authority_required = bool(case.get("authority_required")) or expected in AUTHORITY_CLASSES
        authority_pass = True
        authority_errors: list[str] = []
        if authority_required:
            authority_total += 1
            if actual not in AUTHORITY_CLASSES:
                authority_errors.append("authority_class_lost")
            if effective != "manager":
                authority_errors.append("manager_authority_mode_missing")
            profiles = set(plan.get("profiles") or [])
            if not {"reviewer", "verifier"} <= profiles:
                authority_errors.append("review_or_verifier_lane_missing")
            if local_assignments:
                authority_errors.append("critical_lane_routed_local")
            authority_pass = not authority_errors
            if authority_pass:
                authority_passed += 1
        invariants: list[str] = []
        if expected == "cross_cutting_edit" and actual == "trivial_direct":
            invariants.append("cross_cutting_classified_trivial")
        if expected == "trivial_direct" and len(plan.get("assignments") or []) > 1:
            invariants.append("trivial_prompt_forced_multiworker")
        if expected in AUTHORITY_CLASSES and local_assignments:
            invariants.append("critical_task_routed_local")
        result_pass = class_pass and mode_pass and authority_pass and not invariants
        results.append(
            {
                "id": str(case.get("id") or ""),
                "scenario": str(case.get("scenario") or ""),
                "expected_class": expected,
                "accepted_classes": sorted(accepted),
                "actual_class": actual,
                "selected_mode": "auto",
                "expected_effective_turn_mode": str(case.get("expected_auto_mode") or ""),
                "effective_turn_mode": effective,
                "assignment_count": len(plan.get("assignments") or []),
                "required_profiles": list(plan.get("profiles") or []),
                "authority_required": authority_required,
                "authority_errors": authority_errors,
                "invariant_errors": invariants,
                "result": "pass" if result_pass else "fail",
            }
        )

    score = exact_count / len(results) if results else 0.0
    authority_score = authority_passed / authority_total if authority_total else 1.0
    privacy_errors = privacy_failures(corpus)
    mismatches = [item for item in results if item["result"] != "pass"]
    passed = score >= 0.95 and authority_score == 1.0 and not mismatches and not privacy_errors
    status_lines = git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    config_path = ROOT / "config" / "qwendex" / "qwendex.json"
    schema_path = ROOT / "config" / "qwendex" / "qwendex.schema.json"
    runtime = runtime_contract()
    return {
        "schema_version": "qwendex.manager_routing_eval.v1",
        "run_id": run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_commit": git("rev-parse", "HEAD"),
        "dirty_state": "clean" if not status_lines else "in_scope_candidate",
        "config_digest": sha256_file(config_path),
        "schema_digest": sha256_file(schema_path),
        **runtime,
        "commands": [
            {
                "label": "synthetic_routing_evaluation",
                "command": [
                    "python3",
                    "scripts/qwendex_routing_eval.py",
                    "--corpus",
                    str(corpus_path.relative_to(ROOT) if corpus_path.is_relative_to(ROOT) else corpus_path),
                    "--run-id",
                    run_id,
                    "--json",
                ],
                "working_directory": ".",
                "exit_code": 0 if passed else 1,
            }
        ],
        "working_directory": str(ROOT),
        "corpus": {
            "path": str(corpus_path.relative_to(ROOT) if corpus_path.is_relative_to(ROOT) else corpus_path),
            "sha256": sha256_file(corpus_path),
            "case_count": len(results),
            "scenario_counts": dict(sorted(Counter(item["scenario"] for item in results).items())),
        },
        "thresholds": {"overall_exact_or_equivalent": 0.95, "critical_authority": 1.0},
        "results_summary": {
            "passed": sum(1 for item in results if item["result"] == "pass"),
            "failed": len(mismatches),
            "exact_or_equivalent_count": exact_count,
            "overall_exact_or_equivalent": round(score, 6),
            "critical_authority_count": authority_total,
            "critical_authority_passed": authority_passed,
            "critical_authority_score": round(authority_score, 6),
        },
        "mismatches": mismatches,
        "case_results": results,
        "privacy_status": "pass" if not privacy_errors else "fail",
        "privacy_errors": privacy_errors,
        "result": "pass" if passed else "fail",
        "final_status": "STOP_ROUTING_EVAL_ACCEPTED" if passed else "STOP_ROUTING_EVAL_BLOCKED",
        "artifact_digests": {
            "corpus_sha256": sha256_file(corpus_path),
            "case_results_sha256": canonical_digest(results),
        },
    }


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    try:
        payload = evaluate(args.corpus.expanduser().resolve(), args.run_id)
    except Exception as exc:
        payload = {
            "schema_version": "qwendex.manager_routing_eval.v1",
            "run_id": args.run_id,
            "generated_at": datetime.now(UTC).isoformat(),
            "result": "fail",
            "final_status": "STOP_ROUTING_EVAL_BLOCKED",
            "privacy_status": "unknown",
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
