#!/usr/bin/env python3
"""Append-only SQLite index for local-Qwen/Codex harness receipts.

JSON receipts and transcripts remain canonical on disk. This module only stores
run metadata, file hashes, failure-marker counts, SkillOpt proposal links, and
compact eval fields so the operator console can answer "what happened?" without
turning old transcripts into model memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = (
    Path.home()
    / ".local"
    / "state"
    / "qwendex"
    / "local_qwen_harness.sqlite"
)
SCHEMA_VERSION = 2
MAX_JSON_PARSE_BYTES = 2_000_000
FAILURE_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
    "LOCAL_QWEN_VALIDATOR_FAILED",
    "LOCAL_QWEN_BRIDGE_UNAVAILABLE",
)
NORMALIZED_EVENT_TYPES = {
    "bridge_start",
    "bridge_status",
    "eval_run",
    "live_probe",
    "marker_occurrence",
    "launcher_check",
    "mcp_health",
    "skillopt_dry_run",
    "external_package_audit",
}
SECRET_KEY_RE = re.compile(
    r"(secret|password|credential|api[_-]?key|auth|access[_-]?token|refresh[_-]?token|bearer|pat)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IndexedFile:
    path: Path
    rel_path: str
    bytes_size: int
    mtime_ns: int
    sha256: str
    artifact_kind: str
    schema_hint: str = ""
    run_id: str = ""
    task_name: str = ""
    model_alias: str = ""
    backend_profile: str = ""
    provider: str = ""
    status: str = ""
    success: str = ""
    eval_name: str = ""
    score: float | None = None
    failure_markers: dict[str, int] | None = None
    skillopt: dict[str, Any] | None = None


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path = db_path.expanduser()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS ingest_batches (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT NOT NULL,
          repo_root TEXT NOT NULL,
          source TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT '',
          scanned_paths INTEGER NOT NULL DEFAULT 0,
          indexed_artifacts INTEGER NOT NULL DEFAULT 0,
          skipped_artifacts INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS artifact_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id INTEGER NOT NULL,
          observed_at TEXT NOT NULL,
          path TEXT NOT NULL,
          artifact_kind TEXT NOT NULL,
          bytes INTEGER NOT NULL,
          mtime_ns INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          schema_hint TEXT NOT NULL DEFAULT '',
          failure_marker_count INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY(batch_id) REFERENCES ingest_batches(id)
        );
        CREATE TABLE IF NOT EXISTS run_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id INTEGER NOT NULL,
          artifact_id INTEGER NOT NULL,
          observed_at TEXT NOT NULL,
          source_path TEXT NOT NULL,
          source_sha256 TEXT NOT NULL,
          run_id TEXT NOT NULL DEFAULT '',
          task_name TEXT NOT NULL DEFAULT '',
          model_alias TEXT NOT NULL DEFAULT '',
          backend_profile TEXT NOT NULL DEFAULT '',
          provider TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          success TEXT NOT NULL DEFAULT '',
          eval_name TEXT NOT NULL DEFAULT '',
          score REAL,
          FOREIGN KEY(batch_id) REFERENCES ingest_batches(id),
          FOREIGN KEY(artifact_id) REFERENCES artifact_observations(id)
        );
        CREATE TABLE IF NOT EXISTS failure_marker_observations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id INTEGER NOT NULL,
          artifact_id INTEGER NOT NULL,
          observed_at TEXT NOT NULL,
          marker TEXT NOT NULL,
          count INTEGER NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES ingest_batches(id),
          FOREIGN KEY(artifact_id) REFERENCES artifact_observations(id)
        );
        CREATE TABLE IF NOT EXISTS skillopt_proposal_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          batch_id INTEGER NOT NULL,
          artifact_id INTEGER NOT NULL,
          observed_at TEXT NOT NULL,
          path TEXT NOT NULL,
          sha256 TEXT NOT NULL,
          gate_status TEXT NOT NULL DEFAULT '',
          accepted TEXT NOT NULL DEFAULT '',
          baseline_score REAL,
          candidate_score REAL,
          FOREIGN KEY(batch_id) REFERENCES ingest_batches(id),
          FOREIGN KEY(artifact_id) REFERENCES artifact_observations(id)
        );
        CREATE TABLE IF NOT EXISTS schema_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          schema_version INTEGER NOT NULL,
          event TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS harness_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          observed_at TEXT NOT NULL,
          repo_root TEXT NOT NULL,
          event_type TEXT NOT NULL,
          run_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT '',
          marker TEXT NOT NULL DEFAULT '',
          path TEXT NOT NULL DEFAULT '',
          sha256 TEXT NOT NULL DEFAULT '',
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.execute(
            "INSERT INTO schema_events(observed_at, schema_version, event) VALUES (?, ?, ?)",
            (utc_now(), SCHEMA_VERSION, "schema_initialized"),
        )
    conn.commit()


def redact_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                continue
            redacted[key_text] = redact_metadata(item)
        return redacted
    if isinstance(value, list):
        return [redact_metadata(item) for item in value[:200]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = "" if value is None else str(value)
        if "sk-" in text or "ghp_" in text or "github_pat_" in text:
            return "[redacted]"
        return value
    return as_short_text(value)


def record_event(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    repo_root: Path = ROOT,
    event_type: str,
    run_id: str = "",
    status: str = "",
    marker: str = "",
    path: Path | str = "",
    sha256: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if event_type not in NORMALIZED_EVENT_TYPES:
        raise ValueError(f"unknown harness event type: {event_type}")
    conn = connect(db_path)
    init_db(conn)
    observed_at = utc_now()
    clean_metadata = redact_metadata(metadata or {})
    row = conn.execute(
        """
        INSERT INTO harness_events(
          observed_at, repo_root, event_type, run_id, status, marker, path, sha256, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observed_at,
            str(repo_root),
            event_type,
            run_id,
            status,
            marker,
            str(path),
            sha256,
            json.dumps(clean_metadata, sort_keys=True, separators=(",", ":")),
        ),
    )
    conn.commit()
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "id": int(row.lastrowid),
        "observed_at": observed_at,
        "event_type": event_type,
        "run_id": run_id,
    }


