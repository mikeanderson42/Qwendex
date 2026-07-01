from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts/local_qwen_hook_audit.py"


def load_module():
    spec = importlib.util.spec_from_file_location("local_qwen_hook_audit_test", MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_hook_audit_reports_global_project_and_plugin_sources(tmp_path: Path) -> None:
    module = load_module()
    codex_home = tmp_path / "codex-home"
    plugin_root = tmp_path / "plugin-root"
    project = tmp_path / "project"
    codex_home.mkdir()
    plugin_root.mkdir()
    project.mkdir()
    (codex_home / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    (project / ".codex").mkdir()
    (project / ".codex" / "hooks.json").write_text('{"hooks":{"Stop":[]}}\n', encoding="utf-8")
    (plugin_root / "plugin-hooks.json").write_text('{"hooks":{"PostToolUse":[]}}\n', encoding="utf-8")

    report = module.audit_hooks(
        project_root=project,
        codex_home=codex_home,
        plugin_roots=[plugin_root],
    )

    assert report["schema_version"] == "local_qwen_hook_audit.v1"
    assert report["status"] == "pass"
    assert report["codex_hook_merge_model"] == "additive_concurrent"
    assert {source["scope"] for source in report["sources"]} == {"global", "project", "plugin"}
    assert report["forbidden_behaviors"]["hidden_llm_calls"] == "forbidden"
