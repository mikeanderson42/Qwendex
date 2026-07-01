#!/usr/bin/env python3
"""Report Codex hook sources for the local-Qwen harness.

Codex merges matching hooks from user, project, and plugin scopes. This audit is
read-only: it reports source files, declared event keys, and the harness policy
forbidden behaviors without executing hook commands.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "local_qwen_hook_audit.v1"
DEFAULT_CODEX_HOME = Path.home() / ".codex"

FORBIDDEN_BEHAVIORS = {
    "hidden_llm_calls": "forbidden",
    "automatic_file_rewrites": "forbidden",
    "retry_or_repair_loops": "forbidden",
    "user_prompt_rewrites_that_change_intent": "forbidden",
    "broad_context_injection_every_turn": "forbidden",
}


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def hook_event_keys(payload: dict[str, Any]) -> list[str]:
    hooks = payload.get("hooks")
    if isinstance(hooks, dict):
        return sorted(str(key) for key in hooks)
    return sorted(str(key) for key in payload if str(key).lower().endswith("hook"))


def source_payload(scope: str, path: Path) -> dict[str, Any]:
    exists = path.is_file()
    payload = load_json(path) if exists else {}
    return {
        "scope": scope,
        "path": str(path),
        "exists": exists,
        "events": hook_event_keys(payload),
        "bytes": path.stat().st_size if exists else 0,
    }


def plugin_hook_files(plugin_roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in plugin_roots:
        if not root.exists():
            continue
        if root.is_file():
            found.append(root)
            continue
        for name in ("hooks.json", "plugin-hooks.json"):
            candidate = root / name
            if candidate.is_file():
                found.append(candidate)
    return sorted(found)


def audit_hooks(
    *,
    project_root: Path = ROOT,
    codex_home: Path = DEFAULT_CODEX_HOME,
    plugin_roots: list[Path] | None = None,
) -> dict[str, Any]:
    plugin_roots = plugin_roots or []
    sources = [
        source_payload("global", codex_home / "hooks.json"),
        source_payload("project", project_root / ".codex" / "hooks.json"),
        source_payload("project", project_root / "hooks" / "hooks.json"),
    ]
    sources.extend(source_payload("plugin", path) for path in plugin_hook_files(plugin_roots))
    existing_sources = [source for source in sources if source["exists"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "project_root": str(project_root),
        "codex_home": str(codex_home),
        "codex_hook_merge_model": "additive_concurrent",
        "sources": existing_sources,
        "source_count": len(existing_sources),
        "forbidden_behaviors": FORBIDDEN_BEHAVIORS,
        "allowed_behaviors": {
            "pre_bash_fact_secret_destructive_checks": "allowed",
            "mcp_health_check": "allowed",
            "post_edit_focused_quality_gate": "allowed",
            "stop_or_handoff_receipt_reminder": "allowed",
            "precompact_state_ledger": "allowed",
            "telemetry_with_secret_redaction": "allowed",
        },
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--codex-home", type=Path, default=DEFAULT_CODEX_HOME)
    parser.add_argument("--plugin-root", action="append", type=Path, default=[])
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = audit_hooks(
        project_root=args.project_root,
        codex_home=args.codex_home,
        plugin_roots=args.plugin_root,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"status: {report['status']}")
        print(f"merge_model: {report['codex_hook_merge_model']}")
        for source in report["sources"]:
            events = ",".join(source["events"]) or "none"
            print(f"{source['scope']}: {source['path']} events={events}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