def explain_run(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    run_id: str,
    limit: int = 25,
) -> dict[str, Any]:
    db_path = db_path.expanduser()
    if not db_path.exists():
        return {"status": "missing", "db_path": str(db_path), "run_id": run_id, "events": []}
    limit = max(1, min(limit, 200))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    rows = conn.execute(
        """
        SELECT id, observed_at, event_type, run_id, status, marker, path, sha256, metadata_json
        FROM harness_events
        WHERE run_id = ?
        ORDER BY id ASC
        LIMIT ?
        """,
        (run_id, limit),
    ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"])
        except json.JSONDecodeError:
            metadata = {}
        events.append({**dict(row), "metadata": metadata, "metadata_json": ""})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ready",
        "db_path": str(db_path),
        "run_id": run_id,
        "event_count": len(events),
        "events": events,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def marker_counts(path: Path) -> dict[str, int]:
    counts = {marker: 0 for marker in FAILURE_MARKERS}
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            for marker in FAILURE_MARKERS:
                counts[marker] += chunk.count(marker.encode("utf-8"))
    return {marker: count for marker, count in counts.items() if count}


def default_scan_paths(repo_root: Path = ROOT) -> list[Path]:
    candidates = [
        repo_root / "config" / "local_llm_stack",
        repo_root / "results" / "local_qwen_harness_hardening",
        repo_root / "results" / "local_qwen_proficiency",
        repo_root / "docs" / "generated" / "local_llm_stack",
        repo_root / ".skillopt-sleep" / "staging",
    ]
    return [path for path in candidates if path.exists()]


def iter_candidate_files(paths: Iterable[Path], limit: int = 1000) -> tuple[list[Path], int]:
    selected: list[Path] = []
    skipped = 0
    for raw_path in paths:
        path = raw_path.expanduser()
        if not path.exists():
            skipped += 1
            continue
        if path.is_file():
            candidates = [path]
        else:
            candidates = sorted(
                item
                for item in path.rglob("*")
                if item.is_file() and item.suffix.lower() in {".json", ".jsonl", ".md", ".txt"}
            )
        for candidate in candidates:
            if len(selected) >= limit:
                skipped += 1
                continue
            if is_harness_candidate(candidate):
                selected.append(candidate)
            else:
                skipped += 1
    return selected, skipped


def is_harness_candidate(path: Path) -> bool:
    text = path.as_posix().lower()
    if ".skillopt-sleep/staging" in text:
        return True
    return any(
        needle in text
        for needle in (
            "local_qwen",
            "qwen",
            "local_llm",
            "codex",
            "harness",
            "bridge",
            "benchmark",
            "verification",
            "sampler_probe",
        )
    )


def artifact_kind(path: Path) -> str:
    name = path.name.lower()
    full = path.as_posix().lower()
    if ".skillopt-sleep/staging" in full:
        return "skillopt_proposal"
    if name.startswith("local_model_verification"):
        return "local_model_verification"
    if name.startswith("operator_repo_benchmark"):
        return "operator_repo_benchmark"
    if name.startswith("apples_benchmark"):
        return "apples_to_apples_benchmark"
    if name.startswith("chat_inference_benchmark"):
        return "chat_inference_benchmark"
    if name.startswith("sampler_probe"):
        return "sampler_probe"
    if "local_qwen_harness_hardening" in full:
        return "harness_probe"
    if "architecture" in name or "audit" in name:
        return "architecture_report"
    return "harness_artifact"


def load_small_json(path: Path) -> Any | None:
    if path.suffix.lower() not in {".json", ".jsonl"}:
        return None
    if path.stat().st_size > MAX_JSON_PARSE_BYTES:
        return None
    try:
        if path.suffix.lower() == ".jsonl":
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            return [json.loads(line) for line in lines[:200] if line.strip()]
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None


def find_value(payload: Any, keys: tuple[str, ...]) -> Any:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)) and str(value) != "":
                return value
        for value in payload.values():
            found = find_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for item in payload[:100]:
            found = find_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def as_short_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text if len(text) <= 240 else text[:237] + "..."


