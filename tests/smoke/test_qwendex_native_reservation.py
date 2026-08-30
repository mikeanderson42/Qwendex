import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "qwendex_native_reservation_test",
    ROOT / "scripts" / "qwendex_native_reservation.py",
)
assert _SPEC and _SPEC.loader
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)

PROJECTION_SCHEMA = _MODULE.PROJECTION_SCHEMA
_policy_depth = _MODULE._policy_depth
seal_native_reservation_projection = _MODULE.seal_native_reservation_projection
verify_native_reservation_projection = _MODULE.verify_native_reservation_projection


def _fixture():
    policy = {
        "policy_hash": "a" * 64,
        "max_workers": 4,
        "max_depth": 2,
        "native_max_concurrent_threads": 5,
    }
    decision = {
        "launch_ledger_id": "ledger-1",
        "session_id": "root-session",
        "turn_id": "root-turn",
        "root_session_id": "root-session",
        "runtime_generation": "generation-1",
        "policy_hash": "b" * 64,
    }
    sessions = [
        {
            "agent_id": "native-worker-1",
            "task_id": "task-1",
            "lane": "review",
            "status": "active",
            "origin": "qwendex",
            "created_at": "2026-08-16T00:00:00Z",
            "session_id": "root-session",
            "turn_id": "root-turn",
            "policy_hash": "b" * 64,
            "context_packet": {
                "native_session_id": "child-session",
                "native_turn_id": "child-turn",
                "native_task_name": "/manager/native-worker-1",
                "parent_session_id": "root-session",
                "native_agent_type": "explorer",
                "runtime_generation": "generation-1",
                "runtime_state": "active",
            },
        }
    ]
    return decision, sessions, policy


def test_native_projection_is_path_free_and_explicitly_shadow_only():
    decision, sessions, policy = _fixture()
    projection = seal_native_reservation_projection(
        decision=decision,
        sessions=sessions,
        policy=policy,
        global_active_count=1,
        global_capacity=4,
        owner_route_binding_digest="d" * 64,
    )

    assert projection["schema_version"] == PROJECTION_SCHEMA
    assert projection["status"] == "shadow_only"
    assert projection["trusted"] is False
    assert projection["acknowledgment"]["present"] is False
    assert projection["reservations"][0]["worker_slot"] == 1
    assert projection["reservations"][0]["depth"] is None
    assert projection["reservations"][0]["parent_depth"] is None
    assert projection["reservations"][0]["native_task_name_digest"]
    assert projection["manager"]["owner_route_binding_digest"] == "d" * 64
    assert projection["reservations"][0]["owner_route_binding_digest"] == "d" * 64
    assert "/manager/native-worker-1" not in json.dumps(projection, sort_keys=True)
    assert "/" not in json.dumps(projection, sort_keys=True)

    verification = verify_native_reservation_projection(
        projection,
        expected={
            "session_id": "root-session",
            "turn_id": "root-turn",
            "qwendex_policy_digest": "a" * 64,
            "owner_route_binding_digest": "d" * 64,
        },
    )
    assert verification["ok"] is True
    assert verification["trusted"] is False


def test_native_projection_tamper_is_detected():
    decision, sessions, policy = _fixture()
    projection = seal_native_reservation_projection(
        decision=decision,
        sessions=sessions,
        policy=policy,
        global_active_count=1,
        global_capacity=4,
    )
    projection["capacity"]["global_active_count"] = 2

    verification = verify_native_reservation_projection(projection)

    assert verification["ok"] is False
    assert "projection_digest_invalid" in verification["blockers"]


def test_native_projection_marks_capacity_identity_conflicts_as_integrity_failure():
    decision, sessions, policy = _fixture()
    duplicate = dict(sessions[0])
    duplicate["created_at"] = "2026-08-16T00:00:01Z"
    projection = seal_native_reservation_projection(
        decision=decision,
        sessions=[sessions[0], duplicate],
        policy=policy,
        global_active_count=5,
        global_capacity=4,
    )

    assert projection["status"] == "failed_integrity"
    assert "duplicate_reservation_identity" in projection["blockers"]
    assert "global_native_capacity_overrun" in projection["blockers"]


def test_native_projection_without_launch_identity_is_blocked_external():
    decision, sessions, policy = _fixture()
    decision = {"policy_hash": "b" * 64}
    projection = seal_native_reservation_projection(
        decision=decision,
        sessions=sessions,
        policy=policy,
        global_active_count=1,
        global_capacity=4,
    )

    assert projection["status"] == "blocked_external"
    assert "launch_ledger_identity_missing" in projection["blockers"]
    assert "manager_session_identity_missing" in projection["blockers"]


def test_explicit_zero_max_depth_disables_nested_fallback():
    assert _policy_depth({"max_depth": 0, "nested_spawn": {"max_depth": 2}}) == 0


def test_invalid_owner_route_binding_fails_closed():
    decision, sessions, policy = _fixture()
    projection = seal_native_reservation_projection(
        decision=decision,
        sessions=sessions,
        policy=policy,
        global_active_count=1,
        global_capacity=4,
        owner_route_binding_digest="not-a-digest",
    )
    assert projection["status"] == "failed_integrity"
    assert "owner_route_binding_digest_invalid" in projection["blockers"]
