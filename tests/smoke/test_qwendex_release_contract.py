from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "qwendex_release_gate.py"
TRUSTED_ORIGIN = "https://github.com/qwendex-test/qwendex.git"
CANONICAL_CODEX_SOURCE_COMMIT = "d" * 40
CANONICAL_CODEX_SOURCE_ORIGIN = "https://github.com/openai/codex.git"
CANONICAL_CODEX_PATCH_SHA256 = "2" * 64
CANONICAL_CODEX_CARGO_LOCK_SHA256 = "3" * 64


def load_release_gate():
    spec = importlib.util.spec_from_file_location("qwendex_release_gate", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(*args: str, cwd: Path) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)
    return result.stdout.strip()


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_ids(repo: Path) -> tuple[str, str]:
    return run("git", "rev-parse", "HEAD", cwd=repo), run(
        "git", "rev-parse", "HEAD^{tree}", cwd=repo
    )


def bind_payload(
    release_gate,
    name: str,
    payload: dict,
    *,
    run_id: str,
    generated_at: str,
    commit: str,
    tree: str,
) -> dict:
    binding = {
        "schema_version": release_gate.RECEIPT_BINDING_SCHEMA,
        "generated_at": generated_at,
        "run_id": run_id,
        "gate": name,
        "command_id": release_gate.RECEIPT_COMMAND_IDS[name],
        "source": {"commit": commit, "tree": tree},
    }
    if name in release_gate.STRICT_HEALTH_RECEIPTS:
        binding["health_mode"] = "strict"
    payload["release_binding"] = binding
    binding["payload_sha256"] = release_gate.payload_digest(payload)
    return payload


def commit_and_retag(repo: Path, message: str) -> None:
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-m", message, cwd=repo)
    run("git", "tag", "-fa", "v1.2.3", "-m", "Qwendex v1.2.3", cwd=repo)
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)


def write_ci_attestation(fixture: dict[str, object]) -> None:
    release_gate = load_release_gate()
    repo = Path(fixture["repo"])
    commit, tree = source_ids(repo)
    artifact, blockers = release_gate.artifact_contract(repo, commit)
    assert not blockers
    report = {
        "schema_version": "qwendex.ci.artifact_contract.v1",
        "source_commit": commit,
        "source_tree": tree,
        "status": "pass",
        "blockers": [],
        "artifact_contract": artifact,
    }
    report_path = Path(fixture["authoritative_ci_report"])
    write_json(report_path, report)
    payload = {
        "schema_version": release_gate.CI_ATTESTATION_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pass",
        "conclusion": "success",
        "workflow": "CI",
        "workflow_ref": "qwendex-test/qwendex/.github/workflows/ci.yml@refs/heads/main",
        "workflow_sha": commit,
        "job": "verify",
        "event_name": "push",
        "ref": "refs/heads/main",
        "repository": "qwendex-test/qwendex",
        "commit": commit,
        "tree": tree,
        "run_id": 123456789,
        "run_attempt": 1,
        "run_url": "https://github.com/qwendex-test/qwendex/actions/runs/123456789",
        "checks": sorted(release_gate.CI_REQUIRED_CHECKS),
        "artifact_contract": {
            "status": "pass",
            "tree_manifest_sha256": artifact["tree_manifest_sha256"],
            "report_sha256": sha256_file(report_path),
        },
    }
    write_json(Path(fixture["ci_attestation"]), payload)
    write_json(Path(fixture["authoritative_ci_attestation"]), payload)


def rebind_fixture(fixture: dict[str, object]) -> None:
    release_gate = load_release_gate()
    repo = Path(fixture["repo"])
    commit, tree = source_ids(repo)
    paths = [
        Path(fixture["meta_root"]) / filename
        for filename in release_gate.REQUIRED_RECEIPTS.values()
    ]
    paths.append(Path(fixture["dev_status"]))
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        binding = payload["release_binding"]
        binding["source"] = {"commit": commit, "tree": tree}
        binding["generated_at"] = datetime.now(UTC).isoformat()
        binding["payload_sha256"] = release_gate.payload_digest(payload)
        write_json(path, payload)
    write_ci_attestation(fixture)


def refresh_binding(payload: dict) -> None:
    release_gate = load_release_gate()
    payload["release_binding"]["payload_sha256"] = release_gate.payload_digest(payload)