def as_success(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return as_short_text(value)


def as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_skillopt_report(path: Path) -> dict[str, Any]:
    if path.suffix.lower() not in {".md", ".txt", ".json"}:
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    score_match = re.search(r"held-out\s+([0-9.]+)\s*->\s*([0-9.]+)", text)
    accepted_match = re.search(r"accepted\s*=\s*(true|false)", text, flags=re.IGNORECASE)
    gate = ""
    if "=> reject" in text:
        gate = "reject"
    elif "=> accept" in text:
        gate = "accept"
    return {
        "gate_status": gate,
        "accepted": accepted_match.group(1).lower() if accepted_match else "",
        "baseline_score": float(score_match.group(1)) if score_match else None,
        "candidate_score": float(score_match.group(2)) if score_match else None,
    }


def inspect_file(path: Path, repo_root: Path = ROOT) -> IndexedFile:
    stat = path.stat()
    digest = sha256_file(path)
    payload = load_small_json(path)
    markers = marker_counts(path)
    kind = artifact_kind(path)
    schema_hint = ""
    if isinstance(payload, dict):
        schema_hint = as_short_text(
            find_value(payload, ("schema", "schema_version", "artifact", "benchmark_schema"))
        )
    skillopt = parse_skillopt_report(path) if kind == "skillopt_proposal" else {}
    return IndexedFile(
        path=path,
        rel_path=relative_path(path, repo_root),
        bytes_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest,
        artifact_kind=kind,
        schema_hint=schema_hint,
        run_id=as_short_text(find_value(payload, ("run_id", "id", "receipt_id"))),
        task_name=as_short_text(find_value(payload, ("task_name", "task", "case", "prompt_id"))),
        model_alias=as_short_text(find_value(payload, ("model_alias", "model_name", "model"))),
        backend_profile=as_short_text(find_value(payload, ("backend_profile", "active_backend_profile", "profile"))),
        provider=as_short_text(find_value(payload, ("provider", "backend_kind", "backend"))),
        status=as_short_text(find_value(payload, ("status", "state", "verdict"))),
        success=as_success(find_value(payload, ("success", "ok", "passed", "accepted"))),
        eval_name=as_short_text(find_value(payload, ("eval_name", "suite", "benchmark", "scenario"))),
        score=as_float(find_value(payload, ("score", "accuracy", "pass_rate", "success_rate"))),
        failure_markers=markers,
        skillopt=skillopt,
    )


def relative_path(path: Path, repo_root: Path = ROOT) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.expanduser().as_posix()


def index_paths(
    db_path: Path = DEFAULT_DB_PATH,
    repo_root: Path = ROOT,
    paths: Iterable[Path] | None = None,
    *,
    source: str = "manual",
    note: str = "",
    limit: int = 1000,
) -> dict[str, Any]:
    scan_paths = list(paths) if paths else default_scan_paths(repo_root)
    files, skipped = iter_candidate_files(scan_paths, limit=limit)
    inspected: list[IndexedFile] = []
    for path in files:
        try:
            inspected.append(inspect_file(path, repo_root))
        except OSError:
            skipped += 1
    conn = connect(db_path)
    init_db(conn)
    observed_at = utc_now()
    batch = conn.execute(
        """
        INSERT INTO ingest_batches(
          started_at, repo_root, source, note, scanned_paths, indexed_artifacts, skipped_artifacts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (observed_at, str(repo_root), source, note, len(files), len(inspected), skipped),
    )
    batch_id = int(batch.lastrowid)
    for item in inspected:
        marker_total = sum((item.failure_markers or {}).values())
        artifact = conn.execute(
            """
            INSERT INTO artifact_observations(
              batch_id, observed_at, path, artifact_kind, bytes, mtime_ns, sha256, schema_hint,
              failure_marker_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                observed_at,
                item.rel_path,
                item.artifact_kind,
                item.bytes_size,
                item.mtime_ns,
                item.sha256,
                item.schema_hint,
                marker_total,
            ),
        )
        artifact_id = int(artifact.lastrowid)
        conn.execute(
            """
            INSERT INTO run_observations(
              batch_id, artifact_id, observed_at, source_path, source_sha256, run_id, task_name,
              model_alias, backend_profile, provider, status, success, eval_name, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                artifact_id,
                observed_at,
                item.rel_path,
                item.sha256,
                item.run_id,
                item.task_name,
                item.model_alias,
                item.backend_profile,
                item.provider,
                item.status,
                item.success,
                item.eval_name,
                item.score,
            ),
        )
        for marker, count in (item.failure_markers or {}).items():
            conn.execute(
                """
                INSERT INTO failure_marker_observations(
                  batch_id, artifact_id, observed_at, marker, count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (batch_id, artifact_id, observed_at, marker, count),
            )
        if item.skillopt:
            conn.execute(
                """
                INSERT INTO skillopt_proposal_links(
                  batch_id, artifact_id, observed_at, path, sha256, gate_status, accepted,
                  baseline_score, candidate_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    artifact_id,
                    observed_at,
                    item.rel_path,
                    item.sha256,
                    as_short_text(item.skillopt.get("gate_status")),
                    as_success(item.skillopt.get("accepted")),
                    item.skillopt.get("baseline_score"),
                    item.skillopt.get("candidate_score"),
                ),
            )
    conn.commit()
    summary = ledger_summary(db_path)
    summary["indexed_batch"] = {
        "id": batch_id,
        "indexed_artifacts": len(inspected),
        "skipped_artifacts": skipped,
    }
    return summary


def one_value(conn: sqlite3.Connection, sql: str, args: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, args).fetchone()
    return row[0] if row else None


def ledger_summary(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_path = db_path.expanduser()
    if not db_path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "missing",
            "db_path": str(db_path),
            "append_only": True,
            "canonical_sources": "receipt files and transcripts on disk",
        }
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    counts = {
        table: int(one_value(conn, f"SELECT COUNT(*) FROM {table}") or 0)
        for table in (
            "ingest_batches",
            "artifact_observations",
            "run_observations",
            "failure_marker_observations",
            "skillopt_proposal_links",
            "harness_events",
        )
    }
    latest_batch_row = conn.execute(
        """
        SELECT id, started_at, repo_root, source, note, scanned_paths, indexed_artifacts, skipped_artifacts
        FROM ingest_batches ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    marker_rows = conn.execute(
        """
        SELECT marker, SUM(count) AS count
        FROM failure_marker_observations
        GROUP BY marker
        ORDER BY count DESC, marker
        """
    ).fetchall()
    kind_rows = conn.execute(
        """
        SELECT artifact_kind, COUNT(*) AS count
        FROM artifact_observations
        GROUP BY artifact_kind
        ORDER BY count DESC, artifact_kind
        LIMIT 12
        """
    ).fetchall()
    return {
        "schema_version": int(one_value(conn, "PRAGMA user_version") or SCHEMA_VERSION),
        "status": "ready",
        "db_path": str(db_path),
        "append_only": True,
        "canonical_sources": "receipt files and transcripts on disk",
        "db_bytes": db_path.stat().st_size,
        "counts": counts,
        "latest_batch": dict(latest_batch_row) if latest_batch_row else None,
        "failure_markers": {row["marker"]: row["count"] for row in marker_rows},
        "artifact_kinds": {row["artifact_kind"]: row["count"] for row in kind_rows},
    }


def query_artifacts(
    db_path: Path = DEFAULT_DB_PATH,
    *,
    limit: int = 25,
    kind: str = "",
    marker: str = "",
    path_contains: str = "",
) -> dict[str, Any]:
    db_path = db_path.expanduser()
    if not db_path.exists():
        return {"status": "missing", "db_path": str(db_path), "rows": []}
    limit = max(1, min(limit, 200))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    where = []
    args: list[Any] = []
    if kind:
        where.append("a.artifact_kind = ?")
        args.append(kind)
    if path_contains:
        where.append("a.path LIKE ?")
        args.append(f"%{path_contains}%")
    if marker:
        where.append(
            """
            EXISTS (
              SELECT 1 FROM failure_marker_observations f
              WHERE f.artifact_id = a.id AND f.marker = ?
            )
            """
        )
        args.append(marker)
    where_sql = "WHERE " + " AND ".join(where) if where else ""
    rows = conn.execute(
        f"""
        SELECT
          a.id, a.observed_at, a.path, a.artifact_kind, a.bytes, a.sha256,
          a.schema_hint, a.failure_marker_count,
          r.run_id, r.task_name, r.model_alias, r.backend_profile, r.provider,
          r.status AS run_status, r.success, r.eval_name, r.score
        FROM artifact_observations a
        LEFT JOIN run_observations r ON r.artifact_id = a.id
        {where_sql}
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (*args, limit),
    ).fetchall()
    marker_map = markers_for_artifacts(conn, [int(row["id"]) for row in rows])
    return {
        "status": "ready",
        "db_path": str(db_path),
        "filters": {"kind": kind, "marker": marker, "path_contains": path_contains, "limit": limit},
        "rows": [
            {
                **dict(row),
                "failure_markers": marker_map.get(int(row["id"]), {}),
            }
            for row in rows
        ],
    }


def markers_for_artifacts(conn: sqlite3.Connection, artifact_ids: list[int]) -> dict[int, dict[str, int]]:
    if not artifact_ids:
        return {}
    placeholders = ",".join("?" for _ in artifact_ids)
    rows = conn.execute(
        f"""
        SELECT artifact_id, marker, count
        FROM failure_marker_observations
        WHERE artifact_id IN ({placeholders})
        ORDER BY artifact_id, marker
        """,
        tuple(artifact_ids),
    ).fetchall()
    by_artifact: dict[int, Counter[str]] = {}
    for row in rows:
        by_artifact.setdefault(int(row["artifact_id"]), Counter())[row["marker"]] += int(row["count"])
    return {artifact_id: dict(counts) for artifact_id, counts in by_artifact.items()}


def print_text_summary(data: dict[str, Any]) -> None:
    print(f"status: {data.get('status')}")
    print(f"db: {data.get('db_path')}")
    if data.get("status") != "ready":
        print("canonical: receipt files and transcripts on disk")
        return
    counts = data.get("counts", {})
    print(
        "counts: "
        f"{counts.get('ingest_batches', 0)} batches, "
        f"{counts.get('artifact_observations', 0)} artifacts, "
        f"{counts.get('failure_marker_observations', 0)} marker rows"
    )
    latest = data.get("latest_batch") or {}
    if latest:
        print(f"latest: batch {latest.get('id')} at {latest.get('started_at')} indexed {latest.get('indexed_artifacts')}")
    markers = data.get("failure_markers") or {}
    if markers:
        print("markers:")
        for marker, count in markers.items():
            print(f"  {marker}: {count}")


def command_line() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Append-only local-Qwen harness ledger index")
    parser.add_argument(
        "action",
        choices=["init", "index", "summary", "query", "event", "explain"],
        nargs="?",
        default="summary",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--path", action="append", type=Path, default=[])
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--kind", default="")
    parser.add_argument("--marker", default="")
    parser.add_argument("--path-contains", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--event-type", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = command_line().parse_args(argv)
    if args.action == "init":
        conn = connect(args.db)
        init_db(conn)
        data = ledger_summary(args.db)
    elif args.action == "index":
        data = index_paths(
            args.db,
            args.repo_root,
            args.path or None,
            source="cli",
            note=args.note,
            limit=args.limit,
        )
    elif args.action == "query":
        data = query_artifacts(
            args.db,
            limit=args.limit,
            kind=args.kind,
            marker=args.marker,
            path_contains=args.path_contains,
        )
    elif args.action == "event":
        metadata = json.loads(args.metadata_json)
        if not isinstance(metadata, dict):
            raise SystemExit("--metadata-json must decode to an object")
        data = record_event(
            args.db,
            repo_root=args.repo_root,
            event_type=args.event_type,
            run_id=args.run_id,
            status=args.status,
            marker=args.marker,
            path=args.path[0] if args.path else "",
            metadata=metadata,
        )
    elif args.action == "explain":
        data = explain_run(args.db, run_id=args.run_id, limit=args.limit)
    else:
        data = ledger_summary(args.db)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        if args.action == "query":
            for row in data.get("rows", []):
                print(f"{row['id']}: {row['path']} [{row['artifact_kind']}] markers={row['failure_markers']}")
        elif args.action == "explain":
            print(f"run_id: {data.get('run_id')}")
            for event in data.get("events", []):
                print(f"{event['id']}: {event['event_type']} status={event['status']}")
        else:
            print_text_summary(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
