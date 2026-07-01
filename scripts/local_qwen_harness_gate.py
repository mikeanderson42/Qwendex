#!/usr/bin/env python3
"""Feedforward structural gates for local-Qwen/Codex harness changes."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESS_CATEGORIES = {"harness_core", "harness_docs", "harness_tests", "local_stack_receipt", "skillopt_proposal"}
BLOCKING_MIX_CATEGORIES = {"research_surface", "unrelated", "unknown"}
DEFAULT_RUFF_TARGETS = (
    "scripts/local_qwen_bridge/__init__.py",
    "scripts/local_qwen_bridge/exec_sanitizer.py",
    "scripts/local_qwen_bridge/responses.py",
    "scripts/local_qwen_bridge/server.py",
    "scripts/local_qwen_bridge/sse.py",
    "scripts/local_qwen_bridge/status.py",
    "scripts/local_qwen_bridge/synthetic.py",
    "scripts/local_qwen_bridge/tool_parsing.py",
    "scripts/local_qwen_tool_envelope.py",
    "scripts/local_qwen_response_shaping.py",
    "scripts/local_qwen_bridge_status.py",
    "scripts/local_qwen_document_section_recovery.py",
    "scripts/local_qwen_harness_eval.py",
    "scripts/local_qwen_harness_gate.py",
    "scripts/local_qwen_hook_audit.py",
    "scripts/local_qwen_skillopt_wrapper.py",
    "scripts/qwendex_cli.py",
    "scripts/local_llm_stack.py",
    "scripts/tabbyapi_responses_proxy.py",
)


def classify_path(path: Path) -> str:
    text = path.as_posix()
    name = path.name
    if text.startswith("state/") or text.startswith("docs/generated/support_track/"):
        return "research_surface"
    if text.startswith("scripts/research_") or text.startswith("tests/smoke/test_research_"):
        return "research_surface"
    if text.startswith(".skillopt-sleep/staging/"):
        return "skillopt_proposal"
    if text.startswith("results/local_qwen_harness_hardening/"):
        return "local_stack_receipt"
    if text.startswith("public/qwendex/"):
        return "harness_docs"
    if text.startswith("config/qwendex/"):
        return "harness_core"
    if text in {"scripts/qwendex", "scripts/qwendex_cli.py"}:
        return "harness_core"
    if text.startswith("docs/generated/local_llm_stack/"):
        return "harness_docs" if ("HARNESS" in name or "LOCAL_QWEN" in name or "LOCAL_LLM" in name) else "unknown"
    if text.startswith("tests/smoke/") and any(
        marker in name
        for marker in (
            "local_qwen",
            "tabbyapi_responses_proxy",
            "local_llm",
            "artifact_queue_mcp",
            "harness_eval",
            "qwendex",
        )
    ):
        return "harness_tests"
    if text == ".codex/config.toml" or text.startswith(".codex/agents/"):
        return "harness_core"
    if text.startswith("config/local_llm_stack/"):
        if "benchmark" in name and "summary" not in name:
            return "unknown"
        return "harness_core"
    if text.startswith("scripts/") and any(
        marker in name
        for marker in (
            "llm",
            "local_qwen",
            "local_llm",
            "tabbyapi_responses_proxy",
            "run_local_qwen",
            "run_codex_textgen_bridge",
            "run_koboldcpp_gguf",
            "run_llamacpp_qwopucode_gguf",
            "run_textgen_safe_no_model",
            "run_vllm_qwopucode_gguf",
            "validate_local_qwen",
            "artifact_queue_mcp",
            "qwendex",
        )
    ):
        return "harness_core"
    if text.startswith("scripts/local_qwen_bridge/"):
        return "harness_core"
    if text in {"AGENTS.md", "QWENDEX_STARTUP.md"}:
        return "unknown"
    return "unknown"


def git_paths(args: list[str], repo_root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        return []
    return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]


def current_scope(repo_root: Path = ROOT) -> tuple[list[Path], list[Path]]:
    staged = git_paths(["diff", "--cached", "--name-only"], repo_root)
    dirty = git_paths(["diff", "--name-only"], repo_root)
    untracked = git_paths(["ls-files", "--others", "--exclude-standard"], repo_root)
    return staged, sorted({*dirty, *untracked}, key=lambda path: path.as_posix())


def evaluate_scope(staged_paths: list[Path], dirty_paths: list[Path]) -> dict[str, Any]:
    staged_categories = Counter(classify_path(path) for path in staged_paths)
    dirty_categories = Counter(classify_path(path) for path in dirty_paths)
    staged_has_harness = any(category in HARNESS_CATEGORIES for category in staged_categories)
    blocking_categories = sorted(
        category
        for category in BLOCKING_MIX_CATEGORIES
        if staged_has_harness and staged_categories.get(category, 0)
    )
    return {
        "status": "fail" if blocking_categories else "pass",
        "staged_count": len(staged_paths),
        "dirty_count": len(dirty_paths),
        "staged_categories": dict(sorted(staged_categories.items())),
        "dirty_categories": dict(sorted(dirty_categories.items())),
        "blocking_categories": blocking_categories,
    }


def run_check(name: str, cmd: list[str], *, repo_root: Path = ROOT, timeout: int = 60) -> dict[str, Any]:
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "fail", "command": cmd, "message": str(exc)}
    return {
        "name": name,
        "status": "pass" if result.returncode == 0 else "fail",
        "command": cmd,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-1200:],
        "stderr_tail": result.stderr[-1200:],
    }


def changed_harness_python_files(paths: list[Path]) -> list[str]:
    selected = []
    for path in paths:
        if path.suffix != ".py":
            continue
        if classify_path(path) in {"harness_core", "harness_tests"}:
            selected.append(path.as_posix())
    return sorted(set(selected))


def validate_receipt_schema(repo_root: Path = ROOT) -> dict[str, Any]:
    eval_module = ROOT / "scripts" / "local_qwen_harness_eval.py"
    cmd = [
        "python3",
        "-c",
        (
            "import importlib.util, json, pathlib; "
            f"p=pathlib.Path({str(eval_module)!r}); "
            "s=importlib.util.spec_from_file_location('evalmod', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "print(json.dumps({'required_fields': len(m.REQUIRED_RECEIPT_FIELDS)}))"
        ),
    ]
    return run_check("eval_receipt_schema", cmd, repo_root=repo_root, timeout=20)


def bridge_status_contract_check(repo_root: Path = ROOT) -> dict[str, Any]:
    cmd = [
        "python3",
        "-c",
        (
            "import importlib.util, pathlib; "
            f"p=pathlib.Path({str(repo_root / 'scripts/tabbyapi_responses_proxy.py')!r}); "
            "s=importlib.util.spec_from_file_location('bridge', p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "payload=m.runtime_guard_status_payload(); "
            "assert payload['runtime_guard_version']=='local-qwen-runtime-guard-v1'; "
            "assert payload['guard_thresholds']['turn_tool_call_cap'] >= 1"
        ),
    ]
    return run_check("bridge_status_contract", cmd, repo_root=repo_root, timeout=20)


def run_harness_gate(repo_root: Path = ROOT) -> dict[str, Any]:
    staged, dirty = current_scope(repo_root)
    scope = evaluate_scope(staged, dirty)
    checks: list[dict[str, Any]] = []
    py_files = changed_harness_python_files([*staged, *dirty])
    if py_files:
        checks.append(run_check("py_compile_changed_harness", ["python3", "-m", "py_compile", *py_files], repo_root=repo_root))
    else:
        checks.append({"name": "py_compile_changed_harness", "status": "skip", "message": "no changed harness Python files"})
    checks.append(
        run_check(
            "shell_syntax_launchers",
            ["bash", "-n", "scripts/llm", "scripts/run_local_qwen_codex.sh", "scripts/run_codex_textgen_bridge.sh"],
            repo_root=repo_root,
        )
    )
    if shutil.which("ruff"):
        ruff_targets = changed_harness_python_files(staged) if staged else list(DEFAULT_RUFF_TARGETS)
        ruff_targets = [path for path in ruff_targets if (repo_root / path).exists()]
        checks.append(run_check("ruff_harness_f_e9_i", ["ruff", "check", "--select", "F,E9,I", *ruff_targets, "--ignore", "E501"], repo_root=repo_root))
    else:
        checks.append({"name": "ruff_harness_f_e9_i", "status": "skip", "message": "ruff not installed"})
    checks.append(bridge_status_contract_check(repo_root))
    checks.append(run_check("launcher_drift_validator", ["python3", "scripts/validate_local_qwen_project_launchers.py", "--json"], repo_root=repo_root))
    checks.append(run_check("local_harness_mcp_py_compile", ["python3", "-m", "py_compile", "scripts/artifact_queue_mcp.py"], repo_root=repo_root))
    checks.append(validate_receipt_schema(repo_root))
    failed_checks = [check["name"] for check in checks if check.get("status") == "fail"]
    functional_status = "fail" if failed_checks else "pass"
    drift_status = scope["status"]
    return {
        "schema_version": "local_qwen_harness_gate.v1",
        "functional_status": functional_status,
        "drift_status": drift_status,
        "success": functional_status == "pass" and drift_status == "pass",
        "scope": scope,
        "checks": checks,
        "failures": failed_checks + [f"staged scope blocks: {', '.join(scope['blocking_categories'])}"] if scope["blocking_categories"] else failed_checks,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run local-Qwen harness structural gate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = run_harness_gate(ROOT)
    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        print(f"functional_status: {data['functional_status']}")
        print(f"drift_status: {data['drift_status']}")
        for failure in data["failures"]:
            print(f"- {failure}")
    return 0 if data["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