def write_fake_gh(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
commit = os.environ["QWENDEX_TEST_REMOTE_COMMIT"]
run_id = int(os.environ.get("QWENDEX_TEST_CI_RUN_ID", "123456789"))
run_url = f"https://github.com/qwendex-test/qwendex/actions/runs/{run_id}"
if len(args) >= 2 and args[0] == "api":
    endpoint = args[1]
    if "/git/ref/heads/" in endpoint:
        payload = {"ref": "refs/heads/main", "object": {"sha": commit}}
    elif endpoint.endswith(f"/actions/runs/{run_id}/artifacts?per_page=100"):
        payload = {
            "total_count": 1,
            "artifacts": [
                {
                    "id": 987654321,
                    "name": f"qwendex-ci-attestation-{commit}",
                    "expired": False,
                    "size_in_bytes": 2048,
                }
            ],
        }
    elif endpoint.endswith(f"/actions/runs/{run_id}"):
        payload = {
            "id": run_id,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "workflow_id": 456789,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": commit,
            "head_branch": "main",
            "html_url": run_url,
            "run_attempt": 1,
        }
    elif endpoint.endswith("/actions/workflows/456789"):
        payload = {
            "id": 456789,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "state": "active",
        }
    else:
        raise SystemExit(f"unsupported fake gh api endpoint: {endpoint}")
    print(json.dumps(payload))
    raise SystemExit(0)
if len(args) >= 2 and args[:2] == ["run", "download"]:
    output = Path(args[args.index("--dir") + 1])
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(os.environ["QWENDEX_TEST_CI_ATTESTATION"], output / "qwendex-ci-attestation.json")
    shutil.copy2(os.environ["QWENDEX_TEST_CI_REPORT"], output / "qwendex-artifact-contract.json")
    raise SystemExit(0)
raise SystemExit(f"unsupported fake gh command: {args}")
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def release_fixture(tmp_path: Path) -> dict[str, object]:
    repo = tmp_path / "repo"
    repo.mkdir()
    run("git", "init", "-b", "main", cwd=repo)
    run("git", "config", "user.email", "test@example.invalid", cwd=repo)
    run("git", "config", "user.name", "Qwendex Test", cwd=repo)
    run("git", "remote", "add", "origin", TRUSTED_ORIGIN, cwd=repo)
    (repo / "scripts").mkdir()
    (repo / "config/qwendex").mkdir(parents=True)
    (repo / "public/qwendex").mkdir(parents=True)
    (repo / "scripts/qwendex_cli.py").write_text(
        'VERSION = "1.2.3"\n', encoding="utf-8"
    )
    (repo / "scripts/qwendex_dev_env").write_text(
        'QWENDEX_RELEASE_CODEX_VERSION="0.144.0"\n'
        f'QWENDEX_RELEASE_CODEX_SOURCE_COMMIT="{CANONICAL_CODEX_SOURCE_COMMIT}"\n'
        f'QWENDEX_RELEASE_CODEX_SOURCE_REPO="{CANONICAL_CODEX_SOURCE_ORIGIN}"\n'
        f'QWENDEX_RELEASE_CODEX_PATCH_SHA256="{CANONICAL_CODEX_PATCH_SHA256}"\n'
        f'QWENDEX_RELEASE_CODEX_CARGO_LOCK_SHA256="{CANONICAL_CODEX_CARGO_LOCK_SHA256}"\n',
        encoding="utf-8",
    )
    (repo / "scripts/qwendex_install_deps").write_text(
        'export QWENDEX_CODEX_REQUIRED_VERSION="${QWENDEX_CODEX_REQUIRED_VERSION:-0.144.0}"\n',
        encoding="utf-8",
    )
    write_json(repo / "config/qwendex/qwendex.json", {"version": "1.2.3"})
    write_json(repo / "config/qwendex/qwendex.sample.json", {"version": "1.2.3"})
    (repo / "README.md").write_text(
        "This checkout is seeded as `v1.2.3`. The installer requires `@openai/codex@0.144.0`.\n",
        encoding="utf-8",
    )
    (repo / "RELEASE.md").write_text(
        "# v1.2.3\n\nBuilds the patched Codex `0.144.0` binary.\n",
        encoding="utf-8",
    )
    (repo / "public/qwendex/quickstart.md").write_text(
        "The dependency helper requires Codex CLI `0.144.0`.\n",
        encoding="utf-8",
    )
    (repo / "public/qwendex/release-notes.md").write_text(
        "# Release Notes\n\n## Unreleased\n\n## 1.2.3\n\n- Fixture.\n",
        encoding="utf-8",
    )
    commit_and_retag(repo, "release fixture")

    run_id = "release-test-run"
    meta_root = tmp_path / "evidence" / run_id
    results_root = tmp_path / "results" / run_id
    meta_root.mkdir(parents=True)
    results_root.mkdir(parents=True)
    started = datetime.now(UTC) - timedelta(seconds=2)
    release_gate = load_release_gate()
    commit, tree = source_ids(repo)
    binary = tmp_path / "codex-bin"
    binary.write_bytes(b"fixture codex binary\n")
    binary.chmod(0o755)
    for name, filename in release_gate.REQUIRED_RECEIPTS.items():
        schema_version = release_gate.EXPECTED_RECEIPT_SCHEMAS[name]
        generated_at = datetime.now(UTC).isoformat()
        if name == "codex_build":
            source_head = CANONICAL_CODEX_SOURCE_COMMIT
            build_inputs = {
                "schema_version": release_gate.CODEX_BUILD_INPUTS_SCHEMA,
                "generated_at": generated_at,
                "status": "pass",
                "source_ref": "rust-v0.144.0",
                "source_head": source_head,
                "source_ref_target": source_head,
                "source_tree_manifest_sha256": "1" * 64,
                "source_patch_sha256": CANONICAL_CODEX_PATCH_SHA256,
                "expected_source_patch_sha256": CANONICAL_CODEX_PATCH_SHA256,
                "source_patch_bytes": 4096,
                "changed_paths": sorted(release_gate.CODEX_ALLOWED_BUILD_PATHS),
                "unexpected_changes": [],
                "missing_patch_paths": [],
                "untracked_paths": [],
                "unmerged_entries": [],
                "cargo_lock_sha256": CANONICAL_CODEX_CARGO_LOCK_SHA256,
                "expected_cargo_lock_sha256": CANONICAL_CODEX_CARGO_LOCK_SHA256,
                "cargo_version": "cargo 1.90.0",
                "rustc_version": "rustc 1.90.0",
                "build_isolation": "git-archive-plus-allowlisted-tracked-diff+ephemeral-cargo-home",
                "cargo_home_policy": "ephemeral-empty-no-user-config",
                "cargo_home_config_files": [],
                "project_cargo_config": {
                    "path": "codex-rs/.cargo/config.toml",
                    "exists": True,
                    "sha256": "7" * 64,
                },
                "dependency_fetch_policy": "locked-network-fetch-with-registry-checksums",
                "blockers": [],
            }
            payload = {
                "schema_version": schema_version,
                "generated_at": generated_at,
                "validated_at": generated_at,
                "run_id": run_id,
                "status": "pass",
                "source_ref": "rust-v0.144.0",
                "source_head": source_head,
                "source_patch_sha256": build_inputs["source_patch_sha256"],
                "expected_source_patch_sha256": build_inputs[
                    "expected_source_patch_sha256"
                ],
                "source_tree_manifest_sha256": build_inputs[
                    "source_tree_manifest_sha256"
                ],
                "source_patch_paths": build_inputs["changed_paths"],
                "cargo_lock_sha256": build_inputs["cargo_lock_sha256"],
                "expected_cargo_lock_sha256": build_inputs[
                    "expected_cargo_lock_sha256"
                ],
                "cargo_version": build_inputs["cargo_version"],
                "rustc_version": build_inputs["rustc_version"],
                "build_isolation": build_inputs["build_isolation"],
                "cargo_home_policy": build_inputs["cargo_home_policy"],
                "cargo_home_config_files": build_inputs["cargo_home_config_files"],
                "project_cargo_config": build_inputs["project_cargo_config"],
                "dependency_fetch_policy": build_inputs["dependency_fetch_policy"],
                "build_inputs_sha256": "4" * 64,
                "build_inputs": build_inputs,
                "binary": str(binary),
                "binary_version": "codex-cli 0.144.0",
                "binary_bytes": binary.stat().st_size,
                "binary_sha256": sha256_file(binary),
                "source_receipt_sha256": "5" * 64,
                "preflight": {"status": "pass", "sha256": "6" * 64},
            }
        elif name == "harness_gate":
            payload = {
                "schema_version": schema_version,
                "success": True,
                "functional_status": "pass",
                "drift_status": "pass",
                "failures": [],
            }
        elif name == "harness_eval":
            payload = {
                "schema_version": schema_version,
                "success": True,
                "failures": [],
            }
        elif name in release_gate.DEV_GATE_CONTRACTS:
            gate, command = release_gate.DEV_GATE_CONTRACTS[name]
            payload = {
                "schema_version": schema_version,
                "generated_at": generated_at,
                "run_id": run_id,
                "gate": gate,
                "command": list(command),
                "status": "pass",
            }
        else:
            payload = {"schema_version": schema_version, "status": "pass"}
        if name == "qwendex_check":
            payload["command"] = "check"
            payload["data"] = {
                "health_mode": "strict",
                "effective_policy": {
                    "guard": {"markers": list(release_gate.GUARD_MARKERS)}
                },
            }
        elif name == "qwendex_doctor":
            payload.update({"command": "doctor", "data": {"health_mode": "strict"}})
        elif name == "codex_status":
            payload["command"] = "codex-status"
        elif name == "codex_patch":
            payload.update(
                {"command": "codex-patch", "data": {"supported": True, "applied": True}}
            )
        elif name == "qwendex_eval":
            payload.update(
                {"command": "eval", "data": {"success": True, "failures": []}}
            )
        bind_payload(
            release_gate,
            name,
            payload,
            run_id=run_id,
            generated_at=generated_at,
            commit=commit,
            tree=tree,
        )
        write_json(meta_root / filename, payload)
    dev_status = meta_root / "dev_status.json"
    dev_payload = {
        "schema_version": "qwendex.dev.status.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "verify-release",
        "status": "pass",
        "blockers": [],
    }
    bind_payload(
        release_gate,
        "dev_status",
        dev_payload,
        run_id=run_id,
        generated_at=datetime.now(UTC).isoformat(),
        commit=commit,
        tree=tree,
    )
    write_json(dev_status, dev_payload)
    fixture = {
        "repo": repo,
        "run_id": run_id,
        "meta_root": meta_root,
        "results_root": results_root,
        "dev_status": dev_status,
        "started": started,
        "output": meta_root / "release_validation_summary.json",
        "ci_attestation": meta_root / "qwendex-ci-attestation.json",
        "authoritative_ci_attestation": tmp_path
        / "remote-ci-artifact"
        / "qwendex-ci-attestation.json",
        "authoritative_ci_report": tmp_path
        / "remote-ci-artifact"
        / "qwendex-artifact-contract.json",
        "fake_gh": tmp_path / "bin" / "gh",
    }
    write_fake_gh(Path(fixture["fake_gh"]))
    write_ci_attestation(fixture)
    return fixture


def invoke(
    fixture: dict[str, object],
    *extra: str,
    include_publish_contract: bool = True,
) -> tuple[int, dict]:
    release_gate = load_release_gate()
    args = [
        "--repo-root",
        str(fixture["repo"]),
        "--meta-root",
        str(fixture["meta_root"]),
        "--results-root",
        str(fixture["results_root"]),
        "--dev-status",
        str(fixture["dev_status"]),
        "--output",
        str(fixture["output"]),
        "--tier",
        "release",
        "--expected-version",
        "1.2.3",
        "--expected-tag",
        "v1.2.3",
        "--expected-codex-version",
        "0.144.0",
        "--run-id",
        str(fixture["run_id"]),
        "--run-started-at",
        fixture["started"].isoformat(),
        "--default-branch",
        "main",
    ]
    if include_publish_contract:
        args.extend(
            [
                "--trusted-origin",
                TRUSTED_ORIGIN,
                "--ci-attestation",
                str(fixture["ci_attestation"]),
            ]
        )
    args.extend(extra)
    commit, _ = source_ids(Path(fixture["repo"]))
    remote_commit = str(fixture.get("remote_commit_override") or commit)
    env_updates = {
        "PATH": f"{Path(fixture['fake_gh']).parent}:{os.environ['PATH']}",
        "QWENDEX_TEST_REMOTE_COMMIT": remote_commit,
        "QWENDEX_TEST_CI_ATTESTATION": str(fixture["authoritative_ci_attestation"]),
        "QWENDEX_TEST_CI_REPORT": str(fixture["authoritative_ci_report"]),
        "QWENDEX_TEST_CI_RUN_ID": "123456789",
    }
    previous = {key: os.environ.get(key) for key in env_updates}
    os.environ.update(env_updates)
    try:
        rc = release_gate.main(args)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    payload = json.loads(Path(fixture["output"]).read_text(encoding="utf-8"))
    return rc, payload


def write_live_receipts(fixture: dict[str, object]) -> None:
    release_gate = load_release_gate()
    commit, tree = source_ids(Path(fixture["repo"]))
    generated_at = datetime.now(UTC).isoformat()
    codex_build = json.loads(
        (Path(fixture["meta_root"]) / "codex_build.json").read_text(
            encoding="utf-8"
        )
    )
    payloads = {
        "live_launcher": {
            "schema_version": "qwendex.dev.gate.v1",
            "generated_at": generated_at,
            "run_id": fixture["run_id"],
            "gate": "live_launcher",
            "command": ["scripts/run_local_qwen_codex.sh", "--check"],
            "status": "pass",
            "success": True,
            "returncode": 0,
        },
        "live_reliability": {
            "schema_version": "qwendex.reliability_probe.v1",
            "generated_at": generated_at,
            "status": "pass",
            "base_url": "http://127.0.0.1:1234",
            "require_live_bridge": True,
            "probes": [
                {
                    "name": "models_endpoint",
                    "success": True,
                    "duration_seconds": 0.01,
                    "details": {"status": 200, "models": ["qwen-local"]},
                },
                {
                    "name": "exact_marker",
                    "success": True,
                    "duration_seconds": 0.02,
                    "details": {
                        "status": 200,
                        "exact_match": True,
                        "marker_count": 0,
                        "parse_mode": "json",
                    },
                },
            ],
        },
        "live_codex_acceptance": {
            "schema_version": "qwendex.live_codex_acceptance.v1",
            "generated_at": generated_at,
            "status": "pass",
            "success": True,
            "returncode": 0,
            "fresh_home_created": True,
            "normal_home_unchanged": True,
            "malformed_event_count": 0,
            "command_execution_count": 1,
            "successful_tool_result_count": 1,
            "matching_command_count": 1,
            "tool_round_trip_proven": True,
            "final_text_exact": True,
            "event_final_text_exact": True,
            "final_output_regular": True,
            "launcher_sha256": "8" * 64,
            "launcher_unchanged": True,
            "codex_bin_sha256": codex_build["binary_sha256"],
            "codex_bin_bytes": codex_build["binary_bytes"],
            "codex_bin_unchanged": True,
            "blockers": [],
        },
    }
    for name, payload in payloads.items():
        bind_payload(
            release_gate,
            name,
            payload,
            run_id=str(fixture["run_id"]),
            generated_at=generated_at,
            commit=commit,
            tree=tree,
        )
        write_json(
            Path(fixture["meta_root"]) / release_gate.LIVE_RECEIPTS[name], payload
        )


def test_release_gate_binds_passing_receipts_to_clean_tagged_default_branch(tmp_path):
    fixture = release_fixture(tmp_path)

    rc, payload = invoke(fixture)

    assert rc == 0
    assert payload["schema_version"] == "qwendex.dev.release_summary.v2"
    assert payload["recommendation"] == "publish-ready"
    assert payload["publish_ready"] is True
    assert payload["source"]["branch"] == "main"
    assert payload["source"]["clean"] is True
    assert payload["source"]["remote_default_matches_head"] is True
    assert payload["source"]["origin_matches_trusted"] is True
    assert payload["source"]["tag_annotated"] is True
    assert payload["source"]["tag_matches_head"] is True
    assert {
        item["value"] for item in payload["source"]["version_sources"].values()
    } == {"1.2.3"}
    assert set(payload["gates"]) >= {
        "bootstrap",
        "static_gate",
        "test_gate",
        "config_gate",
        "codex_build",
        "qwendex_check",
        "qwendex_doctor",
        "codex_status",
        "codex_patch",
        "qwendex_eval",
        "harness_gate",
        "harness_eval",
        "dev_status",
    }
    assert all(item["passed"] for item in payload["gates"].values())
    assert len(payload["evidence_sha256"]) == 64
    assert len(payload["receipt_sha256"]) == 64
    release_gate = load_release_gate()
    assert release_gate.verify_release_summary_payload(
        payload, require_publish_ready=True
    ) == []
    assert payload["artifact_contract"]["status"] == "pass"
    assert payload["ci_attestation"]["passed"] is True
    assert payload["source_recheck"]["matches_initial_source"] is True
    assert all(
        item["release_binding"]["source_matches"] for item in payload["gates"].values()
    )


def test_release_live_gate_requires_inference_and_fresh_home_tool_evidence(tmp_path):
    fixture = release_fixture(tmp_path)
    write_live_receipts(fixture)

    rc, payload = invoke(fixture, "--require-live")

    assert rc == 0
    assert payload["live_required"] is True
    assert set(payload["gates"]) >= set(load_release_gate().LIVE_RECEIPTS)
    assert all(
        payload["gates"][name]["passed"]
        for name in load_release_gate().LIVE_RECEIPTS
    )
    assert (
        payload["gates"]["live_codex_acceptance"]["codex_bin_sha256"]
        == payload["gates"]["codex_build"]["binary_sha256"]
    )

    acceptance_path = (
        Path(fixture["meta_root"])
        / load_release_gate().LIVE_RECEIPTS["live_codex_acceptance"]
    )
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["tool_round_trip_proven"] = False
    refresh_binding(acceptance)
    write_json(acceptance_path, acceptance)

    blocked_rc, blocked = invoke(fixture, "--require-live")

    assert blocked_rc == 1
    assert any(
        "live Codex acceptance contract did not pass" in item
        for item in blocked["evidence_blockers"]
    )


def test_release_live_gate_rejects_a_different_codex_binary(tmp_path):
    fixture = release_fixture(tmp_path)
    write_live_receipts(fixture)
    acceptance_path = (
        Path(fixture["meta_root"])
        / load_release_gate().LIVE_RECEIPTS["live_codex_acceptance"]
    )
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    acceptance["codex_bin_sha256"] = "9" * 64
    refresh_binding(acceptance)
    write_json(acceptance_path, acceptance)

    rc, payload = invoke(fixture, "--require-live")

    assert rc == 1
    assert any(
        "live Codex binary does not match validated build evidence" in item
        for item in payload["evidence_blockers"]
    )


def test_release_summary_digest_detects_post_run_publish_decision_tampering(tmp_path):
    fixture = release_fixture(tmp_path)
    rc, payload = invoke(fixture)
    assert rc == 0
    release_gate = load_release_gate()

    payload["publish_ready"] = False
    payload["recommendation"] = "blocked"

    blockers = release_gate.verify_release_summary_payload(
        payload, require_publish_ready=True
    )
    assert "release summary receipt digest mismatch" in blockers
    assert any("publish_ready" in blocker for blocker in blockers)


def test_publish_summary_verifier_rejects_recomputed_minimal_and_malformed_payloads():
    release_gate = load_release_gate()
    minimal = {
        "schema_version": release_gate.SCHEMA_VERSION,
        "status": "pass",
        "recommendation": "publish-ready",
        "publish_ready": True,
        "candidate_mode": False,
        "live_required": True,
        "blockers": [],
        "evidence_blockers": [],
        "publish_blockers": [],
        "source": {"clean": True},
        "gates": {"invented_gate": {"passed": True}},
        "artifact_contract": {"status": "pass"},
        "marker_scan": {"status": "pass"},
        "ci_attestation": {
            "passed": True,
            "online_verification": {
                "required": False,
                "queried": False,
                "artifact_downloaded": False,
                "passed": False,
            },
        },
        "source_recheck": {"matches_initial_source": True},
        "evidence_sha256": "0" * 64,
        "receipt_sha256": "",
    }
    minimal["receipt_sha256"] = release_gate.release_summary_digest(minimal)

    blockers = release_gate.verify_release_summary_payload(
        minimal, require_publish_ready=True
    )

    assert blockers
    assert any("exact_gate_contract" in blocker for blocker in blockers)
    assert any("ci_online_passed" in blocker for blocker in blockers)
    assert any("evidence_digest_matches" in blocker for blocker in blockers)
    for key in (
        "artifact_contract",
        "marker_scan",
        "ci_attestation",
        "source_recheck",
        "gates",
    ):
        malformed = dict(minimal)
        malformed[key] = []
        malformed["receipt_sha256"] = ""
        malformed["receipt_sha256"] = release_gate.release_summary_digest(malformed)
        assert release_gate.verify_release_summary_payload(
            malformed, require_publish_ready=True
        )


def test_publish_gate_rejects_a_locally_forged_ci_file_that_is_not_the_remote_artifact(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    supplied_path = Path(fixture["ci_attestation"])
    supplied = json.loads(supplied_path.read_text(encoding="utf-8"))
    supplied["generated_at"] = datetime.now(UTC).isoformat()
    write_json(supplied_path, supplied)

    rc, payload = invoke(fixture)

    assert rc == 1
    assert payload["ci_attestation"]["checks"]["source_matches"] is True
    online = payload["ci_attestation"]["online_verification"]
    assert online["checks"]["attestation_bytes_match"] is False
    assert online["passed"] is False


def test_publish_gate_rejects_a_stale_local_origin_ref_when_remote_main_advanced(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    fixture["remote_commit_override"] = "f" * 40

    rc, payload = invoke(fixture)

    assert rc == 1
    assert payload["source"]["remote_default_matches_head"] is True
    assert payload["source"]["trusted_remote"]["matches_expected"] is False
    assert (
        payload["source_recheck"]["trusted_remote_recheck"]["matches_expected"]
        is False
    )


def test_publish_summary_file_verifier_replays_remote_ci_source_and_gate_files(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    rc, _ = invoke(fixture)
    assert rc == 0
    release_gate = load_release_gate()
    commit, _ = source_ids(Path(fixture["repo"]))
    updates = {
        "PATH": f"{Path(fixture['fake_gh']).parent}:{os.environ['PATH']}",
        "QWENDEX_TEST_REMOTE_COMMIT": commit,
        "QWENDEX_TEST_CI_ATTESTATION": str(fixture["authoritative_ci_attestation"]),
        "QWENDEX_TEST_CI_REPORT": str(fixture["authoritative_ci_report"]),
        "QWENDEX_TEST_CI_RUN_ID": "123456789",
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        verify_rc = release_gate.verify_release_summary_file(
            Path(fixture["output"]),
            require_publish_ready=True,
            repo_root=Path(fixture["repo"]),
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert verify_rc == 0


def test_publish_summary_replay_rejects_self_declared_codex_digests(tmp_path):
    fixture = release_fixture(tmp_path)
    rc, payload = invoke(fixture)
    assert rc == 0
    release_gate = load_release_gate()
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    codex_payload = json.loads(codex_path.read_text(encoding="utf-8"))
    codex_payload["source_patch_sha256"] = codex_payload[
        "expected_source_patch_sha256"
    ] = "8" * 64
    codex_payload["cargo_lock_sha256"] = codex_payload[
        "expected_cargo_lock_sha256"
    ] = "9" * 64
    codex_payload["build_inputs"]["source_patch_sha256"] = codex_payload[
        "build_inputs"
    ]["expected_source_patch_sha256"] = "8" * 64
    codex_payload["build_inputs"]["cargo_lock_sha256"] = codex_payload[
        "build_inputs"
    ]["expected_cargo_lock_sha256"] = "9" * 64
    refresh_binding(codex_payload)
    write_json(codex_path, codex_payload)
    payload["gates"]["codex_build"]["sha256"] = sha256_file(codex_path)
    payload["gates"]["codex_build"]["bytes"] = codex_path.stat().st_size
    evidence_core = {
        "run_id": payload["run_id"],
        "tier": payload["tier"],
        "source": payload["source"],
        "gates": payload["gates"],
        "artifact_contract": payload["artifact_contract"],
        "marker_scan": payload["marker_scan"],
        "ci_attestation": payload["ci_attestation"],
        "source_recheck": payload["source_recheck"],
    }
    payload["evidence_sha256"] = release_gate.canonical_digest(evidence_core)
    payload["receipt_sha256"] = ""
    payload["receipt_sha256"] = release_gate.release_summary_digest(payload)
    write_json(Path(fixture["output"]), payload)

    commit, _ = source_ids(Path(fixture["repo"]))
    updates = {
        "PATH": f"{Path(fixture['fake_gh']).parent}:{os.environ['PATH']}",
        "QWENDEX_TEST_REMOTE_COMMIT": commit,
        "QWENDEX_TEST_CI_ATTESTATION": str(fixture["authoritative_ci_attestation"]),
        "QWENDEX_TEST_CI_REPORT": str(fixture["authoritative_ci_report"]),
        "QWENDEX_TEST_CI_RUN_ID": "123456789",
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        verify_rc = release_gate.verify_release_summary_file(
            Path(fixture["output"]),
            require_publish_ready=True,
            repo_root=Path(fixture["repo"]),
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert verify_rc == 1


def test_publish_summary_replay_rejects_noncanonical_codex_source_head(tmp_path):
    fixture = release_fixture(tmp_path)
    rc, payload = invoke(fixture)
    assert rc == 0
    release_gate = load_release_gate()
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    codex_payload = json.loads(codex_path.read_text(encoding="utf-8"))
    noncanonical_head = "e" * 40
    codex_payload["source_head"] = noncanonical_head
    codex_payload["build_inputs"]["source_head"] = noncanonical_head
    codex_payload["build_inputs"]["source_ref_target"] = noncanonical_head
    refresh_binding(codex_payload)
    write_json(codex_path, codex_payload)
    payload["gates"]["codex_build"]["sha256"] = sha256_file(codex_path)
    payload["gates"]["codex_build"]["bytes"] = codex_path.stat().st_size
    evidence_core = {
        "run_id": payload["run_id"],
        "tier": payload["tier"],
        "source": payload["source"],
        "gates": payload["gates"],
        "artifact_contract": payload["artifact_contract"],
        "marker_scan": payload["marker_scan"],
        "ci_attestation": payload["ci_attestation"],
        "source_recheck": payload["source_recheck"],
    }
    payload["evidence_sha256"] = release_gate.canonical_digest(evidence_core)
    payload["receipt_sha256"] = ""
    payload["receipt_sha256"] = release_gate.release_summary_digest(payload)
    write_json(Path(fixture["output"]), payload)

    commit, _ = source_ids(Path(fixture["repo"]))
    updates = {
        "PATH": f"{Path(fixture['fake_gh']).parent}:{os.environ['PATH']}",
        "QWENDEX_TEST_REMOTE_COMMIT": commit,
        "QWENDEX_TEST_CI_ATTESTATION": str(fixture["authoritative_ci_attestation"]),
        "QWENDEX_TEST_CI_REPORT": str(fixture["authoritative_ci_report"]),
        "QWENDEX_TEST_CI_RUN_ID": "123456789",
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        verify_rc = release_gate.verify_release_summary_file(
            Path(fixture["output"]),
            require_publish_ready=True,
            repo_root=Path(fixture["repo"]),
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert verify_rc == 1


def test_publish_summary_replay_rechecks_source_and_remote_after_ci(
    tmp_path, monkeypatch, capsys
):
    fixture = release_fixture(tmp_path)
    rc, _ = invoke(fixture)
    assert rc == 0
    release_gate = load_release_gate()
    repo = Path(fixture["repo"])
    original_verify_ci = release_gate.verify_ci_attestation_online

    def mutate_after_ci(*args, **kwargs):
        result = original_verify_ci(*args, **kwargs)
        (repo / "post-ci-drift.txt").write_text("drift\n", encoding="utf-8")
        os.environ["QWENDEX_TEST_REMOTE_COMMIT"] = "f" * 40
        return result

    monkeypatch.setattr(
        release_gate, "verify_ci_attestation_online", mutate_after_ci
    )
    commit, _ = source_ids(repo)
    updates = {
        "PATH": f"{Path(fixture['fake_gh']).parent}:{os.environ['PATH']}",
        "QWENDEX_TEST_REMOTE_COMMIT": commit,
        "QWENDEX_TEST_CI_ATTESTATION": str(fixture["authoritative_ci_attestation"]),
        "QWENDEX_TEST_CI_REPORT": str(fixture["authoritative_ci_report"]),
        "QWENDEX_TEST_CI_RUN_ID": "123456789",
    }
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        verify_rc = release_gate.verify_release_summary_file(
            Path(fixture["output"]),
            require_publish_ready=True,
            repo_root=repo,
        )
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    stderr = capsys.readouterr().err
    assert verify_rc == 1
    assert "publish replay final source recheck failed" in stderr
    assert "trusted remote default branch does not match release HEAD" in stderr


def test_online_ci_download_timeout_returns_structured_blocker(tmp_path, monkeypatch):
    fixture = release_fixture(tmp_path)
    release_gate = load_release_gate()
    repo = Path(fixture["repo"])
    commit, tree = source_ids(repo)
    source = {
        "commit": commit,
        "tree": tree,
        "default_branch": "main",
        "origin_repository": "github.com/qwendex-test/qwendex",
    }
    artifacts, artifact_blockers = release_gate.artifact_contract(repo, commit)
    assert artifact_blockers == []

    def fake_gh_json(*args, **_kwargs):
        endpoint = args[1]
        if endpoint.endswith("/artifacts?per_page=100"):
            return {
                "artifacts": [
                    {
                        "id": 987654321,
                        "name": f"qwendex-ci-attestation-{commit}",
                        "expired": False,
                    }
                ]
            }
        if "/actions/workflows/" in endpoint:
            return {
                "id": 456789,
                "name": "CI",
                "path": ".github/workflows/ci.yml",
                "state": "active",
            }
        return {
            "id": 123456789,
            "name": "CI",
            "path": ".github/workflows/ci.yml",
            "workflow_id": 456789,
            "event": "push",
            "status": "completed",
            "conclusion": "success",
            "head_sha": commit,
            "head_branch": "main",
            "html_url": "https://github.com/qwendex-test/qwendex/actions/runs/123456789",
            "run_attempt": 1,
        }

    def timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("gh run download", 120)

    monkeypatch.setattr(release_gate, "gh_json", fake_gh_json)
    monkeypatch.setattr(release_gate.subprocess, "run", timeout)
    attestation = Path(fixture["ci_attestation"])
    online, blockers = release_gate.verify_ci_attestation_online(
        attestation,
        source,
        artifacts,
        expected_sha256=sha256_file(attestation),
    )

    assert online["passed"] is False
    assert any("Timeout" in blocker or "timed" in blocker for blocker in blockers)


def test_release_gate_fails_closed_for_missing_failed_and_stale_receipts(tmp_path):
    fixture = release_fixture(tmp_path)
    meta_root = Path(fixture["meta_root"])

    (meta_root / "llm_harness_gate.json").unlink()
    rc, missing = invoke(fixture)
    assert rc == 1
    assert any("required receipt missing" in blocker for blocker in missing["blockers"])

    write_json(
        meta_root / "llm_harness_gate.json",
        {"schema_version": "local_qwen_harness_gate.v1", "success": False},
    )
    rc, failed = invoke(fixture)
    assert rc == 1
    assert any("did not pass" in blocker for blocker in failed["blockers"])

    write_json(
        meta_root / "llm_harness_gate.json",
        {"schema_version": "local_qwen_harness_gate.v1", "success": True},
    )
    stale = datetime.now(UTC) - timedelta(hours=1)
    os.utime(
        meta_root / "llm_harness_gate.json", (stale.timestamp(), stale.timestamp())
    )
    rc, stale_payload = invoke(fixture)
    assert rc == 1
    assert any(
        "predates release run" in blocker for blocker in stale_payload["blockers"]
    )


def test_release_gate_fails_closed_for_missing_and_unexpected_receipt_schemas(tmp_path):
    fixture = release_fixture(tmp_path)
    gate_path = Path(fixture["meta_root"]) / "static_gate.json"

    write_json(gate_path, {"status": "pass"})
    rc, missing = invoke(fixture)
    assert rc == 1
    assert any("has no schema_version" in blocker for blocker in missing["blockers"])

    write_json(gate_path, {"schema_version": "unexpected.v9", "status": "pass"})
    rc, mismatch = invoke(fixture)
    assert rc == 1
    assert any("schema mismatch" in blocker for blocker in mismatch["blockers"])


def test_release_gate_binds_dev_gate_run_id_and_generated_time(tmp_path):
    fixture = release_fixture(tmp_path)
    gate_path = Path(fixture["meta_root"]) / "static_gate.json"
    write_json(
        gate_path,
        {
            "schema_version": "qwendex.dev.gate.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": "different-run",
            "status": "pass",
        },
    )

    rc, wrong_run = invoke(fixture)
    assert rc == 1
    assert any("run_id mismatch" in blocker for blocker in wrong_run["blockers"])

    write_json(
        gate_path,
        {
            "schema_version": "qwendex.dev.gate.v1",
            "generated_at": (fixture["started"] - timedelta(seconds=1)).isoformat(),
            "run_id": fixture["run_id"],
            "status": "pass",
        },
    )
    rc, stale_generated = invoke(fixture)
    assert rc == 1
    assert any(
        "generated_at predates release run" in blocker
        for blocker in stale_generated["blockers"]
    )


def test_release_gate_rejects_stale_mislabeled_non_strict_and_tampered_native_receipts(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    path = Path(fixture["meta_root"]) / "qwendex_check.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    payload["release_binding"]["run_id"] = "wrong-run"
    write_json(path, payload)
    rc, wrong_run = invoke(fixture)
    assert rc == 1
    assert any(
        "release binding run_id mismatch" in blocker
        for blocker in wrong_run["blockers"]
    )

    payload["release_binding"]["run_id"] = fixture["run_id"]
    payload["release_binding"]["generated_at"] = (
        fixture["started"] - timedelta(seconds=1)
    ).isoformat()
    write_json(path, payload)
    rc, stale = invoke(fixture)
    assert rc == 1
    assert any(
        "release binding generated_at predates" in blocker
        for blocker in stale["blockers"]
    )

    payload["release_binding"]["generated_at"] = datetime.now(UTC).isoformat()
    payload["command"] = "doctor"
    refresh_binding(payload)
    write_json(path, payload)
    rc, mislabeled = invoke(fixture)
    assert rc == 1
    assert any(
        "native receipt command mismatch" in blocker
        for blocker in mislabeled["blockers"]
    )

    payload["command"] = "check"
    payload["data"]["health_mode"] = "advisory"
    refresh_binding(payload)
    write_json(path, payload)
    rc, advisory = invoke(fixture)
    assert rc == 1
    assert any(
        "not strict health evidence" in blocker for blocker in advisory["blockers"]
    )

    payload["data"]["health_mode"] = "strict"
    payload["release_binding"]["source"]["commit"] = "0" * 40
    refresh_binding(payload)
    write_json(path, payload)
    rc, wrong_source = invoke(fixture)
    assert rc == 1
    assert any(
        "source commit/tree mismatch" in blocker for blocker in wrong_source["blockers"]
    )

    payload["release_binding"]["source"]["commit"] = source_ids(Path(fixture["repo"]))[
        0
    ]
    refresh_binding(payload)
    payload["summary"] = "changed after binding"
    write_json(path, payload)
    rc, tampered = invoke(fixture)
    assert rc == 1
    assert any("payload digest mismatch" in blocker for blocker in tampered["blockers"])


def test_release_gate_requires_current_ci_attestation_for_publish_but_not_candidate(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    ci_path = Path(fixture["ci_attestation"])
    ci_path.unlink()

    rc, missing = invoke(fixture)
    assert rc == 1
    assert any(
        "CI attestation is missing" in blocker
        for blocker in missing["publish_blockers"]
    )

    rc, candidate = invoke(fixture, "--candidate", include_publish_contract=False)
    assert rc == 0
    assert candidate["recommendation"] == "candidate-ready"
    assert candidate["publish_ready"] is False

    write_ci_attestation(fixture)
    payload = json.loads(ci_path.read_text(encoding="utf-8"))
    payload["commit"] = "0" * 40
    write_json(ci_path, payload)
    rc, wrong_commit = invoke(fixture)
    assert rc == 1
    assert wrong_commit["ci_attestation"]["checks"]["source_matches"] is False

    write_ci_attestation(fixture)
    payload = json.loads(ci_path.read_text(encoding="utf-8"))
    payload["generated_at"] = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    write_json(ci_path, payload)
    rc, stale = invoke(fixture)
    assert rc == 1
    assert stale["ci_attestation"]["checks"]["generated_fresh"] is False

    write_ci_attestation(fixture)
    payload = json.loads(ci_path.read_text(encoding="utf-8"))
    payload["run_id"] = str(payload["run_id"])
    write_json(ci_path, payload)
    rc, string_run_id = invoke(fixture)
    assert rc == 1
    assert string_run_id["ci_attestation"]["checks"]["run_identity_valid"] is False


def test_ci_run_url_identity_allows_github_repository_case_normalization(tmp_path):
    fixture = release_fixture(tmp_path)
    release_gate = load_release_gate()
    ci_path = Path(fixture["ci_attestation"])
    payload = json.loads(ci_path.read_text(encoding="utf-8"))
    payload["repository"] = "Qwendex-Test/Qwendex"
    payload["workflow_ref"] = (
        "Qwendex-Test/Qwendex/.github/workflows/ci.yml@refs/heads/main"
    )
    payload["run_url"] = (
        f"https://github.com/Qwendex-Test/Qwendex/actions/runs/{payload['run_id']}"
    )
    write_json(ci_path, payload)
    source, source_blockers, publish_blockers = release_gate.source_contract(
        Path(fixture["repo"]),
        "1.2.3",
        "v1.2.3",
        "0.144.0",
        "main",
        TRUSTED_ORIGIN,
    )
    assert source_blockers == []
    assert publish_blockers == []
    artifacts, artifact_blockers = release_gate.artifact_contract(
        Path(fixture["repo"]), source["commit"]
    )
    assert artifact_blockers == []

    inspected, blockers = release_gate.inspect_ci_attestation(
        ci_path, source, artifacts, datetime.now(UTC), 168
    )

    assert blockers == []
    assert inspected["checks"]["run_identity_valid"] is True


def test_release_gate_requires_trusted_configured_origin_and_annotated_tag(tmp_path):
    fixture = release_fixture(tmp_path)
    repo = Path(fixture["repo"])

    run("git", "remote", "remove", "origin", cwd=repo)
    rc, no_origin = invoke(fixture)
    assert rc == 1
    assert any(
        "configured origin" in blocker for blocker in no_origin["publish_blockers"]
    )

    run("git", "remote", "add", "origin", TRUSTED_ORIGIN, cwd=repo)
    run("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    run("git", "tag", "-d", "v1.2.3", cwd=repo)
    run("git", "tag", "v1.2.3", cwd=repo)
    rc, lightweight = invoke(fixture)
    assert rc == 1
    assert lightweight["source"]["tag_annotated"] is False
    assert any(
        "is not annotated" in blocker for blocker in lightweight["publish_blockers"]
    )


def test_release_gate_rejects_codex_binary_and_build_input_drift(tmp_path):
    fixture = release_fixture(tmp_path)
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    payload = json.loads(codex_path.read_text(encoding="utf-8"))
    binary = Path(payload["binary"])
    binary.write_bytes(binary.read_bytes() + b"tampered")

    rc, binary_drift = invoke(fixture)
    assert rc == 1
    assert (
        binary_drift["gates"]["codex_build"]["codex_build_checks"][
            "binary_digest_matches"
        ]
        is False
    )

    binary.write_bytes(b"fixture codex binary\n")
    payload["build_inputs"]["untracked_paths"] = [".cargo/config.toml"]
    refresh_binding(payload)
    write_json(codex_path, payload)
    rc, source_drift = invoke(fixture)
    assert rc == 1
    assert (
        source_drift["gates"]["codex_build"]["codex_build_checks"][
            "build_inputs_passed"
        ]
        is False
    )


def test_codex_binary_digest_is_streamed_without_path_read_bytes(tmp_path, monkeypatch):
    fixture = release_fixture(tmp_path)
    payload = json.loads(
        (Path(fixture["meta_root"]) / "codex_build.json").read_text(encoding="utf-8")
    )
    release_gate = load_release_gate()

    def reject_read_bytes(_path):
        raise AssertionError("Codex binary validation must stream the binary")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    checks, blockers = release_gate.validate_codex_build_receipt(
        payload,
        "0.144.0",
        CANONICAL_CODEX_SOURCE_COMMIT,
        CANONICAL_CODEX_SOURCE_ORIGIN,
        CANONICAL_CODEX_PATCH_SHA256,
        CANONICAL_CODEX_CARGO_LOCK_SHA256,
    )

    assert blockers == []
    assert checks["binary_digest_matches"] is True


def test_release_gate_rejects_a_noncanonical_allowlisted_codex_patch(tmp_path):
    fixture = release_fixture(tmp_path)
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    payload = json.loads(codex_path.read_text(encoding="utf-8"))
    payload["source_patch_sha256"] = "8" * 64
    payload["build_inputs"]["source_patch_sha256"] = "8" * 64
    refresh_binding(payload)
    write_json(codex_path, payload)

    rc, result = invoke(fixture)

    assert rc == 1
    checks = result["gates"]["codex_build"]["codex_build_checks"]
    assert checks["copied_inputs_match"] is True
    assert checks["canonical_patch_matches"] is False


def test_release_gate_rejects_self_declared_noncanonical_codex_digests(tmp_path):
    fixture = release_fixture(tmp_path)
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    payload = json.loads(codex_path.read_text(encoding="utf-8"))
    payload["source_patch_sha256"] = payload["expected_source_patch_sha256"] = (
        "8" * 64
    )
    payload["cargo_lock_sha256"] = payload["expected_cargo_lock_sha256"] = "9" * 64
    payload["build_inputs"]["source_patch_sha256"] = payload["build_inputs"][
        "expected_source_patch_sha256"
    ] = "8" * 64
    payload["build_inputs"]["cargo_lock_sha256"] = payload["build_inputs"][
        "expected_cargo_lock_sha256"
    ] = "9" * 64
    refresh_binding(payload)
    write_json(codex_path, payload)

    rc, result = invoke(fixture)

    assert rc == 1
    checks = result["gates"]["codex_build"]["codex_build_checks"]
    assert checks["copied_inputs_match"] is True
    assert checks["canonical_patch_matches"] is False
    assert checks["canonical_cargo_lock_matches"] is False
    assert (
        result["source"]["codex_build_digests"]["source_patch_sha256"]["value"]
        == CANONICAL_CODEX_PATCH_SHA256
    )
    assert (
        result["source"]["codex_build_digests"]["cargo_lock_sha256"]["value"]
        == CANONICAL_CODEX_CARGO_LOCK_SHA256
    )


def test_release_gate_rejects_self_consistent_noncanonical_codex_source_head(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    payload = json.loads(codex_path.read_text(encoding="utf-8"))
    noncanonical_head = "e" * 40
    payload["source_head"] = noncanonical_head
    payload["build_inputs"]["source_head"] = noncanonical_head
    payload["build_inputs"]["source_ref_target"] = noncanonical_head
    refresh_binding(payload)
    write_json(codex_path, payload)

    rc, result = invoke(fixture)

    assert rc == 1
    checks = result["gates"]["codex_build"]["codex_build_checks"]
    assert checks["source_ref_head_consistent"] is True
    assert checks["canonical_source_commit_matches"] is False
    assert (
        result["source"]["codex_source_provenance"]["source_commit"]["value"]
        == CANONICAL_CODEX_SOURCE_COMMIT
    )


def test_release_gate_rejects_symlink_and_non_executable_codex_binaries(tmp_path):
    fixture = release_fixture(tmp_path)
    codex_path = Path(fixture["meta_root"]) / "codex_build.json"
    payload = json.loads(codex_path.read_text(encoding="utf-8"))
    binary = Path(payload["binary"])
    symlink = tmp_path / "codex-symlink"
    symlink.symlink_to(binary)
    payload["binary"] = str(symlink)
    refresh_binding(payload)
    write_json(codex_path, payload)

    rc, symlinked = invoke(fixture)
    assert rc == 1
    checks = symlinked["gates"]["codex_build"]["codex_build_checks"]
    assert checks["binary_not_symlink"] is False
    assert checks["binary_regular"] is False

    payload["binary"] = str(binary)
    binary.chmod(0o644)
    refresh_binding(payload)
    write_json(codex_path, payload)
    rc, non_executable = invoke(fixture)
    assert rc == 1
    checks = non_executable["gates"]["codex_build"]["codex_build_checks"]
    assert checks["binary_regular"] is True
    assert checks["binary_digest_matches"] is True
    assert checks["binary_executable"] is False


def test_release_gate_enforces_source_branch_tag_cleanliness_and_version(tmp_path):
    fixture = release_fixture(tmp_path)
    repo = Path(fixture["repo"])

    run("git", "switch", "-c", "release/candidate", cwd=repo)
    rc, wrong_branch = invoke(fixture)
    assert rc == 1
    assert any(
        "not default branch" in blocker for blocker in wrong_branch["publish_blockers"]
    )

    rc, candidate = invoke(fixture, "--candidate")
    assert rc == 0
    assert candidate["recommendation"] == "candidate-ready"
    assert candidate["publish_ready"] is False

    run("git", "switch", "main", cwd=repo)
    (repo / "README.md").write_text(
        "This checkout is seeded as `v1.2.3`.\nDirty.\n", encoding="utf-8"
    )
    rc, dirty = invoke(fixture)
    assert rc == 1
    assert "source worktree is not clean" in dirty["evidence_blockers"]

    run("git", "restore", "README.md", cwd=repo)
    run("git", "tag", "-d", "v1.2.3", cwd=repo)
    rc, missing_tag = invoke(fixture)
    assert rc == 1
    assert any(
        "tag 'v1.2.3' is missing" in blocker
        for blocker in missing_tag["publish_blockers"]
    )

    run("git", "tag", "v1.2.3", cwd=repo)
    write_json(repo / "config/qwendex/qwendex.sample.json", {"version": "9.9.9"})
    commit_and_retag(repo, "drift sample version")
    rc, version_drift = invoke(fixture)
    assert rc == 1
    assert any(
        "version source sample_config" in blocker
        for blocker in version_drift["evidence_blockers"]
    )


def test_release_gate_requires_head_to_match_remote_default_ref(tmp_path):
    fixture = release_fixture(tmp_path)
    repo = Path(fixture["repo"])
    previous = run("git", "rev-parse", "HEAD", cwd=repo)
    run("git", "commit", "--allow-empty", "-m", "new local main", cwd=repo)
    run("git", "tag", "-f", "v1.2.3", cwd=repo)
    run("git", "update-ref", "refs/remotes/origin/main", previous, cwd=repo)

    rc, payload = invoke(fixture)

    assert rc == 1
    assert payload["source"]["remote_default_matches_head"] is False
    assert any(
        "does not match remote default ref" in blocker
        for blocker in payload["publish_blockers"]
    )


def test_release_gate_scans_full_tracked_artifact_for_private_and_runtime_material(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    repo = Path(fixture["repo"])

    private_path = "/" + "home/alice/private/project"
    (repo / "operator-notes.md").write_text(
        f"Local worktree: {private_path}\n", encoding="utf-8"
    )
    (repo / "results").mkdir()
    write_json(repo / "results/run.json", {"status": "pass"})
    write_json(
        repo / "docs/validation/public-summary.json",
        {"status": "pass", "stderr_tail": "raw command transcript"},
    )
    commit_and_retag(repo, "add forbidden artifact fixtures")

    rc, payload = invoke(fixture)

    assert rc == 1
    contract = payload["artifact_contract"]
    assert contract["status"] == "blocked"
    assert contract["private_path_hits"] == ["operator-notes.md:1"]
    assert contract["forbidden_paths"] == ["results/run.json"]
    assert contract["validation_summary_leaks"] == [
        "docs/validation/public-summary.json:stderr_tail"
    ]


def test_release_gate_rejects_incomplete_codex_build_contract(tmp_path):
    fixture = release_fixture(tmp_path)
    codex_build = Path(fixture["meta_root"]) / "codex_build.json"
    write_json(
        codex_build,
        {
            "schema_version": "qwendex.dev.codex_build.v1",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "pass",
            "source_ref": "rust-v0.144.0",
            "binary_version": "codex-cli 0.143.0",
            "binary_bytes": 0,
            "binary_sha256": "not-a-digest",
            "preflight": {"status": "fail"},
        },
    )

    rc, payload = invoke(fixture)

    assert rc == 1
    checks = payload["gates"]["codex_build"]["codex_build_checks"]
    assert checks["preflight_passed"] is False
    assert checks["binary_nonempty"] is False
    assert checks["version_matches"] is False
    assert any("Codex build contract" in blocker for blocker in payload["blockers"])


def test_release_gate_ignores_marker_names_in_policy_metadata_but_blocks_live_marker_output(
    tmp_path,
):
    fixture = release_fixture(tmp_path)

    rc, clean = invoke(fixture)
    assert rc == 0
    assert clean["marker_scan"]["unexpected_counts"] == {}

    write_json(
        Path(fixture["results_root"]) / "live_output.json",
        {"stdout": "LOCAL_MODEL_LOOP_DETECTED"},
    )
    rc, marked = invoke(fixture)
    assert rc == 1
    assert marked["marker_scan"]["unexpected_counts"]["LOCAL_MODEL_LOOP_DETECTED"] == 1
    assert any(
        "unexpected local-model guard markers" in blocker
        for blocker in marked["blockers"]
    )


def test_marker_scan_has_no_size_path_or_timeout_bypass_and_accepts_only_structured_evidence(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    results = Path(fixture["results_root"])
    large = results / "large-live.log"
    large.write_bytes(b"LOCAL_MODEL_LOOP_DETECTED" + b"x" * 5_000_001)
    rc, large_result = invoke(fixture)
    assert rc == 1
    assert large_result["marker_scan"]["scanned_bytes"] > 5_000_000
    assert (
        large_result["marker_scan"]["unexpected_counts"]["LOCAL_MODEL_LOOP_DETECTED"]
        == 1
    )
    large.unlink()

    disguised = results / "malformed_tool_envelope_suppression" / "unrelated.json"
    write_json(disguised, {"stdout": "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED"})
    rc, disguised_result = invoke(fixture)
    assert rc == 1
    assert (
        disguised_result["marker_scan"]["unexpected_counts"][
            "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED"
        ]
        == 1
    )
    disguised.unlink()

    timeout = results / "live-timeout.log"
    timeout.write_text("QWENDEX_TIMEOUT", encoding="utf-8")
    rc, timeout_result = invoke(fixture)
    assert rc == 1
    assert timeout_result["marker_scan"]["unexpected_counts"]["QWENDEX_TIMEOUT"] == 1
    timeout.unlink()

    structured = results / "structured-marker-evidence.json"
    write_json(
        structured,
        {
            "schema_version": "local_qwen_harness_eval.v1",
            "case_id": "oversized_generated_command_recovery",
            "success": True,
            "functional_status": "pass",
            "drift_status": "pass",
            "expected_guard_markers": ["LOCAL_MODEL_TOOL_CALL_TOO_LARGE"],
            "observed_guard_markers": ["LOCAL_MODEL_TOOL_CALL_TOO_LARGE"],
        },
    )
    rc, accepted = invoke(fixture)
    assert rc == 0
    assert accepted["marker_scan"]["expected_counts"] == {
        "LOCAL_MODEL_TOOL_CALL_TOO_LARGE": 1
    }
    assert accepted["marker_scan"]["unexpected_counts"] == {}

    payload = json.loads(structured.read_text(encoding="utf-8"))
    payload["observed_guard_markers"] = ["LOCAL_MODEL_LOOP_DETECTED"]
    write_json(structured, payload)
    rc, invalid = invoke(fixture)
    assert rc == 1
    assert invalid["marker_scan"]["structured_errors"]
    assert invalid["marker_scan"]["unexpected_counts"]["LOCAL_MODEL_LOOP_DETECTED"] == 1


def test_artifact_contract_blocks_macos_root_env_netrc_archives_and_binary_tokens(
    tmp_path,
):
    fixture = release_fixture(tmp_path)
    repo = Path(fixture["repo"])
    (repo / "operator-macos.txt").write_text(
        "/" + "Users/alice/private/project\n", encoding="utf-8"
    )
    (repo / "operator-root.txt").write_text(
        "/" + "root/private/project\n", encoding="utf-8"
    )
    (repo / ".env").write_text("PASSWORD=fixture\n", encoding="utf-8")
    (repo / ".netrc").write_text("machine example.invalid\n", encoding="utf-8")
    (repo / "backup.zip").write_bytes(b"fixture archive")
    (repo / "model.onnx").write_bytes(b"fixture model")
    (repo / "weights.pt").write_bytes(b"fixture weights")
    (repo / "runtime.bin").write_bytes(b"fixture binary")
    fake_secret = ("s" + "k-" + "A" * 24).encode()
    (repo / "binary.dat").write_bytes(b"prefix\0" + fake_secret)
    (repo / "benign-policy.txt").write_text(
        "locked-network-fetch-with-registry-checksums\n", encoding="utf-8"
    )
    commit_and_retag(repo, "add adversarial private artifacts")

    rc, payload = invoke(fixture)
    assert rc == 1
    contract = payload["artifact_contract"]
    assert contract["status"] == "blocked"
    assert set(contract["forbidden_paths"]) >= {
        ".env",
        ".netrc",
        "backup.zip",
        "model.onnx",
        "runtime.bin",
        "weights.pt",
    }
    assert set(contract["private_path_hits"]) >= {
        "operator-macos.txt:1",
        "operator-root.txt:1",
    }
    assert contract["secret_hits"] == ["binary.dat:1"]
    assert contract["binary_files_scanned"] == 1


def test_ci_workflow_emits_attestation_and_runs_actual_artifact_and_downstream_install_contracts():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    pinned_uses = re.findall(r"(?m)^\s+uses:\s+(\S+)", workflow)
    assert pinned_uses == [
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10",
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    ]
    assert workflow.count("runs-on: ubuntu-24.04") == 2
    assert 'python-version: "3.11"' in workflow
    assert re.search(r"(?m)^  verify:\n    needs: python-floor$", workflow)
    for dependency in ("jsonschema==4.26.0", "pytest==9.0.3", "ruff==0.15.20"):
        assert workflow.count(dependency) == 2

    assert "qwendex-ci-attestation" in workflow
    assert "qwendex.ci.attestation.v1" in workflow
    assert "artifact_contract" in workflow
    assert '"config_schema"' in workflow
    assert "scripts/validate_qwendex_config.py --json" in workflow
    assert '"$HOME/qwendex-dev"' in workflow
    assert "qwendex_install_deps --install --no-system --json" in workflow
    assert "qwendex-dev bootstrap --check --no-system" in workflow
    assert "--health-mode strict" in workflow
    assert "QWENDEX_QDEX_DRY_RUN=1" in workflow
    assert 'run_id = int(os.environ["GITHUB_RUN_ID"])' in workflow
    assert '"workflow_ref": os.environ["GITHUB_WORKFLOW_REF"]' in workflow
    assert '"workflow_sha": os.environ["GITHUB_WORKFLOW_SHA"]' in workflow
    assert workflow.count("scripts/qwendex_dev_env sync") == 2
    assert "import tomllib" in workflow
    assert '(Path(os.environ["CODEX_HOME"]) / "config.toml")' in workflow
    assert "codex features list >/dev/null" in workflow


def test_dev_env_stamps_every_release_receipt_with_the_gate_command_contract():
    release_gate = load_release_gate()
    dev_env = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")

    assert "bind_release_receipt()" in dev_env
    assert 'unbound.pop("release_binding", None)' in dev_env
    assert '"schema_version": "qwendex.dev.receipt_binding.v1"' in dev_env
    for gate, command_id in release_gate.RECEIPT_COMMAND_IDS.items():
        assert f"{gate} {command_id}" in dev_env


def test_dev_env_ci_refresh_selects_default_branch_for_same_commit(tmp_path):
    dev_env = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")
    function_match = re.search(
        r"(?ms)^refresh_ci_attestation\(\) \{.*?^\}\n", dev_env
    )
    assert function_match
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_gh = fake_bin / "gh"
    fake_gh.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

args = sys.argv[1:]
commit = "a" * 40
if args[:2] == ["run", "list"]:
    branch_index = args.index("--branch")
    if args[branch_index + 1] != "main":
        raise SystemExit("CI list did not scope the default branch")
    common = {
        "conclusion": "success",
        "event": "push",
        "headSha": commit,
        "status": "completed",
        "workflowName": "CI",
    }
    print(json.dumps([
        {**common, "databaseId": 222, "headBranch": "release/qwendex-v0.5.0"},
        {**common, "databaseId": 111, "headBranch": "main"},
    ]))
    raise SystemExit(0)
if args[:2] == ["run", "download"]:
    output = Path(args[args.index("--dir") + 1])
    output.mkdir(parents=True, exist_ok=True)
    (output / "qwendex-ci-attestation.json").write_text(
        json.dumps({"run_id": int(args[2])}) + "\\n", encoding="utf-8"
    )
    raise SystemExit(0)
raise SystemExit(f"unexpected fake gh command: {args}")
""",
        encoding="utf-8",
    )
    fake_gh.chmod(0o755)
    work_root = tmp_path / "work"
    work_root.mkdir()
    output = tmp_path / "selected-attestation.json"
    script = f"""
set -euo pipefail
WORK_ROOT="$QWENDEX_TEST_WORK_ROOT"
die() {{ printf 'error: %s\\n' "$*" >&2; exit 1; }}
{function_match.group(0)}
refresh_ci_attestation "{'a' * 40}" "$QWENDEX_TEST_OUTPUT" main
"""
    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "QWENDEX_TEST_WORK_ROOT": str(work_root),
            "QWENDEX_TEST_OUTPUT": str(output),
        }
    )

    subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert json.loads(output.read_text(encoding="utf-8"))["run_id"] == 111


def test_run_logged_gate_fails_on_an_early_command_before_a_successful_tail(tmp_path):
    dev_env = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^run_logged_gate\(\) \{.*?^\}\n\n(?=require_json_pass\(\))",
        dev_env,
    )
    assert match
    receipt = tmp_path / "early-failure.json"
    script = f"""
set -uo pipefail
{match.group(0)}
early_failure() {{
  printf 'before-failure\\n'
  false
  printf 'after-failure\\n'
}}
set +e
run_logged_gate {receipt!s} static test-run early_failure
printf '%s\\n' "$?"
"""

    result = subprocess.run(
        ["bash", "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    log = receipt.with_suffix(".log").read_text(encoding="utf-8")

    assert result.stdout.rstrip().endswith("1")
    assert payload["status"] == "fail"
    assert payload["returncode"] == 1
    assert "before-failure" in log
    assert "after-failure" not in log


def test_public_quickstart_uses_a_git_clone_and_does_not_promise_a_generated_standalone_copy():
    quickstart = (ROOT / "public/qwendex/quickstart.md").read_text(encoding="utf-8")
    dev_environment = (ROOT / "public/qwendex/dev-environment.md").read_text(
        encoding="utf-8"
    )

    assert "git clone" in quickstart
    assert "git fetch --tags" in quickstart
    assert "git status --short" in quickstart
    assert "standalone development copy" not in quickstart
    assert "git worktree" in dev_environment


def test_sampler_probe_uses_public_alias_and_environment_override(monkeypatch):
    module_path = ROOT / "scripts/probe_local_sampler_settings.py"

    monkeypatch.delenv("LOCAL_QWEN_MODEL", raising=False)
    spec = importlib.util.spec_from_file_location("sampler_probe_default", module_path)
    assert spec and spec.loader
    default_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(default_module)
    assert default_module.DEFAULT_MODEL == "qwen-local"

    monkeypatch.setenv("LOCAL_QWEN_MODEL", "operator-selected-alias")
    spec = importlib.util.spec_from_file_location("sampler_probe_override", module_path)
    assert spec and spec.loader
    override_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(override_module)
    assert override_module.DEFAULT_MODEL == "operator-selected-alias"


def test_tracked_validation_summaries_exclude_raw_transcripts_sessions_and_private_paths():
    denied_keys = {
        "command",
        "command_receipts_root",
        "receipt_paths",
        "run_root",
        "session_id",
        "stderr_tail",
        "stdout_tail",
    }
    denied_text = (
        re.compile(r"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9_.-]+(?=/)"),
        re.compile(r"(?<![A-Za-z0-9_.-])/var/home/[A-Za-z0-9_.-]+(?=/)"),
        re.compile(r"(?<![A-Za-z0-9_.-])/Users/[A-Za-z0-9_.-]+(?=/)"),
        re.compile(r"(?<![A-Za-z0-9_.-])/root(?=/)"),
        re.compile(
            r"(?<![A-Za-z0-9_.-])/mnt/[a-z]/Users/[A-Za-z0-9_.-]+(?=/)", re.IGNORECASE
        ),
        re.compile(r"\bsession id:\s*[A-Za-z0-9-]+", re.IGNORECASE),
        re.compile(r"\btokens used\b", re.IGNORECASE),
        re.compile(r"results/qwendex_release_validation/"),
    )

    def audit(value, path: tuple[str, ...] = ()) -> list[str]:
        failures: list[str] = []
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = (*path, str(key))
                if key in denied_keys:
                    failures.append(".".join(child_path))
                failures.extend(audit(child, child_path))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                failures.extend(audit(child, (*path, str(index))))
        elif isinstance(value, str) and any(
            pattern.search(value) for pattern in denied_text
        ):
            failures.append(".".join(path))
        return failures

    summaries = sorted((ROOT / "docs/validation").glob("*.json"))
    assert summaries
    for summary in summaries:
        payload = json.loads(summary.read_text(encoding="utf-8"))
        assert audit(payload) == [], summary
