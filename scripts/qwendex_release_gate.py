#!/usr/bin/env python3
"""Build a fail-closed Qwendex release receipt bound to one git tree and run."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit


SCHEMA_VERSION = "qwendex.dev.release_summary.v2"
SUMMARY_DIGEST_FIELD = "receipt_sha256"
RECEIPT_BINDING_SCHEMA = "qwendex.dev.receipt_binding.v1"
CI_ATTESTATION_SCHEMA = "qwendex.ci.attestation.v1"
CODEX_BUILD_INPUTS_SCHEMA = "qwendex.dev.codex_build_inputs.v1"
REQUIRED_RECEIPTS = {
    "bootstrap": "bootstrap.json",
    "static_gate": "static_gate.json",
    "test_gate": "test_gate.json",
    "config_gate": "config_gate.json",
    "codex_build": "codex_build.json",
    "qwendex_check": "qwendex_check.json",
    "qwendex_doctor": "qwendex_doctor.json",
    "codex_status": "codex_status_write.json",
    "codex_patch": "codex_patch_preflight.json",
    "qwendex_eval": "qwendex_eval_all.json",
    "harness_gate": "llm_harness_gate.json",
    "harness_eval": "llm_harness_eval_all.json",
}
LIVE_RECEIPTS = {
    "live_launcher": "local_qwen_launcher_check.json",
    "live_reliability": "local_qwen_reliability.json",
    "live_codex_acceptance": "local_qwen_codex_acceptance.json",
}
CI_RECEIPT = "qwendex-ci-attestation.json"
EXPECTED_RECEIPT_SCHEMAS = {
    "bootstrap": "qwendex.dev.bootstrap.v1",
    "static_gate": "qwendex.dev.gate.v1",
    "test_gate": "qwendex.dev.gate.v1",
    "config_gate": "qwendex.dev.gate.v1",
    "codex_build": "qwendex.dev.codex_build.v1",
    "qwendex_check": "qwendex.cli.v1",
    "qwendex_doctor": "qwendex.cli.v1",
    "codex_status": "qwendex.cli.v1",
    "codex_patch": "qwendex.cli.v1",
    "qwendex_eval": "qwendex.cli.v1",
    "harness_gate": "local_qwen_harness_gate.v1",
    "harness_eval": "local_qwen_harness_eval.v1",
    "dev_status": "qwendex.dev.status.v1",
    "live_launcher": "qwendex.dev.gate.v1",
    "live_reliability": "qwendex.reliability_probe.v1",
    "live_codex_acceptance": "qwendex.live_codex_acceptance.v1",
}
RECEIPT_COMMAND_IDS = {
    "bootstrap": "qwendex.bootstrap.check",
    "static_gate": "qwendex.static.full",
    "test_gate": "qwendex.tests.full",
    "config_gate": "qwendex.config.validate",
    "codex_build": "qwendex.codex.build.validate",
    "qwendex_check": "qwendex.check.strict",
    "qwendex_doctor": "qwendex.doctor.strict",
    "codex_status": "qwendex.codex-status.write",
    "codex_patch": "qwendex.codex-patch.preflight",
    "qwendex_eval": "qwendex.eval.all",
    "harness_gate": "qwendex.harness.gate",
    "harness_eval": "qwendex.harness.eval.all",
    "dev_status": "qwendex.dev-status.release",
    "live_launcher": "qwendex.local-launcher.check",
    "live_reliability": "qwendex.local-reliability.live",
    "live_codex_acceptance": "qwendex.local-codex.fresh-home",
}
DEV_GATE_CONTRACTS = {
    "static_gate": ("static", ("cmd_lint",)),
    "test_gate": ("tests_full", ("cmd_test", "--all")),
    "config_gate": ("config_json", ("cmd_validate_config_json",)),
    "live_launcher": ("live_launcher", ("scripts/run_local_qwen_codex.sh", "--check")),
}
NATIVE_COMMANDS = {
    "qwendex_check": "check",
    "qwendex_doctor": "doctor",
    "codex_status": "codex-status",
    "codex_patch": "codex-patch",
    "qwendex_eval": "eval",
}
STRICT_HEALTH_RECEIPTS = {"qwendex_check", "qwendex_doctor"}
CI_REQUIRED_CHECKS = {
    "artifact_contract",
    "bash_syntax",
    "compile",
    "config_schema",
    "downstream_install",
    "json",
    "lint",
    "pytest",
    "python_floor",
    "strict_surface",
}
CODEX_ALLOWED_BUILD_PATHS = {
    "codex-rs/Cargo.lock",
    "codex-rs/config/src/tui_keymap.rs",
    "codex-rs/core/src/config/config_tests.rs",
    "codex-rs/core/src/config/mod.rs",
    "codex-rs/core/src/hook_runtime.rs",
    "codex-rs/core/src/tools/spec_plan.rs",
    "codex-rs/hooks/src/events/session_start.rs",
    "codex-rs/hooks/src/schema.rs",
    "codex-rs/models-manager/src/manager.rs",
    "codex-rs/tui/src/app/input.rs",
    "codex-rs/tui/src/bottom_pane/status_line_setup.rs",
    "codex-rs/tui/src/bottom_pane/status_line_style.rs",
    "codex-rs/tui/src/bottom_pane/status_surface_preview.rs",
    "codex-rs/tui/src/chatwidget/status_surfaces.rs",
    "codex-rs/tui/src/keymap.rs",
    "codex-rs/tui/src/terminal_visualization_instructions.rs",
}
CODEX_REQUIRED_PATCH_PATHS = CODEX_ALLOWED_BUILD_PATHS - {"codex-rs/Cargo.lock"}
GUARD_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
    "QWENDEX_TIMEOUT",
)
STRUCTURED_MARKER_CASES = {
    "malformed_tool_envelope_suppression": {
        "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
        "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    },
    "oversized_generated_command_recovery": {"LOCAL_MODEL_TOOL_CALL_TOO_LARGE"},
}
FORBIDDEN_TRACKED_PATHS = (
    re.compile(r"(^|/)(?:\.qwendex-dev|\.qwendex/runs|results|state)(?:/|$)"),
    re.compile(r"(^|/)\.skillopt-sleep(?:/|$)"),
    re.compile(r"(^|/)(?:__pycache__|\.pytest_cache|\.ruff_cache|\.mypy_cache)(?:/|$)"),
    re.compile(r"(?:^|/)(?:\.venv|venv)(?:/|$)"),
    re.compile(
        r"\.(?:7z|bin|bz2|ckpt|gz|gguf|log|lz|lzma|onnx|pt|pyc|pth|rar|safetensors|sqlite|sqlite3|tar|tgz|txz|xz|zip)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|/)(?:\.env(?:\.(?!example$|sample$|template$)[^/]+)?|\.netrc|\.npmrc|\.pypirc)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:^|/)(?:id_rsa|id_dsa|id_ecdsa|id_ed25519)(?:\.pub)?$", re.IGNORECASE
    ),
    re.compile(
        r"^config/local_llm_stack/(?:local_harness\.env|stack_manager\.local\.json)$"
    ),
    re.compile(r"^config/local_llm_stack/.*\.private\.json$"),
    re.compile(r"(?:^|/)(?:auth|credentials?)\.json$", re.IGNORECASE),
)
PRIVATE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/var/home/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/Users/[A-Za-z0-9_.-]+(?=/)"),
    re.compile(r"(?<![A-Za-z0-9_.-])/root(?=/)"),
    re.compile(
        r"(?<![A-Za-z0-9_.-])/mnt/[a-z]/Users/[A-Za-z0-9_.-]+(?=/)", re.IGNORECASE
    ),
    re.compile(
        r"(?<![A-Za-z0-9_.-])[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+(?=\\)", re.IGNORECASE
    ),
)
SECRET_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])(?:sk|rk|pk)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
PUBLIC_VALIDATION_DENIED_KEYS = {
    "command",
    "command_receipts_root",
    "receipt_paths",
    "run_root",
    "session_id",
    "stderr_tail",
    "stdout_tail",
}
PUBLIC_VALIDATION_DENIED_TEXT = (
    re.compile(r"\bsession id:\s*[A-Za-z0-9-]+", re.IGNORECASE),
    re.compile(r"\btokens used\b", re.IGNORECASE),
    re.compile(r"results/qwendex_release_validation/"),
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def release_summary_digest(payload: dict[str, Any]) -> str:
    digest_payload = dict(payload)
    digest_payload[SUMMARY_DIGEST_FIELD] = ""
    return canonical_digest(digest_payload)


def verify_release_summary_payload(
    payload: Any, *, require_publish_ready: bool = False
) -> list[str]:
    blockers: list[str] = []
    if not isinstance(payload, dict):
        return ["release summary is not a JSON object"]
    if payload.get("schema_version") != SCHEMA_VERSION:
        blockers.append("release summary schema mismatch")
    receipt_sha = payload.get(SUMMARY_DIGEST_FIELD)
    if not is_sha256(receipt_sha):
        blockers.append("release summary receipt digest is missing or malformed")
    elif receipt_sha != release_summary_digest(payload):
        blockers.append("release summary receipt digest mismatch")
    if require_publish_ready:
        source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}
        artifact = (
            payload.get("artifact_contract")
            if isinstance(payload.get("artifact_contract"), dict)
            else {}
        )
        markers = (
            payload.get("marker_scan")
            if isinstance(payload.get("marker_scan"), dict)
            else {}
        )
        ci = (
            payload.get("ci_attestation")
            if isinstance(payload.get("ci_attestation"), dict)
            else {}
        )
        ci_online = (
            ci.get("online_verification")
            if isinstance(ci.get("online_verification"), dict)
            else {}
        )
        source_recheck = (
            payload.get("source_recheck")
            if isinstance(payload.get("source_recheck"), dict)
            else {}
        )
        initial_remote = (
            source.get("trusted_remote")
            if isinstance(source.get("trusted_remote"), dict)
            else {}
        )
        final_remote = (
            source_recheck.get("trusted_remote_recheck")
            if isinstance(source_recheck.get("trusted_remote_recheck"), dict)
            else {}
        )
        live_required = payload.get("live_required")
        codex_build_digests = (
            source.get("codex_build_digests")
            if isinstance(source.get("codex_build_digests"), dict)
            else {}
        )
        codex_build_checks = (
            gates.get("codex_build", {}).get("codex_build_checks")
            if isinstance(gates.get("codex_build"), dict)
            and isinstance(gates.get("codex_build", {}).get("codex_build_checks"), dict)
            else {}
        )
        codex_source_provenance = (
            source.get("codex_source_provenance")
            if isinstance(source.get("codex_source_provenance"), dict)
            else {}
        )
        source_commit_provenance = codex_source_provenance.get("source_commit")
        source_origin_provenance = codex_source_provenance.get("source_origin")
        canonical_source_provenance_bound = (
            isinstance(source_commit_provenance, dict)
            and source_commit_provenance.get("declarations") == 1
            and bool(
                re.fullmatch(
                    r"[0-9a-f]{40}",
                    str(source_commit_provenance.get("value") or ""),
                )
            )
            and isinstance(source_origin_provenance, dict)
            and source_origin_provenance.get("declarations") == 1
            and remote_identity(str(source_origin_provenance.get("value") or ""))
            == "github.com/openai/codex"
            and codex_build_checks.get("canonical_source_commit_matches") is True
            and codex_build_checks.get("canonical_source_origin_matches") is True
        )
        canonical_build_digests_bound = all(
            isinstance(codex_build_digests.get(name), dict)
            and codex_build_digests[name].get("declarations") == 1
            and is_sha256(codex_build_digests[name].get("value"))
            for name in ("source_patch_sha256", "cargo_lock_sha256")
        ) and all(
            codex_build_checks.get(name) is True
            for name in (
                "canonical_patch_matches",
                "canonical_cargo_lock_matches",
            )
        )
        expected_gates = set(REQUIRED_RECEIPTS) | {"dev_status"}
        if live_required is True:
            expected_gates.update(LIVE_RECEIPTS)
        gate_binding_fields = {
            "present",
            "schema_valid",
            "run_id_matches",
            "gate_matches",
            "command_matches",
            "generated_at_valid",
            "generated_fresh",
            "source_matches",
            "payload_sha256_matches",
        }
        gate_contracts_pass = set(gates) == expected_gates
        if gate_contracts_pass:
            for item in gates.values():
                if not isinstance(item, dict) or item.get("passed") is not True:
                    gate_contracts_pass = False
                    break
                binding = item.get("release_binding")
                if not isinstance(binding, dict) or not all(
                    binding.get(field) is True for field in gate_binding_fields
                ):
                    gate_contracts_pass = False
                    break
        evidence_core = {
            "run_id": payload.get("run_id"),
            "tier": payload.get("tier"),
            "source": source,
            "gates": gates,
            "artifact_contract": artifact,
            "marker_scan": markers,
            "ci_attestation": ci,
            "source_recheck": source_recheck,
        }
        required = {
            "status_passed": payload.get("status") == "pass",
            "recommendation_publish_ready": payload.get("recommendation")
            == "publish-ready",
            "publish_ready": payload.get("publish_ready") is True,
            "not_candidate": payload.get("candidate_mode") is False,
            "live_required_is_boolean": isinstance(live_required, bool),
            "no_blockers": payload.get("blockers") == []
            and payload.get("evidence_blockers") == []
            and payload.get("publish_blockers") == [],
            "source_publish_contract": source.get("clean") is True
            and source.get("branch") == source.get("default_branch")
            and source.get("remote_default_matches_head") is True
            and source.get("origin_matches_trusted") is True
            and source.get("tag_annotated") is True
            and source.get("tag_matches_head") is True,
            "canonical_codex_build_digests_bound": canonical_build_digests_bound,
            "canonical_codex_source_provenance_bound": canonical_source_provenance_bound,
            "initial_trusted_remote_passed": initial_remote.get("required") is True
            and initial_remote.get("queried") is True
            and initial_remote.get("matches_expected") is True,
            "artifact_passed": artifact.get("status") == "pass",
            "markers_passed": markers.get("status") == "pass",
            "ci_passed": ci.get("passed") is True,
            "ci_online_passed": ci_online.get("required") is True
            and ci_online.get("queried") is True
            and ci_online.get("artifact_downloaded") is True
            and ci_online.get("passed") is True
            and bool(ci_online.get("checks"))
            and all(value is True for value in ci_online.get("checks", {}).values()),
            "source_recheck_passed": source_recheck.get("matches_initial_source")
            is True,
            "final_trusted_remote_passed": final_remote.get("required") is True
            and final_remote.get("queried") is True
            and final_remote.get("matches_expected") is True,
            "exact_gate_contract": gate_contracts_pass,
            "evidence_digest_matches": is_sha256(payload.get("evidence_sha256"))
            and payload.get("evidence_sha256") == canonical_digest(evidence_core),
        }
        blockers.extend(
            f"release summary publish-ready check failed: {name}"
            for name, passed in required.items()
            if not passed
        )
    return blockers


def replay_publish_ready_summary(
    payload: dict[str, Any], *, summary_path: Path, repo_root: Path
) -> list[str]:
    blockers: list[str] = []
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    run_id = str(payload.get("run_id") or "")
    evidence_root = (
        summary_path.parent
        if summary_path.parent.name == run_id
        else summary_path.parent / "verify" / run_id
    )
    gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}
    replay_gate_payloads: dict[str, Any] = {}
    for name, item in gates.items():
        if not isinstance(item, dict):
            blockers.append(f"publish replay gate is malformed: {name}")
            continue
        relative = str(item.get("path") or "")
        try:
            gate_path = (evidence_root / relative).resolve()
            gate_path.relative_to(evidence_root.resolve())
            raw = gate_path.read_bytes()
        except (OSError, ValueError) as exc:
            blockers.append(f"publish replay could not read gate {name}: {exc}")
            continue
        if sha256_bytes(raw) != item.get("sha256"):
            blockers.append(f"publish replay gate digest mismatch: {name}")
        try:
            replay_gate_payloads[name] = json.loads(raw)
        except json.JSONDecodeError:
            blockers.append(f"publish replay gate is invalid JSON: {name}")
    trusted_identity = str(source.get("trusted_origin_repository") or "")
    trusted_origin = f"https://{trusted_identity}.git" if trusted_identity else ""
    try:
        replay_source, source_evidence, source_publish = source_contract(
            repo_root,
            str(source.get("version") or ""),
            str(source.get("expected_tag") or ""),
            str(source.get("codex_version") or ""),
            str(source.get("default_branch") or ""),
            trusted_origin,
        )
        blockers.extend(f"publish replay source: {item}" for item in source_evidence)
        blockers.extend(f"publish replay source: {item}" for item in source_publish)
        if replay_source.get("commit") != source.get("commit") or replay_source.get(
            "tree"
        ) != source.get("tree"):
            blockers.append("publish replay source commit/tree changed")
        if replay_source.get("codex_build_digests") != source.get(
            "codex_build_digests"
        ):
            blockers.append("publish replay canonical Codex build digests changed")
        if replay_source.get("codex_source_provenance") != source.get(
            "codex_source_provenance"
        ):
            blockers.append("publish replay canonical Codex source provenance changed")
        replay_source_provenance = replay_source.get("codex_source_provenance")
        replay_source_provenance = (
            replay_source_provenance
            if isinstance(replay_source_provenance, dict)
            else {}
        )
        replay_source_commit = replay_source_provenance.get("source_commit")
        replay_source_origin = replay_source_provenance.get("source_origin")
        replay_source_commit = (
            str(replay_source_commit.get("value") or "")
            if isinstance(replay_source_commit, dict)
            else ""
        )
        replay_source_origin = (
            str(replay_source_origin.get("value") or "")
            if isinstance(replay_source_origin, dict)
            else ""
        )
        replay_build_digests = replay_source.get("codex_build_digests")
        replay_build_digests = (
            replay_build_digests if isinstance(replay_build_digests, dict) else {}
        )
        replay_patch_digest = replay_build_digests.get("source_patch_sha256")
        replay_lock_digest = replay_build_digests.get("cargo_lock_sha256")
        replay_patch_digest = (
            str(replay_patch_digest.get("value") or "")
            if isinstance(replay_patch_digest, dict)
            else ""
        )
        replay_lock_digest = (
            str(replay_lock_digest.get("value") or "")
            if isinstance(replay_lock_digest, dict)
            else ""
        )
        codex_build_payload = replay_gate_payloads.get("codex_build")
        if not isinstance(codex_build_payload, dict):
            blockers.append("publish replay Codex build receipt is not an object")
        else:
            _, codex_build_blockers = validate_codex_build_receipt(
                codex_build_payload,
                str(replay_source.get("codex_version") or ""),
                replay_source_commit,
                replay_source_origin,
                replay_patch_digest,
                replay_lock_digest,
            )
            blockers.extend(
                f"publish replay Codex build: {item}"
                for item in codex_build_blockers
            )
        replay_artifacts, artifact_blockers = artifact_contract(
            repo_root, str(source.get("commit") or "HEAD")
        )
        blockers.extend(f"publish replay artifact: {item}" for item in artifact_blockers)
        if replay_artifacts.get("tree_manifest_sha256") != payload.get(
            "artifact_contract", {}
        ).get("tree_manifest_sha256"):
            blockers.append("publish replay artifact manifest changed")
        remote, remote_blockers = trusted_remote_branch_tip(
            trusted_origin,
            str(source.get("default_branch") or ""),
            str(source.get("commit") or ""),
        )
        blockers.extend(f"publish replay remote: {item}" for item in remote_blockers)
        if not remote.get("matches_expected"):
            blockers.append("publish replay trusted remote does not match")
        source_recheck, recheck_blockers = source_still_matches(
            repo_root, source, trusted_origin=trusted_origin
        )
        blockers.extend(f"publish replay recheck: {item}" for item in recheck_blockers)
        if source_recheck.get("matches_initial_source") is not True:
            blockers.append("publish replay source recheck failed")
        ci = (
            payload.get("ci_attestation")
            if isinstance(payload.get("ci_attestation"), dict)
            else {}
        )
        ci_path = summary_path.parent / CI_RECEIPT
        ci_online, ci_blockers = verify_ci_attestation_online(
            ci_path,
            replay_source,
            replay_artifacts,
            expected_sha256=str(ci.get("sha256") or ""),
        )
        blockers.extend(f"publish replay CI: {item}" for item in ci_blockers)
        if ci_online.get("passed") is not True:
            blockers.append("publish replay online CI verification failed")
        final_source_recheck, final_recheck_blockers = source_still_matches(
            repo_root, replay_source, trusted_origin=trusted_origin
        )
        blockers.extend(
            f"publish replay final recheck: {item}"
            for item in final_recheck_blockers
        )
        if final_source_recheck.get("matches_initial_source") is not True:
            blockers.append("publish replay final source recheck failed")
    except Exception as exc:
        blockers.append(f"publish replay failed closed: {exc}")
    return list(dict.fromkeys(blockers))


def verify_release_summary_file(
    path: Path, *, require_publish_ready: bool, repo_root: Path | None = None
) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"release-summary-verify: blocked - {exc}", file=sys.stderr)
        return 1
    blockers = verify_release_summary_payload(
        payload, require_publish_ready=require_publish_ready
    )
    if require_publish_ready and isinstance(payload, dict):
        blockers.extend(
            replay_publish_ready_summary(
                payload,
                summary_path=path,
                repo_root=(repo_root or Path.cwd()).resolve(),
            )
        )
    if blockers:
        print(
            "release-summary-verify: blocked - " + "; ".join(blockers),
            file=sys.stderr,
        )
        return 1
    print(f"release-summary-verify: pass - {path}")
    return 0


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def is_sha256(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "")))


def payload_digest(payload: dict[str, Any]) -> str:
    unbound = {key: value for key, value in payload.items() if key != "release_binding"}
    return canonical_digest(unbound)


def remote_identity(value: str) -> str:
    """Return host/owner/repo without credentials, transport, suffix, or case drift."""
    raw = value.strip()
    if not raw:
        return ""
    scp_match = re.fullmatch(r"(?:[^@/\s]+@)?([^:/\s]+):(.+)", raw)
    if scp_match and "://" not in raw:
        host, path = scp_match.groups()
    else:
        parsed = urlsplit(raw)
        if parsed.scheme not in {"https", "ssh"} or not parsed.hostname:
            return ""
        host, path = parsed.hostname, parsed.path
    normalized_path = path.strip("/")
    if normalized_path.endswith(".git"):
        normalized_path = normalized_path[:-4]
    if not normalized_path or len(normalized_path.split("/")) != 2:
        return ""
    return f"{host.lower()}/{normalized_path.lower()}"


def attestation_repository_identity(value: str) -> str:
    raw = value.strip().strip("/")
    if not raw:
        return ""
    if "://" in raw or raw.startswith("git@"):
        return remote_identity(raw)
    if len(raw.split("/")) != 2:
        return ""
    return f"github.com/{raw.lower().removesuffix('.git')}"


def github_repository_slug(identity: str) -> str:
    prefix = "github.com/"
    return identity[len(prefix) :] if identity.startswith(prefix) else ""


def gh_json(*args: str, timeout: int = 60) -> Any:
    result = subprocess.run(
        ["gh", *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"gh {' '.join(args)} failed: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh {' '.join(args)} returned invalid JSON") from exc


def trusted_remote_branch_tip(
    trusted_origin: str, default_branch: str, expected_commit: str
) -> tuple[dict[str, Any], list[str]]:
    identity = remote_identity(trusted_origin)
    slug = github_repository_slug(identity)
    item: dict[str, Any] = {
        "queried": False,
        "repository": identity,
        "branch": default_branch,
        "commit": "",
        "matches_expected": False,
    }
    if not slug:
        return item, ["trusted origin is not a GitHub owner/repository URL"]
    try:
        payload = gh_json(
            "api", f"repos/{slug}/git/ref/heads/{quote(default_branch, safe='')}"
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return item, [f"could not query trusted remote default branch: {exc}"]
    commit = ""
    if isinstance(payload, dict) and isinstance(payload.get("object"), dict):
        commit = str(payload["object"].get("sha") or "")
    item.update(
        {
            "queried": True,
            "commit": commit,
            "matches_expected": bool(commit) and commit == expected_commit,
        }
    )
    blockers = []
    if not commit:
        blockers.append("trusted remote default branch response has no commit")
    elif commit != expected_commit:
        blockers.append("trusted remote default branch does not match release HEAD")
    return item, blockers


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip() if result.returncode == 0 else ""


def git_blob(repo: Path, oid: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "blob", oid],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git cat-file blob {oid} failed")
    return result.stdout


def tree_entries(repo: Path, treeish: str = "HEAD") -> list[dict[str, str]]:
    result = subprocess.run(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", "--full-tree", treeish],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout).decode(errors="replace").strip()
        )
    entries: list[dict[str, str]] = []
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        entries.append(
            {
                "mode": mode,
                "type": kind,
                "oid": oid,
                "path": raw_path.decode("utf-8", errors="surrogateescape"),
            }
        )
    return entries


def line_hits(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[int]:
    return [
        line_no
        for line_no, line in enumerate(text.splitlines(), start=1)
        if any(pattern.search(line) for pattern in patterns)
    ]


def public_validation_leaks(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    leaks: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            if key in PUBLIC_VALIDATION_DENIED_KEYS:
                leaks.append(".".join(child_path))
            leaks.extend(public_validation_leaks(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            leaks.extend(public_validation_leaks(child, (*path, str(index))))
    elif isinstance(value, str) and any(
        pattern.search(value) for pattern in PUBLIC_VALIDATION_DENIED_TEXT
    ):
        leaks.append(".".join(path))
    return leaks


def artifact_contract(
    repo: Path, treeish: str = "HEAD"
) -> tuple[dict[str, Any], list[str]]:
    entries = tree_entries(repo, treeish)
    forbidden_paths: list[str] = []
    private_path_hits: list[str] = []
    secret_hits: list[str] = []
    validation_summary_leaks: list[str] = []
    unsafe_symlinks: list[str] = []
    unsupported_entries: list[str] = []
    manifest_lines: list[str] = []
    binary_files_scanned = 0

    for entry in entries:
        path = entry["path"]
        manifest_lines.append(f"{entry['mode']} {entry['type']} {entry['oid']}\t{path}")
        if any(pattern.search(path) for pattern in FORBIDDEN_TRACKED_PATHS):
            forbidden_paths.append(path)
        if entry["type"] != "blob":
            unsupported_entries.append(path)
            continue
        data = git_blob(repo, entry["oid"])
        if b"\0" in data:
            binary_files_scanned += 1
        text = data.decode("utf-8", errors="replace")
        private_path_hits.extend(
            f"{path}:{line}" for line in line_hits(text, PRIVATE_PATH_PATTERNS)
        )
        secret_hits.extend(
            f"{path}:{line}" for line in line_hits(text, SECRET_PATTERNS)
        )
        if entry["mode"] == "120000":
            target = text.strip()
            normalized = posixpath.normpath(
                posixpath.join(posixpath.dirname(path), target)
            )
            if (
                target.startswith(("/", "~"))
                or re.match(r"^[A-Za-z]:[\\/]", target)
                or normalized == ".."
                or normalized.startswith("../")
            ):
                unsafe_symlinks.append(path)
            continue
        if path.startswith("docs/validation/") and path.endswith(".json"):
            try:
                validation_payload = json.loads(text)
            except json.JSONDecodeError:
                validation_summary_leaks.append(f"{path}:invalid-json")
            else:
                validation_summary_leaks.extend(
                    f"{path}:{item}"
                    for item in public_validation_leaks(validation_payload)
                )

    blockers = []
    for label, values in (
        ("forbidden tracked runtime/private paths", forbidden_paths),
        ("private absolute workspace paths", private_path_hits),
        ("secret-shaped material", secret_hits),
        (
            "raw transcript/session fields in public validation summaries",
            validation_summary_leaks,
        ),
        ("unsafe tracked symlinks", unsafe_symlinks),
        ("unsupported tracked git entries", unsupported_entries),
    ):
        if values:
            blockers.append(f"artifact contract found {len(values)} {label}")
    return {
        "status": "pass" if not blockers else "blocked",
        "tracked_file_count": len(entries),
        "binary_files_scanned": binary_files_scanned,
        "tree_manifest_sha256": sha256_bytes(
            ("\n".join(manifest_lines) + "\n").encode()
        ),
        "forbidden_paths": forbidden_paths,
        "private_path_hits": private_path_hits,
        "secret_hits": secret_hits,
        "validation_summary_leaks": validation_summary_leaks,
        "unsafe_symlinks": unsafe_symlinks,
        "unsupported_entries": unsupported_entries,
    }, blockers


def version_from_python(data: bytes) -> str:
    module = ast.parse(data.decode("utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == "VERSION"
            for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                return node.value.value
    return ""


def version_sources(
    repo: Path, entries: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    blobs = {entry["path"]: entry for entry in entries if entry["type"] == "blob"}

    def read(path: str) -> bytes:
        entry = blobs.get(path)
        return git_blob(repo, entry["oid"]) if entry else b""

    sources: dict[str, dict[str, str]] = {}
    raw = read("scripts/qwendex_cli.py")
    sources["cli"] = {
        "value": version_from_python(raw) if raw else "",
        "sha256": sha256_bytes(raw),
    }
    for name, path in (
        ("config", "config/qwendex/qwendex.json"),
        ("sample_config", "config/qwendex/qwendex.sample.json"),
    ):
        raw = read(path)
        try:
            value = str(json.loads(raw).get("version", "")) if raw else ""
        except json.JSONDecodeError:
            value = ""
        sources[name] = {"value": value, "sha256": sha256_bytes(raw)}
    patterns = (
        ("readme", "README.md", r"seeded as `v([^`]+)`"),
        (
            "release_notes",
            "public/qwendex/release-notes.md",
            r"(?m)^## (?!Unreleased\s*$)([^\s]+)\s*$",
        ),
        ("release_file", "RELEASE.md", r"(?m)^# v([^\s]+)\s*$"),
    )
    for name, path, pattern in patterns:
        raw = read(path)
        match = (
            re.search(pattern, raw.decode("utf-8", errors="replace")) if raw else None
        )
        sources[name] = {
            "value": match.group(1) if match else "",
            "sha256": sha256_bytes(raw),
        }
    return sources


def codex_version_sources(
    repo: Path, entries: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    blobs = {entry["path"]: entry for entry in entries if entry["type"] == "blob"}
    patterns = (
        (
            "dev_env",
            "scripts/qwendex_dev_env",
            r'QWENDEX_RELEASE_CODEX_VERSION="([^"]+)"',
        ),
        (
            "installer",
            "scripts/qwendex_install_deps",
            r'QWENDEX_CODEX_REQUIRED_VERSION="\$\{QWENDEX_CODEX_REQUIRED_VERSION:-([^}]+)\}"',
        ),
        ("readme", "README.md", r"@openai/codex@([0-9]+\.[0-9]+\.[0-9]+)"),
        (
            "quickstart",
            "public/qwendex/quickstart.md",
            r"requires Codex CLI `([^`]+)`",
        ),
        (
            "release_file",
            "RELEASE.md",
            r"Codex `([0-9]+\.[0-9]+\.[0-9]+)`",
        ),
    )
    sources: dict[str, dict[str, str]] = {}
    for name, path, pattern in patterns:
        entry = blobs.get(path)
        raw = git_blob(repo, entry["oid"]) if entry else b""
        match = (
            re.search(pattern, raw.decode("utf-8", errors="replace")) if raw else None
        )
        sources[name] = {
            "value": match.group(1) if match else "",
            "sha256": sha256_bytes(raw),
        }
    return sources


def codex_build_digest_sources(
    repo: Path, entries: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    """Read the literal canonical Codex build digests from the tracked tree."""
    blobs = {entry["path"]: entry for entry in entries if entry["type"] == "blob"}
    path = "scripts/qwendex_dev_env"
    entry = blobs.get(path)
    raw = git_blob(repo, entry["oid"]) if entry else b""
    text = raw.decode("utf-8", errors="replace")
    patterns = {
        "source_patch_sha256": (
            r'(?m)^QWENDEX_RELEASE_CODEX_PATCH_SHA256="([0-9a-f]{64})"$'
        ),
        "cargo_lock_sha256": (
            r'(?m)^QWENDEX_RELEASE_CODEX_CARGO_LOCK_SHA256="([0-9a-f]{64})"$'
        ),
    }
    sources: dict[str, dict[str, Any]] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, text)
        sources[name] = {
            "value": matches[0] if len(matches) == 1 else "",
            "declarations": len(matches),
            "path": path,
            "source_sha256": sha256_bytes(raw),
        }
    return sources


def codex_source_provenance_sources(
    repo: Path, entries: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:
    blobs = {entry["path"]: entry for entry in entries if entry["type"] == "blob"}
    path = "scripts/qwendex_dev_env"
    entry = blobs.get(path)
    raw = git_blob(repo, entry["oid"]) if entry else b""
    text = raw.decode("utf-8", errors="replace")
    patterns = {
        "source_commit": (
            r'(?m)^QWENDEX_RELEASE_CODEX_SOURCE_COMMIT="([0-9a-f]{40})"$'
        ),
        "source_origin": (
            r'(?m)^QWENDEX_RELEASE_CODEX_SOURCE_REPO="([^"]+)"$'
        ),
    }
    sources: dict[str, dict[str, Any]] = {}
    for name, pattern in patterns.items():
        matches = re.findall(pattern, text)
        sources[name] = {
            "value": matches[0] if len(matches) == 1 else "",
            "declarations": len(matches),
            "path": path,
            "source_sha256": sha256_bytes(raw),
        }
    return sources


def source_contract(
    repo: Path,
    expected_version: str,
    expected_tag: str,
    expected_codex_version: str,
    default_branch_arg: str,
    trusted_origin_arg: str,
) -> tuple[dict[str, Any], list[str], list[str]]:
    commit = git(repo, "rev-parse", "HEAD")
    tree = git(repo, "rev-parse", "HEAD^{tree}")
    entries = tree_entries(repo, commit)
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    dirty_lines = [
        line
        for line in git(
            repo, "status", "--porcelain=v1", "--untracked-files=all"
        ).splitlines()
        if line
    ]
    if default_branch_arg:
        default_branch = default_branch_arg
        default_branch_source = "argument"
    else:
        remote_head = git(
            repo,
            "symbolic-ref",
            "--quiet",
            "--short",
            "refs/remotes/origin/HEAD",
            check=False,
        )
        default_branch = (
            remote_head.split("/", 1)[1]
            if remote_head.startswith("origin/")
            else "main"
        )
        default_branch_source = "origin/HEAD" if remote_head else "fallback"
    configured_origin = git(repo, "remote", "get-url", "origin", check=False)
    configured_origin_identity = remote_identity(configured_origin)
    trusted_origin_identity = remote_identity(trusted_origin_arg)
    tag_ref = f"refs/tags/{expected_tag}"
    tag_type = git(repo, "cat-file", "-t", tag_ref, check=False)
    tag_target = git(
        repo, "rev-parse", "--verify", f"{tag_ref}^{{commit}}", check=False
    )
    remote_default_ref = f"refs/remotes/origin/{default_branch}"
    remote_default_commit = git(
        repo, "rev-parse", "--verify", f"{remote_default_ref}^{{commit}}", check=False
    )
    versions = version_sources(repo, entries)
    codex_versions = codex_version_sources(repo, entries)
    codex_build_digests = codex_build_digest_sources(repo, entries)
    codex_source_provenance = codex_source_provenance_sources(repo, entries)
    evidence_blockers: list[str] = []
    publish_blockers: list[str] = []
    if dirty_lines:
        evidence_blockers.append("source worktree is not clean")
    if not branch:
        publish_blockers.append("source HEAD is detached")
    elif branch != default_branch:
        publish_blockers.append(
            f"source branch {branch!r} is not default branch {default_branch!r}"
        )
    if not remote_default_commit:
        publish_blockers.append(f"remote default ref {remote_default_ref!r} is missing")
    elif remote_default_commit != commit:
        publish_blockers.append(
            f"HEAD does not match remote default ref {remote_default_ref!r}"
        )
    if not configured_origin_identity:
        publish_blockers.append(
            "configured origin is missing or is not a supported repository URL"
        )
    if not trusted_origin_identity:
        publish_blockers.append(
            "trusted origin was not supplied or is not a supported repository URL"
        )
    elif (
        configured_origin_identity
        and configured_origin_identity != trusted_origin_identity
    ):
        publish_blockers.append("configured origin does not match the trusted origin")
    if expected_tag != f"v{expected_version}":
        evidence_blockers.append("expected tag does not equal v<expected-version>")
    if not tag_target:
        publish_blockers.append(f"expected local tag {expected_tag!r} is missing")
    elif tag_target != commit:
        publish_blockers.append(
            f"expected local tag {expected_tag!r} does not point at HEAD"
        )
    if tag_target and tag_type != "tag":
        publish_blockers.append(f"expected local tag {expected_tag!r} is not annotated")
    for name, item in versions.items():
        if item["value"] != expected_version:
            evidence_blockers.append(
                f"version source {name} is {item['value']!r}, expected {expected_version!r}"
            )
    if not expected_codex_version:
        evidence_blockers.append("expected Codex version was not supplied")
    for name, item in codex_versions.items():
        if item["value"] != expected_codex_version:
            evidence_blockers.append(
                f"Codex version source {name} is {item['value']!r}, expected {expected_codex_version!r}"
            )
    for name, item in codex_build_digests.items():
        if item.get("declarations") != 1 or not is_sha256(item.get("value")):
            evidence_blockers.append(
                f"Codex build digest source {name} is missing, duplicated, or malformed"
            )
    source_commit_item = codex_source_provenance.get("source_commit", {})
    if source_commit_item.get("declarations") != 1 or not re.fullmatch(
        r"[0-9a-f]{40}", str(source_commit_item.get("value") or "")
    ):
        evidence_blockers.append(
            "Codex source commit is missing, duplicated, or malformed"
        )
    source_origin_item = codex_source_provenance.get("source_origin", {})
    if (
        source_origin_item.get("declarations") != 1
        or remote_identity(str(source_origin_item.get("value") or ""))
        != "github.com/openai/codex"
    ):
        evidence_blockers.append(
            "Codex source origin is missing, duplicated, malformed, or not openai/codex"
        )
    return (
        {
            "commit": commit,
            "tree": tree,
            "branch": branch,
            "default_branch": default_branch,
            "default_branch_source": default_branch_source,
            "remote_default_ref": remote_default_ref,
            "remote_default_commit": remote_default_commit,
            "remote_default_matches_head": bool(remote_default_commit)
            and remote_default_commit == commit,
            "origin_repository": configured_origin_identity,
            "trusted_origin_repository": trusted_origin_identity,
            "origin_matches_trusted": bool(configured_origin_identity)
            and configured_origin_identity == trusted_origin_identity,
            "clean": not dirty_lines,
            "dirty_paths": [
                line[3:] if len(line) > 3 else line for line in dirty_lines
            ],
            "version": expected_version,
            "version_sources": versions,
            "codex_version": expected_codex_version,
            "codex_version_sources": codex_versions,
            "codex_build_digests": codex_build_digests,
            "codex_source_provenance": codex_source_provenance,
            "expected_tag": expected_tag,
            "tag_exists": bool(tag_target),
            "tag_type": tag_type,
            "tag_annotated": tag_type == "tag",
            "tag_target": tag_target,
            "tag_matches_head": bool(tag_target) and tag_target == commit,
        },
        evidence_blockers,
        publish_blockers,
    )


def receipt_passed(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if "status" in payload:
        return payload.get("status") == "pass"
    if "success" in payload:
        return payload.get("success") is True
    return False


def validate_receipt_binding(
    name: str,
    payload: dict[str, Any],
    run_id: str,
    run_started: datetime,
    inspected_at: datetime,
    source_commit: str,
    source_tree: str,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    binding = payload.get("release_binding")
    result: dict[str, Any] = {
        "present": isinstance(binding, dict),
        "schema_valid": False,
        "run_id_matches": False,
        "gate_matches": False,
        "command_matches": False,
        "generated_at_valid": False,
        "generated_fresh": False,
        "source_matches": False,
        "payload_sha256_matches": False,
    }
    if not isinstance(binding, dict):
        return result, [f"required receipt has no release_binding object: {name}"]
    result["schema_valid"] = binding.get("schema_version") == RECEIPT_BINDING_SCHEMA
    if not result["schema_valid"]:
        blockers.append(f"release binding schema mismatch: {name}")
    result["run_id_matches"] = binding.get("run_id") == run_id
    if not result["run_id_matches"]:
        blockers.append(f"release binding run_id mismatch: {name}")
    result["gate_matches"] = binding.get("gate") == name
    if not result["gate_matches"]:
        blockers.append(f"release binding gate mismatch: {name}")
    result["command_matches"] = binding.get("command_id") == RECEIPT_COMMAND_IDS.get(
        name
    )
    if not result["command_matches"]:
        blockers.append(f"release binding command_id mismatch: {name}")
    generated_at = str(binding.get("generated_at") or "")
    if not generated_at:
        blockers.append(f"release binding has no generated_at: {name}")
    else:
        try:
            generated_time = parse_utc(generated_at)
        except ValueError:
            blockers.append(f"release binding has invalid generated_at: {name}")
        else:
            result["generated_at_valid"] = True
            result["generated_fresh"] = (
                run_started <= generated_time <= inspected_at + timedelta(minutes=5)
            )
            if generated_time < run_started:
                blockers.append(
                    f"release binding generated_at predates release run: {name}"
                )
            elif generated_time > inspected_at + timedelta(minutes=5):
                blockers.append(
                    f"release binding generated_at is implausibly in the future: {name}"
                )
    binding_source = binding.get("source")
    result["source_matches"] = (
        isinstance(binding_source, dict)
        and binding_source.get("commit") == source_commit
        and binding_source.get("tree") == source_tree
    )
    if not result["source_matches"]:
        blockers.append(f"release binding source commit/tree mismatch: {name}")
    expected_payload_sha = payload_digest(payload)
    result["payload_sha256_matches"] = (
        binding.get("payload_sha256") == expected_payload_sha
    )
    if not result["payload_sha256_matches"]:
        blockers.append(f"release binding payload digest mismatch: {name}")
    if name in STRICT_HEALTH_RECEIPTS and binding.get("health_mode") != "strict":
        blockers.append(f"release binding is not strict health evidence: {name}")
    return result, blockers


def validate_codex_build_receipt(
    payload: dict[str, Any],
    expected_codex_version: str,
    expected_source_commit: str,
    expected_source_origin: str,
    expected_source_patch_sha256: str,
    expected_cargo_lock_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    preflight = payload.get("preflight")
    build_inputs = payload.get("build_inputs")
    source_ref = str(payload.get("source_ref") or "")
    derived_version = (
        source_ref.removeprefix("rust-v") if source_ref.startswith("rust-v") else ""
    )
    required_version = expected_codex_version or derived_version
    binary_path = Path(str(payload.get("binary") or "")).expanduser()
    binary_exists = binary_path.exists()
    binary_is_symlink = binary_path.is_symlink()
    binary_regular = binary_path.is_file() and not binary_is_symlink
    binary_executable = (
        binary_regular
        and bool(binary_path.stat().st_mode & 0o111)
        and os.access(binary_path, os.X_OK)
    )
    binary_bytes = payload.get("binary_bytes")
    binary_sha = str(payload.get("binary_sha256") or "")
    binary_size_matches = (
        binary_regular
        and isinstance(binary_bytes, int)
        and binary_bytes == binary_path.stat().st_size
    )
    binary_digest_matches = (
        binary_regular
        and is_sha256(binary_sha)
        and sha256_file(binary_path) == binary_sha
    )
    nested = build_inputs if isinstance(build_inputs, dict) else {}
    changed_paths = nested.get("changed_paths")
    clean_inputs = all(
        isinstance(nested.get(key), list) and not nested.get(key)
        for key in (
            "blockers",
            "unexpected_changes",
            "missing_patch_paths",
            "untracked_paths",
            "unmerged_entries",
        )
    )
    source_consistent = bool(source_ref) and all(
        (
            nested.get("source_ref") == source_ref,
            nested.get("source_head") == payload.get("source_head"),
            nested.get("source_ref_target") == payload.get("source_head"),
        )
    )
    optional_top_ref_target = str(payload.get("source_ref_target") or "")
    declared_source_commits = [
        payload.get("source_head"),
        nested.get("source_head"),
        nested.get("source_ref_target"),
    ]
    declared_source_commits.extend(
        value
        for value in (
            payload.get("expected_source_commit"),
            nested.get("expected_source_commit"),
        )
        if value
    )
    canonical_source_commit_matches = bool(
        re.fullmatch(r"[0-9a-f]{40}", expected_source_commit)
    ) and all(
        value == expected_source_commit
        for value in declared_source_commits
    )
    if optional_top_ref_target:
        canonical_source_commit_matches = (
            canonical_source_commit_matches
            and optional_top_ref_target == expected_source_commit
        )
    expected_source_identity = remote_identity(expected_source_origin)
    declared_source_origins = [
        str(value)
        for value in (
            payload.get("source_origin"),
            payload.get("expected_source_origin"),
            nested.get("source_origin"),
            nested.get("expected_source_origin"),
        )
        if value
    ]
    canonical_source_origin_matches = (
        expected_source_identity == "github.com/openai/codex"
        and all(
            remote_identity(value) == expected_source_identity
            for value in declared_source_origins
        )
    )
    digest_fields = (
        payload.get("source_patch_sha256"),
        payload.get("expected_source_patch_sha256"),
        payload.get("source_tree_manifest_sha256"),
        payload.get("cargo_lock_sha256"),
        payload.get("expected_cargo_lock_sha256"),
        payload.get("build_inputs_sha256"),
        payload.get("source_receipt_sha256"),
        nested.get("source_patch_sha256"),
        nested.get("expected_source_patch_sha256"),
        nested.get("source_tree_manifest_sha256"),
        nested.get("cargo_lock_sha256"),
        nested.get("expected_cargo_lock_sha256"),
        (
            nested.get("project_cargo_config", {}).get("sha256")
            if isinstance(nested.get("project_cargo_config"), dict)
            else ""
        ),
        preflight.get("sha256") if isinstance(preflight, dict) else "",
    )
    copies_match = all(
        (
            payload.get("source_patch_sha256") == nested.get("source_patch_sha256"),
            payload.get("expected_source_patch_sha256")
            == nested.get("expected_source_patch_sha256"),
            payload.get("source_tree_manifest_sha256")
            == nested.get("source_tree_manifest_sha256"),
            payload.get("cargo_lock_sha256") == nested.get("cargo_lock_sha256"),
            payload.get("expected_cargo_lock_sha256")
            == nested.get("expected_cargo_lock_sha256"),
            payload.get("cargo_version") == nested.get("cargo_version"),
            payload.get("rustc_version") == nested.get("rustc_version"),
            payload.get("build_isolation") == nested.get("build_isolation"),
            payload.get("cargo_home_policy") == nested.get("cargo_home_policy"),
            payload.get("cargo_home_config_files")
            == nested.get("cargo_home_config_files"),
            payload.get("project_cargo_config")
            == nested.get("project_cargo_config"),
            payload.get("dependency_fetch_policy")
            == nested.get("dependency_fetch_policy"),
            payload.get("source_patch_paths") == nested.get("changed_paths"),
        )
    )
    checks = {
        "run_id_present": bool(payload.get("run_id")),
        "preflight_passed": isinstance(preflight, dict)
        and preflight.get("status") == "pass",
        "binary_exists": binary_exists,
        "binary_not_symlink": not binary_is_symlink,
        "binary_regular": binary_regular,
        "binary_executable": binary_executable,
        "binary_nonempty": isinstance(binary_bytes, int)
        and not isinstance(binary_bytes, bool)
        and binary_bytes > 0,
        "binary_size_matches": binary_size_matches,
        "binary_sha256_present": is_sha256(binary_sha),
        "binary_digest_matches": binary_digest_matches,
        "expected_version": required_version,
        "source_ref_matches_version": bool(required_version)
        and source_ref == f"rust-v{required_version}",
        "version_matches": bool(required_version)
        and str(payload.get("binary_version") or "").strip()
        == f"codex-cli {required_version}",
        "build_inputs_schema_valid": nested.get("schema_version")
        == CODEX_BUILD_INPUTS_SCHEMA,
        "build_inputs_passed": nested.get("status") == "pass" and clean_inputs,
        "source_ref_head_consistent": source_consistent,
        "canonical_source_commit_matches": canonical_source_commit_matches,
        "canonical_source_origin_matches": canonical_source_origin_matches,
        "changed_paths_allowlisted": isinstance(changed_paths, list)
        and len(changed_paths) == len(set(changed_paths))
        and CODEX_REQUIRED_PATCH_PATHS
        <= set(changed_paths)
        <= CODEX_ALLOWED_BUILD_PATHS,
        "source_patch_nonempty": isinstance(nested.get("source_patch_bytes"), int)
        and not isinstance(nested.get("source_patch_bytes"), bool)
        and nested.get("source_patch_bytes") > 0,
        "canonical_patch_matches": is_sha256(expected_source_patch_sha256)
        and all(
            value == expected_source_patch_sha256
            for value in (
                payload.get("source_patch_sha256"),
                payload.get("expected_source_patch_sha256"),
                nested.get("source_patch_sha256"),
                nested.get("expected_source_patch_sha256"),
            )
        ),
        "canonical_cargo_lock_matches": is_sha256(expected_cargo_lock_sha256)
        and all(
            value == expected_cargo_lock_sha256
            for value in (
                payload.get("cargo_lock_sha256"),
                payload.get("expected_cargo_lock_sha256"),
                nested.get("cargo_lock_sha256"),
                nested.get("expected_cargo_lock_sha256"),
            )
        ),
        "digest_shapes_valid": all(is_sha256(value) for value in digest_fields),
        "copied_inputs_match": copies_match,
        "toolchain_present": bool(nested.get("cargo_version"))
        and bool(nested.get("rustc_version")),
        "isolated_locked_build": nested.get("build_isolation")
        == "git-archive-plus-allowlisted-tracked-diff+ephemeral-cargo-home",
        "cargo_home_isolated": nested.get("cargo_home_policy")
        == "ephemeral-empty-no-user-config"
        and nested.get("cargo_home_config_files") == [],
        "project_cargo_config_bound": isinstance(
            nested.get("project_cargo_config"), dict
        )
        and nested.get("project_cargo_config", {}).get("path")
        == "codex-rs/.cargo/config.toml"
        and nested.get("project_cargo_config", {}).get("exists") is True
        and is_sha256(nested.get("project_cargo_config", {}).get("sha256")),
        "dependency_fetch_locked": nested.get("dependency_fetch_policy")
        == "locked-network-fetch-with-registry-checksums",
    }
    required_checks = {
        key: value for key, value in checks.items() if key != "expected_version"
    }
    if not all(required_checks.values()):
        blockers.append("Codex build contract did not pass")
    return checks, blockers


def inspect_receipt(
    name: str,
    path: Path,
    meta_root: Path,
    run_started: datetime,
    run_id: str,
    expected_codex_version: str,
    expected_source_commit: str,
    expected_source_origin: str,
    expected_source_patch_sha256: str,
    expected_cargo_lock_sha256: str,
    source_commit: str,
    source_tree: str,
    inspected_at: datetime,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    item: dict[str, Any] = {"path": "", "exists": path.is_file(), "passed": False}
    try:
        item["path"] = str(path.resolve().relative_to(meta_root.resolve()))
    except ValueError:
        blockers.append(f"receipt is outside isolated meta root: {path.name}")
        item["path"] = path.name
    if not path.is_file():
        blockers.append(f"required receipt missing: {path.name}")
        return item, blockers
    raw = path.read_bytes()
    stat = path.stat()
    modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
    item.update(
        {
            "bytes": len(raw),
            "sha256": sha256_bytes(raw),
            "modified_at": modified_at.isoformat(),
            "fresh": modified_at >= run_started,
        }
    )
    if modified_at < run_started:
        blockers.append(f"required receipt predates release run: {path.name}")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        blockers.append(f"required receipt is invalid JSON: {path.name}: {exc}")
        return item, blockers
    if not isinstance(payload, dict):
        blockers.append(f"required receipt is not a JSON object: {path.name}")
        return item, blockers
    item.update(
        {
            "schema_version": payload.get("schema_version", "")
            if isinstance(payload, dict)
            else "",
            "generated_at": payload.get("generated_at", "")
            if isinstance(payload, dict)
            else "",
            "status": payload.get("status", "") if isinstance(payload, dict) else "",
            "success": payload.get("success") if isinstance(payload, dict) else None,
            "passed": receipt_passed(payload),
        }
    )
    schema_version = item["schema_version"]
    expected_schema = EXPECTED_RECEIPT_SCHEMAS.get(name, "")
    item["expected_schema_version"] = expected_schema
    item["schema_valid"] = bool(schema_version) and (
        not expected_schema or schema_version == expected_schema
    )
    if not schema_version:
        blockers.append(f"required receipt has no schema_version: {path.name}")
    elif expected_schema and schema_version != expected_schema:
        blockers.append(
            f"required receipt schema mismatch: {path.name}: {schema_version!r} != {expected_schema!r}"
        )
    payload_generated_at = item["generated_at"]
    item["payload_generated_at_valid"] = False
    item["payload_generated_fresh"] = False
    if payload_generated_at:
        try:
            generated_time = parse_utc(str(payload_generated_at))
        except ValueError:
            blockers.append(f"required receipt has invalid generated_at: {path.name}")
        else:
            item["payload_generated_at_valid"] = True
            item["payload_generated_fresh"] = (
                run_started <= generated_time <= inspected_at + timedelta(minutes=5)
            )
            if generated_time < run_started:
                blockers.append(
                    f"required receipt generated_at predates release run: {path.name}"
                )
            elif generated_time > inspected_at + timedelta(minutes=5):
                blockers.append(
                    f"required receipt generated_at is implausibly in the future: {path.name}"
                )
    if isinstance(payload, dict):
        binding_checks, binding_blockers = validate_receipt_binding(
            name,
            payload,
            run_id,
            run_started,
            inspected_at,
            source_commit,
            source_tree,
        )
        item["release_binding"] = binding_checks
        blockers.extend(binding_blockers)
    if expected_schema == "qwendex.dev.gate.v1":
        item["run_id"] = payload.get("run_id") if isinstance(payload, dict) else None
        item["run_id_matches"] = item["run_id"] == run_id
        if not item["run_id_matches"]:
            blockers.append(f"required gate receipt run_id mismatch: {path.name}")
        expected_gate, expected_command = DEV_GATE_CONTRACTS.get(name, ("", ()))
        if payload.get("gate") != expected_gate:
            blockers.append(f"required gate receipt gate mismatch: {path.name}")
        if tuple(payload.get("command") or ()) != expected_command:
            blockers.append(f"required gate receipt command mismatch: {path.name}")
    expected_native_command = NATIVE_COMMANDS.get(name)
    if expected_native_command and payload.get("command") != expected_native_command:
        blockers.append(f"required native receipt command mismatch: {path.name}")
    if name in STRICT_HEALTH_RECEIPTS:
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("health_mode") != "strict":
            blockers.append(
                f"required native receipt is not strict health evidence: {path.name}"
            )
    if name == "dev_status":
        if payload.get("mode") != "verify-release" or payload.get("blockers"):
            blockers.append(f"release dev status contract did not pass: {path.name}")
    if name == "qwendex_eval":
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or data.get("success") is not True
            or data.get("failures")
        ):
            blockers.append(f"Qwendex eval contract did not pass: {path.name}")
    if name == "codex_patch":
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or data.get("supported") is not True
            or data.get("applied") is not True
        ):
            blockers.append(f"Codex patch preflight contract did not pass: {path.name}")
    if name == "harness_gate" and (
        payload.get("success") is not True
        or payload.get("functional_status") != "pass"
        or payload.get("drift_status") != "pass"
        or payload.get("failures")
    ):
        blockers.append(f"local harness gate contract did not pass: {path.name}")
    if name == "harness_eval" and (
        payload.get("success") is not True or payload.get("failures")
    ):
        blockers.append(f"local harness eval contract did not pass: {path.name}")
    if name == "live_reliability":
        probes = payload.get("probes")
        probes_by_name = {
            str(probe.get("name") or ""): probe
            for probe in probes
            if isinstance(probe, dict)
        } if isinstance(probes, list) else {}
        models = probes_by_name.get("models_endpoint", {})
        exact = probes_by_name.get("exact_marker", {})
        exact_details = exact.get("details") if isinstance(exact, dict) else {}
        if not (
            payload.get("require_live_bridge") is True
            and set(probes_by_name) == {"models_endpoint", "exact_marker"}
            and models.get("success") is True
            and exact.get("success") is True
            and isinstance(exact_details, dict)
            and exact_details.get("exact_match") is True
            and exact_details.get("marker_count") == 0
        ):
            blockers.append(f"live reliability contract did not pass: {path.name}")
    if name == "live_codex_acceptance" and not (
        payload.get("success") is True
        and payload.get("returncode") == 0
        and payload.get("fresh_home_created") is True
        and payload.get("normal_home_unchanged") is True
        and payload.get("malformed_event_count") == 0
        and payload.get("tool_round_trip_proven") is True
        and isinstance(payload.get("command_execution_count"), int)
        and 1 <= payload.get("command_execution_count") <= 3
        and payload.get("successful_tool_result_count")
        == payload.get("command_execution_count")
        and payload.get("matching_command_count")
        == payload.get("command_execution_count")
        and payload.get("final_text_exact") is True
        and payload.get("event_final_text_exact") is True
        and payload.get("final_output_regular") is True
        and payload.get("launcher_unchanged") is True
        and is_sha256(payload.get("launcher_sha256"))
        and payload.get("codex_bin_unchanged") is True
        and is_sha256(payload.get("codex_bin_sha256"))
        and isinstance(payload.get("codex_bin_bytes"), int)
        and not isinstance(payload.get("codex_bin_bytes"), bool)
        and payload.get("codex_bin_bytes") > 0
        and payload.get("blockers") == []
    ):
        blockers.append(f"live Codex acceptance contract did not pass: {path.name}")
    if name == "live_codex_acceptance":
        item["launcher_sha256"] = payload.get("launcher_sha256", "")
        item["codex_bin_sha256"] = payload.get("codex_bin_sha256", "")
        item["codex_bin_bytes"] = payload.get("codex_bin_bytes", 0)
    if not item["passed"]:
        blockers.append(f"required receipt did not pass: {path.name}")
    if name == "codex_build" and isinstance(payload, dict):
        if payload.get("run_id") != run_id:
            blockers.append(f"Codex build receipt run_id mismatch: {path.name}")
        codex_checks, codex_blockers = validate_codex_build_receipt(
            payload,
            expected_codex_version,
            expected_source_commit,
            expected_source_origin,
            expected_source_patch_sha256,
            expected_cargo_lock_sha256,
        )
        item["codex_build_checks"] = codex_checks
        item["binary_sha256"] = payload.get("binary_sha256", "")
        item["binary_bytes"] = payload.get("binary_bytes", 0)
        blockers.extend(f"{blocker}: {path.name}" for blocker in codex_blockers)
    return item, blockers


def marker_scan(
    roots: list[Path], *, excluded_paths: set[Path] | None = None
) -> tuple[dict[str, Any], list[str]]:
    marker_re = re.compile("|".join(re.escape(marker) for marker in GUARD_MARKERS))
    counts: Counter[str] = Counter()
    expected: Counter[str] = Counter()
    metadata: Counter[str] = Counter()
    unexpected: Counter[str] = Counter()
    unexpected_hits: list[dict[str, Any]] = []
    structured_errors: list[str] = []
    scanned_files = 0
    scanned_bytes = 0

    def json_hits(
        value: Any,
    ) -> tuple[Counter[str], Counter[str], Counter[str], list[str]]:
        document_expected: Counter[str] = Counter()
        document_metadata: Counter[str] = Counter()
        document_unexpected: Counter[str] = Counter()
        errors: list[str] = []
        structured_valid = False
        if isinstance(value, dict) and (
            "expected_guard_markers" in value or "observed_guard_markers" in value
        ):
            case_id = str(value.get("case_id") or "")
            allowed = STRUCTURED_MARKER_CASES.get(case_id, set())
            declared = value.get("expected_guard_markers")
            observed = value.get("observed_guard_markers")
            structured_valid = (
                value.get("schema_version") == "local_qwen_harness_eval.v1"
                and value.get("success") is True
                and value.get("functional_status") == "pass"
                and value.get("drift_status") == "pass"
                and isinstance(declared, list)
                and isinstance(observed, list)
                and len(declared) == len(set(declared))
                and len(observed) == len(set(observed))
                and set(declared) == set(observed)
                and bool(declared)
                and set(declared) <= allowed
            )
            if not structured_valid:
                errors.append(
                    f"invalid structured expected-marker evidence for case {case_id!r}"
                )

        def walk(child: Any, path: tuple[str, ...] = ()) -> None:
            if (
                path == ("data", "effective_policy", "guard", "markers")
                and isinstance(value, dict)
                and value.get("schema_version") == "qwendex.cli.v1"
                and value.get("command") == "check"
            ):
                if isinstance(child, list) and all(
                    marker in GUARD_MARKERS for marker in child
                ):
                    document_metadata.update(str(marker) for marker in child)
                else:
                    errors.append("invalid effective-policy marker metadata")
                return
            if path == ("expected_guard_markers",) and structured_valid:
                return
            if path == ("observed_guard_markers",) and structured_valid:
                document_expected.update(str(marker) for marker in child)
                return
            if isinstance(child, dict):
                for key, grandchild in child.items():
                    document_unexpected.update(marker_re.findall(str(key)))
                    walk(grandchild, (*path, str(key)))
            elif isinstance(child, list):
                for index, grandchild in enumerate(child):
                    walk(grandchild, (*path, str(index)))
            elif isinstance(child, str):
                document_unexpected.update(marker_re.findall(child))

        walk(value)
        return document_expected, document_metadata, document_unexpected, errors

    excluded = {path.resolve() for path in (excluded_paths or set())}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.resolve() in excluded:
                continue
            try:
                raw = path.read_bytes()
            except OSError as exc:
                structured_errors.append(
                    f"could not scan marker evidence {path.name}: {exc}"
                )
                continue
            scanned_files += 1
            scanned_bytes += len(raw)
            text = raw.decode("utf-8", errors="replace")
            expected_hits: Counter[str] = Counter()
            metadata_hits: Counter[str] = Counter()
            unexpected_file_hits: Counter[str] = Counter()
            if path.suffix == ".json":
                try:
                    payload = json.loads(text)
                except json.JSONDecodeError:
                    unexpected_file_hits = Counter(marker_re.findall(text))
                else:
                    expected_hits, metadata_hits, unexpected_file_hits, errors = (
                        json_hits(payload)
                    )
                    structured_errors.extend(
                        f"{path.name}: {error}" for error in errors
                    )
            else:
                unexpected_file_hits = Counter(marker_re.findall(text))
            expected.update(expected_hits)
            metadata.update(metadata_hits)
            unexpected.update(unexpected_file_hits)
            counts.update(expected_hits)
            counts.update(metadata_hits)
            counts.update(unexpected_file_hits)
            for marker, count in sorted(unexpected_file_hits.items()):
                unexpected_hits.append(
                    {
                        "path": str(path.relative_to(root)),
                        "marker": marker,
                        "count": count,
                    }
                )
    blockers = []
    if unexpected:
        blockers.append(
            "release evidence contains unexpected local-model guard markers"
        )
    if structured_errors:
        blockers.append(
            "release evidence contains invalid structured marker expectations"
        )
    return {
        "status": "pass" if not blockers else "blocked",
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "counts": dict(counts),
        "expected_counts": dict(expected),
        "metadata_counts": dict(metadata),
        "unexpected_counts": dict(unexpected),
        "unexpected_hits": unexpected_hits,
        "structured_errors": structured_errors,
    }, blockers


def inspect_ci_attestation(
    path: Path,
    source: dict[str, Any],
    artifacts: dict[str, Any],
    inspected_at: datetime,
    max_age_hours: int,
) -> tuple[dict[str, Any], list[str]]:
    item: dict[str, Any] = {
        "path": path.name,
        "exists": path.is_file(),
        "passed": False,
    }
    blockers: list[str] = []
    if not path.is_file():
        return item, [f"required CI attestation is missing: {path.name}"]
    raw = path.read_bytes()
    item.update({"bytes": len(raw), "sha256": sha256_bytes(raw)})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return item, [f"CI attestation is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return item, ["CI attestation is not a JSON object"]
    generated_at = str(payload.get("generated_at") or "")
    generated_valid = False
    generated_fresh = False
    if generated_at:
        try:
            generated_time = parse_utc(generated_at)
        except ValueError:
            blockers.append("CI attestation has invalid generated_at")
        else:
            generated_valid = True
            generated_fresh = (
                inspected_at - timedelta(hours=max_age_hours)
                <= generated_time
                <= inspected_at + timedelta(minutes=5)
            )
            if generated_time < inspected_at - timedelta(hours=max_age_hours):
                blockers.append("CI attestation is older than the allowed maximum age")
            elif generated_time > inspected_at + timedelta(minutes=5):
                blockers.append(
                    "CI attestation generated_at is implausibly in the future"
                )
    else:
        blockers.append("CI attestation has no generated_at")
    repository = attestation_repository_identity(str(payload.get("repository") or ""))
    origin_repository = str(source.get("origin_repository") or "")
    raw_run_id = payload.get("run_id")
    run_id = str(raw_run_id or "")
    run_url = str(payload.get("run_url") or "")
    expected_run_url = (
        f"https://github.com/{repository.removeprefix('github.com/')}/actions/runs/{run_id}"
        if repository and run_id
        else ""
    )
    checks = payload.get("checks")
    checked = (
        set(checks)
        if isinstance(checks, list) and all(isinstance(value, str) for value in checks)
        else set()
    )
    artifact_evidence = payload.get("artifact_contract")
    artifact_matches = (
        isinstance(artifact_evidence, dict)
        and artifact_evidence.get("status") == "pass"
        and artifact_evidence.get("tree_manifest_sha256")
        == artifacts.get("tree_manifest_sha256")
        and is_sha256(artifact_evidence.get("report_sha256"))
    )
    checks_map = {
        "schema_valid": payload.get("schema_version") == CI_ATTESTATION_SCHEMA,
        "status_passed": payload.get("status") == "pass"
        and payload.get("conclusion") == "success",
        "generated_at_valid": generated_valid,
        "generated_fresh": generated_fresh,
        "workflow_matches": payload.get("workflow") == "CI"
        and payload.get("job") == "verify"
        and payload.get("workflow_ref")
        == (
            f"{str(payload.get('repository') or '')}/.github/workflows/ci.yml@"
            f"{str(payload.get('ref') or '')}"
        )
        and payload.get("workflow_sha") == payload.get("commit"),
        "event_is_default_push": payload.get("event_name") == "push"
        and payload.get("ref") == f"refs/heads/{source.get('default_branch', '')}",
        "source_matches": payload.get("commit") == source.get("commit")
        and payload.get("tree") == source.get("tree"),
        "repository_matches": bool(repository) and repository == origin_repository,
        "run_identity_valid": isinstance(raw_run_id, int)
        and not isinstance(raw_run_id, bool)
        and raw_run_id > 0
        and isinstance(payload.get("run_attempt"), int)
        and not isinstance(payload.get("run_attempt"), bool)
        and payload.get("run_attempt", 0) > 0
        and run_url.casefold() == expected_run_url.casefold(),
        "required_checks_present": CI_REQUIRED_CHECKS <= checked,
        "artifact_contract_matches": artifact_matches,
    }
    for label, passed in checks_map.items():
        if not passed:
            blockers.append(f"CI attestation check failed: {label}")
    item.update(
        {
            "schema_version": payload.get("schema_version", ""),
            "generated_at": generated_at,
            "repository": repository,
            "run_id": run_id,
            "run_url": run_url,
            "checks": checks_map,
            "passed": not blockers,
        }
    )
    return item, blockers


def verify_ci_attestation_online(
    path: Path,
    source: dict[str, Any],
    artifacts: dict[str, Any],
    *,
    expected_sha256: str,
) -> tuple[dict[str, Any], list[str]]:
    item: dict[str, Any] = {
        "required": True,
        "queried": False,
        "artifact_downloaded": False,
        "passed": False,
        "checks": {},
    }
    blockers: list[str] = []
    try:
        supplied_raw = path.read_bytes()
        supplied = json.loads(supplied_raw)
    except (OSError, json.JSONDecodeError) as exc:
        return item, [f"could not read CI attestation for online verification: {exc}"]
    if not isinstance(supplied, dict):
        return item, ["CI attestation for online verification is not an object"]
    if not is_sha256(expected_sha256) or sha256_bytes(supplied_raw) != expected_sha256:
        return item, ["CI attestation changed between local and online verification"]
    identity = str(source.get("origin_repository") or "")
    slug = github_repository_slug(identity)
    run_id = supplied.get("run_id")
    if not slug:
        return item, ["online CI verification requires a GitHub origin"]
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id <= 0:
        return item, ["online CI verification requires a positive numeric run_id"]
    try:
        run_payload = gh_json("api", f"repos/{slug}/actions/runs/{run_id}")
        artifact_payload = gh_json(
            "api", f"repos/{slug}/actions/runs/{run_id}/artifacts?per_page=100"
        )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return item, [f"could not verify CI run online: {exc}"]
    if not isinstance(run_payload, dict):
        return item, ["GitHub CI run response is not an object"]
    workflow_id = run_payload.get("workflow_id")
    workflow_payload: Any = None
    if isinstance(workflow_id, int) and not isinstance(workflow_id, bool):
        try:
            workflow_payload = gh_json(
                "api", f"repos/{slug}/actions/workflows/{workflow_id}"
            )
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            blockers.append(f"could not verify CI workflow online: {exc}")
    online_checks = {
        "run_id_matches": run_payload.get("id") == run_id,
        "workflow_matches": run_payload.get("name") == "CI",
        "workflow_path_matches": run_payload.get("path")
        == ".github/workflows/ci.yml",
        "workflow_identity_matches": isinstance(workflow_payload, dict)
        and workflow_payload.get("id") == workflow_id
        and workflow_payload.get("name") == "CI"
        and workflow_payload.get("path") == ".github/workflows/ci.yml"
        and workflow_payload.get("state") == "active",
        "event_is_push": run_payload.get("event") == "push",
        "status_completed": run_payload.get("status") == "completed",
        "conclusion_success": run_payload.get("conclusion") == "success",
        "commit_matches": run_payload.get("head_sha") == source.get("commit"),
        "branch_matches": run_payload.get("head_branch")
        == source.get("default_branch"),
        "url_matches": run_payload.get("html_url") == supplied.get("run_url"),
        "attempt_matches": run_payload.get("run_attempt")
        == supplied.get("run_attempt"),
    }
    artifact_name = f"qwendex-ci-attestation-{source.get('commit', '')}"
    artifact_rows = (
        artifact_payload.get("artifacts", [])
        if isinstance(artifact_payload, dict)
        else []
    )
    matching_artifacts = [
        row
        for row in artifact_rows
        if isinstance(row, dict)
        and row.get("name") == artifact_name
        and row.get("expired") is False
    ]
    online_checks["artifact_unique"] = len(matching_artifacts) == 1
    item.update(
        {
            "queried": True,
            "repository": identity,
            "run_id": run_id,
            "run_url": run_payload.get("html_url", ""),
            "artifact_name": artifact_name,
            "artifact_id": matching_artifacts[0].get("id")
            if len(matching_artifacts) == 1
            else None,
        }
    )
    if not all(online_checks.values()):
        blockers.extend(
            f"online CI verification check failed: {name}"
            for name, passed in online_checks.items()
            if not passed
        )
        item["checks"] = online_checks
        return item, blockers

    try:
        with tempfile.TemporaryDirectory(prefix="qwendex-ci-verify-") as temp_dir:
            result = subprocess.run(
                [
                    "gh",
                    "run",
                    "download",
                    str(run_id),
                    "--repo",
                    slug,
                    "--name",
                    artifact_name,
                    "--dir",
                    temp_dir,
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=120,
            )
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                blockers.append(
                    f"could not download authoritative CI artifact: {detail}"
                )
            else:
                downloaded_attestation = Path(temp_dir) / CI_RECEIPT
                downloaded_report = Path(temp_dir) / "qwendex-artifact-contract.json"
                online_checks["attestation_present"] = downloaded_attestation.is_file()
                online_checks["artifact_report_present"] = downloaded_report.is_file()
                downloaded_attestation_raw = (
                    downloaded_attestation.read_bytes()
                    if downloaded_attestation.is_file()
                    else b""
                )
                downloaded_report_raw = (
                    downloaded_report.read_bytes()
                    if downloaded_report.is_file()
                    else b""
                )
                online_checks["attestation_bytes_match"] = bool(
                    downloaded_attestation_raw
                ) and sha256_bytes(downloaded_attestation_raw) == expected_sha256
                online_checks["supplied_path_unchanged"] = (
                    sha256_bytes(path.read_bytes()) == expected_sha256
                )
                report: Any = None
                if downloaded_report_raw:
                    try:
                        report = json.loads(downloaded_report_raw)
                    except json.JSONDecodeError:
                        report = None
                artifact_evidence = supplied.get("artifact_contract")
                online_checks["artifact_report_digest_matches"] = (
                    isinstance(artifact_evidence, dict)
                    and bool(downloaded_report_raw)
                    and artifact_evidence.get("report_sha256")
                    == sha256_bytes(downloaded_report_raw)
                )
                report_contract = (
                    report.get("artifact_contract")
                    if isinstance(report, dict)
                    else None
                )
                online_checks["artifact_report_source_matches"] = (
                    isinstance(report, dict)
                    and report.get("schema_version")
                    == "qwendex.ci.artifact_contract.v1"
                    and report.get("status") == "pass"
                    and report.get("source_commit") == source.get("commit")
                    and report.get("source_tree") == source.get("tree")
                    and isinstance(report_contract, dict)
                    and report_contract.get("tree_manifest_sha256")
                    == artifacts.get("tree_manifest_sha256")
                )
                item["artifact_downloaded"] = True
    except (OSError, subprocess.TimeoutExpired) as exc:
        blockers.append(f"could not download/read authoritative CI artifact: {exc}")
    blockers.extend(
        f"online CI artifact check failed: {name}"
        for name, passed in online_checks.items()
        if not passed
    )
    item["checks"] = online_checks
    item["passed"] = not blockers
    return item, blockers


def source_still_matches(
    repo: Path,
    source: dict[str, Any],
    *,
    trusted_origin: str = "",
) -> tuple[dict[str, Any], list[str]]:
    def local_state() -> tuple[str, str, bool]:
        return (
            git(repo, "rev-parse", "HEAD"),
            git(repo, "rev-parse", "HEAD^{tree}"),
            bool(git(repo, "status", "--porcelain=v1", "--untracked-files=all")),
        )

    initial_commit, initial_tree, initial_dirty = local_state()
    initial_local_matches = (
        initial_commit == source.get("commit")
        and initial_tree == source.get("tree")
        and not initial_dirty
    )
    result: dict[str, Any] = {
        "initial_local_recheck": {
            "commit": initial_commit,
            "tree": initial_tree,
            "clean": not initial_dirty,
            "matches_initial_source": initial_local_matches,
        }
    }
    blockers: list[str] = []
    if not initial_local_matches:
        blockers.append("source changed or became dirty while the release gate was running")
    remote_matches = True
    if trusted_origin:
        remote_recheck, remote_blockers = trusted_remote_branch_tip(
            trusted_origin,
            str(source.get("default_branch") or ""),
            str(source.get("commit") or ""),
        )
        result["trusted_remote_recheck"] = {"required": True, **remote_recheck}
        blockers.extend(remote_blockers)
        remote_matches = not remote_blockers
    final_commit, final_tree, final_dirty = local_state()
    final_local_matches = (
        final_commit == source.get("commit")
        and final_tree == source.get("tree")
        and not final_dirty
    )
    result.update(
        {
            "commit": final_commit,
            "tree": final_tree,
            "clean": not final_dirty,
            "local_matches_initial_source": initial_local_matches
            and final_local_matches,
            "final_local_recheck": {
                "commit": final_commit,
                "tree": final_tree,
                "clean": not final_dirty,
                "matches_initial_source": final_local_matches,
            },
        }
    )
    if not final_local_matches and initial_local_matches:
        blockers.append("source changed or became dirty during the remote recheck")
    result["matches_initial_source"] = (
        initial_local_matches and remote_matches and final_local_matches
    )
    return result, blockers


def parse_gate_overrides(values: list[str]) -> dict[str, Path]:
    parsed: dict[str, Path] = {}
    for value in values:
        name, sep, raw_path = value.partition("=")
        if not sep or not name or not raw_path:
            raise ValueError(f"--gate must be NAME=PATH: {value}")
        parsed[name] = Path(raw_path)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--meta-root", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--dev-status", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tier", default="release")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-tag", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-started-at", required=True)
    parser.add_argument("--default-branch", default="")
    parser.add_argument(
        "--trusted-origin",
        default=os.environ.get("QWENDEX_RELEASE_TRUSTED_ORIGIN", ""),
        help="Canonical trusted origin URL; required for publish-ready evidence",
    )
    parser.add_argument(
        "--ci-attestation",
        type=Path,
        default=Path(os.environ["QWENDEX_CI_ATTESTATION"])
        if os.environ.get("QWENDEX_CI_ATTESTATION")
        else None,
        help="Passing CI attestation downloaded from the matching GitHub Actions run",
    )
    parser.add_argument(
        "--ci-max-age-hours",
        type=int,
        default=int(os.environ.get("QWENDEX_CI_MAX_AGE_HOURS", "168")),
    )
    parser.add_argument("--expected-codex-version", default="")
    parser.add_argument(
        "--gate", action="append", default=[], help="Override or add NAME=PATH receipt"
    )
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument(
        "--candidate",
        action="store_true",
        help="Allow non-default branch/missing tag for candidate evidence; never publish-ready",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "verify-summary":
        verify_parser = argparse.ArgumentParser(
            prog="qwendex_release_gate.py verify-summary",
            description="Verify a sealed Qwendex release summary.",
        )
        verify_parser.add_argument("--summary", type=Path, required=True)
        verify_parser.add_argument("--repo-root", type=Path, default=Path.cwd())
        verify_parser.add_argument("--require-publish-ready", action="store_true")
        verify_args = verify_parser.parse_args(raw_argv[1:])
        return verify_release_summary_file(
            verify_args.summary.resolve(),
            require_publish_ready=verify_args.require_publish_ready,
            repo_root=verify_args.repo_root.resolve(),
        )
    args = build_parser().parse_args(raw_argv)
    generated_at = datetime.now(UTC)
    evidence_blockers: list[str] = []
    publish_blockers: list[str] = []
    try:
        run_started = parse_utc(args.run_started_at)
    except ValueError as exc:
        run_started = generated_at
        evidence_blockers.append(f"invalid run-started-at: {exc}")
    if run_started > generated_at + timedelta(minutes=5):
        evidence_blockers.append("run-started-at is implausibly in the future")
    if args.ci_max_age_hours <= 0:
        evidence_blockers.append("ci-max-age-hours must be positive")
    repo = args.repo_root.resolve()
    meta_root = args.meta_root.resolve()
    results_root = args.results_root.resolve()
    output = args.output.resolve()
    if meta_root.name != args.run_id:
        evidence_blockers.append(
            "meta root is not an exact per-run directory for the supplied run id"
        )
    if results_root.name != args.run_id:
        evidence_blockers.append(
            "results root is not an exact per-run directory for the supplied run id"
        )
    try:
        output.relative_to(meta_root)
    except ValueError:
        evidence_blockers.append(
            "release summary output is outside the isolated meta root"
        )
    if args.tier != "release":
        publish_blockers.append("only tier=release can be publish-ready")
    try:
        source, source_evidence, source_publish = source_contract(
            repo,
            args.expected_version,
            args.expected_tag,
            args.expected_codex_version,
            args.default_branch,
            args.trusted_origin,
        )
        evidence_blockers.extend(source_evidence)
        publish_blockers.extend(source_publish)
    except Exception as exc:
        source = {"error": str(exc)}
        evidence_blockers.append(f"could not bind release source: {exc}")
    if args.candidate:
        source["trusted_remote"] = {"required": False, "queried": False}
    else:
        trusted_remote, trusted_remote_blockers = trusted_remote_branch_tip(
            args.trusted_origin,
            str(source.get("default_branch") or args.default_branch),
            str(source.get("commit") or ""),
        )
        source["trusted_remote"] = {"required": True, **trusted_remote}
        publish_blockers.extend(
            f"trusted_remote: {blocker}" for blocker in trusted_remote_blockers
        )
    try:
        artifacts, artifact_blockers = artifact_contract(
            repo, str(source.get("commit") or "HEAD")
        )
        evidence_blockers.extend(artifact_blockers)
    except Exception as exc:
        artifacts = {"status": "blocked", "error": str(exc)}
        evidence_blockers.append(f"could not scan tracked release artifact: {exc}")

    receipt_paths = {
        name: meta_root / filename for name, filename in REQUIRED_RECEIPTS.items()
    }
    receipt_paths["dev_status"] = args.dev_status.resolve()
    if args.require_live:
        receipt_paths.update(
            {name: meta_root / filename for name, filename in LIVE_RECEIPTS.items()}
        )
    try:
        receipt_paths.update(parse_gate_overrides(args.gate))
    except ValueError as exc:
        evidence_blockers.append(str(exc))
    codex_source_provenance = (
        source.get("codex_source_provenance")
        if isinstance(source.get("codex_source_provenance"), dict)
        else {}
    )
    expected_source_commit = str(
        (
            codex_source_provenance.get("source_commit")
            if isinstance(codex_source_provenance.get("source_commit"), dict)
            else {}
        ).get("value")
        or ""
    )
    expected_source_origin = str(
        (
            codex_source_provenance.get("source_origin")
            if isinstance(codex_source_provenance.get("source_origin"), dict)
            else {}
        ).get("value")
        or ""
    )
    codex_build_digests = (
        source.get("codex_build_digests")
        if isinstance(source.get("codex_build_digests"), dict)
        else {}
    )
    expected_source_patch_sha256 = str(
        (
            codex_build_digests.get("source_patch_sha256")
            if isinstance(codex_build_digests.get("source_patch_sha256"), dict)
            else {}
        ).get("value")
        or ""
    )
    expected_cargo_lock_sha256 = str(
        (
            codex_build_digests.get("cargo_lock_sha256")
            if isinstance(codex_build_digests.get("cargo_lock_sha256"), dict)
            else {}
        ).get("value")
        or ""
    )
    gates: dict[str, Any] = {}
    for name, path in sorted(receipt_paths.items()):
        item, blockers = inspect_receipt(
            name,
            path.resolve(),
            meta_root,
            run_started,
            args.run_id,
            args.expected_codex_version,
            expected_source_commit,
            expected_source_origin,
            expected_source_patch_sha256,
            expected_cargo_lock_sha256,
            str(source.get("commit") or ""),
            str(source.get("tree") or ""),
            generated_at,
        )
        gates[name] = item
        evidence_blockers.extend(f"{name}: {blocker}" for blocker in blockers)
    if args.require_live:
        build_evidence = gates.get("codex_build", {})
        live_evidence = gates.get("live_codex_acceptance", {})
        if not (
            is_sha256(build_evidence.get("binary_sha256"))
            and live_evidence.get("codex_bin_sha256")
            == build_evidence.get("binary_sha256")
            and live_evidence.get("codex_bin_bytes")
            == build_evidence.get("binary_bytes")
        ):
            evidence_blockers.append(
                "live_codex_acceptance: live Codex binary does not match validated build evidence"
            )

    ci_path = (
        args.ci_attestation.resolve()
        if args.ci_attestation is not None
        else (repo / ".qwendex-dev" / "results" / "meta" / CI_RECEIPT).resolve()
    )
    ci_attestation, ci_blockers = inspect_ci_attestation(
        ci_path,
        source,
        artifacts,
        generated_at,
        max(args.ci_max_age_hours, 1),
    )
    publish_blockers.extend(f"ci_attestation: {blocker}" for blocker in ci_blockers)
    if args.candidate:
        ci_attestation["online_verification"] = {
            "required": False,
            "queried": False,
            "passed": False,
        }
    else:
        ci_online, ci_online_blockers = verify_ci_attestation_online(
            ci_path,
            source,
            artifacts,
            expected_sha256=str(ci_attestation.get("sha256") or ""),
        )
        ci_attestation["online_verification"] = ci_online
        ci_attestation["passed"] = bool(ci_attestation.get("passed")) and bool(
            ci_online.get("passed")
        )
        publish_blockers.extend(
            f"ci_attestation_online: {blocker}" for blocker in ci_online_blockers
        )

    markers, marker_blockers = marker_scan(
        [meta_root, results_root], excluded_paths={output}
    )
    evidence_blockers.extend(marker_blockers)
    try:
        source_recheck, source_recheck_blockers = source_still_matches(
            repo,
            source,
            trusted_origin=args.trusted_origin if not args.candidate else "",
        )
    except Exception as exc:
        source_recheck = {"matches_initial_source": False, "error": str(exc)}
        source_recheck_blockers = [f"could not recheck release source: {exc}"]
    evidence_blockers.extend(source_recheck_blockers)
    evidence_blockers = list(dict.fromkeys(evidence_blockers))
    publish_blockers = list(dict.fromkeys(publish_blockers))
    effective_blockers = list(evidence_blockers)
    if not args.candidate:
        effective_blockers.extend(publish_blockers)
    status = "pass" if not effective_blockers else "blocked"
    publish_ready = (
        not args.candidate and not evidence_blockers and not publish_blockers
    )
    recommendation = (
        "publish-ready"
        if publish_ready
        else "candidate-ready"
        if args.candidate and not evidence_blockers
        else "blocked"
    )
    evidence_core = {
        "run_id": args.run_id,
        "tier": args.tier,
        "source": source,
        "gates": gates,
        "artifact_contract": artifacts,
        "marker_scan": markers,
        "ci_attestation": ci_attestation,
        "source_recheck": source_recheck,
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at.isoformat(),
        "run_id": args.run_id,
        "run_started_at": run_started.isoformat(),
        "tier": args.tier,
        "status": status,
        "recommendation": recommendation,
        "publish_ready": publish_ready,
        "candidate_mode": args.candidate,
        "live_required": args.require_live,
        "blockers": effective_blockers,
        "evidence_blockers": evidence_blockers,
        "publish_blockers": publish_blockers,
        "source": source,
        "gates": gates,
        "artifact_contract": artifacts,
        "marker_scan": markers,
        "ci_attestation": ci_attestation,
        "source_recheck": source_recheck,
        "evidence_sha256": canonical_digest(evidence_core),
        SUMMARY_DIGEST_FIELD: "",
    }
    payload[SUMMARY_DIGEST_FIELD] = release_summary_digest(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    summary_blockers = verify_release_summary_payload(
        json.loads(output.read_text(encoding="utf-8")),
        require_publish_ready=publish_ready,
    )
    if summary_blockers:
        print(
            "release-gate: blocked (summary-seal-invalid) - "
            + "; ".join(summary_blockers),
            file=sys.stderr,
        )
        return 1
    print(f"release-gate: {status} ({recommendation}) - {output}")
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
