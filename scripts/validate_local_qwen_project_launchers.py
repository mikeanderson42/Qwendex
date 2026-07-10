#!/usr/bin/env python3
"""Validate Qwendex local-Qwen launcher wiring.

The standalone Qwendex repo owns the canonical launcher. Downstream projects can
opt into wrapper drift checks by setting LOCAL_QWEN_PROJECT_ROOTS to a
colon-separated list of project roots.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CENTRAL_LAUNCHER = ROOT / "scripts" / "run_local_qwen_codex.sh"
REQUIRED_SNIPPETS = (
    "EXPECTED_BRIDGE_VERSION=",
    "LOCAL_QWEN_GUARD_PROFILE=",
    "LOCAL_QWEN_CODEX_MAX_TOOL_CALLS",
    "ensure_bridge_runtime_guard_matches",
    "check_mcp_bins",
    'LOCAL_QWEN_CODEX_SANDBOX_MODE="${LOCAL_QWEN_CODEX_SANDBOX_MODE:-workspace-write}"',
    '--sandbox "$LOCAL_QWEN_CODEX_SANDBOX_MODE"',
    "model_context_window=$CODEX_CONTEXT_WINDOW",
    "model_auto_compact_token_limit=$CODEX_AUTO_COMPACT_LIMIT",
)
WRAPPER_REQUIRED_SNIPPETS = (
    'PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
    'CENTRAL_LAUNCHER="${LOCAL_QWEN_CANONICAL_LAUNCHER:-',
    'exec "$CENTRAL_LAUNCHER" --cwd "$PROJECT_ROOT" "$@"',
)


def project_roots_from_env() -> list[Path]:
    raw = os.environ.get("LOCAL_QWEN_PROJECT_ROOTS", "")
    return [Path(item).expanduser() for item in raw.split(":") if item.strip()]


def validate_central_launcher() -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    if not CENTRAL_LAUNCHER.is_file():
        return [f"missing central launcher: {CENTRAL_LAUNCHER}"], warnings
    if not os.access(CENTRAL_LAUNCHER, os.X_OK):
        failures.append(f"central launcher is not executable: {CENTRAL_LAUNCHER}")
    text = CENTRAL_LAUNCHER.read_text(encoding="utf-8")
    for snippet in REQUIRED_SNIPPETS:
        if snippet not in text:
            failures.append(f"central launcher missing snippet: {snippet}")
    return failures, warnings


def validate_wrapper(root: Path) -> dict[str, Any]:
    wrapper = root / "scripts" / "run_local_qwen_codex.sh"
    result: dict[str, Any] = {"root": str(root), "wrapper": str(wrapper), "ok": True, "failures": []}
    if not wrapper.is_file():
        result["ok"] = False
        result["failures"].append("missing wrapper")
        return result
    text = wrapper.read_text(encoding="utf-8")
    for snippet in WRAPPER_REQUIRED_SNIPPETS:
        if snippet not in text:
            result["ok"] = False
            result["failures"].append(f"missing wrapper snippet: {snippet}")
    return result


def run() -> dict[str, Any]:
    failures, warnings = validate_central_launcher()
    wrappers = [validate_wrapper(root) for root in project_roots_from_env()]
    for wrapper in wrappers:
        if not wrapper["ok"]:
            failures.extend(f"{wrapper['root']}: {failure}" for failure in wrapper["failures"])
    status = "pass" if not failures else "fail"
    return {
        "schema_version": "qwendex.launcher_validation.v1",
        "status": status,
        "central_launcher": str(CENTRAL_LAUNCHER),
        "configured_project_roots": [str(root) for root in project_roots_from_env()],
        "wrappers": wrappers,
        "warnings": warnings,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args()
    payload = run()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(payload["status"])
        for failure in payload["failures"]:
            print(f"FAIL: {failure}")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
