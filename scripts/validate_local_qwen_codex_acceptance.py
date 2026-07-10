#!/usr/bin/env python3
"""Validate a fresh-home Codex tool round-trip through the local Qwendex bridge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


PROMPT = (
    "Use the shell tool exactly once to run printf TOOL_OK. "
    "After it succeeds, reply exactly TOOL_OK."
)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file_evidence(path: Path) -> dict[str, Any]:
    regular = path.is_file() and not path.is_symlink()
    executable = regular and os.access(path, os.X_OK)
    return {
        "regular": regular,
        "executable": executable,
        "bytes": path.stat().st_size if regular else 0,
        "sha256": sha256_file(path) if regular else "",
    }


def tree_digest(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {"exists": False, "entries": 0, "sha256": sha256_bytes(b"")}
    lines: list[bytes] = []
    entries = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if path.is_symlink():
            kind = "symlink"
            content_digest = sha256_bytes(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            kind = "file"
            content_digest = sha256_bytes(path.read_bytes())
        elif path.is_dir():
            kind = "dir"
            content_digest = ""
        else:
            kind = "other"
            content_digest = ""
        lines.append(
            f"{relative}\0{kind}\0{mode:o}\0{info.st_size}\0{content_digest}\n".encode(
                "utf-8"
            )
        )
        entries += 1
    return {
        "exists": True,
        "entries": entries,
        "sha256": sha256_bytes(b"".join(lines)),
    }


def parse_events(raw: str) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    malformed = 0
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("{"):
            malformed += 1
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(payload, dict):
            events.append(payload)
        else:
            malformed += 1
    return events, malformed


def run_acceptance(
    *,
    launcher: Path,
    codex_bin: Path,
    workdir: Path,
    fresh_home: Path,
    normal_home: Path,
    final_output: Path,
    timeout: int,
) -> dict[str, Any]:
    blockers: list[str] = []
    launcher_before = regular_file_evidence(launcher)
    codex_before = regular_file_evidence(codex_bin)
    if not launcher_before["executable"]:
        blockers.append("acceptance launcher is not a regular executable file")
    if not codex_before["executable"]:
        blockers.append("acceptance Codex binary is not a regular executable file")
    if not workdir.is_dir() or workdir.is_symlink():
        blockers.append("acceptance workdir is not a regular directory")
    if normal_home.is_symlink():
        blockers.append("normal-home decoy must not be a symlink")
    if fresh_home.is_symlink():
        blockers.append("fresh Codex home must not be a symlink")
    if fresh_home.exists() and not fresh_home.is_symlink():
        if any(fresh_home.iterdir()):
            blockers.append("fresh Codex home was not empty before acceptance")
        else:
            fresh_home.rmdir()
    final_output.unlink(missing_ok=True)
    normal_before = tree_digest(normal_home)
    command = [
        str(launcher),
        "--cwd",
        str(workdir),
        "--fresh-home",
        str(fresh_home),
        "--minimal",
        "--ephemeral",
        "--json",
        "--output-last-message",
        str(final_output),
        "--exec",
        PROMPT,
    ]
    env = os.environ.copy()
    env["CODEX_BIN"] = str(codex_bin)
    # Use a controlled decoy as the normal home. If --fresh-home wiring ever
    # regresses, the launcher will mutate this path and the digest check fails;
    # an unrelated active Codex session cannot create false drift here.
    # Cover both Codex's explicit home and any accidental fallback through
    # HOME/XDG. The entire decoy root is hashed before and after the run.
    env["HOME"] = str(normal_home)
    env["CODEX_HOME"] = str(normal_home / ".codex")
    env["XDG_CACHE_HOME"] = str(normal_home / ".cache")
    env["XDG_CONFIG_HOME"] = str(normal_home / ".config")
    env["XDG_DATA_HOME"] = str(normal_home / ".local" / "share")
    env["XDG_STATE_HOME"] = str(normal_home / ".local" / "state")
    env["LOCAL_QWEN_CHECK_MCP_BINS"] = "0"
    env["LOCAL_QWEN_CODEX_CWD"] = str(workdir)
    env["LOCAL_QWEN_CODEX_ADD_DIRS"] = str(workdir)
    env["LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS"] = str(workdir)
    env.pop("CODEX_OSS_BASE_URL", None)
    try:
        result = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            text=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        returncode: int | str = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = "timeout"
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        blockers.append("fresh-home Codex acceptance timed out")
    except OSError as exc:
        returncode = "not_run"
        stdout = ""
        stderr = str(exc)
        blockers.append(f"fresh-home Codex acceptance could not start: {type(exc).__name__}")
    normal_after = tree_digest(normal_home)
    safe_home_unchanged = normal_before == normal_after
    if not safe_home_unchanged:
        blockers.append("normal Codex home changed during fresh-home acceptance")
    events, malformed_events = parse_events(stdout)
    command_items = [
        event.get("item")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "command_execution"
    ]
    successful_tool_items = [
        item
        for item in command_items
        if item.get("status") == "completed"
        and item.get("exit_code") == 0
        and str(item.get("aggregated_output") or "").strip() == "TOOL_OK"
    ]
    command_matches = [
        item
        for item in command_items
        if "printf TOOL_OK"
        in re.sub(r"\s+", " ", str(item.get("command") or "")).strip()
    ]
    agent_messages = [
        str(event["item"].get("text") or "")
        for event in events
        if event.get("type") == "item.completed"
        and isinstance(event.get("item"), dict)
        and event["item"].get("type") == "agent_message"
    ]
    final_output_regular = final_output.is_file() and not final_output.is_symlink()
    final_text = final_output.read_text(encoding="utf-8") if final_output_regular else ""
    final_exact = final_text.strip() == "TOOL_OK"
    event_final_exact = bool(agent_messages) and agent_messages[-1].strip() == "TOOL_OK"
    if returncode != 0:
        blockers.append(f"fresh-home Codex exited with {returncode}")
    if malformed_events:
        blockers.append("Codex JSON event stream contained malformed events")
    tool_round_trip_proven = (
        1 <= len(command_items) <= 3
        and len(successful_tool_items) == len(command_items)
        and len(command_matches) == len(command_items)
    )
    if not tool_round_trip_proven:
        blockers.append("fresh-home Codex did not complete a bounded proven shell tool round-trip")
    if not final_exact or not event_final_exact:
        blockers.append("fresh-home Codex final assistant text was not exactly TOOL_OK")
    if not final_output_regular:
        blockers.append("fresh-home Codex final-output evidence is not a regular file")
    fresh_home_created = fresh_home.is_dir() and not fresh_home.is_symlink()
    if not fresh_home_created:
        blockers.append("fresh Codex home was not created")
    launcher_after = regular_file_evidence(launcher)
    codex_after = regular_file_evidence(codex_bin)
    launcher_unchanged = launcher_before == launcher_after
    codex_bin_unchanged = codex_before == codex_after
    if not launcher_unchanged:
        blockers.append("acceptance launcher changed during execution")
    if not codex_bin_unchanged:
        blockers.append("acceptance Codex binary changed during execution")
    stdout_bytes = stdout.encode("utf-8")
    stderr_bytes = stderr.encode("utf-8")
    return {
        "schema_version": "qwendex.live_codex_acceptance.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pass" if not blockers else "fail",
        "success": not blockers,
        "returncode": returncode,
        "fresh_home_created": fresh_home_created,
        "normal_home_unchanged": safe_home_unchanged,
        "normal_home_before": normal_before,
        "normal_home_after": normal_after,
        "event_count": len(events),
        "malformed_event_count": malformed_events,
        "command_execution_count": len(command_items),
        "successful_tool_result_count": len(successful_tool_items),
        "matching_command_count": len(command_matches),
        "tool_round_trip_proven": tool_round_trip_proven,
        "final_text_exact": final_exact,
        "event_final_text_exact": event_final_exact,
        "final_text_sha256": sha256_bytes(final_text.encode("utf-8")),
        "final_output_regular": final_output_regular,
        "launcher_sha256": launcher_after["sha256"],
        "launcher_unchanged": launcher_unchanged,
        "codex_bin_sha256": codex_after["sha256"],
        "codex_bin_bytes": codex_after["bytes"],
        "codex_bin_unchanged": codex_bin_unchanged,
        "stdout_bytes": len(stdout_bytes),
        "stdout_sha256": sha256_bytes(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
        "stderr_sha256": sha256_bytes(stderr_bytes),
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", type=Path, required=True)
    parser.add_argument("--codex-bin", type=Path, required=True)
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--fresh-home", type=Path, required=True)
    parser.add_argument("--normal-home", type=Path, required=True)
    parser.add_argument("--final-output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = run_acceptance(
        launcher=args.launcher.resolve(),
        codex_bin=args.codex_bin.resolve(),
        workdir=args.workdir.resolve(),
        fresh_home=args.fresh_home.resolve(),
        normal_home=args.normal_home.resolve(),
        final_output=args.final_output.resolve(),
        timeout=max(30, args.timeout),
    )
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["status"])
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
