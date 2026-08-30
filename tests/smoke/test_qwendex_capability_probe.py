from __future__ import annotations

import json
import importlib.util
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def _load_probe():
    path = ROOT / "scripts" / "qwendex_capability_probe.py"
    spec = importlib.util.spec_from_file_location("qwendex_capability_probe_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.probe


probe = _load_probe()


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = tmp_path / "runtime"
    generation_id = "rtg-capability123456"
    generation = runtime / "generations" / generation_id
    wrapper = generation / "tree" / "scripts" / "qdex"
    wrapper.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "qdex", wrapper)
    wrapper.chmod(0o700)
    selector = tmp_path / "qdex"
    shutil.copy2(ROOT / "scripts" / "qdex", selector)
    selector.chmod(0o700)
    manifest = {
        "schema_version": "qwendex.runtime_generation.v1",
        "generation_id": generation_id,
        "status": "validated",
        "result": "pass",
        "contract_sha256": "e" * 64,
        "manifest_sha256": "a" * 64,
        "contract": {"config_sha256": "f" * 64},
        "artifact_digests": {"runtime_tree": "d" * 64},
    }
    generation.mkdir(parents=True, exist_ok=True)
    (generation / "generation.json").write_text(json.dumps(manifest), encoding="utf-8")
    (runtime / "current.json").parent.mkdir(parents=True, exist_ok=True)
    (runtime / "current.json").write_text(json.dumps({"current": generation_id}), encoding="utf-8")
    return runtime, selector, generation


def _env(tmp_path: Path) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_") and key not in {"CODEX_HOME", "CODEX_AGENT_USE"}
    }
    env.update(
        {
            "QWENDEX_ROOT": str(ROOT),
            "QWENDEX_DEV_ROOT": str(ROOT),
            "QWENDEX_STATE_DB": str(tmp_path / "state.sqlite"),
            "QWENDEX_LEDGER_DB": str(tmp_path / "ledger.sqlite"),
            "QWENDEX_PERFORMANCE_DB": str(tmp_path / "performance.sqlite"),
            "QWENDEX_META_ROOT": str(tmp_path / "meta"),
            "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
            "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "status.json"),
        }
    )
    return env


def test_capability_probe_is_no_model_and_binds_policy(tmp_path):
    runtime, selector, _ = _fixture(tmp_path)
    receipt_path = tmp_path / "receipt.json"
    receipt = probe(
        runtime_root=runtime,
        selector_path=selector,
        source_root=ROOT,
        output_path=receipt_path,
        runtime_env=_env(tmp_path),
    )
    assert receipt["status"] == "pass", receipt["blockers"]
    assert receipt["trust"] == {
        "structural_only": True,
        "live_process_verified": False,
        "provider_attestation_verified": False,
    }
    assert receipt["launch_contract"]["permission_mode"] == "read-only"
    assert receipt["launch_contract"]["max_depth"] == 2
    assert receipt["launch_contract"]["worker_cap"] == 4
    assert all(receipt["checks"].values())
    assert receipt_path.stat().st_mode & 0o077 == 0
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["receipt_digest"] == receipt["receipt_digest"]


def test_capability_probe_blocks_unvalidated_generation(tmp_path):
    runtime, selector, generation = _fixture(tmp_path)
    manifest = json.loads((generation / "generation.json").read_text(encoding="utf-8"))
    manifest["status"] = "candidate"
    (generation / "generation.json").write_text(json.dumps(manifest), encoding="utf-8")
    receipt = probe(runtime_root=runtime, selector_path=selector, source_root=ROOT, runtime_env=_env(tmp_path))
    assert receipt["status"] == "blocked_external"
    assert "runtime_generation_not_validated" in receipt["blockers"]


def test_capability_probe_can_qualify_unactivated_candidate_without_selector_mutation(tmp_path):
    runtime, selector, current_generation = _fixture(tmp_path)
    candidate_id = "rtg-candidate123456"
    candidate = runtime / "generations" / candidate_id
    candidate_wrapper = candidate / "tree" / "scripts" / "qdex"
    candidate_wrapper.parent.mkdir(parents=True)
    shutil.copy2(ROOT / "scripts" / "qdex", candidate_wrapper)
    candidate_wrapper.chmod(0o700)
    candidate_manifest = json.loads((current_generation / "generation.json").read_text(encoding="utf-8"))
    candidate_manifest["generation_id"] = candidate_id
    candidate.mkdir(parents=True, exist_ok=True)
    (candidate / "generation.json").write_text(json.dumps(candidate_manifest), encoding="utf-8")
    current_before = (runtime / "current.json").read_text(encoding="utf-8")

    receipt = probe(
        runtime_root=runtime,
        selector_path=selector,
        source_root=ROOT,
        runtime_env=_env(tmp_path),
        generation_id=candidate_id,
    )

    assert receipt["status"] == "pass", receipt["blockers"]
    assert receipt["generation_id"] == candidate_id
    assert (runtime / "current.json").read_text(encoding="utf-8") == current_before
