#!/usr/bin/env python3
"""Privacy-minimized local persistence for Qwendex exploration telemetry.

This module deliberately accepts only normalized metadata from the public CLI.
It never stores query text, commands, paths, prompts, or tool output.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping


EVENT_SCHEMA_VERSION = "qwendex.performance_event.v1"
SUMMARY_SCHEMA_VERSION = "qwendex.performance_summary.v1"
BENCHMARK_SCHEMA_VERSION = "qwendex.performance_benchmark.v1"
DATABASE_SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 250

_REPOSITORY_SCOPE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

_ROLE_VALUES = {"root", "worker", "unknown"}
_PHASE_VALUES = {"session", "tool", "compaction", "subagent", "stop", "startup"}
_EVENT_KIND_VALUES = {
    "prompt_submit",
    "tool_call",
    "subagent_start",
    "subagent_stop",
    "context_pressure",
    "run_stop",
    "startup_observation",
}
_TOOL_FAMILY_VALUES = {
    "search",
    "read",
    "edit",
    "validation",
    "startup",
    "context",
    "collaboration",
    "other",
    "unknown",
}
_QUERY_CLASS_VALUES = {
    "path_lookup",
    "literal",
    "regex",
    "symbol_reference",
    "structural",
    "semantic",
    "read",
    "validation",
    "not_applicable",
    "unknown",
}
_SCOPE_CLASS_VALUES = {"repository_root", "known_subtree", "outside_repo", "unspecified"}
_TERMINAL_VALUES = {
    "observed",
    "pending",
    "completed",
    "failed",
    "aborted_or_incomplete",
    "completed_without_start",
}
_INPUT_BUCKET_VALUES = {"none", "1-32", "33-128", "129-512", "513+"}
_WAIT_TIMEOUT_BUCKET_VALUES = {
    "not_applicable",
    "not_provided",
    "at_most_30s",
    "31_to_60s",
    "61_to_120s",
    "over_120s",
    "invalid",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _elapsed_ms(started_at: str, completed_at: str) -> float | None:
    started = _parse_utc(started_at)
    completed = _parse_utc(completed_at)
    if started is None or completed is None:
        return None
    return max(0.0, (completed - started).total_seconds() * 1000)


def _number(value: Any, *, minimum: float = 0.0) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, int | float):
        return None
    parsed = float(value)
    if parsed < minimum or parsed > 1_000_000_000_000:
        return None
    return parsed


def _integer(value: Any, *, minimum: int = 0) -> int | None:
    parsed = _number(value, minimum=minimum)
    if parsed is None or int(parsed) != parsed:
        return None
    return int(parsed)


def _bool_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, bool) else None


def _known(value: Any, allowed: set[str], fallback: str) -> str:
    text = str(value or "").strip()
    return text if text in allowed else fallback


def _connect(path: Path, *, write: bool) -> sqlite3.Connection:
    target = path.expanduser()
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000)
    else:
        uri = target.resolve(strict=False).as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    if write:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if version > DATABASE_SCHEMA_VERSION:
        raise ValueError("performance database schema is newer than this Qwendex runtime")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS qwendex_performance_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_performance_runs (
          run_id TEXT PRIMARY KEY,
          repository_scope_digest TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL DEFAULT '',
          terminal_classification TEXT NOT NULL DEFAULT 'active',
          first_edit_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS qwendex_performance_events (
          event_id TEXT PRIMARY KEY,
          schema_version TEXT NOT NULL,
          run_id TEXT NOT NULL,
          repository_scope_digest TEXT NOT NULL,
          manager_launch_digest TEXT NOT NULL,
          turn_digest TEXT NOT NULL,
          agent_role TEXT NOT NULL,
          phase TEXT NOT NULL,
          event_kind TEXT NOT NULL,
          tool_family TEXT NOT NULL,
          query_class TEXT NOT NULL,
          scope_class TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT NOT NULL DEFAULT '',
          duration_ms REAL,
          input_size_bucket TEXT NOT NULL,
          output_bytes INTEGER,
          result_count INTEGER,
          success INTEGER,
          truncated INTEGER,
          duplicate_within_run INTEGER,
          terminal_classification TEXT NOT NULL,
          query_fingerprint TEXT NOT NULL DEFAULT '',
          event_key_digest TEXT NOT NULL DEFAULT '',
          wait_timeout_bucket TEXT NOT NULL DEFAULT 'not_applicable',
          instrumentation_duration_ms REAL NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS qwendex_performance_events_run_started
          ON qwendex_performance_events(run_id, started_at);
        CREATE INDEX IF NOT EXISTS qwendex_performance_events_repo_started
          ON qwendex_performance_events(repository_scope_digest, started_at);
        CREATE INDEX IF NOT EXISTS qwendex_performance_events_pending
          ON qwendex_performance_events(run_id, event_key_digest, terminal_classification);
        CREATE INDEX IF NOT EXISTS qwendex_performance_events_query
          ON qwendex_performance_events(run_id, query_fingerprint);
        """
    )
    columns = {
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(qwendex_performance_events)").fetchall()
    }
    if "wait_timeout_bucket" not in columns:
        conn.execute(
            "ALTER TABLE qwendex_performance_events "
            "ADD COLUMN wait_timeout_bucket TEXT NOT NULL DEFAULT 'not_applicable'"
        )
    if version < DATABASE_SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
    conn.commit()


