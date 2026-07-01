#!/usr/bin/env python3
"""Safe SkillOpt-Sleep wrapper for the local-Qwen harness CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TIMEOUT_SECONDS = 240
EXTERNAL_ACTIONS = {"status", "harvest", "dry-run", "run", "schedule", "unschedule"}
LOCAL_ACTIONS = {"proposal-summary"}
ALL_ACTIONS = EXTERNAL_ACTIONS | LOCAL_ACTIONS


def build_skillopt_command(action: str, *, project: Path, backend: str = "", source: str = "") -> list[str]:
    if action not in EXTERNAL_ACTIONS:
        raise ValueError(f"unsupported external SkillOpt action: {action}")
    command = ["skillopt-sleep", action, "--project", str(project)]
    if source:
        command.extend(["--source", source])
    effective_backend = backend.strip()
    if action in {"dry-run", "run"}:
        command.extend(["--backend", effective_backend or "mock"])
    return command


def proposal_summary(project: Path) -> dict[str, Any]:
    staging = project / ".skillopt-sleep" / "staging"
    rows: list[dict[str, Any]] = []
    if staging.exists():
        for path in sorted(staging.rglob("*")):
            if path.is_file() and path.name in {"report.md", "proposal.json", "adoption_gate.json"}:
                rows.append({"path": str(path), "bytes": path.stat().st_size})
    return {
        "status": "ready",
        "project": str(project),
        "staging_dir": str(staging),
        "proposal_count": len(rows),
        "proposals": rows,
    }


def doctor_summary(project: Path = ROOT) -> dict[str, Any]:
    staging = proposal_summary(project)
    return {
        "available": shutil.which("skillopt-sleep") is not None,
        "command": shutil.which("skillopt-sleep") or "",
        "project": str(project),
        "safe_default_backend": "mock",
        "adopt_exposed": False,
        "staged_proposals": staging["proposal_count"],
    }


def run_skillopt_action(
    action: str,
    *,
    project: Path = ROOT,
    backend: str = "",
    source: str = "",
    json_output: bool = False,
    allow_codex_budget: bool = False,
    execute: bool = True,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    if action not in ALL_ACTIONS:
        return {"status": "fail", "message": f"unknown SkillOpt action: {action}"}
    if action == "proposal-summary":
        return proposal_summary(project)
    effective_backend = backend.strip()
    if action == "run" and effective_backend == "codex" and not allow_codex_budget:
        return {
            "status": "blocked",
            "message": "SkillOpt run --backend codex requires --allow-codex-budget",
            "project": str(project),
            "action": action,
            "backend": effective_backend,
        }
    if shutil.which("skillopt-sleep") is None:
        return {"status": "unavailable", "message": "skillopt-sleep not found on PATH", "action": action}
    command = build_skillopt_command(action, project=project, backend=effective_backend, source=source)
    if not execute:
        return {"status": "ready", "command": command, "action": action, "backend": effective_backend or ("mock" if action in {"dry-run", "run"} else "")}
    result = subprocess.run(
        command,
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    status = "pass" if result.returncode == 0 else "fail"
    return {
        "status": status,
        "action": action,
        "backend": effective_backend or ("mock" if action in {"dry-run", "run"} else ""),
        "project": str(project),
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout if json_output else result.stdout[-4000:],
        "stderr": result.stderr[-4000:],
        "proposal_summary": proposal_summary(project),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Safely wrap SkillOpt-Sleep for local-Qwen harness work")
    parser.add_argument("action", choices=sorted(ALL_ACTIONS))
    parser.add_argument("--project", type=Path, default=ROOT)
    parser.add_argument("--backend", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--allow-codex-budget", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = run_skillopt_action(
        args.action,
        project=args.project,
        backend=args.backend,
        source=args.source,
        json_output=args.json,
        allow_codex_budget=args.allow_codex_budget,
    )
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"status: {data.get('status')}")
        if data.get("message"):
            print(data["message"])
    return 0 if data.get("status") in {"pass", "ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
