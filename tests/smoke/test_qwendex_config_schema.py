from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts" / "validate_qwendex_config.py"
JSONSCHEMA_VERSION = "4.26.0"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def isolated_surface(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "config" / "qwendex", repo / "config" / "qwendex")
    shutil.copy2(ROOT / "RELEASE.md", repo / "RELEASE.md")
    return repo


def run_validator(repo: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--repo-root", str(repo), "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    return result, json.loads(result.stdout)


def error_codes(payload: dict[str, Any]) -> set[str]:
    return {str(item["code"]) for item in payload["errors"]}


def test_published_qwendex_configs_validate_against_draft_2020_12() -> None:
    result, payload = run_validator(ROOT)

    assert result.returncode == 0, result.stderr or result.stdout
    assert payload["status"] == "pass"
    assert payload["validator"] == "jsonschema.Draft202012Validator"
    assert payload["schema_draft"] == "https://json-schema.org/draft/2020-12/schema"
    assert payload["required_jsonschema_version"] == JSONSCHEMA_VERSION
    assert payload["observed_jsonschema_version"] == JSONSCHEMA_VERSION
    assert payload["published_configs"] == [
        "config/qwendex/qwendex.json",
        "config/qwendex/qwendex.sample.json",
    ]
    assert payload["errors"] == []


def test_validator_blocks_config_that_violates_published_schema(tmp_path: Path) -> None:
    repo = isolated_surface(tmp_path)
    config_path = repo / "config" / "qwendex" / "qwendex.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["routing"]["fallback_seat"] = "qwen"
    write_json(config_path, config)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert payload["status"] == "blocked"
    assert "config_schema_violation" in error_codes(payload)
    assert any("fallback_seat" in item["path"] for item in payload["errors"])


@pytest.mark.parametrize(
    ("keys", "value", "needle"),
    [
        (("sandbox", "trusted_roots"), ["."], "trusted_roots"),
        (("eval", "mode"), "live-required", "mode"),
        (("eval", "live_requires_running_stack"), True, "live_requires_running_stack"),
        (("learning", "auto_harvest"), True, "auto_harvest"),
        (("learning", "codex_budget_requires_approval"), True, "codex_budget_requires_approval"),
        (("learning", "mode"), "manual", "mode"),
        (("guard", "profile"), "max_safety", "profile"),
        (("orchestration", "shortcut"), "Alt+X", "shortcut"),
        (("orchestration", "max_subagents"), 3, "max_subagents"),
        (("orchestration", "mode_order"), ["off"], "mode_order"),
        (("orchestration", "estimator"), {"enabled": False}, "estimator"),
        (("orchestration", "close_stale_policy"), "close all", "close_stale_policy"),
        (("orchestration", "local_subagents", "shortcut_command"), "false", "shortcut_command"),
        (("orchestration", "mode_profiles", "auto", "offload_target"), "100%", "offload_target"),
        (("seats", "qwen", "model"), "other-local-model", "model"),
        (("seats", "primary", "authority"), "read_only_review", "authority"),
        (("seats", "custom"), {"model": "gpt-5.5"}, "custom"),
    ],
)
def test_removed_or_unsupported_controls_are_rejected_by_published_schema(
    tmp_path: Path,
    keys: tuple[str, ...],
    value: Any,
    needle: str,
) -> None:
    repo = isolated_surface(tmp_path)
    config_path = repo / "config" / "qwendex" / "qwendex.sample.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    target = config
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    write_json(config_path, config)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert "config_schema_violation" in error_codes(payload)
    assert any(
        needle in f"{item['path']} {item['message']}"
        for item in payload["errors"]
    )


def test_validator_blocks_invalid_semver_and_published_version_drift(tmp_path: Path) -> None:
    repo = isolated_surface(tmp_path)
    default_path = repo / "config" / "qwendex" / "qwendex.json"
    default = json.loads(default_path.read_text(encoding="utf-8"))
    default["version"] = "01.5.0"
    write_json(default_path, default)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert "invalid_semver" in error_codes(payload)

    default["version"] = "0.5.1"
    write_json(default_path, default)
    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert "published_version_mismatch" in error_codes(payload)
    assert "release_version_mismatch" in error_codes(payload)


def test_validator_blocks_compact_limit_at_or_above_context_window(tmp_path: Path) -> None:
    repo = isolated_surface(tmp_path)
    config_path = repo / "config" / "qwendex" / "qwendex.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["seats"]["sandbox"]["compact_limit"] = config["seats"]["sandbox"][
        "context_window"
    ]
    write_json(config_path, config)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert "invalid_context_budget" in error_codes(payload)
    assert any("sandbox/compact_limit" in item["path"] for item in payload["errors"])