def _salt(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT value FROM qwendex_performance_meta WHERE key = 'query_fingerprint_salt'"
    ).fetchone()
    if row is not None and str(row["value"]):
        return str(row["value"])
    value = secrets.token_hex(32)
    conn.execute(
        "INSERT OR REPLACE INTO qwendex_performance_meta(key, value) VALUES ('query_fingerprint_salt', ?)",
        (value,),
    )
    return value


def _fingerprint(salt: str, material: Any, *, prefix: str) -> str:
    text = str(material or "")
    digest = hmac.new(salt.encode("ascii"), text.encode("utf-8", "replace"), hashlib.sha256).hexdigest()
    return f"{prefix}:{digest}"


def _run_id(salt: str, material: Any) -> str:
    return "run_" + _fingerprint(salt, material or secrets.token_hex(16), prefix="hmac-sha256").split(":", 1)[1]


def _event_id() -> str:
    return "pevt_" + secrets.token_hex(16)


def _run_upsert(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    repository_scope_digest: str,
    started_at: str,
    updated_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO qwendex_performance_runs(
          run_id, repository_scope_digest, started_at, completed_at,
          terminal_classification, first_edit_at, updated_at
        ) VALUES (?, ?, ?, '', 'active', '', ?)
        ON CONFLICT(run_id) DO UPDATE SET
          repository_scope_digest = excluded.repository_scope_digest,
          updated_at = excluded.updated_at
        """,
        (run_id, repository_scope_digest, started_at, updated_at),
    )


def _event_values(conn: sqlite3.Connection, record: Mapping[str, Any]) -> dict[str, Any]:
    salt = _salt(conn)
    now = str(record.get("started_at") or utc_now())
    query_enabled = bool(record.get("query_fingerprints", True))
    query_material = record.get("query_material")
    query_fingerprint = (
        _fingerprint(salt, query_material, prefix="hmac-sha256")
        if query_enabled and str(query_material or "")
        else ""
    )
    return {
        "event_id": _event_id(),
        "schema_version": EVENT_SCHEMA_VERSION,
        "run_id": _run_id(salt, record.get("run_material")),
        "repository_scope_digest": str(record.get("repository_scope_digest") or ""),
        "manager_launch_digest": _fingerprint(salt, record.get("manager_launch_material"), prefix="hmac-sha256"),
        "turn_digest": _fingerprint(salt, record.get("turn_material"), prefix="hmac-sha256"),
        "agent_role": _known(record.get("agent_role"), _ROLE_VALUES, "unknown"),
        "phase": _known(record.get("phase"), _PHASE_VALUES, "tool"),
        "event_kind": _known(record.get("event_kind"), _EVENT_KIND_VALUES, "tool_call"),
        "tool_family": _known(record.get("tool_family"), _TOOL_FAMILY_VALUES, "unknown"),
        "query_class": _known(record.get("query_class"), _QUERY_CLASS_VALUES, "unknown"),
        "scope_class": _known(record.get("scope_class"), _SCOPE_CLASS_VALUES, "unspecified"),
        "started_at": now,
        "completed_at": str(record.get("completed_at") or ""),
        "duration_ms": _number(record.get("duration_ms")),
        "input_size_bucket": _known(record.get("input_size_bucket"), _INPUT_BUCKET_VALUES, "none"),
        "output_bytes": _integer(record.get("output_bytes")),
        "result_count": _integer(record.get("result_count")),
        "success": _bool_or_none(record.get("success")),
        "truncated": _bool_or_none(record.get("truncated")),
        "duplicate_within_run": None,
        "terminal_classification": _known(record.get("terminal_classification"), _TERMINAL_VALUES, "observed"),
        "query_fingerprint": query_fingerprint,
        "event_key_digest": _fingerprint(salt, record.get("event_key_material"), prefix="hmac-sha256"),
        "wait_timeout_bucket": _known(
            record.get("wait_timeout_bucket"),
            _WAIT_TIMEOUT_BUCKET_VALUES,
            "not_applicable",
        ),
        "instrumentation_duration_ms": 0.0,
    }


def _insert_event(conn: sqlite3.Connection, values: Mapping[str, Any]) -> None:
    columns = list(values)
    conn.execute(
        f"INSERT INTO qwendex_performance_events ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
        tuple(values[column] for column in columns),
    )


def _duplicate_within_run(conn: sqlite3.Connection, values: Mapping[str, Any]) -> int | None:
    fingerprint = str(values.get("query_fingerprint") or "")
    if not fingerprint:
        return None
    row = conn.execute(
        """
        SELECT 1 FROM qwendex_performance_events
        WHERE run_id = ? AND query_fingerprint = ? AND event_kind = 'tool_call'
        LIMIT 1
        """,
        (values["run_id"], fingerprint),
    ).fetchone()
    return 1 if row is not None else 0


def _refresh_run_counts(conn: sqlite3.Connection, run_id: str) -> None:
    row = conn.execute(
        """
        SELECT COUNT(*) AS count,
               SUM(CASE WHEN terminal_classification = 'pending' THEN 1 ELSE 0 END) AS incomplete
        FROM qwendex_performance_events
        WHERE run_id = ?
        """,
        (run_id,),
    ).fetchone()
    _ = row


def record_event(path: Path, record: Mapping[str, Any]) -> dict[str, Any]:
    """Persist a normalized event and return safe capture metadata only."""
    started_clock = time.perf_counter()
    action = str(record.get("action") or "lifecycle")
    repository_scope_digest = str(record.get("repository_scope_digest") or "")
    if not _REPOSITORY_SCOPE_DIGEST.fullmatch(repository_scope_digest):
        return {"captured": False, "reason": "missing_or_invalid_repository_scope"}
    try:
        with _connect(path, write=True) as conn:
            values = _event_values(conn, record)
            if not values["repository_scope_digest"]:
                return {"captured": False, "reason": "missing_repository_scope"}
            _run_upsert(
                conn,
                run_id=values["run_id"],
                repository_scope_digest=values["repository_scope_digest"],
                started_at=values["started_at"],
                updated_at=utc_now(),
            )
            matched = False
            event_id = values["event_id"]
            if action == "tool_start":
                values["event_kind"] = "tool_call"
                values["terminal_classification"] = "pending"
                values["duplicate_within_run"] = _duplicate_within_run(conn, values)
                _insert_event(conn, values)
                if values["tool_family"] == "edit":
                    conn.execute(
                        """
                        UPDATE qwendex_performance_runs
                        SET first_edit_at = CASE WHEN first_edit_at = '' THEN ? ELSE first_edit_at END,
                            updated_at = ?
                        WHERE run_id = ?
                        """,
                        (values["started_at"], utc_now(), values["run_id"]),
                    )
            elif action == "tool_finish":
                pending = conn.execute(
                    """
                    SELECT event_id, started_at FROM qwendex_performance_events
                    WHERE run_id = ? AND event_key_digest = ?
                      AND event_kind = 'tool_call' AND terminal_classification = 'pending'
                    ORDER BY started_at DESC LIMIT 1
                    """,
                    (values["run_id"], values["event_key_digest"]),
                ).fetchone()
                completed_at = values["completed_at"] or utc_now()
                terminal = "completed" if values["success"] != 0 else "failed"
                if pending is not None:
                    matched = True
                    event_id = str(pending["event_id"])
                    duration = values["duration_ms"]
                    if duration is None:
                        duration = _elapsed_ms(str(pending["started_at"]), completed_at)
                    conn.execute(
                        """
                        UPDATE qwendex_performance_events
                        SET completed_at = ?, duration_ms = ?, output_bytes = ?, result_count = ?,
                            success = ?, truncated = ?, terminal_classification = ?
                        WHERE event_id = ?
                        """,
                        (
                            completed_at,
                            duration,
                            values["output_bytes"],
                            values["result_count"],
                            values["success"],
                            values["truncated"],
                            terminal,
                            event_id,
                        ),
                    )
                else:
                    values["event_kind"] = "tool_call"
                    values["completed_at"] = completed_at
                    values["terminal_classification"] = "completed_without_start"
                    _insert_event(conn, values)
            else:
                _insert_event(conn, values)
                if action == "stop":
                    conn.execute(
                        """
                        UPDATE qwendex_performance_events
                        SET completed_at = ?, terminal_classification = 'aborted_or_incomplete'
                        WHERE run_id = ? AND event_kind = 'tool_call'
                          AND terminal_classification = 'pending'
                        """,
                        (utc_now(), values["run_id"]),
                    )
                    conn.execute(
                        """
                        UPDATE qwendex_performance_runs
                        SET completed_at = ?, terminal_classification = 'stopped', updated_at = ?
                        WHERE run_id = ?
                        """,
                        (utc_now(), utc_now(), values["run_id"]),
                    )
            overhead = round((time.perf_counter() - started_clock) * 1000, 3)
            conn.execute(
                "UPDATE qwendex_performance_events SET instrumentation_duration_ms = ? WHERE event_id = ?",
                (overhead, event_id),
            )
            conn.commit()
        return {"captured": True, "matched_pre_event": matched, "instrumentation_duration_ms": overhead}
    except (OSError, sqlite3.Error, ValueError):
        return {"captured": False, "reason": "storage_unavailable"}


def _where_clause(*, repository_scope_digest: str = "", since_days: int = 0) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if repository_scope_digest:
        clauses.append("repository_scope_digest = ?")
        params.append(repository_scope_digest)
    if since_days > 0:
        cutoff = (datetime.now(UTC) - timedelta(days=since_days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        clauses.append("started_at >= ?")
        params.append(cutoff)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _refresh_all_runs(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM qwendex_performance_runs WHERE NOT EXISTS (SELECT 1 FROM qwendex_performance_events e WHERE e.run_id = qwendex_performance_runs.run_id)"
    )


def maintain(path: Path, *, retention_days: int, max_events: int) -> dict[str, int]:
    """Apply retention only when a summary or explicit maintenance action requests it."""
    if not path.expanduser().exists():
        return {"classified_incomplete": 0, "expired_events": 0, "max_event_trimmed": 0}
    with _connect(path, write=True) as conn:
        now = utc_now()
        stale_before = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        classified = conn.execute(
            """
            UPDATE qwendex_performance_events
            SET completed_at = ?, terminal_classification = 'aborted_or_incomplete'
            WHERE event_kind = 'tool_call' AND terminal_classification = 'pending'
              AND (run_id IN (SELECT run_id FROM qwendex_performance_runs WHERE completed_at <> '') OR started_at < ?)
            """,
            (now, stale_before),
        ).rowcount
        cutoff = (datetime.now(UTC) - timedelta(days=max(1, retention_days))).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        expired = conn.execute(
            "DELETE FROM qwendex_performance_events WHERE started_at < ?",
            (cutoff,),
        ).rowcount
        total = int(conn.execute("SELECT COUNT(*) FROM qwendex_performance_events").fetchone()[0])
        trim_count = max(0, total - max(1, max_events))
        if trim_count:
            conn.execute(
                """
                DELETE FROM qwendex_performance_events
                WHERE event_id IN (
                  SELECT event_id FROM qwendex_performance_events
                  ORDER BY started_at ASC, event_id ASC LIMIT ?
                )
                """,
                (trim_count,),
            )
        _refresh_all_runs(conn)
        conn.commit()
    return {
        "classified_incomplete": max(0, int(classified or 0)),
        "expired_events": max(0, int(expired or 0)),
        "max_event_trimmed": trim_count,
    }


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _duration_metric(values: list[float]) -> dict[str, Any] | str:
    if not values:
        return "not_observed"
    return {
        "observed": len(values),
        "median_ms": round(_percentile(values, 0.5), 3),
        "p95_ms": round(_percentile(values, 0.95), 3),
    }


def _empty_summary(*, repository_scope_digest: str, since_days: int, maintenance: Mapping[str, int]) -> dict[str, Any]:
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "repository_scope_digest": repository_scope_digest or "all_observed_repositories",
        "since_days": since_days or None,
        "runs_observed": 0,
        "tool_calls_by_family": {},
        "search_read_calls_per_run": "not_observed",
        "search_output_bytes": "not_observed",
        "duplicate_query_rate": "not_observed",
        "root_subagent_overlap": "not_observed",
        "compaction_event_count": "not_observed",
        "time_to_first_edit": "not_observed",
        "startup_preflight_durations": "not_observed",
        "validation_command_durations": "not_observed",
        "telemetry_coverage": "not_observed",
        "incomplete_event_rate": "not_observed",
        "instrumentation_overhead": "not_observed",
        "maintenance": dict(maintenance),
    }


def summary(
    path: Path,
    *,
    retention_days: int,
    max_events: int,
    repository_scope_digest: str = "",
    since_days: int = 0,
) -> dict[str, Any]:
    maintenance = maintain(path, retention_days=retention_days, max_events=max_events)
    if not path.expanduser().exists():
        return _empty_summary(
            repository_scope_digest=repository_scope_digest,
            since_days=since_days,
            maintenance=maintenance,
        )
    try:
        with _connect(path, write=False) as conn:
            where, params = _where_clause(
                repository_scope_digest=repository_scope_digest,
                since_days=since_days,
            )
            rows = [dict(row) for row in conn.execute(
                "SELECT * FROM qwendex_performance_events" + where,
                params,
            ).fetchall()]
            run_rows = [dict(row) for row in conn.execute(
                "SELECT * FROM qwendex_performance_runs" + where,
                params,
            ).fetchall()]
    except (OSError, sqlite3.Error, ValueError):
        return _empty_summary(
            repository_scope_digest=repository_scope_digest,
            since_days=since_days,
            maintenance=maintenance,
        )
    if not rows:
        return _empty_summary(
            repository_scope_digest=repository_scope_digest,
            since_days=since_days,
            maintenance=maintenance,
        )
    tool_rows = [row for row in rows if row["event_kind"] == "tool_call"]
    families: dict[str, int] = {}
    for row in tool_rows:
        family = str(row["tool_family"])
        families[family] = families.get(family, 0) + 1
    run_ids = {str(row["run_id"]) for row in rows}
    run_count = len(run_ids)
    search_count = sum(1 for row in tool_rows if row["tool_family"] == "search")
    read_count = sum(1 for row in tool_rows if row["tool_family"] == "read")
    search_output = [float(row["output_bytes"]) for row in tool_rows if row["tool_family"] == "search" and row["output_bytes"] is not None]
    query_rows = [row for row in tool_rows if str(row["query_fingerprint"] or "")]
    duplicate_count = sum(int(row["duplicate_within_run"] or 0) for row in query_rows)
    overlap_total = 0
    overlap_possible = 0
    by_run: dict[str, dict[str, set[str]]] = {}
    for row in query_rows:
        roles = by_run.setdefault(str(row["run_id"]), {"root": set(), "worker": set()})
        role = str(row["agent_role"])
        if role in roles:
            roles[role].add(str(row["query_fingerprint"]))
    for roles in by_run.values():
        if roles["root"] and roles["worker"]:
            overlap_total += len(roles["root"] & roles["worker"])
            overlap_possible += len(roles["root"] | roles["worker"])
    first_edit_values: list[float] = []
    for row in run_rows:
        elapsed = _elapsed_ms(str(row["started_at"]), str(row["first_edit_at"]))
        if elapsed is not None:
            first_edit_values.append(elapsed)
    startup_values = [
        float(row["duration_ms"])
        for row in rows
        if row["tool_family"] == "startup" and row["duration_ms"] is not None
    ]
    validation_values = [
        float(row["duration_ms"])
        for row in tool_rows
        if row["tool_family"] == "validation" and row["duration_ms"] is not None
    ]
    overhead_values = [float(row["instrumentation_duration_ms"]) for row in rows if row["instrumentation_duration_ms"] is not None]
    pending = sum(1 for row in tool_rows if row["terminal_classification"] == "pending")
    completed_or_classified = len(tool_rows) - pending
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "repository_scope_digest": repository_scope_digest or "all_observed_repositories",
        "since_days": since_days or None,
        "runs_observed": run_count,
        "tool_calls_by_family": dict(sorted(families.items())),
        "search_read_calls_per_run": (
            {"search_total": search_count, "read_total": read_count, "search_mean": round(search_count / run_count, 3), "read_mean": round(read_count / run_count, 3)}
            if run_count else "not_observed"
        ),
        "search_output_bytes": (
            {"observed_events": len(search_output), "total": int(sum(search_output)), "mean": round(sum(search_output) / len(search_output), 3)}
            if search_output else "not_observed"
        ),
        "duplicate_query_rate": (
            {"observed_queries": len(query_rows), "duplicate_queries": duplicate_count, "rate": round(duplicate_count / len(query_rows), 6)}
            if query_rows else "not_observed"
        ),
        "root_subagent_overlap": (
            {"observed_query_fingerprints": overlap_possible, "overlap_count": overlap_total, "rate": round(overlap_total / overlap_possible, 6)}
            if overlap_possible else "not_observed"
        ),
        "compaction_event_count": sum(1 for row in rows if row["event_kind"] == "context_pressure"),
        "time_to_first_edit": _duration_metric(first_edit_values),
        "startup_preflight_durations": _duration_metric(startup_values),
        "validation_command_durations": _duration_metric(validation_values),
        "telemetry_coverage": (
            {"tool_events": len(tool_rows), "complete_or_classified": completed_or_classified, "rate": round(completed_or_classified / len(tool_rows), 6)}
            if tool_rows else "not_observed"
        ),
        "incomplete_event_rate": (
            {"tool_events": len(tool_rows), "incomplete_events": pending, "rate": round(pending / len(tool_rows), 6)}
            if tool_rows else "not_observed"
        ),
        "instrumentation_overhead": _duration_metric(overhead_values),
        "maintenance": maintenance,
    }


def runs(path: Path, *, limit: int, repository_scope_digest: str = "") -> list[dict[str, Any]]:
    if not path.expanduser().exists():
        return []
    try:
        with _connect(path, write=False) as conn:
            where, params = _where_clause(repository_scope_digest=repository_scope_digest)
            rows = conn.execute(
                "SELECT * FROM qwendex_performance_runs" + where + " ORDER BY started_at DESC LIMIT ?",
                [*params, max(1, min(limit, 100))],
            ).fetchall()
            result: list[dict[str, Any]] = []
            for ordinal, row in enumerate(rows, start=1):
                run_id = str(row["run_id"])
                counts = {
                    str(item["tool_family"]): int(item["count"])
                    for item in conn.execute(
                        """
                        SELECT tool_family, COUNT(*) AS count FROM qwendex_performance_events
                        WHERE run_id = ? AND event_kind = 'tool_call' GROUP BY tool_family
                        """,
                        (run_id,),
                    ).fetchall()
                }
                incomplete = int(conn.execute(
                    """
                    SELECT COUNT(*) FROM qwendex_performance_events
                    WHERE run_id = ? AND event_kind = 'tool_call' AND terminal_classification = 'pending'
                    """,
                    (run_id,),
                ).fetchone()[0])
                event_count = int(conn.execute(
                    "SELECT COUNT(*) FROM qwendex_performance_events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0])
                result.append({
                    "run_number": ordinal,
                    "repository_scope_digest": str(row["repository_scope_digest"]),
                    "started_at": str(row["started_at"]),
                    "completed_at": str(row["completed_at"]) or None,
                    "terminal_classification": str(row["terminal_classification"]),
                    "event_count": event_count,
                    "tool_calls_by_family": dict(sorted(counts.items())),
                    "incomplete_event_count": incomplete,
                })
            return result
    except (OSError, sqlite3.Error, ValueError):
        return []


def status(path: Path) -> dict[str, Any]:
    target = path.expanduser()
    data: dict[str, Any] = {
        "storage": "local_sqlite",
        "database_exists": target.is_file(),
        "database_schema_version": None,
        "event_count": "not_observed",
        "run_count": "not_observed",
        "database_bytes": target.stat().st_size if target.is_file() else 0,
    }
    if not target.is_file():
        return data
    try:
        with _connect(target, write=False) as conn:
            data["database_schema_version"] = int(conn.execute("PRAGMA user_version").fetchone()[0])
            data["event_count"] = int(conn.execute("SELECT COUNT(*) FROM qwendex_performance_events").fetchone()[0])
            data["run_count"] = int(conn.execute("SELECT COUNT(*) FROM qwendex_performance_runs").fetchone()[0])
    except (OSError, sqlite3.Error, ValueError):
        data["database_schema_version"] = "unavailable"
    return data


def benchmark() -> dict[str, Any]:
    """Measure synthetic capture overhead in an isolated temporary database.

    This is deliberately an instrumentation check, not a search-performance
    claim. Its sentinel values exercise the no-raw-persistence boundary and
    never touch the caller's configured performance database or repository.
    """
    repository_scope_digest = "sha256:" + ("0" * 64)
    sentinels = (
        b"benchmark-query-secret",
        b"/benchmark/private/path",
        b"benchmark-output-secret",
    )
    with tempfile.TemporaryDirectory(prefix="qwendex-performance-benchmark-") as temp_dir:
        database = Path(temp_dir) / "qwendex-performance.sqlite"
        records = [
            {
                "action": "startup",
                "repository_scope_digest": repository_scope_digest,
                "run_material": "benchmark-run-/benchmark/private/path",
                "manager_launch_material": "benchmark-launch",
                "turn_material": "benchmark-turn",
                "event_key_material": "startup",
                "agent_role": "root",
                "phase": "startup",
                "event_kind": "startup_observation",
                "tool_family": "startup",
                "query_class": "not_applicable",
                "scope_class": "repository_root",
                "duration_ms": 3.0,
            },
            {
                "action": "tool_start",
                "repository_scope_digest": repository_scope_digest,
                "run_material": "benchmark-run-/benchmark/private/path",
                "manager_launch_material": "benchmark-launch",
                "turn_material": "benchmark-turn",
                "event_key_material": "search-1",
                "agent_role": "root",
                "phase": "tool",
                "event_kind": "tool_call",
                "tool_family": "search",
                "query_class": "literal",
                "scope_class": "repository_root",
                "query_material": "benchmark-query-secret",
                "query_fingerprints": True,
                "input_size_bucket": "33-128",
            },
            {
                "action": "tool_finish",
                "repository_scope_digest": repository_scope_digest,
                "run_material": "benchmark-run-/benchmark/private/path",
                "manager_launch_material": "benchmark-launch",
                "turn_material": "benchmark-turn",
                "event_key_material": "search-1",
                "agent_role": "root",
                "phase": "tool",
                "event_kind": "tool_call",
                "tool_family": "search",
                "query_class": "literal",
                "scope_class": "repository_root",
                "output_bytes": len(b"benchmark-output-secret"),
                "result_count": 1,
                "success": True,
            },
            {
                "action": "tool_start",
                "repository_scope_digest": repository_scope_digest,
                "run_material": "benchmark-run-/benchmark/private/path",
                "manager_launch_material": "benchmark-launch",
                "turn_material": "benchmark-turn",
                "event_key_material": "aborted-1",
                "agent_role": "worker",
                "phase": "tool",
                "event_kind": "tool_call",
                "tool_family": "read",
                "query_class": "read",
                "scope_class": "known_subtree",
            },
            {
                "action": "stop",
                "repository_scope_digest": repository_scope_digest,
                "run_material": "benchmark-run-/benchmark/private/path",
                "manager_launch_material": "benchmark-launch",
                "turn_material": "benchmark-turn",
                "event_key_material": "stop",
                "agent_role": "root",
                "phase": "stop",
                "event_kind": "run_stop",
                "tool_family": "other",
                "query_class": "not_applicable",
                "scope_class": "repository_root",
            },
        ]
        results = [record_event(database, record) for record in records]
        summary_payload = summary(
            database,
            retention_days=14,
            max_events=50_000,
            repository_scope_digest=repository_scope_digest,
        )
        raw_database = b"".join(
            candidate.read_bytes()
            for candidate in sorted(database.parent.glob(database.name + "*"))
            if candidate.is_file()
        )
    captured = sum(1 for result in results if result.get("captured") is True)
    hook_overhead_values = [
        float(result["instrumentation_duration_ms"])
        for result in results
        if result.get("captured") is True
        and isinstance(result.get("instrumentation_duration_ms"), int | float)
    ]
    overhead = _duration_metric(hook_overhead_values)
    if isinstance(overhead, dict):
        overhead["mean_ms"] = round(sum(hook_overhead_values) / len(hook_overhead_values), 3)
        overhead["total_ms"] = round(sum(hook_overhead_values), 3)
    overhead_ok = (
        isinstance(overhead, Mapping)
        and float(overhead.get("median_ms") or 0) < 5
        and float(overhead.get("p95_ms") or 0) < 15
    )
    aggregate_ok = (
        int(summary_payload.get("runs_observed") or 0) == 1
        and isinstance(summary_payload.get("telemetry_coverage"), Mapping)
        and float(summary_payload["telemetry_coverage"].get("rate") or 0) == 1.0
    )
    privacy_ok = not any(sentinel in raw_database for sentinel in sentinels)
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "suite": "exploration",
        "execution": "synthetic_isolated",
        "event_coverage": {
            "expected_events": len(records),
            "captured_events": captured,
            "rate": round(captured / len(records), 6) if records else 0.0,
        },
        "instrumentation_overhead": overhead,
        "aggregate_summary": {
            "status": "pass" if aggregate_ok else "blocked",
            "runs_observed": summary_payload.get("runs_observed", 0),
        },
        "privacy_scan": {
            "status": "pass" if privacy_ok else "blocked",
            "raw_sentinel_persistence": "none" if privacy_ok else "detected",
        },
        "paired_run_wall_time_overhead": "not_observed",
        "status": "pass" if captured == len(records) and overhead_ok and aggregate_ok and privacy_ok else "blocked",
    }


def purge(path: Path) -> dict[str, int]:
    target = path.expanduser()
    if not target.exists():
        return {"purged_events": 0, "purged_runs": 0}
    with _connect(target, write=True) as conn:
        events = int(conn.execute("SELECT COUNT(*) FROM qwendex_performance_events").fetchone()[0])
        run_count = int(conn.execute("SELECT COUNT(*) FROM qwendex_performance_runs").fetchone()[0])
        conn.execute("DELETE FROM qwendex_performance_events")
        conn.execute("DELETE FROM qwendex_performance_runs")
        conn.execute("DELETE FROM qwendex_performance_meta")
        _salt(conn)
        conn.commit()
    return {"purged_events": events, "purged_runs": run_count}
