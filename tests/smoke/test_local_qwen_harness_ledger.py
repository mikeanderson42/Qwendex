import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/local_qwen_harness_ledger.py"


def load_module():
    spec = importlib.util.spec_from_file_location("local_qwen_harness_ledger_test", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ledger_indexes_receipt_metadata_without_transcript_text(tmp_path):
    ledger = load_module()
    db = tmp_path / "ledger.sqlite"
    receipt = tmp_path / "local_model_verification_sample.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "local_qwen_reliability.v1",
                "run_id": "run-123",
                "task_name": "duplicate-read-finalization",
                "model_alias": "qwen-local",
                "backend_profile": "qwopus-test",
                "provider": "bridge",
                "status": "failed",
                "success": False,
                "score": 0.25,
                "transcript": "SHOULD_NOT_STORE_FULL_TRANSCRIPT",
                "failure": "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    indexed = ledger.index_paths(db, tmp_path, [receipt], source="test", note="unit-test")
    summary = ledger.ledger_summary(db)
    query = ledger.query_artifacts(
        db,
        marker="LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
        limit=5,
    )

    assert indexed["indexed_batch"]["indexed_artifacts"] == 1
    assert summary["status"] == "ready"
    assert summary["counts"]["artifact_observations"] == 1
    assert summary["failure_markers"] == {"LOCAL_MODEL_TOOL_CALL_TOO_LARGE": 1}
    assert query["rows"][0]["model_alias"] == "qwen-local"
    assert query["rows"][0]["failure_markers"] == {"LOCAL_MODEL_TOOL_CALL_TOO_LARGE": 1}
    assert "SHOULD_NOT_STORE_FULL_TRANSCRIPT" not in json.dumps(query, sort_keys=True)

    conn = sqlite3.connect(db)
    column_rows = conn.execute("PRAGMA table_info(run_observations)").fetchall()
    column_names = {row[1] for row in column_rows}
    assert "transcript" not in column_names
    assert "content" not in column_names


def test_ledger_records_normalized_events_and_explains_run(tmp_path):
    ledger = load_module()
    db = tmp_path / "ledger.sqlite"

    first = ledger.record_event(
        db,
        repo_root=ROOT,
        event_type="bridge_start",
        run_id="run-123",
        status="pass",
        metadata={"bridge_version": "test", "secret": "password=supersecretvalue123"},
    )
    second = ledger.record_event(
        db,
        repo_root=ROOT,
        event_type="eval_run",
        run_id="run-123",
        status="pass",
        metadata={"cases": 2},
    )
    explanation = ledger.explain_run(db, run_id="run-123", limit=10)

    assert first["status"] == "pass"
    assert second["status"] == "pass"
    assert explanation["status"] == "ready"
    assert explanation["run_id"] == "run-123"
    assert [event["event_type"] for event in explanation["events"]] == ["bridge_start", "eval_run"]
    assert "secret" not in json.dumps(explanation)


def test_ledger_indexes_skillopt_proposal_link(tmp_path):
    ledger = load_module()
    db = tmp_path / "ledger.sqlite"
    staging = tmp_path / ".skillopt-sleep/staging/20260630"
    staging.mkdir(parents=True)
    report = staging / "report.md"
    report.write_text(
        "held-out 0.100 -> 0.200 => accept (accepted=True)\n",
        encoding="utf-8",
    )

    indexed = ledger.index_paths(db, tmp_path, [staging], source="test")
    summary = ledger.ledger_summary(db)

    assert indexed["indexed_batch"]["indexed_artifacts"] == 1
    assert summary["counts"]["skillopt_proposal_links"] == 1
    proposal = sqlite3.connect(db).execute(
        "SELECT gate_status, accepted, baseline_score, candidate_score FROM skillopt_proposal_links"
    ).fetchone()
    assert proposal == ("accept", "true", 0.1, 0.2)
