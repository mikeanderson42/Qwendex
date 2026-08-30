#!/usr/bin/env python3
"""Path-free, advisory native-reservation projections for Qwendex.

The Manager ledger already records the identities needed by an owner
supervisor to reason about native workers.  This module projects that state
without exposing repository paths and without treating local bookkeeping as a
provider attestation.  It is deliberately pure: callers inject the observed
decision, sessions, policy, and capacity values.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any


PROJECTION_SCHEMA = "qwendex.native_reservation_projection.v1"
VERIFICATION_SCHEMA = "qwendex.native_reservation_projection_verification.v1"
TERMINAL_STATUSES = frozenset(
    {"completed", "blocked", "failed", "closed", "tombstoned", "waived"}
)
STATUS_VALUES = frozenset({"shadow_only", "blocked_external", "failed_integrity"})
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
HEX_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def digest_json(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _text(value: object, *, limit: int = 160) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) > limit or "\x00" in text:
        return ""
    return text


def _path_free_identifier(value: object) -> str:
    """Keep IDs readable while preventing a path from entering the receipt."""
    text = _text(value)
    if not text:
        return ""
    if IDENTIFIER_RE.fullmatch(text):
        return text
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"


def _digest(value: object) -> str:
    text = _text(value, limit=128).lower()
    if text.startswith("sha256:"):
        text = text[7:]
    return text if HEX_DIGEST_RE.fullmatch(text) else ""


def _positive_int(value: object, *, default: int = 0, maximum: int = 64) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0 <= parsed <= maximum else default


def _policy_depth(policy: Mapping[str, Any]) -> int:
    # An explicit zero is a real policy: it disables nested spawning. Do not
    # fall through to a stale nested_spawn value just because zero is falsey.
    if "max_depth" in policy:
        return _positive_int(policy.get("max_depth"), maximum=2)
    nested = policy.get("nested_spawn")
    if isinstance(nested, Mapping):
        return _positive_int(nested.get("max_depth"), maximum=2)
    return 0


def _reservation_status(session: Mapping[str, Any]) -> str:
    status = _text(session.get("status"), limit=32).lower()
    return status if status in {"reserved", "active"} else ""


def _reservation_projection(
    session: Mapping[str, Any],
    *,
    slot: int,
    worker_cap: int,
    native_thread_cap: int,
    max_depth: int,
    default_root_session_id: str,
    default_turn_id: str,
    runtime_generation: str,
    owner_route_binding_digest: str,
    owner_manager_intent_digest: str,
    owner_reservation_id: str,
) -> dict[str, Any]:
    packet = session.get("context_packet")
    packet = packet if isinstance(packet, Mapping) else {}
    reservation_id = _path_free_identifier(session.get("agent_id"))
    work_id = _path_free_identifier(session.get("task_id"))
    native_session_id = _path_free_identifier(
        packet.get("native_session_id") or session.get("session_id")
    )
    native_turn_id = _path_free_identifier(
        packet.get("native_turn_id") or session.get("turn_id") or default_turn_id
    )
    root_session_id = _path_free_identifier(
        packet.get("root_session_id") or default_root_session_id
    )
    native_task_name = _text(packet.get("native_task_name"), limit=512)
    raw_depth = packet.get("depth")
    observed_depth: int | None
    if raw_depth is None or isinstance(raw_depth, bool):
        observed_depth = None
    else:
        try:
            parsed_depth = int(raw_depth)
        except (TypeError, ValueError):
            parsed_depth = -1
        observed_depth = parsed_depth if 0 <= parsed_depth <= 2 else None
    parent_depth = observed_depth - 1 if observed_depth is not None and observed_depth > 0 else (
        -1 if observed_depth == 0 else None
    )
    native_generation = _path_free_identifier(
        packet.get("runtime_generation") or runtime_generation
    )
    return {
        "reservation_id": reservation_id,
        "work_id": work_id,
        "qwendex_attempt_id": reservation_id,
        "identity_scope": "qwendex_native_reservation",
        "lane": _path_free_identifier(session.get("lane")),
        "status": _reservation_status(session),
        "origin": _text(session.get("origin"), limit=32),
        "worker_slot": slot,
        "worker_slot_source": "turn_local_observation",
        "worker_cap": worker_cap,
        "native_thread_cap": native_thread_cap,
        "depth": observed_depth,
        "parent_depth": parent_depth,
        "max_depth": max_depth,
        "root_session_id": root_session_id,
        "parent_session_id": _path_free_identifier(packet.get("parent_session_id")),
        "qwendex_session_id": native_session_id,
        "qwendex_turn_id": native_turn_id,
        "qwendex_runtime_generation": native_generation,
        "native_agent_type": _path_free_identifier(packet.get("native_agent_type")),
        "native_task_name_digest": (
            hashlib.sha256(native_task_name.encode("utf-8")).hexdigest()
            if native_task_name
            else ""
        ),
        "planned_agent_id": _path_free_identifier(packet.get("planned_agent_id")),
        "policy_digest": _digest(session.get("policy_hash")),
        # Opaque owner binding only.  The owner supplies the route decision;
        # Qwendex carries its digest but never interprets or overrides the
        # selected model/profile.
        "owner_route_binding_digest": owner_route_binding_digest,
        "manager_intent_digest": owner_manager_intent_digest,
        "owner_reservation_id": _path_free_identifier(owner_reservation_id),
    }


def seal_native_reservation_projection(
    *,
    decision: Mapping[str, Any],
    sessions: Iterable[Mapping[str, Any]],
    policy: Mapping[str, Any],
    global_active_count: int,
    global_capacity: int,
    owner_route_binding_digest: object = "",
    owner_manager_intent_digest: object = "",
    owner_reservation_id: object = "",
) -> dict[str, Any]:
    """Build a deterministic, path-free observation of native reservations.

    The observed slot is local to the current Manager decision. It is not a
    claim about the complete global pool, and the projection never carries an
    external owner runner attempt identity.
    """
    selected_policy = policy if isinstance(policy, Mapping) else {}
    launch_ledger_id = _path_free_identifier(
        decision.get("launch_ledger_id") or decision.get("ledger_id")
    )
    manager_session_id = _path_free_identifier(decision.get("session_id"))
    manager_turn_id = _path_free_identifier(decision.get("turn_id"))
    root_session_id = _path_free_identifier(
        decision.get("root_session_id") or decision.get("session_id")
    )
    runtime_generation = _path_free_identifier(decision.get("runtime_generation"))
    runtime_contract_digest = _digest(decision.get("runtime_contract_sha256"))
    manager_policy_digest = _digest(decision.get("policy_hash"))
    qwendex_policy_digest = _digest(selected_policy.get("policy_hash"))
    raw_owner_route_binding_digest = _text(owner_route_binding_digest, limit=128).lower()
    owner_route_binding_digest = _digest(raw_owner_route_binding_digest)
    raw_owner_manager_intent_digest = _text(owner_manager_intent_digest, limit=128).lower()
    owner_manager_intent_digest = _digest(raw_owner_manager_intent_digest)
    owner_reservation_id = _path_free_identifier(owner_reservation_id)
    worker_cap = _positive_int(
        selected_policy.get("max_workers") or selected_policy.get("max_threads"),
        maximum=4,
    )
    native_thread_cap = _positive_int(
        selected_policy.get("native_max_concurrent_threads") or worker_cap + 1,
        maximum=5,
    )
    max_depth = _policy_depth(selected_policy)
    capacity = _positive_int(global_capacity, maximum=4)
    active_count = _positive_int(global_active_count, maximum=64)
    if not worker_cap:
        worker_cap = capacity
    if not native_thread_cap and worker_cap:
        native_thread_cap = min(5, worker_cap + 1)

    native_sessions = [
        dict(session)
        for session in sessions
        if isinstance(session, Mapping)
        and _text(session.get("origin"), limit=32) == "qwendex"
        and _reservation_status(session)
    ]
    native_sessions.sort(
        key=lambda item: (
            _text(item.get("created_at"), limit=64),
            _text(item.get("agent_id")),
        )
    )
    reservations = [
        _reservation_projection(
            session,
            slot=index,
            worker_cap=worker_cap,
            native_thread_cap=native_thread_cap,
            max_depth=max_depth,
            default_root_session_id=root_session_id,
            default_turn_id=manager_turn_id,
            runtime_generation=runtime_generation,
            owner_route_binding_digest=owner_route_binding_digest,
            owner_manager_intent_digest=owner_manager_intent_digest,
            owner_reservation_id=owner_reservation_id,
        )
        for index, session in enumerate(native_sessions, start=1)
    ]

    blockers: list[str] = [
        "provider_ack_missing",
        "trusted_runtime_attestation_missing",
    ]
    integrity_blockers: list[str] = []
    if len({item["reservation_id"] for item in reservations}) != len(reservations):
        integrity_blockers.append("duplicate_reservation_identity")
    if capacity and active_count > capacity:
        integrity_blockers.append("global_native_capacity_overrun")
    if worker_cap and native_thread_cap and worker_cap > native_thread_cap:
        integrity_blockers.append("native_thread_cap_below_worker_cap")
    if not launch_ledger_id:
        blockers.append("launch_ledger_identity_missing")
    if not manager_session_id:
        blockers.append("manager_session_identity_missing")
    if not manager_policy_digest or not qwendex_policy_digest:
        blockers.append("policy_digest_missing")
    if not runtime_generation:
        blockers.append("runtime_generation_missing")
    if raw_owner_route_binding_digest and not owner_route_binding_digest:
        integrity_blockers.append("owner_route_binding_digest_invalid")
    if raw_owner_manager_intent_digest and not owner_manager_intent_digest:
        integrity_blockers.append("owner_manager_intent_digest_invalid")
    if not reservations:
        blockers.append("native_reservation_not_observed")
    if integrity_blockers:
        blockers.extend(integrity_blockers)

    status = "failed_integrity" if integrity_blockers else (
        "blocked_external"
        if any(item in blockers for item in (
            "launch_ledger_identity_missing",
            "manager_session_identity_missing",
            "policy_digest_missing",
            "runtime_generation_missing",
            "native_reservation_not_observed",
        ))
        else "shadow_only"
    )
    projection: dict[str, Any] = {
        "schema_version": PROJECTION_SCHEMA,
        "status": status,
        "trusted": False,
        "authority": "advisory_observation",
        "source": "qwendex_manager_ledger",
        "blockers": sorted(set(blockers)),
        "manager": {
            "launch_ledger_id": launch_ledger_id,
            "ledger_digest": hashlib.sha256(launch_ledger_id.encode("utf-8")).hexdigest()
            if launch_ledger_id
            else "",
            "session_id": manager_session_id,
            "turn_id": manager_turn_id,
            "root_session_id": root_session_id,
            "manager_policy_digest": manager_policy_digest,
            "qwendex_policy_digest": qwendex_policy_digest,
            "runtime_generation": runtime_generation,
            "runtime_generation_digest": hashlib.sha256(runtime_generation.encode("utf-8")).hexdigest()
            if runtime_generation
            else "",
            "runtime_generation_digest_kind": "generation_identifier",
            "runtime_contract_digest": runtime_contract_digest,
            "owner_route_binding_digest": owner_route_binding_digest,
            "manager_intent_digest": owner_manager_intent_digest,
            "owner_reservation_id": owner_reservation_id,
        },
        "capacity": {
            "global_active_count": active_count,
            "global_capacity": capacity,
            "remaining": max(0, capacity - active_count) if capacity else None,
            "worker_cap": worker_cap,
            "native_thread_cap": native_thread_cap,
            "max_depth": max_depth,
            "scope": "global_native_pool",
        },
        "reservations": reservations,
        "acknowledgment": {
            "schema": "qwendex.native_reservation_ack.not_present",
            "present": False,
            "signature_verified": False,
            "provider_attested": False,
        },
    }
    projection["projection_digest"] = digest_json({**projection, "projection_digest": ""})
    return projection


def verify_native_reservation_projection(
    projection: object,
    *,
    expected: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify structure and binding without promoting local observation."""
    blockers: list[str] = []
    if not isinstance(projection, Mapping):
        return {
            "schema_version": VERIFICATION_SCHEMA,
            "ok": False,
            "status": "failed_integrity",
            "trusted": False,
            "blockers": ["projection_invalid"],
        }
    if projection.get("schema_version") != PROJECTION_SCHEMA:
        blockers.append("schema_invalid")
    claimed = _text(projection.get("projection_digest"), limit=128).lower()
    clean = dict(projection)
    clean["projection_digest"] = ""
    if not HEX_DIGEST_RE.fullmatch(claimed) or claimed != digest_json(clean):
        blockers.append("projection_digest_invalid")
    if projection.get("trusted") is not False:
        blockers.append("projection_trust_claim_invalid")
    status = _text(projection.get("status"), limit=32)
    if status not in STATUS_VALUES:
        blockers.append("projection_status_invalid")
    if not isinstance(projection.get("reservations"), list):
        blockers.append("reservations_invalid")
    expected = expected if isinstance(expected, Mapping) else {}
    manager = projection.get("manager")
    manager = manager if isinstance(manager, Mapping) else {}
    for key in ("session_id", "turn_id", "qwendex_policy_digest"):
        expected_value = _text(expected.get(key), limit=160)
        if expected_value and str(manager.get(key) or "") != expected_value:
            blockers.append(f"binding_mismatch_{key}")
    expected_owner_route_binding_digest = _digest(expected.get("owner_route_binding_digest"))
    if expected.get("owner_route_binding_digest") not in (None, "") and not expected_owner_route_binding_digest:
        blockers.append("binding_expected_owner_route_binding_digest_invalid")
    elif expected_owner_route_binding_digest and not hmac.compare_digest(
        str(manager.get("owner_route_binding_digest") or ""),
        expected_owner_route_binding_digest,
    ):
        blockers.append("binding_mismatch_owner_route_binding_digest")
    expected_manager_intent_digest = _digest(expected.get("manager_intent_digest"))
    if expected.get("manager_intent_digest") not in (None, "") and not expected_manager_intent_digest:
        blockers.append("binding_expected_manager_intent_digest_invalid")
    elif expected_manager_intent_digest and not hmac.compare_digest(
        str(manager.get("manager_intent_digest") or ""),
        expected_manager_intent_digest,
    ):
        blockers.append("binding_mismatch_manager_intent_digest")
    expected_owner_reservation_id = _path_free_identifier(expected.get("owner_reservation_id"))
    if expected.get("owner_reservation_id") not in (None, "") and not expected_owner_reservation_id:
        blockers.append("binding_expected_owner_reservation_id_invalid")
    elif expected_owner_reservation_id and not hmac.compare_digest(
        str(manager.get("owner_reservation_id") or ""),
        expected_owner_reservation_id,
    ):
        blockers.append("binding_mismatch_owner_reservation_id")
    return {
        "schema_version": VERIFICATION_SCHEMA,
        "ok": not blockers,
        "status": status if status in STATUS_VALUES else "failed_integrity",
        "trusted": False,
        "blockers": sorted(set(blockers)),
        "projection_digest": claimed,
    }