def test_validator_blocks_jsonschema_dependency_pin_drift(tmp_path: Path) -> None:
    repo = isolated_surface(tmp_path)
    dependencies_path = repo / "config" / "qwendex" / "dependencies.json"
    dependencies = json.loads(dependencies_path.read_text(encoding="utf-8"))
    dependencies["validation_python_module_versions"]["jsonschema"] = "0.0.0"
    write_json(dependencies_path, dependencies)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert "dependency_version_mismatch" in error_codes(payload)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("draft", "schema_draft_mismatch"),
        ("identifier", "schema_id_mismatch"),
        ("meta_schema", "invalid_schema"),
        ("schema_version", "schema_id_mismatch"),
        ("external_ref", "unbounded_schema_reference"),
    ],
)
def test_validator_blocks_published_schema_drift(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    repo = isolated_surface(tmp_path)
    schema_path = repo / "config" / "qwendex" / "qwendex.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if mutation == "draft":
        schema["$schema"] = "http://json-schema.org/draft-07/schema#"
    elif mutation == "identifier":
        schema["$id"] = "https://qwendex.local/schema/qwendex.config.v2.json"
    elif mutation == "meta_schema":
        schema["type"] = "not-a-json-schema-type"
    elif mutation == "schema_version":
        schema["properties"]["schema_version"]["const"] = "qwendex.config.v2"
    elif mutation == "external_ref":
        schema["properties"]["routing"]["$ref"] = "https://example.invalid/routing.json"
    else:  # pragma: no cover - keeps additions to the parameter table explicit
        raise AssertionError(mutation)
    write_json(schema_path, schema)

    result, payload = run_validator(repo)

    assert result.returncode == 1
    assert payload["status"] == "blocked"
    assert expected_code in error_codes(payload)


def test_jsonschema_pin_and_schema_gate_are_wired_end_to_end() -> None:
    dependencies = json.loads(
        (ROOT / "config" / "qwendex" / "dependencies.json").read_text(encoding="utf-8")
    )
    installer = (ROOT / "scripts" / "qwendex_install_deps").read_text(encoding="utf-8")
    dev_env = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "jsonschema" in dependencies["validation_python_modules"]
    assert (
        dependencies["validation_python_module_versions"]["jsonschema"]
        == JSONSCHEMA_VERSION
    )
    assert 'QWENDEX_JSONSCHEMA_REQUIRED_VERSION:-4.26.0' in installer
    assert '"jsonschema==$QWENDEX_JSONSCHEMA_REQUIRED_VERSION"' in installer
    assert '"jsonschema": os.environ.get(' in installer
    assert '"python_pip_policy": {' in installer
    assert 'python3 "$DEV_ROOT/scripts/validate_qwendex_config.py"' in dev_env
    assert workflow.count("jsonschema==4.26.0") == 2
    assert workflow.count("scripts/validate_qwendex_config.py --json") == 2


def extract_shell_function(script: str, name: str) -> str:
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}\n", script)
    assert match, f"missing shell function: {name}"
    return match.group(0)


def pip_install_command_for_policy(installer: str, *, externally_managed: bool) -> str:
    functions = "\n".join(
        extract_shell_function(installer, name)
        for name in ("python_externally_managed", "install_python_tools")
    )
    probe_rc = 0 if externally_managed else 1
    shell = f"""
set -euo pipefail
MODE=install
INSTALL_USER=1
LOG_FILE=/dev/null
QWENDEX_JSONSCHEMA_REQUIRED_VERSION=4.26.0
QWENDEX_PYTEST_REQUIRED_VERSION=9.0.3
QWENDEX_RUFF_REQUIRED_VERSION=0.15.20
python_compatible() {{ return 0; }}
python3() {{
  if [[ "${{1:-}}" == "-" ]]; then
    return {probe_rc}
  fi
  return 0
}}
run_logged() {{ printf '%s\\n' "$*"; }}
{functions}
install_python_tools
"""
    result = subprocess.run(
        ["bash", "-c", shell],
        text=True,
        capture_output=True,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_installer_adds_pep668_override_only_for_user_site_on_managed_python() -> None:
    installer = (ROOT / "scripts" / "qwendex_install_deps").read_text(encoding="utf-8")

    ordinary = pip_install_command_for_policy(installer, externally_managed=False)
    managed = pip_install_command_for_policy(installer, externally_managed=True)

    assert "--user" in ordinary
    assert "--break-system-packages" not in ordinary
    assert "--user" in managed
    assert "--break-system-packages" in managed
    assert "--prefix" not in managed
    assert "--target" not in managed


def test_installer_pep668_probe_executes_on_managed_python(tmp_path: Path) -> None:
    installer = (ROOT / "scripts" / "qwendex_install_deps").read_text(encoding="utf-8")
    probe = extract_shell_function(installer, "python_externally_managed")
    marker_stdlib = tmp_path / "stdlib"
    marker_stdlib.mkdir()
    (marker_stdlib / "EXTERNALLY-MANAGED").touch()
    sitecustomize = tmp_path / "sitecustomize.py"
    sitecustomize.write_text(
        "\n".join(
            [
                "import sys",
                "import sysconfig",
                "sys.prefix = sys.base_prefix",
                "_original_get_path = sysconfig.get_path",
                "def _get_path(name, *args, **kwargs):",
                f"    return {str(marker_stdlib)!r} if name == 'stdlib' else _original_get_path(name, *args, **kwargs)",
                "sysconfig.get_path = _get_path",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = f"{tmp_path}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{probe}\npython_externally_managed"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, "PEP 668 probe failed or hid a Python error"


def test_installer_receipt_records_interpreter_specific_user_pip_policy() -> None:
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    result = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_install_deps"), "--check", "--json"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    payload = json.loads(result.stdout)
    policy = payload["python_pip_policy"]
    stdlib = sysconfig.get_path("stdlib")
    expected_managed = bool(
        sys.prefix == sys.base_prefix
        and stdlib
        and (Path(stdlib) / "EXTERNALLY-MANAGED").is_file()
    )

    assert policy == {
        "break_system_packages": expected_managed,
        "externally_managed": expected_managed,
        "scope": "user",
        "system_site_writes": False,
    }
