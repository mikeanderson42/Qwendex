import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[2]
QWENDEX = ROOT / "scripts" / "qwendex"
QWENDEX_MODULE = ROOT / "scripts" / "qwendex_cli.py"


_AMBIENT_QWENDEX_RUNTIME_KEYS = {
    "CODEX_AGENT_USE",
    "QWENDEX_EFFECTIVE_AGENT_USE",
    "QWENDEX_KAVEMAN_ENABLED",
    "QWENDEX_KAVEMAN_DIRECTIVE",
    "QWENDEX_LOCAL_SUBAGENTS",
    "QWENDEX_ORCHESTRATION_MODE",
    "QWENDEX_OUTPUT_POLICY",
    "QWENDEX_QDEX_PERMISSION_MODE",
    "QWENDEX_QDEX_PERMISSION_SOURCE",
    "QWENDEX_QDEX_LAUNCH_ID",
    "QWENDEX_QDEX_LAUNCH_POLICY_HASH",
    "QWENDEX_QDEX_LAUNCH_MODE",
    "QWENDEX_QDEX_LAUNCH_AGENT_USE",
    "QWENDEX_QDEX_LAUNCH_MAX_WORKERS",
    "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED",
    "QWENDEX_RUN_ID",
}


def isolated_qwendex_runtime_env(overrides=None):
    """Keep a parent managed Qdex launch out of direct CLI fixtures."""
    environment = dict(os.environ)
    for key in tuple(environment):
        if key in _AMBIENT_QWENDEX_RUNTIME_KEYS or key.startswith(
            ("QWENDEX_AGENT_", "QWENDEX_MANAGER_")
        ):
            environment.pop(key)
    environment.update(overrides or {})
    return environment


def load_qwendex():
    spec = importlib.util.spec_from_file_location("qwendex_cli_test", QWENDEX_MODULE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_script_module(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_qwendex(*args, env=None):
    result = subprocess.run(
        [str(QWENDEX), *args],
        cwd=ROOT,
        env=isolated_qwendex_runtime_env(env),
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result


def run_qwendex_concurrently(argument_sets, *, env):
    worker = (
        "import os, sys\n"
        "print('READY', flush=True)\n"
        "sys.stdin.read(1)\n"
        "os.execve(sys.argv[1], sys.argv[1:], os.environ)\n"
    )
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", worker, str(QWENDEX), *arguments],
            cwd=ROOT,
            env=isolated_qwendex_runtime_env(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for arguments in argument_sets
    ]
    try:
        for process in processes:
            assert process.stdout is not None
            assert process.stdout.readline().strip() == "READY"
        for process in processes:
            assert process.stdin is not None
            process.stdin.write("1")
            process.stdin.flush()
            process.stdin.close()
        results = []
        for process in processes:
            returncode = process.wait(timeout=60)
            assert process.stdout is not None
            assert process.stderr is not None
            results.append(
                subprocess.CompletedProcess(
                    process.args,
                    returncode,
                    process.stdout.read(),
                    process.stderr.read(),
                )
            )
        return results
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def json_result(*args, env=None):
    result = run_qwendex(*args, env=env)
    assert result.returncode == 0, result.stderr or result.stdout
    return parse_json_result(result)


def parse_json_result(result):
    data = json.loads(result.stdout)
    for key in ("status", "summary", "version", "artifacts", "next_actions", "errors"):
        assert key in data
    return data


def qdex_v2_config_values(args):
    return [
        args[index + 1]
        for index, item in enumerate(args[:-1])
        if item == "--config"
    ]


def assert_qdex_v2_policy_prefix(args, *, expected_native_threads=None):
    assert args[0] == "--no-alt-screen"
    values = qdex_v2_config_values(args)
    assert "features.multi_agent_v2.enabled=true" in values
    if expected_native_threads is not None:
        assert (
            f"features.multi_agent_v2.max_concurrent_threads_per_session={expected_native_threads}"
            in values
        )
    else:
        assert any(
            value.startswith("features.multi_agent_v2.max_concurrent_threads_per_session=")
            for value in values
        )
    for field in (
        "min_wait_timeout_ms",
        "max_wait_timeout_ms",
        "default_wait_timeout_ms",
    ):
        assert any(value.startswith(f"features.multi_agent_v2.{field}=") for value in values)
    for field in (
        "multi_agent_mode_hint_text",
        "root_agent_usage_hint_text",
        "subagent_usage_hint_text",
    ):
        assert any(
            value.startswith(f"features.multi_agent_v2.{field}=")
            for value in values
        )


def assert_qdex_caller_args_before_policy(args, expected):
    start = next(
        index
        for index in range(len(args) - len(expected) + 1)
        if args[index : index + len(expected)] == expected
    )
    assert args[start + len(expected)] == "--config"


def with_live_manager_identity(env):
    qwendex = load_qwendex()
    pid = os.getpid()
    start_ticks = qwendex.process_start_ticks(pid)
    assert start_ticks
    return {
        **env,
        "QWENDEX_MANAGER_LAUNCH_PID": str(pid),
        "QWENDEX_MANAGER_LAUNCH_START_TICKS": start_ticks,
    }


def test_qwendex_parser_exposes_public_commands():
    qwendex = load_qwendex()
    parser = qwendex.command_line()

    assert parser.parse_args(["check"]).command == "check"
    assert parser.parse_args(["check", "--health-mode", "advisory"]).health_mode == "advisory"
    assert parser.parse_args(["check", "--health-mode", "strict"]).health_mode == "strict"
    assert parser.parse_args(["up", "--dry-run"]).command == "up"
    assert parser.parse_args(["down", "--dry-run"]).command == "down"
    assert parser.parse_args(["restart", "--dry-run"]).command == "restart"
    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["doctor", "--health-mode", "advisory"]).health_mode == "advisory"
    assert parser.parse_args(["doctor", "--health-mode", "strict"]).health_mode == "strict"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK"]).command == "exec"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK", "--seat", "auto"]).seat == "auto"
    assert parser.parse_args(["exec", "Reply exactly QWENDEX_OK", "--cwd", "/tmp/qwendex-project"]).cwd == "/tmp/qwendex-project"
    assert parser.parse_args(["eval", "--case", "exact_marker"]).command == "eval"
    assert parser.parse_args(["receipt", "latest"]).command == "receipt"
    assert parser.parse_args(["route"]).command == "route"
    assert parser.parse_args(["seat", "qwen"]).command == "seat"
    assert parser.parse_args(["task", "create", "--title", "T"]).command == "task"
    assert parser.parse_args(["context", "snapshot", "--task-id", "task_1"]).command == "context"
    assert parser.parse_args(["handoff", "create", "--task-id", "task_1"]).command == "handoff"
    assert parser.parse_args(["evidence", "query", "--task-id", "task_1"]).command == "evidence"
    assert parser.parse_args(["queue", "status"]).command == "queue"
    assert parser.parse_args(["learn", "dry-run", "--backend", "mock"]).command == "learn"
    assert parser.parse_args(["manager", "--mode", "manager_only"]).command == "manager"
    assert parser.parse_args(["manager", "mode", "--set", "auto"]).action == "mode"
    assert parser.parse_args(["manager", "mode", "--toggle"]).toggle is True
    assert parser.parse_args(["manager", "kaveman", "--toggle"]).action == "kaveman"
    assert parser.parse_args(["manager", "local", "--set", "off"]).action == "local"
    assert parser.parse_args(["manager", "estimate", "--prompt", "Fix a typo"]).action == "estimate"
    assert parser.parse_args(["manager", "preflight", "--interactive-prompt-unknown", "--dry-run"]).action == "preflight"
    assert parser.parse_args(["manager", "launch-status", "--pid", "123", "--repo-root", "."]).pid == 123
    assert parser.parse_args(["manager", "reconcile", "--pending-validation", "--dry-run"]).action == "reconcile"
    assert parser.parse_args(["manager", "repair", "--safe"]).action == "repair"
    assert parser.parse_args(["manager", "repair", "--safe"]).safe is True
    assert parser.parse_args(["--agent-use", "Manager", "agent", "policy"]).agent_use == "Manager"
    assert parser.parse_args(["agent", "status"]).command == "agent"
    assert parser.parse_args(["agent", "inspect", "agent-1"]).target == "agent-1"
    assert parser.parse_args(["agent", "close", "all", "--timeout", "1s"]).action == "close"
    assert parser.parse_args(["agent", "profiles"]).action == "profiles"
    assert parser.parse_args(["agent", "team"]).action == "team"
    assert parser.parse_args(["agent", "plan", "--prompt", "Ship it"]).action == "plan"
    assert parser.parse_args(["agent", "hook", "Stop", "--event-json", "{}"]).action == "hook"
    assert parser.parse_args(["agent", "hook", "Stop", "--codex-hook-output"]).codex_hook_output is True
    assert parser.parse_args(["agent", "hook-config"]).action == "hook-config"
    assert parser.parse_args(["agent", "hook-config", "--install", "--codex-home", "/tmp/codex"]).install is True
    assert parser.parse_args(["agent", "hook-config", "--verify", "--codex-home", "/tmp/codex"]).verify is True
    assert parser.parse_args(["agent", "locks"]).action == "locks"
    assert parser.parse_args(["codex-status", "--write", "/tmp/qwendex-status.json"]).command == "codex-status"
    assert parser.parse_args(["codex-patch", "preflight", "--codex-bin", "codex"]).command == "codex-patch"
    assert parser.parse_args(["codex-patch", "apply", "--source", "/tmp/codex", "--dry-run"]).dry_run is True
    assert parser.parse_args(["estimate", "--prompt", "Fix a typo"]).command == "estimate"
    assert parser.parse_args(["performance", "status"]).command == "performance"
    assert parser.parse_args(["performance", "summary", "--repo-root", "/tmp/repo", "--since-days", "7"]).since_days == 7
    assert parser.parse_args(["performance", "runs", "--limit", "5"]).limit == 5
    assert parser.parse_args(["performance", "purge", "--approve"]).approve is True
    assert parser.parse_args(["performance", "benchmark", "--suite", "exploration"]).suite == "exploration"
    assert parser.parse_args(["llmstack", "check"]).command == "llmstack"
    assert parser.parse_args(["llmstack", "check"]).action == "check"
    assert parser.parse_args(["llmstack", "doctor"]).action == "doctor"
    assert parser.parse_args(["llmstack", "up", "--dry-run"]).action == "up"
    assert parser.parse_args(["llmstack", "down", "--dry-run"]).action == "down"
    restart = parser.parse_args(["llmstack", "restart", "bridge", "--dry-run"])
    assert restart.action == "restart"
    assert restart.service == "bridge"
    assert parser.parse_args(["version"]).command == "version"


def test_manager_runtime_identity_allows_in_place_qwendex_source_edits(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    runtime_source = tmp_path / "qwendex_cli.py"
    runtime_source.write_text("before managed edit\n", encoding="utf-8")
    monkeypatch.setattr(qwendex, "__file__", str(runtime_source))

    config = qwendex.deep_merge(
        qwendex.DEFAULT_CONFIG,
        {"state": {"db": str(tmp_path / "qwendex.sqlite")}},
    )
    state_identity, ledger_identity = qwendex.manager_store_identities(config)
    runtime_identity = qwendex.manager_runtime_identity()
    env = {
        qwendex.MANAGER_STATE_DB_IDENTITY_ENV: state_identity,
        qwendex.MANAGER_LEDGER_DB_IDENTITY_ENV: ledger_identity,
        qwendex.MANAGER_RUNTIME_IDENTITY_ENV: runtime_identity,
        qwendex.MANAGER_LAUNCH_KEY_ENV: "launch-key",
        qwendex.MANAGER_LAUNCH_NONCE_ENV: "launch-nonce",
    }
    decision = {
        "state_db_identity": state_identity,
        "ledger_db_identity": ledger_identity,
        "runtime_identity": runtime_identity,
        "launch_key": "launch-key",
        "launch_nonce": "launch-nonce",
    }

    runtime_source.write_text("after managed edit\n", encoding="utf-8")
    reason, details = qwendex.manager_decision_static_mismatch(
        config,
        decision,
        env=env,
    )

    assert reason == ""
    assert all(details.values())
    assert qwendex.manager_runtime_identity() == runtime_identity

    other_runtime = tmp_path / "other_qwendex_cli.py"
    other_runtime.write_text("different runtime location\n", encoding="utf-8")
    monkeypatch.setattr(qwendex, "__file__", str(other_runtime))
    assert qwendex.manager_runtime_identity() != runtime_identity


def test_qwendex_version_and_config_are_in_sync():
    qwendex = load_qwendex()
    project_config = json.loads((ROOT / "config" / "qwendex" / "qwendex.json").read_text(encoding="utf-8"))
    sample_config = json.loads((ROOT / "config" / "qwendex" / "qwendex.sample.json").read_text(encoding="utf-8"))
    version = json_result("version", "--json")

    assert qwendex.VERSION == "0.6.2"
    assert version["data"]["version"] == qwendex.VERSION
    assert project_config["version"] == qwendex.VERSION
    assert sample_config["version"] == qwendex.VERSION
    assert f"v{qwendex.VERSION}" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"## {qwendex.VERSION}" in (ROOT / "public" / "qwendex" / "release-notes.md").read_text(encoding="utf-8")


def test_qwendex_check_and_doctor_emit_stable_json(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    check = json_result("check", "--json", env=env)
    doctor = json_result("doctor", "--json", env=env)

    assert check["status"] == "pass"
    assert doctor["status"] == "pass"
    assert doctor["data"]["critical_issues"] == []
    assert "public/qwendex/README.md" in doctor["artifacts"]
    assert check["data"]["manager_estimate"]["mode"] == "auto"
    assert doctor["data"]["manager_estimate"]["reasoning_policy"]["main_session"]["reasoning_source"] == "user_selected"
    assert 1 <= len(check["data"]["high_value_add"]) <= 2
    assert 1 <= len(doctor["data"]["high_value_add"]) <= 2


def test_qwendex_check_and_doctor_report_stale_manager_state_without_failing(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    advisory_check = run_qwendex("check", "--health-mode", "advisory", "--json", env=env)
    advisory_doctor = run_qwendex("doctor", "--health-mode", "advisory", "--json", env=env)
    strict_check = run_qwendex("check", "--health-mode", "strict", "--json", env=env)
    strict_doctor = run_qwendex("doctor", "--health-mode", "strict", "--json", env=env)

    advisory_check_data = parse_json_result(advisory_check)
    advisory_doctor_data = parse_json_result(advisory_doctor)
    strict_check_data = parse_json_result(strict_check)
    strict_doctor_data = parse_json_result(strict_doctor)

    assert advisory_check.returncode == 0
    assert advisory_check_data["status"] == "pass"
    assert advisory_check_data["data"]["manager_health_mode"] == "advisory"
    assert "stale manager writer sessions" in " ".join(advisory_check_data["data"]["manager_health_issues"])
    assert advisory_doctor.returncode == 0
    assert advisory_doctor_data["status"] == "pass"
    assert advisory_doctor_data["data"]["manager_health_mode"] == "advisory"
    assert "stale manager writer sessions" in " ".join(advisory_doctor_data["data"]["manager_health_issues"])
    assert advisory_check_data["errors"] == []
    assert advisory_doctor_data["errors"] == []
    assert strict_check.returncode == 0
    assert strict_check_data["status"] == "pass"
    assert strict_check_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_check_data["data"]["manager_health_issues"])
    assert strict_doctor.returncode == 0
    assert strict_doctor_data["status"] == "pass"
    assert strict_doctor_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_doctor_data["data"]["manager_health_issues"])


def test_qwendex_llmstack_public_contract_and_dry_run_json(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    check = json_result("llmstack", "check", "--json", env=env)
    doctor = json_result("llmstack", "doctor", "--json", env=env)
    restart = json_result("llmstack", "restart", "bridge", "--dry-run", "--json", env=env)

    assert check["command"] == "llmstack"
    assert check["status"] == "pass"
    assert doctor["status"] == "pass"
    contract = check["data"]["contract"]
    for key in (
        "services_configured",
        "backend_endpoint",
        "model_alias",
        "codex_bridge",
        "guard_config",
        "receipts_results",
        "missing_optional_host_programs",
    ):
        assert key in contract
    assert contract["config"]["sample_config"] == "config/local_llm_stack/stack_manager.sample.json"
    assert contract["config"]["local_config"] == "config/local_llm_stack/stack_manager.local.json"
    assert contract["config"]["local_config_ignored"] is True
    assert contract["public_boundary"]["bundles_host_programs"] is False
    assert contract["public_boundary"]["bundles_model_weights"] is False
    assert contract["private_hits"] == []
    assert {"textgen", "litellm", "bridge"} <= set(contract["services_configured"])
    assert restart["status"] == "pass"
    assert restart["data"]["delegate"]["status"] == "ready"
    assert restart["data"]["delegate"]["command"][:3] == [str(ROOT / "scripts" / "llm"), "restart", "bridge"]


def test_llmstack_public_configs_are_copy_safe_and_connected():
    qwendex = load_qwendex()
    public_paths = [
        ROOT / "config/local_llm_stack/stack_manager.json",
        ROOT / "config/local_llm_stack/stack_manager.sample.json",
        ROOT / "config/local_llm_stack/profiles.example.json",
        ROOT / "config/local_llm_stack/litellm.local.yaml",
        ROOT / "config/local_llm_stack/litellm.textgen.local.yaml",
        ROOT / "config/local_llm_stack/textgen_cmd_flags.txt",
        ROOT / "scripts/run_textgen_safe_no_model.sh",
        ROOT / "scripts/run_llamacpp_qwen_gguf.sh",
        ROOT / "scripts/run_vllm_qwen_gguf.sh",
        ROOT / "scripts/run_koboldcpp_gguf.sh",
        ROOT / "scripts/qwendex_testbench",
        ROOT / "public/qwendex/testbench.md",
        ROOT / "llmstack",
        ROOT / "scripts/windows/open.ps1",
    ]
    forbidden_patterns = (
        r"(?<![A-Za-z0-9_.-])/home/[A-Za-z0-9_.-]+(?=/)",
        r"(?<![A-Za-z0-9_.-])/mnt/[a-z]/Users/[A-Za-z0-9_.-]+(?=/)",
    )

    for path in public_paths:
        assert path.exists(), path
        text = path.read_text(encoding="utf-8")
        for pattern in forbidden_patterns:
            assert re.search(pattern, text) is None, f"{pattern} leaked through {path.relative_to(ROOT)}"

    sample = json.loads((ROOT / "config/local_llm_stack/stack_manager.sample.json").read_text(encoding="utf-8"))
    active = json.loads((ROOT / "config/local_llm_stack/stack_manager.json").read_text(encoding="utf-8"))
    profiles = json.loads((ROOT / "config/local_llm_stack/profiles.example.json").read_text(encoding="utf-8"))

    assert active == sample
    assert sample["default_backend_profile"] == "example-llamacpp-qwen-coder-gguf-32k"
    assert {service["name"] for service in sample["services"]} == {"textgen", "litellm", "bridge"}
    assert {profile["backend_kind"] for profile in profiles["backend_profiles"]} >= {"textgen", "llamacpp-gguf", "vllm-gguf", "koboldcpp-gguf"}
    assert qwendex.llmstack_public_contract()["status"] == "pass"


def test_qwendex_config_precedence_and_profiles(tmp_path):
    qwendex = load_qwendex()
    project_config = tmp_path / "qwendex.json"
    project_config.write_text(
        json.dumps({"default_seat": "audit", "seats": {"qwen": {"context_window": 32768}}}),
        encoding="utf-8",
    )

    cfg = qwendex.load_qwendex_config(
        cli_overrides={"default_seat": "release"},
        env={"QWENDEX_DEFAULT_SEAT": "qwen"},
        project_config=project_config,
        user_config=tmp_path / "missing-user.json",
    )

    assert cfg["default_seat"] == "release"
    assert cfg["seats"]["qwen"]["context_window"] == 32768
    assert {"primary", "qwen", "audit", "release", "sandbox"} <= set(cfg["seats"])
    assert cfg["learning"]["mode"] == "stage_only"
    assert cfg["orchestration"]["mode"] == "auto"
    assert cfg["routing"]["mode"] == "token_saver"
    assert "state" in cfg
    assert "mcp_tools" not in cfg
    assert "prompt_template" not in cfg["seats"]["qwen"]


def test_qwendex_config_blocks_unknown_keys_and_secret_values(tmp_path):
    qwendex = load_qwendex()
    unknown_config = tmp_path / "unknown.json"
    removed_seat_config = tmp_path / "removed-seat-key.json"
    secret_config = tmp_path / "secret.json"
    unknown_config.write_text(json.dumps({"unknown": True}), encoding="utf-8")
    removed_seat_config.write_text(
        json.dumps({"seats": {"qwen": {"prompt_template": "unused.jinja"}}}),
        encoding="utf-8",
    )
    secret_config.write_text(json.dumps({"receipts": {"dir": "password=supersecretvalue123"}}), encoding="utf-8")

    try:
        qwendex.load_qwendex_config(project_config=unknown_config, user_config=tmp_path / "missing.json")
    except ValueError as exc:
        assert "unknown top-level key" in str(exc)
    else:
        raise AssertionError("unknown config key should fail")

    try:
        qwendex.load_qwendex_config(
            project_config=removed_seat_config,
            user_config=tmp_path / "missing.json",
        )
    except ValueError as exc:
        assert "unknown seats.qwen key: prompt_template" in str(exc)
    else:
        raise AssertionError("removed seat config key should fail")

    try:
        qwendex.load_qwendex_config(project_config=secret_config, user_config=tmp_path / "missing.json")
    except ValueError as exc:
        assert "secret-like keys or values" in str(exc)
    else:
        raise AssertionError("secret-like config value should fail")


@pytest.mark.parametrize(
    ("override", "expected_path"),
    [
        ({"sandbox": {"trusted_roots": ["."]}}, "sandbox.trusted_roots"),
        ({"eval": {"mode": "live-required"}}, "eval.mode"),
        ({"eval": {"live_requires_running_stack": True}}, "eval.live_requires_running_stack"),
        ({"learning": {"codex_budget_requires_approval": True}}, "learning.codex_budget_requires_approval"),
        ({"learning": {"mode": "manual"}}, "learning.mode"),
        ({"guard": {"profile": "max_safety"}}, "guard.profile"),
        ({"orchestration": {"manager_only_available": False}}, "orchestration.manager_only_available"),
        ({"orchestration": {"shortcut": "Alt+X"}}, "orchestration.shortcut"),
        ({"orchestration": {"shortcut_command": "false"}}, "orchestration.shortcut_command"),
        ({"orchestration": {"max_subagents": 3}}, "orchestration.max_subagents"),
        ({"orchestration": {"stale_after_minutes": 10}}, "orchestration.stale_after_minutes"),
        ({"orchestration": {"mode_order": ["off"]}}, "orchestration.mode_order"),
        ({"orchestration": {"estimator": {"enabled": False}}}, "orchestration.estimator"),
        ({"orchestration": {"close_stale_policy": "close all"}}, "orchestration.close_stale_policy"),
        ({"orchestration": {"auto_deploy_when": []}}, "orchestration.auto_deploy_when"),
        ({"orchestration": {"manager_responsibilities": []}}, "orchestration.manager_responsibilities"),
        ({"orchestration": {"borrowed_patterns": []}}, "orchestration.borrowed_patterns"),
        ({"orchestration": {"local_subagents": {"shortcut": "Alt+X"}}}, "orchestration.local_subagents.shortcut"),
        ({"orchestration": {"kaveman": {"shortcut_command": "false"}}}, "orchestration.kaveman.shortcut_command"),
        ({"orchestration": {"mode_profiles": {"auto": {"offload_target": "100%"}}}}, "orchestration.mode_profiles.auto.offload_target"),
        ({"seats": {"qwen": {"model": "other-local-model"}}}, "seats.qwen key: model"),
        ({"seats": {"primary": {"authority": "read_only_review"}}}, "seats.primary.authority"),
        ({"seats": {"qwen": {"backend": "codex"}}}, "seats.qwen.backend"),
        ({"seats": {"sandbox": {"compact_limit": 32768}}}, "seats.sandbox.compact_limit"),
        ({"seats": {"custom": {"model": "gpt-5.5"}}}, "unknown seat: custom"),
    ],
)
def test_removed_inert_config_controls_are_rejected_at_runtime(tmp_path, override, expected_path):
    qwendex = load_qwendex()
    config_path = tmp_path / "removed-control.json"
    config_path.write_text(json.dumps(override), encoding="utf-8")

    with pytest.raises(ValueError, match=re.escape(expected_path)):
        qwendex.load_qwendex_config(
            project_config=config_path,
            user_config=tmp_path / "missing.json",
        )


def test_qwendex_exact_exec_and_qwen_seat_write_reviewable_receipts(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--synthetic", "--json", env=env)
    primary_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "primary", "--synthetic", "--json", env=env)
    seat_data = json_result("seat", "qwen", "--json", env=env)

    exec_receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))
    primary_receipt = json.loads(Path(primary_data["artifacts"][0]).read_text(encoding="utf-8"))
    seat_receipt = json.loads(Path(seat_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert exec_data["data"]["output"] == "QWENDEX_OK"
    assert exec_receipt["task_class"] == "exec"
    assert exec_receipt["model"] == "qwen-local"
    assert exec_receipt["review_status"] == "synthetic_offline_only"
    assert exec_receipt["eval_result"] == "synthetic_not_evidence"
    assert exec_receipt["execution_performed"] is False
    assert exec_receipt["availability_evidence"] is False
    assert primary_receipt["seat"] == "primary"
    assert primary_receipt["model"] == "gpt-5.5"
    assert primary_receipt["review_status"] == "synthetic_offline_only"
    assert seat_receipt["seat"] == "qwen"
    assert seat_receipt["review_status"] == "configured_requires_gpt_review"
    assert seat_receipt["eval_result"] == "not_run"
    assert seat_receipt["availability"]["status"] == "not_probed"
    assert seat_receipt["markers"] == []
    assert seat_receipt["files_touched"]["status"] == "not_executed"
    assert exec_receipt["effective_policy"]["sandbox"]["mode"] == "workspace-write"
    assert "guard" in exec_receipt["effective_policy"]

    normal = json_result(
        "exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--dry-run", "--json",
        env={**env, "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0"},
    )
    assert normal["artifacts"] == []
    assert normal["data"]["execution_performed"] is False
    assert normal["data"]["availability_evidence"] is False
    assert normal["data"]["command"][:2] == ["codex", "exec"]
    assert "output" not in normal["data"]


def test_qwendex_exec_dry_run_respects_cwd_and_mcp_override(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    env = {
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "receipts"),
        "QWENDEX_STATE_DB": str(tmp_path / "state.sqlite"),
        "QWENDEX_MCP_TRUSTED_ROOTS": f"{ROOT}:{project}",
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    primary = json_result(
        "exec",
        "Summarize the project.",
        "--seat",
        "primary",
        "--cwd",
        str(project),
        "--dry-run",
        "--json",
        env=env,
    )
    qwen = json_result(
        "exec",
        "Summarize the project.",
        "--seat",
        "qwen",
        "--cwd",
        str(project),
        "--dry-run",
        "--json",
        env=env,
    )
    default_roots = json_result(
        "exec", "Summarize the project.", "--seat", "primary", "--cwd", str(project),
        "--dry-run", "--json",
        env={
            "QWENDEX_STATE_DB": str(tmp_path / "default-roots.sqlite"),
            "QWENDEX_MCP_TRUSTED_ROOTS": "",
        },
    )

    primary_cmd = primary["data"]["command"]
    qwen_cmd = qwen["data"]["command"]

    assert primary["data"]["seat"] == "primary"
    assert qwen["data"]["seat"] == "qwen"
    assert primary_cmd[primary_cmd.index("-C") + 1] == str(project)
    assert 'mcp_servers.local-harness.command="python3"' in " ".join(primary_cmd)
    assert "mcp_servers.local-harness.args" in " ".join(primary_cmd)
    assert str(ROOT / "scripts" / "artifact_queue_mcp.py") in " ".join(primary_cmd)
    assert f"{ROOT}:{project}" in " ".join(primary_cmd)
    assert qwen_cmd[qwen_cmd.index("--cwd") + 1] == str(project)
    assert qwen_cmd[qwen_cmd.index("--sandbox") + 1] == "workspace-write"
    assert "--minimal" in qwen_cmd
    assert "--ephemeral" in qwen_cmd
    assert qwen["data"]["execution_policy"]["tool_surface"]["local_harness_mcp_enabled"] is False
    trusted_root_arg = next(
        item
        for item in default_roots["data"]["command"]
        if item.startswith("mcp_servers.local-harness.env.ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS=")
    )
    assert trusted_root_arg.endswith(json.dumps(str(project)))
    assert default_roots["data"]["execution_policy"]["mcp_trusted_roots"] == [str(project)]


def test_qwendex_exec_infers_high_risk_authority_and_enforces_read_only_audit(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }
    security = json_result(
        "exec", "Review security authentication changes.", "--seat", "qwen", "--dry-run", "--json", env=env
    )
    public_docs = json_result(
        "exec", "Review public docs claims.", "--seat", "qwen", "--dry-run", "--json", env=env
    )
    explicit_protocol = json_result(
        "exec", "Review this change.", "--seat", "qwen", "--task-class", "protocol changes",
        "--dry-run", "--json", env=env,
    )
    attempted_security_downgrade = json_result(
        "exec", "Review security authentication changes.", "--seat", "qwen",
        "--task-class", "bounded patch", "--dry-run", "--json", env=env,
    )
    audit = json_result(
        "exec", "Inspect the repository.", "--seat", "audit", "--dry-run", "--json", env=env
    )
    sandbox = json_result(
        "exec", "Run an isolated probe.", "--seat", "sandbox", "--dry-run", "--json", env=env
    )

    for result, task_class in (
        (security, "security"),
        (public_docs, "public docs claims"),
        (explicit_protocol, "protocol changes"),
        (attempted_security_downgrade, "security"),
    ):
        assert result["data"]["seat"] == "primary"
        assert result["data"]["task_class"] == task_class
        assert result["data"]["routing"]["reason"] == "primary_authority_required"
        assert result["data"]["routing"]["local_qwen_eligible"] is False

    assert security["data"]["task_class_source"] == "prompt_primary_guard"
    assert attempted_security_downgrade["data"]["task_class_source"] == "prompt_primary_guard"
    assert explicit_protocol["data"]["task_class_source"] == "explicit"

    audit_command = audit["data"]["command"]
    assert audit["data"]["seat"] == "audit"
    assert audit_command[audit_command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in audit_command
    assert "mcp_servers.local-harness" not in " ".join(audit_command)
    assert audit["data"]["execution_policy"]["tool_surface"] == {
        "source": "codex_builtin_read_only",
        "local_harness_mcp_enabled": False,
        "user_config_enabled": False,
        "write_capable": False,
    }
    sandbox_command = sandbox["data"]["command"]
    assert sandbox["data"]["seat"] == "sandbox"
    assert sandbox_command[sandbox_command.index("--sandbox") + 1] == "read-only"
    assert sandbox["data"]["execution_policy"]["authority"] == "isolated_probe"
    assert sandbox["data"]["execution_policy"]["tool_surface"]["write_capable"] is False


def test_qwendex_exec_honors_global_read_only_sandbox_config(tmp_path):
    project_config = tmp_path / "qwendex.json"
    project_config.write_text(json.dumps({"sandbox": {"mode": "read-only"}}), encoding="utf-8")
    result = json_result(
        "--config", str(project_config), "exec", "Inspect bounded code.", "--seat", "primary",
        "--dry-run", "--json",
        env={"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")},
    )

    command = result["data"]["command"]
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in command
    assert "mcp_servers.local-harness" not in " ".join(command)
    assert result["data"]["execution_policy"]["tool_surface"]["write_capable"] is False


def test_qwendex_exec_feeds_policy_to_child_and_labels_unobserved_evidence(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    child_record = tmp_path / "child.json"
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
Path(os.environ["QWENDEX_CHILD_RECORD"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "guard_profile": os.environ.get("QWENDEX_GUARD_PROFILE"),
    "max_wall": os.environ.get("QWENDEX_MAX_WALL_TIME_SECONDS"),
    "max_tools": os.environ.get("QWENDEX_MAX_TOOL_CALLS"),
    "context_window": os.environ.get("QWENDEX_CONTEXT_WINDOW"),
    "compact_limit": os.environ.get("QWENDEX_COMPACT_LIMIT"),
    "max_output": os.environ.get("QWENDEX_MAX_OUTPUT_TOKENS"),
    "tool_output": os.environ.get("QWENDEX_TOOL_OUTPUT_TOKEN_LIMIT"),
}), encoding="utf-8")
print("bounded result")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_CHILD_RECORD": str(child_record),
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "receipts"),
    }
    result = json_result(
        "exec", "Inspect bounded code.", "--seat", "primary", "--timeout", "30", "--json", env=env
    )
    child = json.loads(child_record.read_text(encoding="utf-8"))
    receipt = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))

    assert child["guard_profile"] == "balanced"
    assert child["max_wall"] == "-1"
    assert child["max_tools"] == "-1"
    assert child["context_window"] == "200000"
    assert child["compact_limit"] == "56000"
    assert child["max_output"] == "2048"
    assert child["tool_output"] == "1200"
    assert "tool_output_token_limit=1200" in child["args"]
    assert "model_context_window=200000" in child["args"]
    assert "model_auto_compact_token_limit=56000" in child["args"]
    assert receipt["tool_calls"]["status"] == "not_observed"
    assert receipt["files_touched"]["status"] == "not_observed"
    assert receipt["execution_performed"] is True
    assert receipt["availability_evidence"] is True


def test_qwendex_exec_guard_markers_fail_even_with_zero_returncode(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$QWENDEX_TEST_MARKER\"\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    qwendex = load_qwendex()
    config = qwendex.load_qwendex_config()
    for index, marker in enumerate(config["guard"]["markers"]):
        result = run_qwendex(
            "exec", "Inspect bounded code.", "--seat", "primary", "--json",
            env={
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "QWENDEX_TEST_MARKER": marker,
                "QWENDEX_STATE_DB": str(tmp_path / f"state-{index}.sqlite"),
                "QWENDEX_RESULTS_ROOT": str(tmp_path / f"receipts-{index}"),
            },
        )
        payload = parse_json_result(result)
        receipt = json.loads(Path(payload["artifacts"][0]).read_text(encoding="utf-8"))
        assert result.returncode != 0, marker
        assert payload["status"] == "fail", marker
        assert payload["data"]["markers"] == [marker]
        assert "guard markers detected" in payload["errors"][0]
        assert receipt["eval_result"] == "fail"
        assert receipt["availability_evidence"] is False


def test_qwendex_sandbox_local_result_requires_gpt_review(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    config = qwendex.load_qwendex_config()
    config["state"]["db"] = str(tmp_path / "state.sqlite")
    config["receipts"]["dir"] = str(tmp_path / "receipts")
    monkeypatch.setattr(
        qwendex.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "sandbox result", ""),
    )
    args = qwendex.command_line().parse_args(
        ["exec", "Run an isolated probe.", "--seat", "sandbox", "--json"]
    )

    result = qwendex.command_exec(args, config)
    receipt = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))

    assert result["status"] == "pass"
    assert receipt["review_status"] == "requires_gpt_review"
    assert receipt["effective_policy"]["sandbox"]["mode"] == "read-only"
    assert receipt["execution_policy"]["tool_surface"]["write_capable"] is False


def test_qwendex_local_probe_and_exec_share_base_and_model(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    config = qwendex.load_qwendex_config()
    config["routing"]["local_probe_url"] = "http://127.0.0.1:43210/custom/v1/models"
    config["routing"]["local_model"] = "qwen-custom"
    config["state"]["db"] = str(tmp_path / "state.sqlite")
    config["receipts"]["dir"] = str(tmp_path / "receipts")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "custom local result", "")

    monkeypatch.setattr(qwendex.subprocess, "run", fake_run)
    args = qwendex.command_line().parse_args(
        ["exec", "Inspect bounded code.", "--seat", "qwen", "--json"]
    )
    result = qwendex.command_exec(args, config)
    receipt = json.loads(Path(result["artifacts"][0]).read_text(encoding="utf-8"))

    assert observed["env"]["LOCAL_QWEN_BASE"] == "http://127.0.0.1:43210/custom"
    assert observed["env"]["LOCAL_QWEN_MODEL"] == "qwen-custom"
    assert observed["command"][observed["command"].index("--sandbox") + 1] == "workspace-write"
    assert receipt["model"] == "qwen-custom"
    assert receipt["routing"]["model"] == "qwen-custom"
    assert receipt["execution_policy"]["local_probe_url"].endswith("/custom/v1/models")
    assert receipt["execution_policy"]["local_base_url"].endswith(":43210/custom")

    invalid = qwendex.load_qwendex_config()
    invalid["routing"]["local_probe_url"] = "http://127.0.0.1:43210/models"
    assert any(
        "must end with /v1/models" in failure
        for failure in qwendex.validate_qwendex_config(invalid)
    )


def test_qwendex_local_exec_minimal_mode_disables_mcp_and_uses_selected_sandbox():
    launcher = (ROOT / "scripts" / "run_local_qwen_codex.sh").read_text(encoding="utf-8")
    validator = load_script_module("validate_local_qwen_project_launchers")

    assert 'LOCAL_QWEN_CODEX_SANDBOX_MODE="${LOCAL_QWEN_CODEX_SANDBOX_MODE:-workspace-write}"' in launcher
    assert 'exec_args+=(--sandbox "$LOCAL_QWEN_CODEX_SANDBOX_MODE")' in launcher
    assert 'mcp_override_args=()' in launcher
    failures, warnings = validator.validate_central_launcher()
    assert failures == []
    assert warnings == []


def test_qwendex_testbench_public_surface_is_visible_and_sandboxed():
    script = ROOT / "scripts" / "qwendex_testbench"
    text = script.read_text(encoding="utf-8")

    assert script.exists()
    assert ">_ OpenAI Codex (v%s) /w Qwendex" in text
    assert "--no-alt-screen" in text
    assert "qwendex-local" in text
    assert "qwendex-full" in text
    assert "$BENCH_ROOT/bin" in text
    assert "qwendex-bench" in text
    assert "qwebdex-bench is a typo" in text
    assert "llmstack" in text
    assert "QWENDEX_BENCH_PROJECT" in text
    assert "QWENDEX_BENCH_ROOT" in text
    assert "QWENDEX_CODEX_STATUS_FILE" in text
    assert "$QWENDEX\" codex-status --plain" in text
    assert "Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready]" not in text
    assert "Local: [Y]" not in text
    assert "QWENDEX_MCP_TRUSTED_ROOTS" in text
    assert "codex-patch preflight" in text
    assert "codex-status --write" in text
    assert 'bench_command="$(shell_quote "$BENCH_CMD")"' in text
    assert '$bench_command env' in text
    assert "exec bash" in text


def test_qwendex_dev_env_public_surface_is_visible_and_isolated():
    script = ROOT / "scripts" / "qwendex_dev_env"
    text = script.read_text(encoding="utf-8")
    installer = ROOT / "scripts" / "qwendex_install_deps"
    installer_text = installer.read_text(encoding="utf-8")
    dependencies = json.loads((ROOT / "config" / "qwendex" / "dependencies.json").read_text(encoding="utf-8"))

    assert script.exists()
    assert installer.exists()
    assert os.access(installer, os.X_OK)
    assert dependencies["schema_version"] == "qwendex.dependencies.v1"
    assert {"bash", "python3", "git", "rsync", "curl", "codex"} <= set(dependencies["required_commands"])
    assert {"pytest", "ruff"} <= set(dependencies["validation_python_modules"])
    assert 'QWENDEX_CODEX_REQUIRED_VERSION:-0.144.6' in installer_text
    assert 'QWENDEX_CODEX_NPM_SPEC:-@openai/codex@$QWENDEX_CODEX_REQUIRED_VERSION' in installer_text
    assert 'npm install -g --prefix "$HOME/.local" "$codex_npm_spec"' in installer_text
    assert '"pytest==$QWENDEX_PYTEST_REQUIRED_VERSION"' in installer_text
    assert '"ruff==$QWENDEX_RUFF_REQUIRED_VERSION"' in installer_text
    assert "cargo install ripgrep --locked" in installer_text
    assert "QWENDEX_DEV_ROOT" in text
    assert "$HOME/qwendex-dev" in text
    assert "WORK_ROOT=\"$DEV_ROOT/.qwendex-dev\"" in text
    assert 'PERFORMANCE_DB="$WORK_ROOT/state/qwendex-performance.sqlite"' in text
    assert "QWENDEX_PERFORMANCE_DB" in text
    assert "performance_db_under_work_root" in text
    assert "INSTALL_DEPS_JSON" in text
    assert "qwendex_install_deps" in text
    assert "install_deps.json" in text
    assert "$WORK_ROOT/env.sh" in text
    assert "$HOME/.local/bin" in text
    assert "codex-main" in text
    assert "QWENDEX_DEV_CODEX_BIN" in text
    assert "QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG" in text
    assert "senior Qwendex product maintainer" in text
    assert "After context compaction" in text
    assert "resume from the newest user request" in text
    assert "qwendex-dev snapshot" in text
    assert "context snapshot/reminder/compact-plan" in text
    assert "At the start of substantial tasks" in text
    assert "check Agent Manager/Kaveman/Local state" in text
    assert "planning, lifecycle records, and validation suggestions as advisory" in text
    assert "Keep critical-path implementation local when appropriate" in text
    assert "integrate worker results before relying on them" in text
    assert "Qwendex Manager never authorizes prompts, root tools, publication, or final responses" in text
    assert "cmd_verify" in text
    assert "cmd_bootstrap" in text
    assert "cmd_doctor" in text
    assert "cmd_status_json" in text
    assert "hook_source_count" in text
    assert "active dev CODEX_HOME has no hooks" in text
    assert "cmd_codex_source" in text
    assert "cmd_clean" in text
    assert "cmd_repair_copy" in text
    assert "cmd_promote" in text
    assert "cmd_stage" in text
    assert "cmd_snapshot" in text
    assert "cmd_open_yolo" in text
    assert "QWENDEX_CODEX_YOLO_FLAG" in text
    assert "--dangerously-bypass-approvals-and-sandbox" in text
    assert "verify --tier quick|full|live|release" in text
    assert "QWENDEX_LEDGER_DB" in text
    assert "LOCAL_QWEN_HARNESS_LEDGER_DB" in text
    assert "release_verify_qwendex.sqlite" in text
    assert "dev_status.json" in text
    assert "release_validation_summary.json" in text
    assert "qwendex_release_gate.py" in text
    assert "static_gate.json" in text
    assert "test_gate.json" in text
    assert "config_gate.json" in text
    assert "codex_build.json" in text
    assert "--run-id" in text
    assert "--run-started-at" in text
    assert "llmstack_check.json" in text
    assert "codex-patch apply" in text
    assert re.search(
        r"cargo build\s+--locked\s+--release\s+-p codex-cli\s+"
        r"-p codex-code-mode-host\s+--bin codex\s+--bin codex-code-mode-host",
        text.replace("\\\n", " "),
    )
    assert "Codex code-mode host is missing or not executable" in text
    assert "status_line = [\"model-with-reasoning\", \"current-dir\", \"qwendex-manager\"]" in text
    assert "qwendex_toggle_manager = \"alt-m\"" in text
    assert "qwendex_toggle_kaveman = \"alt-k\"" in text
    assert "qwendex_toggle_local = \"alt-l\"" in text
    assert "codex-source sync|patch|build|preflight" in text
    assert "open.ps1" in text
    assert "--exclude '.git/'" in text
    assert "--exclude 'results/'" in text
    assert "--exclude '/bin/'" in text
    assert "git -C \"$DEV_ROOT\" add -A" in text

    doc = (ROOT / "public" / "qwendex" / "dev-environment.md").read_text(encoding="utf-8")
    assert "scripts/qwendex_dev_env sync" in doc
    assert "scripts/qwendex_install_deps --install" in doc
    assert "install_deps.json" in doc
    assert "qwendex-dev bootstrap" in doc
    assert "qwendex-dev doctor" in doc
    assert "qwendex-dev verify --tier release" in doc
    assert "qwendex-dev stage" in doc
    assert "qwendex-dev codex-source patch" in doc
    assert "Bare `qwendex-dev` opens Codex" in doc
    assert "--dangerously-bypass-approvals-and-sandbox" in doc
    assert "repair-copy" in doc
    assert "~/qwendex-dev" in doc
    assert "fallback execution plane" in doc
    assert "codex-code-mode-host" in doc
    assert "QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG=1" in doc
    assert "patched-tui.example.toml" in doc
    assert 'local patch_codex="${QWENDEX_DEV_CODEX_BIN:-$DEV_CODEX_DEFAULT}"' in text
    assert 'if [[ ! -x "$patch_codex" ]]; then' in text
    assert 'patch_codex="$MAIN_CODEX_BIN"' in text
    assert "cargo metadata --format-version 1 >/dev/null" in text
    assert "cargo metadata --no-deps --format-version 1" not in text
    assert "diff HEAD --binary --full-index --no-ext-diff" in text
    assert '"diff", "HEAD", "--binary", "--full-index", "--no-ext-diff"' in text

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "Connectedness Rule" in agents
    assert "state source or config field" in agents
    assert "Stop and repair" in agents

    startup = (ROOT / "QWENDEX_STARTUP.md").read_text(encoding="utf-8")
    assert "Connectedness Check" in startup
    assert "patched Codex build" in startup
    assert "Bare `qwendex-dev` opens Codex" in startup

    maintainer_skill = (ROOT / ".codex" / "skills" / "qwendex-dev-maintainer" / "SKILL.md").read_text(encoding="utf-8")
    assert "connectedness chain" in maintainer_skill
    assert "Stop-The-Line Conditions" in maintainer_skill

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".qwendex-dev/" in gitignore

    for rel in (
        "docs/development/architecture-map.md",
        "docs/development/failure-modes.md",
        "docs/development/release-runbook.md",
        "docs/development/contribution-workflow.md",
        "docs/development/decision-log.md",
        ".codex/skills/qwendex-dev-maintainer/SKILL.md",
        ".codex/skills/qwendex-release-gate/SKILL.md",
        ".codex/skills/qwendex-local-bridge-triage/SKILL.md",
        ".codex/skills/qwendex-codex-patch/SKILL.md",
    ):
        assert (ROOT / rel).exists(), rel


def test_qwendex_install_deps_check_rejects_wrong_executable_codex(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    install_log = tmp_path / "install.log"
    fake_home.mkdir()
    fake_bin.mkdir()
    fake_codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 9.9.9\\n'\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_")
    }
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QWENDEX_INSTALL_LOG": str(install_log),
        }
    )

    result = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_install_deps"), "--check", "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["required_codex_version"] == "0.144.6"
    assert payload["codex_compatible"] is False
    assert payload["tools"]["codex"]["path"] == str(fake_codex)
    assert payload["tools"]["codex"]["version"] == "codex-cli 9.9.9"
    assert payload["incompatible_required"] == [
        "codex version 'codex-cli 9.9.9' does not match required 'codex-cli 0.144.6'"
    ]


def test_qwendex_install_deps_check_rejects_codex_version_with_extra_tokens(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    fake_home.mkdir()
    fake_bin.mkdir()
    fake_codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.6 extra\\n'\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_")
    }
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QWENDEX_INSTALL_LOG": str(tmp_path / "install.log"),
        }
    )

    result = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_install_deps"), "--check", "--json"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["codex_compatible"] is False
    assert payload["tools"]["codex"]["normalized_output"] == (
        "codex-cli 0.144.6 extra"
    )
    assert payload["incompatible_required"] == [
        "codex version 'codex-cli 0.144.6 extra' does not match required "
        "'codex-cli 0.144.6'"
    ]


def test_qwendex_install_deps_failed_npm_logs_real_rc_and_stays_blocked(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    fake_home.mkdir()
    fake_bin.mkdir()

    scripts = {
        "codex": "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.6 extra\\n'\n",
        "npm": "#!/usr/bin/env bash\nexit 37\n",
        "python3": (
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == \"-m\" && \"${2:-}\" == \"pip\" ]]; then\n"
            "  exit 0\n"
            "fi\n"
            f'exec "{sys.executable}" "$@"\n'
        ),
        "cargo": "#!/usr/bin/env bash\nexit 0\n",
        "rustfmt": "#!/usr/bin/env bash\nexit 0\n",
        "rg": "#!/usr/bin/env bash\nexit 0\n",
    }
    for name, text in scripts.items():
        path = fake_bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)

    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_")
    }
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QWENDEX_INSTALL_LOG": str(install_log),
        }
    )

    result = subprocess.run(
        [
            str(ROOT / "scripts" / "qwendex_install_deps"),
            "--install",
            "--no-system",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 1, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert payload["codex_compatible"] is False
    log_text = install_log.read_text(encoding="utf-8")
    assert (
        f"command failed (37): npm install -g --prefix {fake_home / '.local'} "
        "@openai/codex@0.144.6"
    ) in log_text


def test_qwendex_install_deps_requests_system_python_when_version_is_too_old(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    apt_log = tmp_path / "apt.log"
    fake_home.mkdir()
    fake_bin.mkdir()
    scripts = {
        "python3": (
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == \"-c\" ]]; then exit 1; fi\n"
            "if [[ \"${1:-}\" == \"-m\" && \"${2:-}\" == \"pip\" ]]; then exit 0; fi\n"
            f'exec "{sys.executable}" "$@"\n'
        ),
        "apt-get": (
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"$QWENDEX_TEST_APT_LOG\"\n"
        ),
        "id": (
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == \"-u\" ]]; then printf '0\\n'; else exec /usr/bin/id \"$@\"; fi\n"
        ),
        "codex": "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.6\\n'\n",
    }
    for name, text in scripts.items():
        path = fake_bin / name
        path.write_text(text, encoding="utf-8")
        path.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("QWENDEX_")
    }
    env.update(
        {
            "HOME": str(fake_home),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "QWENDEX_INSTALL_LOG": str(tmp_path / "install.log"),
            "QWENDEX_TEST_APT_LOG": str(apt_log),
        }
    )

    subprocess.run(
        [
            "bash",
            str(ROOT / "scripts" / "qwendex_install_deps"),
            "--install",
            "--no-user",
            "--json",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    install_commands = apt_log.read_text(encoding="utf-8").splitlines()
    assert any(
        command.startswith("install -y ") and "python3" in command.split()
        for command in install_commands
    )


def test_public_quickstart_pre_v050_rollback_uses_distinct_roots():
    quickstart = (ROOT / "public" / "qwendex" / "quickstart.md").read_text(
        encoding="utf-8"
    )
    normalized = " ".join(quickstart.split())

    assert "Releases before `0.5.0` did not support the same-root checkout layout safely." in quickstart
    assert (
        "git -C ~/qwendex-v0.4-source worktree add --detach "
        "~/qwendex-v0.4-runtime v0.4.0"
    ) in quickstart
    assert "QWENDEX_DEV_SOURCE_ROOT=~/qwendex-v0.4-source" in quickstart
    assert "QWENDEX_DEV_ROOT=~/qwendex-v0.4-runtime" in quickstart
    assert "~/qwendex-v0.4-runtime/scripts/qwendex_dev_env sync" in quickstart
    assert "source ~/qwendex-v0.4-runtime/.qwendex-dev/env.sh" in quickstart
    assert "detached runtime worktree keeps v0.4's git-worktree health contract intact" in normalized
    assert "avoid importing a newer Codex home or SQLite ledger" in normalized


def same_root_dev_env_fixture(tmp_path):
    fake_home = tmp_path / "home"
    checkout = fake_home / "qwendex-dev"
    external_bin = tmp_path / "external-bin"
    fake_codex = external_bin / "codex"
    fake_home.mkdir()
    external_bin.mkdir()
    shutil.copytree(
        ROOT,
        checkout,
        ignore=shutil.ignore_patterns(
            ".git",
            ".qwendex-dev",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "*.pyc",
        ),
    )
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    fake_codex.write_text(
        """#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then
  printf 'codex-cli 0.144.6\\n'
fi
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("QWENDEX_", "LOCAL_QWEN_", "CODEX_"))
    }
    env.update(
        {
            "HOME": str(fake_home),
            "XDG_CONFIG_HOME": str(fake_home / ".config"),
            "PATH": f"{external_bin}:{os.environ['PATH']}",
        }
    )
    return fake_home, checkout, fake_codex, env


def test_qwendex_dev_env_same_root_writes_one_parseable_project_table(tmp_path):
    _, checkout, _, env = same_root_dev_env_fixture(tmp_path)

    sync = subprocess.run(
        [str(checkout / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert sync.returncode == 0, sync.stderr or sync.stdout
    config_path = checkout / ".qwendex-dev" / "codex_home" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    config = tomllib.loads(config_text)
    assert config["model"] == "gpt-5.6-terra"
    assert config["model_reasoning_effort"] == "max"
    assert config_text.count(f'[projects."{checkout}"]') == 1
    assert config["projects"] == {str(checkout): {"trust_level": "trusted"}}


def test_release_verification_status_write_is_run_scoped(tmp_path):
    shared_status = tmp_path / "operator-codex-status.json"
    shared_status.write_text("operator status must remain intact\n", encoding="utf-8")
    run_meta = tmp_path / "release-run"
    run_meta.mkdir()
    state_db = tmp_path / "release-state.sqlite"
    command = (
        'source "$1"; '
        'status_file="$(verification_status_file_for_run release "$2" "$3")"; '
        'write_verification_codex_status "$status_file" "$2/codex_status_write.json"; '
        'printf "%s\\n" "$status_file"'
    )
    result = subprocess.run(
        ["bash", "-lc", command, "bash", str(ROOT / "scripts" / "qwendex_dev_env"), str(run_meta), str(shared_status)],
        cwd=ROOT,
        env={
            **isolated_qwendex_runtime_env(),
            "HOME": str(tmp_path / "home"),
            "QWENDEX_DEV_ENV_LIBRARY": "1",
            "QWENDEX_STATE_DB": str(state_db),
            "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert Path(result.stdout.strip()) == run_meta / "codex_status.json"
    assert shared_status.read_text(encoding="utf-8") == "operator status must remain intact\n"
    assert (run_meta / "codex_status.json").is_file()
    assert (run_meta / "codex_status_write.json").is_file()
    script = (ROOT / "scripts" / "qwendex_dev_env").read_text(encoding="utf-8")
    assert 'verification_status_file_for_run "$tier"' in script
    assert 'write_verification_codex_status "$verification_status_file"' in script


def test_qwendex_dev_env_second_same_root_sync_skips_its_codex_wrapper(tmp_path):
    fake_home, checkout, fake_codex, env = same_root_dev_env_fixture(tmp_path)
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    first_sync = subprocess.run(
        [str(dev_env), "sync"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert first_sync.returncode == 0, first_sync.stderr or first_sync.stdout

    second_env = {
        **env,
        "PATH": f"{checkout / 'bin'}:{fake_codex.parent}:{os.environ['PATH']}",
    }
    second_sync = subprocess.run(
        [str(dev_env), "sync"],
        cwd=checkout,
        env=second_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    codex_main = subprocess.run(
        [str(checkout / "bin" / "codex-main"), "--version"],
        cwd=checkout,
        env=second_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    qdex = subprocess.run(
        [str(fake_home / ".local" / "bin" / "qdex"), "--repo", str(checkout), "--json"],
        cwd=checkout,
        env={
            **second_env,
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert second_sync.returncode == 0, second_sync.stderr or second_sync.stdout
    assert codex_main.returncode == 0, codex_main.stderr or codex_main.stdout
    assert codex_main.stdout.strip() == "codex-cli 0.144.6"
    assert str(fake_codex) in (checkout / "bin" / "codex-main").read_text(encoding="utf-8")
    assert qdex.returncode == 0, qdex.stderr or qdex.stdout
    dry_run = json.loads(qdex.stdout)
    assert dry_run["schema_version"] == "qwendex.qdex.dry_run.v1"
    assert dry_run["target_repo"] == str(checkout)
    assert not (checkout / "bin" / "codex").exists()
    assert dry_run["command"][0] == str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime")


def test_qdex_permission_mode_precedence_is_safe_and_yolo_is_explicit(tmp_path):
    fake_home, checkout, _, env = same_root_dev_env_fixture(tmp_path)
    schema = json.loads(
        (checkout / "config" / "qwendex" / "qwendex.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["qdex"]["properties"]["permission_mode"]["default"] == "workspace-write"
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    synced = subprocess.run(
        [str(dev_env), "sync"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert synced.returncode == 0, synced.stderr or synced.stdout
    qdex = fake_home / ".local" / "bin" / "qdex"
    base_env = {
        **env,
        "QWENDEX_QDEX_DRY_RUN": "1",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    def dry_run(*args, extra_env=None):
        return subprocess.run(
            [str(qdex), "--repo", str(checkout), "--json", *args],
            cwd=checkout,
            env={**base_env, **(extra_env or {})},
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )

    published = dry_run()
    assert published.returncode == 0, published.stderr or published.stdout
    published_payload = json.loads(published.stdout)
    assert published_payload["qdex_permission_mode"] == "workspace-write"
    assert published_payload["qdex_permission_source"] == "published-config"
    assert "--dangerously-bypass-approvals-and-sandbox" not in published_payload["command"]
    assert published_payload["command"].count("--sandbox") == 1

    operator_config = fake_home / ".config" / "qwendex" / "qdex.json"
    operator_config.parent.mkdir(parents=True)
    operator_config.write_text('{"permission_mode": "yolo"}\n', encoding="utf-8")
    operator = dry_run()
    assert operator.returncode == 0, operator.stderr or operator.stdout
    operator_payload = json.loads(operator.stdout)
    assert operator_payload["qdex_permission_mode"] == "yolo"
    assert operator_payload["qdex_permission_source"] == "operator-config"
    assert operator_payload["command"].count("--dangerously-bypass-approvals-and-sandbox") == 1

    environment = dry_run(extra_env={"QWENDEX_QDEX_PERMISSION_MODE": "workspace-write"})
    assert environment.returncode == 0, environment.stderr or environment.stdout
    environment_payload = json.loads(environment.stdout)
    assert environment_payload["qdex_permission_mode"] == "workspace-write"
    assert environment_payload["qdex_permission_source"] == "environment"
    assert "--dangerously-bypass-approvals-and-sandbox" not in environment_payload["command"]

    cli = dry_run(
        "--qdex-permission-mode",
        "yolo",
        "exec",
        "--",
        "--literal-native-value",
        extra_env={"QWENDEX_QDEX_PERMISSION_MODE": "workspace-write"},
    )
    assert cli.returncode == 0, cli.stderr or cli.stdout
    cli_payload = json.loads(cli.stdout)
    assert cli_payload["qdex_permission_mode"] == "yolo"
    assert cli_payload["qdex_permission_source"] == "cli"
    assert cli_payload["command"].count("--dangerously-bypass-approvals-and-sandbox") == 1
    assert cli_payload["command"][-2:] == ["--", "--literal-native-value"]

    invalid_env = dry_run(extra_env={"QWENDEX_QDEX_PERMISSION_MODE": "unsafe"})
    assert invalid_env.returncode == 2
    assert "invalid permission_mode from environment" in invalid_env.stderr

    operator_config.write_text('{"permission_mode": "unsafe"}\n', encoding="utf-8")
    invalid_operator = dry_run()
    assert invalid_operator.returncode == 2
    assert "invalid permission_mode from operator-config" in invalid_operator.stderr

    invalid_cli = dry_run("--qdex-permission-mode", "unsafe")
    assert invalid_cli.returncode == 2
    assert "requires yolo or workspace-write" in invalid_cli.stderr


def test_qwendex_upgrade_ignores_stale_main_codex_and_installed_qdex_opens_other_repo(tmp_path):
    fake_home, checkout, fake_codex, env = same_root_dev_env_fixture(tmp_path)
    args_file = tmp_path / "installed-qdex-args.txt"
    fake_codex.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$QWENDEX_FAKE_CODEX_ARGS"
for arg in "$@"; do
  if [[ "$arg" == "--version" ]]; then
    printf 'codex-cli 0.144.6\\n'
    break
  fi
done
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    legacy_codex = checkout / "bin" / "codex"
    legacy_codex.parent.mkdir(exist_ok=True)
    legacy_codex.write_text(
        "#!/usr/bin/env bash\n# QWENDEX-GENERATED-CODEX-WRAPPER\nexec /missing/legacy-codex \"$@\"\n",
        encoding="utf-8",
    )
    legacy_codex.chmod(0o755)
    upgrade_env = {
        **env,
        "PATH": f"{checkout / 'bin'}:{fake_codex.parent}:{os.environ['PATH']}",
        "QWENDEX_MAIN_CODEX_BIN": str(legacy_codex),
        "QWENDEX_FAKE_CODEX_ARGS": str(args_file),
    }

    sync = subprocess.run(
        [str(checkout / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=checkout,
        env=upgrade_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    downstream_repo = tmp_path / "downstream-repo"
    downstream_repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=downstream_repo,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    installed_qdex = fake_home / ".local" / "bin" / "qdex"
    launched = subprocess.run(
        [str(installed_qdex), "-C", str(downstream_repo), "--version"],
        cwd=downstream_repo,
        env=upgrade_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert sync.returncode == 0, sync.stderr or sync.stdout
    assert not legacy_codex.exists()
    assert installed_qdex.is_file()
    assert launched.returncode == 0, launched.stderr or launched.stdout
    assert launched.stdout.strip() == "codex-cli 0.144.6"
    launched_args = args_file.read_text(encoding="utf-8").splitlines()
    assert launched_args[launched_args.index("-C") + 1] == str(downstream_repo)
    runtime = (checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime").read_text(
        encoding="utf-8"
    )
    assert str(fake_codex) in runtime
    assert str(legacy_codex) not in runtime


def test_qwendex_dev_env_removes_only_known_legacy_codex_wrapper(tmp_path):
    _, checkout, _, env = same_root_dev_env_fixture(tmp_path)
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    first = subprocess.run([str(dev_env), "sync"], cwd=checkout, env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr or first.stdout
    legacy = checkout / "bin" / "codex"
    legacy.write_text("#!/usr/bin/env bash\ndev_codex_default=/tmp/codex-build/bin/codex\n", encoding="utf-8")
    legacy.chmod(0o755)

    migrated = subprocess.run([str(dev_env), "sync"], cwd=checkout, env=env, text=True, capture_output=True, check=False)

    assert migrated.returncode == 0, migrated.stderr or migrated.stdout
    assert not legacy.exists()


def test_qwendex_dev_env_refuses_unknown_file_at_deprecated_codex_path(tmp_path):
    _, checkout, _, env = same_root_dev_env_fixture(tmp_path)
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    first = subprocess.run([str(dev_env), "sync"], cwd=checkout, env=env, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr or first.stdout
    unknown = checkout / "bin" / "codex"
    unknown.write_text("#!/usr/bin/env bash\nprintf 'user-owned\\n'\n", encoding="utf-8")
    unknown.chmod(0o755)

    blocked = subprocess.run([str(dev_env), "sync"], cwd=checkout, env=env, text=True, capture_output=True, check=False)

    assert blocked.returncode != 0
    assert "refusing to replace unknown file" in blocked.stderr
    assert "user-owned" in unknown.read_text(encoding="utf-8")


def test_qwendex_dev_env_preserves_upstream_codex_and_versions_model_cache(tmp_path):
    _, checkout, fake_codex, env = same_root_dev_env_fixture(tmp_path)
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    sync = subprocess.run(
        [str(dev_env), "sync"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout

    sourced = subprocess.run(
        [
            "bash",
            "-c",
            'before_home=${CODEX_HOME-__unset__}; source "$1"; printf "%s\\n%s\\n%s\\n%s\\n" "$(command -v codex)" "$QWENDEX_MODELS_CACHE_FILE" "${CODEX_HOME-__unset__}" "$QWENDEX_CODEX_RUNTIME"',
            "qwendex-env-probe",
            str(checkout / ".qwendex-dev" / "env.sh"),
        ],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert sourced.returncode == 0, sourced.stderr or sourced.stdout
    resolved_codex, cache_file, sourced_home, runtime = sourced.stdout.splitlines()
    assert resolved_codex == str(fake_codex)
    assert cache_file == "models_cache.qwendex-0.144.6.json"
    assert sourced_home == "__unset__"
    assert runtime == str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime")


def test_qwendex_dev_codex_wrapper_requires_code_mode_host(tmp_path):
    _, checkout, _, env = same_root_dev_env_fixture(tmp_path)
    dev_env = checkout / "scripts" / "qwendex_dev_env"
    sync = subprocess.run(
        [str(dev_env), "sync"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout

    build_bin = checkout / ".qwendex-dev" / "codex-build" / "bin"
    dev_codex = build_bin / "codex"
    code_mode_host = build_bin / "codex-code-mode-host"
    dev_codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.6\\n'\n",
        encoding="utf-8",
    )
    dev_codex.chmod(0o755)

    blocked = subprocess.run(
        [str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime"), "--version"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert blocked.returncode == 127
    assert "Codex code-mode host is missing or not executable" in blocked.stderr
    doctor = subprocess.run(
        [str(dev_env), "doctor"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    dev_status = json.loads(
        (checkout / ".qwendex-dev" / "results" / "meta" / "dev_status.json").read_text(
            encoding="utf-8"
        )
    )
    assert doctor.returncode != 0
    assert dev_status["status"] == "blocked"
    assert "codex-code-mode-host companion" in " ".join(dev_status["blockers"])

    code_mode_host.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    code_mode_host.chmod(0o755)
    ready = subprocess.run(
        [str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime"), "--version"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    assert ready.returncode == 0, ready.stderr or ready.stdout
    assert ready.stdout.strip() == "codex-cli 0.144.6"


def assert_same_root_supports_quoted_path(tmp_path, path_fragment):
    quoted_root = tmp_path / path_fragment
    quoted_root.mkdir()
    fake_home, checkout, _, env = same_root_dev_env_fixture(quoted_root)
    dev_env = checkout / "scripts" / "qwendex_dev_env"

    for _ in range(2):
        sync = subprocess.run(
            [str(dev_env), "sync"],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert sync.returncode == 0, sync.stderr or sync.stdout

    config = tomllib.loads(
        (checkout / ".qwendex-dev" / "codex_home" / "config.toml").read_text(
            encoding="utf-8"
        )
    )
    qwendex = subprocess.run(
        [str(checkout / "bin" / "qwendex"), "version", "--json"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    qwendex_dev = subprocess.run(
        [str(checkout / "bin" / "qwendex-dev"), "env"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    sourced_env = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s\\n" "$QWENDEX_DEV_ROOT"',
            "qwendex-env-probe",
            str(checkout / ".qwendex-dev" / "env.sh"),
        ],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    codex = subprocess.run(
        [str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime"), "--version"],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    qdex = subprocess.run(
        [str(fake_home / ".local" / "bin" / "qdex"), "--repo", str(checkout), "--json"],
        cwd=checkout,
        env={
            **env,
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert config["projects"] == {str(checkout): {"trust_level": "trusted"}}
    assert qwendex.returncode == 0, qwendex.stderr or qwendex.stdout
    assert json.loads(qwendex.stdout)["data"]["version"] == "0.6.2"
    assert qwendex_dev.returncode == 0, qwendex_dev.stderr or qwendex_dev.stdout
    assert sourced_env.returncode == 0, sourced_env.stderr or sourced_env.stdout
    assert sourced_env.stdout.strip() == str(checkout)
    assert codex.returncode == 0, codex.stderr or codex.stdout
    assert codex.stdout.strip() == "codex-cli 0.144.6"
    assert qdex.returncode == 0, qdex.stderr or qdex.stdout
    dry_run = json.loads(qdex.stdout)
    assert dry_run["target_repo"] == str(checkout)
    assert dry_run["manager_target_repo"] == str(checkout)
    assert dry_run["codex_home"] == str(checkout / ".qwendex-dev" / "codex_home")
    assert dry_run["command"][0] == str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime")


def test_qwendex_dev_env_same_root_supports_apostrophe_paths(tmp_path):
    assert_same_root_supports_quoted_path(tmp_path, "operator's qwendex root")


def test_qwendex_dev_env_same_root_supports_double_quote_paths(tmp_path):
    assert_same_root_supports_quoted_path(tmp_path, 'operator "quoted" qwendex root')


def test_qwendex_dev_default_launches_repo_with_yolo_codex(tmp_path):
    fake_home = tmp_path / "home"
    dev_root = tmp_path / "qwendex-dev"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    args_file = tmp_path / "codex-args.json"
    cwd_file = tmp_path / "codex-cwd.txt"

    fake_bin.mkdir()
    fake_home.mkdir()
    (fake_home / ".codex").mkdir()
    (fake_home / ".codex" / "hooks.json").write_text('{"hooks":{"PreToolUse":[]}}\n', encoding="utf-8")
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("codex-cli 0.143.0")
    raise SystemExit(0)

Path(os.environ["QWENDEX_FAKE_CODEX_ARGS"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
Path(os.environ["QWENDEX_FAKE_CODEX_CWD"]).write_text(os.getcwd(), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_code_mode_host = fake_bin / "codex-code-mode-host"
    fake_code_mode_host.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_code_mode_host.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        "QWENDEX_MAIN_CODEX_BIN": str(fake_codex),
        "QWENDEX_DEV_CODEX_BIN": str(fake_codex),
        "QWENDEX_FAKE_CODEX_ARGS": str(args_file),
        "QWENDEX_FAKE_CODEX_CWD": str(cwd_file),
    }

    sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout

    launched = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )

    assert launched.returncode == 0, launched.stderr or launched.stdout
    args = json.loads(args_file.read_text(encoding="utf-8"))
    config = (dev_root / ".qwendex-dev" / "codex_home" / "config.toml").read_text(encoding="utf-8")
    assert cwd_file.read_text(encoding="utf-8") == str(dev_root)
    assert_qdex_v2_policy_prefix(args)
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    assert args[args.index("-C") + 1] == str(dev_root)
    example_config = (dev_root / ".qwendex-dev" / "codex_home" / "patched-tui.example.toml").read_text(encoding="utf-8")
    assert "qwendex-manager" in config
    assert 'qwendex_toggle_manager = "alt-m"' not in config
    assert 'qwendex_toggle_kaveman = "alt-k"' not in config
    assert 'qwendex_toggle_local = "alt-l"' not in config
    assert 'qwendex_toggle_manager = "alt-m"' in example_config
    assert 'qwendex_toggle_kaveman = "alt-k"' in example_config
    assert 'qwendex_toggle_local = "alt-l"' in example_config

    opt_in_sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env={**env, "QWENDEX_DEV_ENABLE_PATCHED_TUI_CONFIG": "1"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert opt_in_sync.returncode == 0, opt_in_sync.stderr or opt_in_sync.stdout
    opt_in_config = (dev_root / ".qwendex-dev" / "codex_home" / "config.toml").read_text(encoding="utf-8")
    assert 'qwendex_toggle_manager = "alt-m"' in opt_in_config
    assert 'qwendex_toggle_kaveman = "alt-k"' in opt_in_config
    assert 'qwendex_toggle_local = "alt-l"' in opt_in_config
    dev_status = json.loads((dev_root / ".qwendex-dev" / "results" / "meta" / "dev_status.json").read_text(encoding="utf-8"))
    assert dev_status["codex"]["hook_source_count"] == 0
    assert dev_status["codex"]["global_hook_source_count"] == 1
    assert "active dev CODEX_HOME has no hooks" in " ".join(dev_status["warnings"])


def test_qdex_manager_preflight_is_advisory_and_exports_env_when_available(tmp_path):
    fake_home = tmp_path / "home"
    dev_root = tmp_path / "qwendex-dev"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    args_file = tmp_path / "qdex-codex-call.json"
    version_run_id_file = tmp_path / "qdex-version-run-id.txt"

    fake_bin.mkdir()
    fake_home.mkdir()
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    version_run_id_path = os.environ.get("QWENDEX_FAKE_CODEX_VERSION_RUN_ID")
    if version_run_id_path:
        Path(version_run_id_path).write_text(os.environ.get("QWENDEX_RUN_ID", ""), encoding="utf-8")
    print("codex-cli 0.143.0")
    raise SystemExit(0)

Path(os.environ["QWENDEX_FAKE_CODEX_ARGS"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "cwd": os.getcwd(),
    "codex_home": os.environ.get("CODEX_HOME", ""),
    "manager_target_repo": os.environ.get("QWENDEX_MANAGER_TARGET_REPO", ""),
    "manager_session_id": os.environ.get("QWENDEX_MANAGER_SESSION_ID", ""),
    "manager_ledger_id": os.environ.get("QWENDEX_MANAGER_LEDGER_ID", ""),
    "manager_root_agent_id": os.environ.get("QWENDEX_MANAGER_ROOT_AGENT_ID", ""),
    "manager_launch_pid": os.environ.get("QWENDEX_MANAGER_LAUNCH_PID", ""),
    "manager_launch_start_ticks": os.environ.get("QWENDEX_MANAGER_LAUNCH_START_TICKS", ""),
    "manager_policy_hash": os.environ.get("QWENDEX_MANAGER_POLICY_HASH", ""),
    "effective_agent_use": os.environ.get("QWENDEX_EFFECTIVE_AGENT_USE", ""),
    "agent_policy_hash": os.environ.get("QWENDEX_AGENT_POLICY_HASH", ""),
    "agent_policy_source": os.environ.get("QWENDEX_AGENT_POLICY_SOURCE", ""),
    "qdex_permission_mode": os.environ.get("QWENDEX_QDEX_PERMISSION_MODE", ""),
    "qdex_permission_source": os.environ.get("QWENDEX_QDEX_PERMISSION_SOURCE", ""),
    "run_id": os.environ.get("QWENDEX_RUN_ID", ""),
}), encoding="utf-8")
""",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    fake_code_mode_host = fake_bin / "codex-code-mode-host"
    fake_code_mode_host.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_code_mode_host.chmod(0o755)

    env = {
        **os.environ,
        "HOME": str(fake_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "QWENDEX_DEV_ROOT": str(dev_root),
        "QWENDEX_DEV_SOURCE_ROOT": str(ROOT),
        "QWENDEX_MAIN_CODEX_BIN": str(fake_codex),
        "QWENDEX_DEV_CODEX_BIN": str(fake_codex),
        "QWENDEX_FAKE_CODEX_ARGS": str(args_file),
        "QWENDEX_FAKE_CODEX_VERSION_RUN_ID": str(version_run_id_file),
    }
    for key in (
        "CODEX_HOME",
        "QWENDEX_AGENT_USE",
        "CODEX_AGENT_USE",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED",
        "QWENDEX_MANAGER_UNHOOKED_REASON",
        "QWENDEX_QDEX_PERMISSION_MODE",
        "QWENDEX_QDEX_PERMISSION_SOURCE",
    ):
        env.pop(key, None)
    sync = subprocess.run(
        [str(ROOT / "scripts" / "qwendex_dev_env"), "sync"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert sync.returncode == 0, sync.stderr or sync.stdout
    assert not (fake_home / ".local" / "state" / "qwendex").exists()
    qdex = fake_home / ".local" / "bin" / "qdex"
    assert qdex.exists()

    set_mode = subprocess.run(
        [
            "bash",
            "-lc",
            'source "$QWENDEX_DEV_ROOT/.qwendex-dev/env.sh"; "$QWENDEX_DEV_ROOT/scripts/qwendex" manager mode --set manager --json',
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert set_mode.returncode == 0, set_mode.stderr or set_mode.stdout

    caller_codex_home = tmp_path / "caller-codex-home"
    caller_codex_home.mkdir()
    default_home_dry_run = subprocess.run(
        [str(qdex), "--repo", str(ROOT), "--json"],
        cwd=ROOT,
        env={
            **env,
            "CODEX_HOME": str(caller_codex_home),
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert default_home_dry_run.returncode == 0, default_home_dry_run.stderr or default_home_dry_run.stdout
    default_home_payload = json.loads(default_home_dry_run.stdout)
    assert default_home_payload["codex_home"] == str(dev_root / ".qwendex-dev" / "codex_home")
    assert default_home_payload["manager_target_repo"] == str(ROOT)

    legacy_preserve_flag_dry_run = subprocess.run(
        [str(qdex), "--repo", str(ROOT), "--json"],
        cwd=ROOT,
        env={
            **env,
            "CODEX_HOME": str(caller_codex_home),
            "QWENDEX_QDEX_PRESERVE_CODEX_HOME": "1",
            "QWENDEX_QDEX_DRY_RUN": "1",
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert legacy_preserve_flag_dry_run.returncode == 0, legacy_preserve_flag_dry_run.stderr or legacy_preserve_flag_dry_run.stdout
    assert json.loads(legacy_preserve_flag_dry_run.stdout)["codex_home"] == str(dev_root / ".qwendex-dev" / "codex_home")
    assert default_home_payload["internal_runtime"] == str(dev_root / ".qwendex-dev" / "bin" / "qwendex-codex-runtime")
    assert default_home_payload["selected_target"] == default_home_payload["internal_runtime"]
    assert default_home_payload["permission_mode"] == "workspace-write"
    assert default_home_payload["qdex_permission_mode"] == "workspace-write"
    assert default_home_payload["qdex_permission_source"] == "published-config"
    assert "--dangerously-bypass-approvals-and-sandbox" not in default_home_payload["command"]
    assert "--sandbox" in default_home_payload["command"]
    assert default_home_payload["manager_preflight"]["data"]["qdex_permission_mode"] == "workspace-write"
    assert default_home_payload["manager_preflight"]["data"]["qdex_permission_source"] == "published-config"

    launched_without_hooks = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert launched_without_hooks.returncode == 0
    assert "Qwendex Manager preflight: ready" in launched_without_hooks.stderr
    assert "hooks: missing" in launched_without_hooks.stderr
    assert args_file.exists()
    args_file.unlink()

    env_override_launch = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env={**env, "QWENDEX_AGENT_USE": "Heavy"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert env_override_launch.returncode == 0
    assert "Qwendex Manager preflight: ready" in env_override_launch.stderr
    assert args_file.exists()
    args_file.unlink()

    launched = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env={
            **env,
            "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
            "QWENDEX_MANAGER_SESSION_ID": "stale-session",
            "QWENDEX_MANAGER_LEDGER_ID": "stale-ledger",
            "QWENDEX_MANAGER_ROOT_AGENT_ID": "stale-root",
            "QWENDEX_MANAGER_LAUNCH_PID": "999999999",
            "QWENDEX_MANAGER_LAUNCH_START_TICKS": "stale-start",
            "QWENDEX_MANAGER_POLICY_HASH": "stale-policy",
            "QWENDEX_MANAGER_STOP_STATUS": "stale-stop",
        },
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert launched.returncode == 0, launched.stderr or launched.stdout
    call = json.loads(args_file.read_text(encoding="utf-8"))
    assert_qdex_v2_policy_prefix(call["args"], expected_native_threads=5)
    assert "--dangerously-bypass-approvals-and-sandbox" not in call["args"]
    assert call["args"].count("--sandbox") == 1
    assert call["args"][call["args"].index("--sandbox") + 1] == "workspace-write"
    assert "--dangerously-bypass-hook-trust" in call["args"]
    assert f'projects={{"{ROOT}"={{trust_level="trusted"}}}}' in call["args"]
    assert call["args"][call["args"].index("-C") + 1] == str(ROOT)
    assert call["manager_session_id"].startswith("mgrsess_")
    assert call["manager_ledger_id"].startswith("mgrldg_")
    assert call["manager_root_agent_id"].startswith("manager-root-mgrldg_")
    assert call["manager_session_id"] != "stale-session"
    assert call["manager_ledger_id"] != "stale-ledger"
    assert call["manager_root_agent_id"] != "stale-root"
    assert int(call["manager_launch_pid"]) > 0
    assert call["manager_launch_pid"] != "999999999"
    assert call["manager_launch_start_ticks"]
    assert call["manager_launch_start_ticks"] != "stale-start"
    assert call["manager_policy_hash"] != "stale-policy"
    assert call["manager_policy_hash"]
    assert call["codex_home"] == str(dev_root / ".qwendex-dev" / "codex_home")
    assert call["manager_target_repo"] == str(ROOT)
    assert call["effective_agent_use"] == "Manager"
    assert call["agent_policy_hash"] == call["manager_policy_hash"]
    assert call["agent_policy_source"] == "manager-mode"
    assert call["qdex_permission_mode"] == "workspace-write"
    assert call["qdex_permission_source"] == "published-config"
    assert re.fullmatch(r"[0-9a-f]{32}", call["run_id"])

    compatible_env = {
        **env,
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    args_file.unlink()
    json_exec = subprocess.run(
        [str(qdex), "--repo", str(ROOT), "exec", "--json", "report status"],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert json_exec.returncode == 0, json_exec.stderr or json_exec.stdout
    json_call = json.loads(args_file.read_text(encoding="utf-8"))
    exec_index = json_call["args"].index("exec")
    assert json_call["args"][exec_index : exec_index + 3] == [
        "exec",
        "--json",
        "report status",
    ]
    assert json_call["args"][exec_index + 3] == "--config"
    assert re.fullmatch(r"[0-9a-f]{32}", json_call["run_id"])
    assert json_call["run_id"] != call["run_id"]

    args_file.unlink()
    native_cd = subprocess.run(
        [str(qdex), "-C", str(ROOT), "exec", "report cwd"],
        cwd=tmp_path,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert native_cd.returncode == 0, native_cd.stderr or native_cd.stdout
    native_cd_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert native_cd_call["args"].count("-C") == 1
    assert_qdex_caller_args_before_policy(
        native_cd_call["args"],
        ["-C", str(ROOT), "exec", "report cwd"],
    )
    assert native_cd_call["manager_target_repo"] == str(ROOT)

    args_file.unlink()
    relative_cd = subprocess.run(
        [str(qdex), "-C", ROOT.name, "exec", "report relative cwd"],
        cwd=ROOT.parent,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert relative_cd.returncode == 0, relative_cd.stderr or relative_cd.stdout
    relative_cd_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert_qdex_caller_args_before_policy(
        relative_cd_call["args"],
        ["-C", ROOT.name, "exec", "report relative cwd"],
    )
    assert relative_cd_call["cwd"] == str(ROOT.parent)
    assert relative_cd_call["manager_target_repo"] == str(ROOT)

    args_file.unlink()
    add_dir = subprocess.run(
        [str(qdex), "--repo", str(ROOT), "exec", "--add-dir", str(tmp_path), "report roots"],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert add_dir.returncode == 0, add_dir.stderr or add_dir.stdout
    add_dir_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert_qdex_caller_args_before_policy(
        add_dir_call["args"],
        ["exec", "--add-dir", str(tmp_path), "report roots"],
    )

    args_file.unlink()
    directory_prompt = subprocess.run(
        [str(qdex), "--repo", str(ROOT), "exec", str(tmp_path)],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert directory_prompt.returncode == 0, directory_prompt.stderr or directory_prompt.stdout
    directory_prompt_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert_qdex_caller_args_before_policy(
        directory_prompt_call["args"],
        ["exec", str(tmp_path)],
    )

    args_file.unlink()
    literal_passthrough = subprocess.run(
        [
            str(qdex), "-C", str(ROOT),
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "exec", "--", "--repo", "literal-value",
        ],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert literal_passthrough.returncode == 0, literal_passthrough.stderr or literal_passthrough.stdout
    literal_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert literal_call["args"].count("--dangerously-bypass-approvals-and-sandbox") == 1
    assert literal_call["args"].count("--dangerously-bypass-hook-trust") == 1
    literal_exec_index = literal_call["args"].index("exec")
    assert literal_call["args"][literal_exec_index + 1] == "--config"
    assert literal_call["args"][-3:] == ["--", "--repo", "literal-value"]

    args_file.unlink()
    non_git_cwd = subprocess.run(
        [str(qdex), "exec", "report cwd"],
        cwd=tmp_path,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert non_git_cwd.returncode == 0, non_git_cwd.stderr or non_git_cwd.stdout
    non_git_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert "-C" not in non_git_call["args"]
    assert "--cd" not in non_git_call["args"]
    assert non_git_call["cwd"] == str(tmp_path)
    assert non_git_call["manager_target_repo"] == str(tmp_path)

    args_file.unlink()
    help_result = subprocess.run(
        [str(qdex), "--help"],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert help_result.returncode == 0, help_result.stderr or help_result.stdout
    help_call = json.loads(args_file.read_text(encoding="utf-8"))
    assert help_call["args"] == ["--help"]
    assert help_call["manager_ledger_id"] == ""
    assert "Qwendex Manager preflight" not in help_result.stderr

    args_file.unlink()
    version_result = subprocess.run(
        [str(qdex), "--version"],
        cwd=ROOT,
        env=compatible_env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert version_result.returncode == 0, version_result.stderr or version_result.stdout
    assert version_result.stdout.strip() == "codex-cli 0.143.0"
    assert version_result.stderr == ""
    assert not args_file.exists()
    assert version_run_id_file.read_text(encoding="utf-8") == ""


def test_qwendex_codex_status_tracks_manager_state_and_writes_surface_file(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    blocked_mode = run_qwendex("manager", "mode", "--set", "manager", "--json", env=env)
    blocked_mode_data = parse_json_result(blocked_mode)
    assert blocked_mode.returncode == 0
    assert blocked_mode_data["status"] == "pass"
    assert blocked_mode_data["data"]["deployment_contract"]["status"] == "standby"
    assert blocked_mode_data["data"]["manager_health"]["status"] == "standby"
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-footer",
        "--lane",
        "footer-status",
        "--task-id",
        "footer-status",
        "--objective",
        "Keep Manager Mode footer status connected to an active lane",
        "--json",
        env=env,
    )
    json_result("manager", "local", "--set", "on", "--json", env=env)
    status = json_result("codex-status", "--write", str(status_file), "--json", env=env)
    plain = run_qwendex("codex-status", "--plain", env=env)

    assert status["data"]["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert status["data"]["mode"] == "manager"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy_source"] == "manager-mode"
    assert status["data"]["qdex_permission_mode"] == "workspace-write"
    assert status["data"]["qdex_permission_source"] == "published-config"
    assert plain.stdout.strip() == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert written["agent_use"] == "Manager"
    assert written["agent_policy_source"] == "manager-mode"
    assert written["qdex_permission_mode"] == "workspace-write"
    assert written["qdex_permission_source"] == "published-config"
    assert written["kaveman_enabled"] is False
    assert written["local_usable"] is True

    kaveman = json_result("manager", "kaveman", "--toggle", "--json", env=env)
    assert kaveman["data"]["kaveman_enabled"] is True
    written_after_kaveman = json.loads(status_file.read_text(encoding="utf-8"))
    assert written_after_kaveman["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [Y] | Local: [Ready] (Alt+M/K/L)"
    assert written_after_kaveman["kaveman_directive"]

    toggled = json_result("manager", "mode", "--toggle", "--json", env=env)
    assert toggled["data"]["mode"] == "off"
    written_after_toggle = json.loads(status_file.read_text(encoding="utf-8"))
    assert written_after_toggle["text"] == "{Qwendex} Agent Manager: [Off] | Kaveman: [Y] | Local: [Ready] (Alt+M/K/L)"


def test_qwendex_codex_status_warns_on_state_db_mismatch(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env_a = {
        "QWENDEX_STATE_DB": str(tmp_path / "state-a.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    env_b = {
        "QWENDEX_STATE_DB": str(tmp_path / "state-b.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    json_result("manager", "mode", "--set", "manager", "--json", env=env_a)
    json_result("manager", "kaveman", "--set", "on", "--json", env=env_a)
    json_result("codex-status", "--write", str(status_file), "--json", env=env_a)

    mismatch = json_result("codex-status", "--json", env=env_b)
    diagnostics = mismatch["data"]["status_file_diagnostics"]

    assert mismatch["data"]["text"] == "{Qwendex} Agent Manager: [Auto] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert diagnostics["status_file_state_mismatch"] is True
    assert diagnostics["status_file_state_db"] == env_a["QWENDEX_STATE_DB"]
    assert mismatch["data"]["warnings"]
    assert "codex-status --write" in " ".join(mismatch["data"]["next_actions"])

    refreshed = json_result("codex-status", "--write", str(status_file), "--json", env=env_b)
    written = json.loads(status_file.read_text(encoding="utf-8"))

    assert refreshed["data"]["status_file_diagnostics"]["status_file_state_mismatch"] is True
    assert written["status_file_diagnostics"]["status_file_state_mismatch"] is False
    assert written["status_file_diagnostics"]["status_file_state_db"] == env_b["QWENDEX_STATE_DB"]
    assert not written["status_file_diagnostics"]["warnings"]
    assert "no verified Qwendex Codex hooks" in " ".join(written["warnings"])


def test_qwendex_session_controls_are_isolated_from_defaults_and_status_authority(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    default_env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }
    session_a = {
        **default_env,
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-a.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-a",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-a-status.json"),
    }
    session_b = {
        **default_env,
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-b.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-b",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-b-status.json"),
    }

    json_result("manager", "mode", "--set", "off", "--json", env=default_env)
    json_result("manager", "kaveman", "--set", "off", "--json", env=default_env)
    json_result("manager", "mode", "--set", "manager", "--json", env=session_a)
    json_result("manager", "kaveman", "--set", "on", "--json", env=session_a)

    a_status = json_result("codex-status", "--write", session_a["QWENDEX_CODEX_STATUS_FILE"], "--json", env=session_a)
    b_status = json_result("codex-status", "--write", session_b["QWENDEX_CODEX_STATUS_FILE"], "--json", env=session_b)
    default_status = json_result("codex-status", "--json", env=default_env)

    assert a_status["data"]["mode"] == "manager"
    assert a_status["data"]["kaveman_enabled"] is True
    assert a_status["data"]["agent_policy_hash"] != b_status["data"]["agent_policy_hash"]
    assert b_status["data"]["mode"] == "off"
    assert b_status["data"]["kaveman_enabled"] is False
    assert default_status["data"]["mode"] == "off"
    assert default_status["data"]["kaveman_enabled"] is False

    assert a_status["data"]["control_state"]["scope"] == "per_launch_session"
    assert a_status["data"]["control_state"]["session_id"] == "session-a"
    assert a_status["data"]["status_authority"]["authoritative_for_open_session"] is True
    assert b_status["data"]["status_authority"]["session_id"] == "session-b"
    assert default_status["data"]["status_authority"]["scope"] == "aggregate_compatibility"
    assert default_status["data"]["status_authority"]["authoritative_for_open_session"] is False

    a_record = json.loads(Path(session_a["QWENDEX_MANAGER_SESSION_STATE_FILE"]).read_text(encoding="utf-8"))
    b_record = json.loads(Path(session_b["QWENDEX_MANAGER_SESSION_STATE_FILE"]).read_text(encoding="utf-8"))
    assert a_record["selected_mode"] == "manager"
    assert a_record["kaveman_enabled"] is True
    assert b_record["selected_mode"] == "off"
    assert b_record["kaveman_enabled"] is False


def test_qwendex_session_kaveman_toggle_snapshots_active_turn_and_refreshes_next_turn(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    default_env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    })
    session_env = {
        **default_env,
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-a.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-a",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-a-status.json"),
    }

    json_result("manager", "mode", "--set", "off", "--json", env=default_env)
    json_result("manager", "kaveman", "--set", "off", "--json", env=default_env)
    json_result("manager", "mode", "--set", "manager", "--json", env=session_env)
    json_result("manager", "kaveman", "--set", "on", "--json", env=session_env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=session_env
    )
    initial_hash = preflight["data"]["policy_hash"]
    active_env = {
        **session_env,
        **preflight["data"]["exports"],
        "QWENDEX_QDEX_LAUNCH_POLICY_HASH": initial_hash,
    }
    before = json_result("codex-status", "--json", env=active_env)
    prompt_hook = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "root-session-a",
            "turn_id": "root-turn-a",
            "cwd": str(repo),
            "prompt": "Use subagents to map the repository and verify the active output policy.",
        }),
        "--json",
        env=active_env,
    )
    root_context = prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    decision = prompt_hook["data"]["manager_decision"]
    assignment = prompt_hook["data"]["agent_plan"]["assignments"][0]

    json_result("manager", "kaveman", "--set", "off", "--json", env=session_env)
    after = json_result("codex-status", "--json", env=active_env)
    child_hook = json_result(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "runtime-child-a",
            "agent_type": assignment["profile"],
            "task_name": assignment["agent_id"],
            "parent_session_id": "root-session-a",
            "session_id": "child-session-a",
            "turn_id": "child-turn-a",
            "cwd": str(repo),
        }),
        "--json",
        env=active_env,
    )
    child_context = child_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    next_prompt_hook = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "root-session-b",
            "turn_id": "root-turn-b",
            "cwd": str(repo),
            "prompt": "Inspect the current repository state and report the selected output policy.",
        }),
        "--json",
        env=active_env,
    )
    next_root_context = next_prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    next_child_hook = json_result(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "runtime-child-b",
            "agent_type": assignment["profile"],
            "task_name": assignment["agent_id"],
            "parent_session_id": "root-session-b",
            "session_id": "child-session-b",
            "turn_id": "child-turn-b",
            "cwd": str(repo),
        }),
        "--json",
        env=active_env,
    )
    next_child_context = next_child_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]

    assert decision["policy_hash"] == initial_hash
    assert "Qwendex output policy: Kaveman enabled." in root_context
    assert "Qwendex output policy: Kaveman enabled." in child_context
    assert "Qwendex output policy: Kaveman enabled." not in next_root_context
    assert "Qwendex output policy: Kaveman enabled." not in next_child_context
    assert after["data"]["kaveman_enabled"] is False
    assert after["data"]["agent_policy_hash"] != initial_hash
    assert after["data"]["status_authority"]["effective_policy_hash"] == initial_hash
    assert after["data"]["status_authority"]["next_turn_policy_hash"] == after["data"]["agent_policy_hash"]
    assert after["data"]["status_authority"]["policy_drift"] is True
    assert after["data"]["status_authority"]["restart_required"] is False
    assert after["data"]["status_authority"]["kaveman_applies_at"] == "next_user_prompt"
    assert next_prompt_hook["data"]["accepted_turn_policy_hash"] == after["data"]["agent_policy_hash"]
    assert after["data"]["qdex_permission"] == before["data"]["qdex_permission"]
    assert after["data"]["local_enabled"] == before["data"]["local_enabled"]

    fresh_env = {
        **default_env,
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-d.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-d",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-d-status.json"),
    }
    fresh = json_result("codex-status", "--json", env=fresh_env)
    assert fresh["data"]["mode"] == "off"
    assert fresh["data"]["kaveman_enabled"] is False


def test_qwendex_session_mode_change_is_requested_until_a_capacity_restart(tmp_path):
    session_env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-a.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-a",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-a-status.json"),
        "QWENDEX_QDEX_LAUNCH_MODE": "off",
        "QWENDEX_QDEX_LAUNCH_AGENT_USE": "Off",
        "QWENDEX_QDEX_LAUNCH_MAX_WORKERS": "0",
        "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED": "0",
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    json_result("manager", "mode", "--set", "off", "--json", env=session_env)
    json_result("manager", "local", "--set", "off", "--json", env=session_env)
    before = json_result("codex-status", "--json", env=session_env)
    json_result("manager", "mode", "--set", "manager", "--json", env=session_env)
    requested = json_result("codex-status", "--json", env=session_env)
    manager_requested = json_result("manager", "status", "--json", env=session_env)
    prompt = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "root-session-a",
            "turn_id": "root-turn-a",
            "prompt": "Map the repository with bounded helpers.",
        }),
        "--json",
        env=session_env,
    )
    json_result("manager", "mode", "--set", "off", "--json", env=session_env)
    restored = json_result("codex-status", "--json", env=session_env)

    assert before["data"]["mode"] == "off"
    assert requested["data"]["mode"] == "manager"
    assert requested["data"]["effective_turn_mode"] == "off"
    assert "Requested Manager Mode → active Off (restart)" in requested["data"]["text"]
    assert requested["data"]["status_authority"]["mode_restart_required"] is True
    assert requested["data"]["status_authority"]["restart_required"] is True
    assert manager_requested["data"]["requested_agent_policy"]["mode"] == "manager"
    assert manager_requested["data"]["agent_policy"]["mode"] == "off"
    assert manager_requested["data"]["effective_turn_mode"] == "off"
    assert manager_requested["data"]["effective_max_subagents"] == 0
    assert manager_requested["data"]["status_authority"]["mode_restart_required"] is True
    assert prompt["data"]["agent_policy"]["mode"] == "off"
    assert "Active Qwendex agent mode: Off." in prompt["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert restored["data"]["mode"] == "off"
    assert restored["data"]["effective_turn_mode"] == "off"
    assert restored["data"]["status_authority"]["restart_required"] is False
    assert restored["data"]["qdex_permission"] == before["data"]["qdex_permission"]
    assert restored["data"]["local_enabled"] == before["data"]["local_enabled"]


def test_qwendex_session_local_change_is_requested_until_a_capacity_restart(tmp_path):
    session_env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-a.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-a",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-a-status.json"),
        "QWENDEX_QDEX_LAUNCH_MODE": "manager",
        "QWENDEX_QDEX_LAUNCH_AGENT_USE": "Manager",
        "QWENDEX_QDEX_LAUNCH_MAX_WORKERS": "4",
        "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED": "0",
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    json_result("manager", "mode", "--set", "manager", "--json", env=session_env)
    json_result("manager", "local", "--set", "off", "--json", env=session_env)
    before = json_result("codex-status", "--json", env=session_env)
    json_result("manager", "local", "--set", "on", "--json", env=session_env)
    requested = json_result("codex-status", "--json", env=session_env)

    assert before["data"]["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Off] (Alt+M/K/L)"
    assert requested["data"]["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready (restart)] (Alt+M/K/L)"
    assert requested["data"]["local_enabled"] is True
    assert requested["data"]["effective_local_enabled"] is False
    assert requested["data"]["policy_transition"]["local_restart_required"] is True
    assert requested["data"]["status_authority"]["restart_required"] is True

    fresh_env = {
        **session_env,
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-b.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "session-b",
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "session-b-status.json"),
        "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED": "1",
    }
    fresh = json_result("codex-status", "--json", env=fresh_env)

    assert fresh["data"]["local_enabled"] is True
    assert fresh["data"]["effective_local_enabled"] is True
    assert fresh["data"]["policy_transition"]["local_restart_required"] is False
    assert fresh["data"]["status_authority"]["local_restart_required"] is False


def test_qwendex_codex_status_reports_unusable_local_state(tmp_path):
    status_file = tmp_path / "codex_status.json"
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    json_result("manager", "local", "--set", "on", "--json", env=env)
    status = json_result("codex-status", "--write", str(status_file), "--json", env=env)
    manager = json_result("manager", "status", "--json", env=env)

    assert status["data"]["text"] == "{Qwendex} Agent Manager: [Auto] | Kaveman: [N] | Local: [Unavailable] (Alt+M/K/L)"
    assert status["data"]["local_enabled"] is True
    assert status["data"]["local_usable"] is False
    assert status["data"]["local_state"] == "unavailable"
    assert manager["data"]["local_subagents"]["enabled"] is True


def test_qwendex_codex_patch_preflight_version_manifest(tmp_path):
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.143.0\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    data = json_result("codex-patch", "preflight", "--codex-bin", str(fake_codex), "--json")

    assert data["status"] == "pass"
    assert data["data"]["version"]["version"] == "0.143.0"
    assert data["data"]["supported"] is True
    assert data["data"]["manifest"]["status_line_item"] == "qwendex-manager"
    assert "qwendex_toggle_manager" in data["data"]["manifest"]["keymap_actions"]
    assert "qwendex_toggle_kaveman" in data["data"]["manifest"]["keymap_actions"]
    assert "qwendex_toggle_local" in data["data"]["manifest"]["keymap_actions"]


def test_qwendex_codex_patch_apply_updates_supported_source_checkout(tmp_path):
    qwendex = load_qwendex()
    source = tmp_path / "codex"
    anchors_by_path = {
        str(spec["path"]): "\n".join(str(anchor) for anchor in spec["anchors"])
        for spec in qwendex.CODEX_PATCH_MANIFESTS["0.143.0"]["source_anchors"]
    }
    for spec in qwendex.codex_source_patch_specs("0.143.0"):
        rel = str(spec["path"])
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        old_fragments = "\n".join(old for old, _new in spec["replacements"])
        path.write_text(f"{anchors_by_path.get(rel, '')}\n{old_fragments}\n", encoding="utf-8")

    fake_codex = tmp_path / "codex-bin"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.143.0\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    applied = json_result("codex-patch", "apply", "--codex-bin", str(fake_codex), "--source", str(source), "--json")
    preflight = json_result(
        "codex-patch",
        "preflight",
        "--codex-bin",
        str(fake_codex),
        "--source",
        str(source),
        "--require-applied",
        "--json",
    )
    reapplied = json_result("codex-patch", "apply", "--codex-bin", str(fake_codex), "--source", str(source), "--json")

    assert applied["status"] == "pass"
    assert applied["data"]["apply"]["changed"] is True
    assert preflight["status"] == "pass"
    assert preflight["data"]["applied"] is True
    assert reapplied["status"] == "pass"
    assert reapplied["data"]["apply"]["changed"] is False
    assert qwendex.QWENDEX_CODEX_PATCH_MARKER in (
        source / "codex-rs/tui/src/app/input.rs"
    ).read_text(encoding="utf-8")
    assert (
        "fn run_qwendex_toggle_command(&mut self, tui: &mut tui::Tui, label: &str, args: &[&str])"
        in (source / "codex-rs/tui/src/app/input.rs").read_text(encoding="utf-8")
    )
    assert (
        '"Latest task progress from update_plan (omitted until available)"\n            }\n'
        in (source / "codex-rs/tui/src/bottom_pane/status_line_setup.rs").read_text(
            encoding="utf-8"
        )
    )
    assert "qwendex_toggle_manager" in (
        source / "codex-rs/tui/src/keymap.rs"
    ).read_text(encoding="utf-8")
    assert "qwendex_toggle_kaveman" in (
        source / "codex-rs/tui/src/keymap.rs"
    ).read_text(encoding="utf-8")
    terminal_instructions = (
        source / "codex-rs/tui/src/terminal_visualization_instructions.rs"
    ).read_text(encoding="utf-8")
    status_preview = (
        source / "codex-rs/tui/src/bottom_pane/status_surface_preview.rs"
    ).read_text(encoding="utf-8")
    assert "Local: [Ready]" in status_preview
    assert "Local: [Y]" not in status_preview
    assert "qwendex_kaveman_directive" in terminal_instructions
    assert "QWENDEX_CODEX_STATUS_FILE" in terminal_instructions
    assert "Qwendex Kaveman directive" in terminal_instructions
    assert "if !visualization_enabled && kaveman_directive.is_none()" in terminal_instructions
    assert "return control_instructions;" in terminal_instructions
    models_manager = (
        source / "codex-rs/models-manager/src/manager.rs"
    ).read_text(encoding="utf-8")
    assert qwendex.QWENDEX_CODEX_PATCH_MARKER in models_manager
    assert "QWENDEX_MODELS_CACHE_FILE" in models_manager
    assert "var_os" in models_manager
    config_mod = (source / "codex-rs/core/src/config/mod.rs").read_text(encoding="utf-8")
    config_tests = (
        source / "codex-rs/core/src/config/config_tests.rs"
    ).read_text(encoding="utf-8")
    assert "multi_agent_v2_ignores_legacy_agents_max_threads" in config_tests
    assert "downstream legacy setting keeps V2 launches backward compatible" in config_mod
    wait_handler = (
        source / "codex-rs/core/src/tools/handlers/multi_agents_v2/wait.rs"
    ).read_text(encoding="utf-8")
    wait_spec = (
        source / "codex-rs/core/src/tools/handlers/multi_agents_spec.rs"
    ).read_text(encoding="utf-8")
    assert "WaitOutcome::NoRunningAgents" in wait_handler
    assert "Do not retry wait_agent" in wait_handler
    assert "Returns immediately when no child is running" in wait_spec


def test_qwendex_codex_patch_preflight_rejects_partially_applied_source(tmp_path):
    qwendex = load_qwendex()
    source = tmp_path / "codex"
    manifest = qwendex.CODEX_PATCH_MANIFESTS["0.144.6"]
    for index, spec in enumerate(manifest["source_anchors"]):
        rel = str(spec["path"])
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(str(anchor) for anchor in spec["anchors"])
        if index == 0:
            text += f"\n// {qwendex.QWENDEX_CODEX_PATCH_MARKER}\n"
        path.write_text(text + "\n", encoding="utf-8")

    fake_codex = tmp_path / "codex-bin"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.144.6\\n'\n", encoding="utf-8")
    fake_codex.chmod(0o755)

    state = qwendex.codex_source_patch_state(source, manifest)
    preflight_result = run_qwendex(
        "codex-patch",
        "preflight",
        "--codex-bin",
        str(fake_codex),
        "--source",
        str(source),
        "--require-applied",
        "--json",
    )
    preflight = json.loads(preflight_result.stdout)

    assert state["anchors_ok"] is True
    assert state["partially_applied"] is True
    assert state["applied"] is False
    assert len(state["patch_marker_hits"]) == 1
    assert len(state["missing_patch_markers"]) == len(manifest["source_anchors"]) - 1
    assert preflight_result.returncode == 1
    assert preflight["status"] == "blocked"
    assert preflight["data"]["applied"] is False


def test_qwendex_route_command_and_auto_exec_prefer_local_qwen_when_available(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--synthetic", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "qwen"
    assert route["data"]["requested_seat"] == "auto"
    assert route["data"]["local_qwen"]["available"] is True
    assert route["data"]["local_subagents"]["local_state"] == "ready"
    assert route["data"]["local_subagents"]["local_available"] is True
    assert route["data"]["local_subagents"]["local_usable"] is True
    assert route["data"]["local_subagents"]["indicator"] == "(Alt+L) Local: [Ready]"
    assert route["data"]["routing"]["prefer_local_qwen_when_available"] is True
    assert exec_data["data"]["seat"] == "qwen"
    assert exec_data["data"]["routing"]["seat"] == "qwen"
    assert receipt["routing"]["seat"] == "qwen"
    assert receipt["model"] == "qwen-local"


def test_qwendex_auto_route_falls_back_to_primary_when_local_qwen_is_unavailable(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "exec", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--synthetic", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert route["status"] == "pass"
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen_eligible"] is True
    assert route["data"]["local_subagents"]["enabled"] is True
    assert route["data"]["local_subagents"]["local_state"] == "unavailable"
    assert route["data"]["local_subagents"]["local_available"] is False
    assert route["data"]["local_subagents"]["local_usable"] is False
    assert route["data"]["local_subagents"]["indicator"] == "(Alt+L) Local: [Unavailable]"
    assert route["data"]["local_subagents"]["usable"] is False
    assert exec_data["data"]["seat"] == "primary"
    assert receipt["seat"] == "primary"
    assert receipt["model"] == "gpt-5.5"


def test_qwendex_route_unavailable_probe_keeps_local_intent_and_falls_back_primary(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_UNAVAILABLE": "1",
    }

    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert route["data"]["seat"] == "primary"
    assert route["data"]["reason"] == "fallback_seat"
    assert route["data"]["reasoning_source"] == "fallback_policy"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is True
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen"]["source"] == "env"
    assert route["data"]["local_qwen"]["reason"] == "forced_unavailable"
    assert route["data"]["local_subagents"]["enabled"] is True
    assert route["data"]["local_subagents"]["available"] is False
    assert route["data"]["local_subagents"]["local_state"] == "unavailable"
    assert route["data"]["local_subagents"]["local_available"] is False
    assert route["data"]["local_subagents"]["local_usable"] is False
    assert route["data"]["local_subagents"]["usable"] is False


def test_qwendex_route_force_available_false_falls_back_to_primary(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    local = json_result("manager", "local", "--set", "on", "--json", env=env)
    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert local["data"]["local_subagents"]["enabled"] is True
    assert local["data"]["local_subagents"]["local_state"] == "unavailable"
    assert local["data"]["local_subagents"]["available"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen"]["available"] is False
    assert route["data"]["local_qwen"]["reason"] == "forced_unavailable"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is True


def test_qwendex_route_prefer_local_respects_local_toggle_off(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    local = json_result("manager", "local", "--set", "off", "--json", env=env)
    route = json_result("route", "--task-class", "artifact summary", "--prefer-local", "--json", env=env)

    assert local["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["reason"] == "local_subagents_disabled"
    assert route["data"]["reasoning_source"] == "fallback_policy"
    assert route["data"]["token_saver_used"] is False
    assert route["data"]["local_qwen_eligible"] is False
    assert route["data"]["local_qwen"]["source"] == "not_probed"
    assert route["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["local_subagents"]["usable"] is False


def test_qwendex_local_off_route_never_selects_qwen(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "receipts"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    local = json_result("manager", "local", "--set", "off", "--json", env=env)
    route = json_result("route", "--task-class", "exec", "--prefer-local", "--json", env=env)
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--seat", "auto", "--synthetic", "--json", env=env)
    receipt = json.loads(Path(exec_data["artifacts"][0]).read_text(encoding="utf-8"))

    assert local["data"]["local_subagents"]["enabled"] is False
    assert route["data"]["seat"] == "primary"
    assert route["data"]["local_qwen_eligible"] is False
    assert route["data"]["token_saver_used"] is False
    assert exec_data["data"]["seat"] == "primary"
    assert exec_data["data"]["routing"]["seat"] == "primary"
    assert exec_data["data"]["routing"]["local_qwen_eligible"] is False
    assert receipt["seat"] == "primary"
    assert receipt["model"] == "gpt-5.5"


def test_qwendex_receipt_blocks_outside_json_reads(tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"not": "a qwendex receipt"}), encoding="utf-8")

    result = run_qwendex("receipt", str(outside), "--json")
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "trusted receipt roots" in data["summary"]


def test_qwendex_receipt_verifies_schema_and_digest(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }
    exec_data = json_result("exec", "Reply exactly QWENDEX_OK", "--synthetic", "--json", env=env)
    receipt_path = Path(exec_data["artifacts"][0])

    loaded = json_result("receipt", str(receipt_path), "--json", env=env)
    assert loaded["data"]["verification"]["verified"] is True

    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["output"] = "CHANGED"
    receipt_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = run_qwendex("receipt", str(receipt_path), "--json", env=env)
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "verification failed" in data["summary"]
    assert "sha256 mismatch" in " ".join(data["errors"])


def test_qwendex_eval_defaults_to_full_suite(monkeypatch):
    qwendex = load_qwendex()
    calls = {}

    class FakeEvalModule:
        DEFAULT_RESULTS_ROOT = ROOT / "results" / "fake"

        @staticmethod
        def run_harness_eval(**kwargs):
            calls.update(kwargs)
            return {
                "success": True,
                "case_ids": ["a", "b"],
                "receipts": ["results/fake/a.json", "results/fake/b.json"],
                "failures": [],
            }

    monkeypatch.setattr(qwendex, "script_module", lambda name: FakeEvalModule)
    cfg = qwendex.load_qwendex_config(project_config=ROOT / "config/qwendex/qwendex.json", user_config=ROOT / "missing-user.json")
    args = qwendex.command_line().parse_args(["eval"])

    data = qwendex.command_eval(args, cfg)

    assert data["status"] == "pass"
    assert calls["case_id"] == ""
    assert calls["run_all"] is True
    assert data["data"]["metrics"]["total_cases"] == 2
    assert data["data"]["metrics"]["passed_cases"] == 2
    assert data["data"]["manager_estimate"]["release_risk"] in {"low", "medium", "high"}


def test_qwendex_learning_allowlist_preflight_denies_unsafe_paths(tmp_path):
    qwendex = load_qwendex()
    unsafe_paths = [
        Path("hooks/hooks.json"),
        Path(".codex/config.toml"),
        Path("config/local_llm_stack/local_harness.env"),
        Path("scripts/local_qwen_bridge/server.py"),
        Path("state/research/example.csv"),
        Path("public/qwendex/security.md"),
    ]

    for path in unsafe_paths:
        assert qwendex.is_learning_preflight_path_allowed(path) is False

    assert qwendex.is_learning_preflight_path_allowed(Path("tests/smoke/../../hooks/hooks.json")) is False
    assert qwendex.is_learning_preflight_path_allowed(Path("/tmp/qwendex-not-in-repo/SKILL.md")) is False
    assert qwendex.is_learning_preflight_path_allowed(Path(".codex/skills/qwendex-note/SKILL.md")) is True
    result = run_qwendex("learn", "adopt", "--proposal", str(tmp_path / "missing.json"), "--json")
    data = parse_json_result(result)
    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "explicit approval" in data["summary"]

    approved_missing = run_qwendex("learn", "adopt", "--proposal", str(tmp_path / "missing.json"), "--approve", "--json")
    approved_missing_data = parse_json_result(approved_missing)
    assert approved_missing.returncode != 0
    assert approved_missing_data["status"] == "blocked"
    assert "valid proposal" in approved_missing_data["summary"]

    proposal = tmp_path / "proposal.json"
    proposal.write_text(json.dumps({"changed_files": ["tests/smoke/test_allowed.py"]}), encoding="utf-8")
    approved = json_result("learn", "adopt", "--proposal", str(proposal), "--approve", "--json")
    assert approved["status"] == "pass"
    assert approved["data"]["paths"] == ["tests/smoke/test_allowed.py"]
    assert approved["data"]["preflight_status"] == "pass"
    assert approved["data"]["adoption_performed"] is False
    assert approved["data"]["mutation_performed"] is False
    assert "no files were adopted" in approved["summary"]

    malformed = tmp_path / "malformed-proposal.json"
    malformed.write_text(
        json.dumps({"changed_files": ["tests/smoke/test_allowed.py", {"path": "hooks/hooks.json"}]}),
        encoding="utf-8",
    )
    malformed_result = run_qwendex("learn", "adopt", "--proposal", str(malformed), "--approve", "--json")
    malformed_data = parse_json_result(malformed_result)
    assert malformed_result.returncode != 0
    assert malformed_data["status"] == "blocked"
    assert "metadata" in " ".join(malformed_data["errors"])


def test_qwendex_learning_builtin_mock_default_and_disabled_mode(monkeypatch):
    qwendex = load_qwendex()
    config = json.loads(json.dumps(qwendex.DEFAULT_CONFIG))
    parser = qwendex.command_line()

    monkeypatch.setattr(qwendex.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        qwendex,
        "script_module",
        lambda name: (_ for _ in ()).throw(AssertionError("external wrapper must not run")),
    )
    result = qwendex.command_learn(parser.parse_args(["learn", "dry-run"]), config)
    assert result["status"] == "pass"
    assert result["artifacts"] == []
    assert result["data"]["source"] == "builtin_mock"
    assert result["data"]["backend"] == "mock"
    assert result["data"]["backend_source"] == "config_default"
    assert result["data"]["execution_performed"] is False
    assert result["data"]["mutation_performed"] is False
    assert result["data"]["proposal_generated"] is False
    assert result["data"]["adoption_performed"] is False

    config["learning"]["mode"] = "disabled"
    disabled = qwendex.command_learn(parser.parse_args(["learn", "dry-run"]), config)
    assert disabled["status"] == "blocked"
    assert disabled["errors"] == ["learning.mode=disabled"]
    assert disabled["data"]["execution_performed"] is False

    disabled_status = qwendex.command_learn(parser.parse_args(["learn", "status"]), config)
    assert disabled_status["status"] == "pass"
    assert disabled_status["data"]["status"] == "disabled"


def test_qwendex_learning_uses_configured_default_backend(monkeypatch):
    qwendex = load_qwendex()
    config = json.loads(json.dumps(qwendex.DEFAULT_CONFIG))
    config["learning"]["default_backend"] = "codex"
    calls = {}

    class FakeSkillOpt:
        @staticmethod
        def run_skillopt_action(action, **kwargs):
            calls.update({"action": action, **kwargs})
            return {"status": "ready", "action": action, "backend": kwargs["backend"]}

    monkeypatch.setattr(qwendex.shutil, "which", lambda name: "/usr/bin/skillopt-sleep")
    monkeypatch.setattr(qwendex, "script_module", lambda name: FakeSkillOpt)
    args = qwendex.command_line().parse_args(["learn", "dry-run"])
    result = qwendex.command_learn(args, config)

    assert result["status"] == "pass"
    assert calls["backend"] == "codex"
    assert result["data"]["backend"] == "codex"
    assert result["data"]["backend_source"] == "config_default"


def test_qwendex_redacts_secret_like_values_from_output_and_receipts(tmp_path):
    qwendex = load_qwendex()
    cfg = qwendex.load_qwendex_config(
        cli_overrides={"receipts": {"dir": str(tmp_path)}},
        project_config=ROOT / "config/qwendex/qwendex.json",
        user_config=tmp_path / "missing.json",
    )
    secret_text = "password=supersecretvalue123 secret=anothersecretvalue456 api_key=fakeapikeyvalue789"

    envelope = qwendex.stable_envelope(command="test", status="fail", summary=secret_text, errors=[secret_text], data={"stdout": secret_text})
    receipt = qwendex.write_receipt(cfg, "redaction", {"stdout_tail": secret_text})
    receipt_data = json.loads(receipt.read_text(encoding="utf-8"))

    assert "[redacted]" in envelope["summary"]
    assert "[redacted]" in envelope["errors"][0]
    assert "[redacted]" in envelope["data"]["stdout"]
    assert "[redacted]" in receipt_data["stdout_tail"]
    assert "supersecretvalue123" not in json.dumps(envelope) + json.dumps(receipt_data)


def test_qwendex_manager_mode_cycles_status_and_legacy_alias(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    auto = json_result("manager", "mode", "--set", "auto", "--json", env=env)
    cycled = json_result("manager", "mode", "--cycle", "--json", env=env)
    status = json_result("manager", "status", "--json", env=env)
    legacy_result = run_qwendex("manager", "--mode", "manager_only", "--max-subagents", "6", "--stale-after-minutes", "45", "--shortcut", "--json", env=env)
    legacy = parse_json_result(legacy_result)
    disabled = json_result(
        "manager",
        "--mode",
        "manager_only",
        "--max-subagents",
        "8",
        "--stale-after-minutes",
        "45",
        "--shortcut",
        "--json",
        env={**env, "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled"},
    )

    assert auto["data"]["mode"] == "auto"
    assert auto["data"]["label"] == "Auto"
    assert auto["data"]["ui_indicator"] == "(Alt+M) Agent Manager: [ Auto ]"
    assert auto["data"]["kaveman_indicator"] == "(Alt+K) Kaveman: [N]"
    assert auto["data"]["kaveman_enabled"] is False
    assert auto["data"]["local_indicator"] == "(Alt+L) Local: [Ready]"
    assert auto["data"]["hotkeys"] == {
        "source": "codex_tui_keymap",
        "manager": "Alt+M",
        "local": "Alt+L",
        "kaveman": "Alt+K",
        "configurable_in_qwendex": False,
    }
    assert cycled["data"]["mode"] == "lite"
    assert status["data"]["mode"] == "lite"
    assert status["data"]["active_subagents"]["count"] == 0
    assert status["data"]["stale_sessions"]["count"] == 0
    assert len(status["data"]["high_value_add"]) <= 2

    assert legacy_result.returncode == 0
    assert legacy["status"] == "standby"
    assert legacy["data"]["mode"] == "manager"
    assert legacy["data"]["legacy_mode"] == "manager_only"
    assert legacy["data"]["label"] == "Manager Mode"
    assert legacy["data"]["manager_deploy_policy"] == "auto"
    assert legacy["data"]["deployment_contract"]["blocking"] is False
    assert legacy["data"]["deployment_contract"]["advisory"] is True
    assert legacy["data"]["deployment_contract"]["healthy"] is True
    assert legacy["data"]["deployment_contract"]["status"] == "standby"
    assert "shortcut" not in legacy["data"]
    assert "shortcut_command" not in legacy["data"]
    assert legacy["data"]["max_subagents"] == 6
    assert legacy["data"]["stale_after_minutes"] == 45
    assert "borrowed_patterns" not in legacy["data"]
    assert {"selected_model", "selected_reasoning", "reasoning_source", "escalation_reason", "token_saver_used", "local_qwen_eligible"} <= set(legacy["data"]["lane_template"][0])

    assert disabled["status"] == "ready"
    assert disabled["data"]["manager_deploy_policy"] == "disabled"
    assert disabled["data"]["deployment_contract"]["blocking"] is False
    assert disabled["data"]["deployment_contract"]["advisory"] is True
    assert disabled["data"]["deployment_contract"]["healthy"] is True
    assert disabled["data"]["max_subagents"] == 8


def test_qwendex_manager_mode_toggle_cycles_full_duty_order(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result("manager", "mode", "--set", "off", "--json", env=env)
    seen = []
    for _ in range(6):
        result = run_qwendex("manager", "mode", "--toggle", "--json", env=env)
        toggled = parse_json_result(result)
        assert result.returncode == 0
        assert toggled["status"] == "pass"
        seen.append(toggled["data"]["mode"])

    assert seen == ["auto", "lite", "medium", "heavy", "manager", "off"]

    status_result = run_qwendex("manager", "status", "--json", env=env)
    status = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status["data"]["mode"] == "off"
    assert status["data"]["agent_policy"]["mode"] == "off"
    assert status["data"]["agent_policy_source"] == "manager-mode"
    assert status["data"]["agent_policy"]["root_can_spawn"] is False
    assert status["data"]["deployment_contract"]["status"] == "ready"


def test_qwendex_selected_manager_mode_drives_agent_policy_and_hooks(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    env = with_live_manager_identity(env)
    selected = json_result("manager", "mode", "--set", "manager", "--json", env=env)
    policy = json_result("agent", "policy", "--json", env=env)
    status = json_result("manager", "status", "--json", env=env)

    assert selected["data"]["mode"] == "manager"
    assert selected["data"]["agent_policy"]["mode"] == "manager"
    assert selected["data"]["agent_policy_source"] == "manager-mode"
    assert policy["data"]["agent_policy"]["mode"] == "manager"
    assert policy["data"]["agent_policy"]["source"] == "manager-mode"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy"]["require_agent_ledger"] is False
    assert status["data"]["agent_policy"]["require_final_report_contract"] is False

    preflight = json_result(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--json",
        env=env,
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "selected-manager-session",
        "turn_id": "selected-manager-turn",
        "cwd": str(ROOT),
    }
    prompt_hook = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            **turn_identity,
            "prompt": "Use manager mode with subagents to prove selected manager mode gates finalization",
        }),
        "--json",
        env=manager_env,
    )
    required_assignments = [
        assignment
        for assignment in prompt_hook["data"]["manager_decision"]["agent_plan"]["assignments"]
        if assignment["required"]
    ]
    for assignment in required_assignments:
        json_result(
            "manager",
            "assign",
            "--agent-id",
            assignment["agent_id"],
            "--lane",
            assignment["lane"],
            "--task-id",
            prompt_hook["data"]["manager_decision"]["agent_task_id"],
            "--objective",
            "prove selected manager mode gates finalization",
            "--required",
            "--json",
            env=env,
        )
    advisory_stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({**turn_identity, "last_assistant_message": "Done."}),
        "--json",
        env=manager_env,
    )
    advisory_stop = parse_json_result(advisory_stop_result)

    assert advisory_stop_result.returncode == 0
    assert advisory_stop["data"]["agent_policy"]["mode"] == "manager"
    assert advisory_stop["data"]["agent_policy"]["source"] == "manager-mode"
    assert advisory_stop["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert advisory_stop["data"]["hook_result"].get("decision") != "block"
    assert advisory_stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]

    off = json_result("manager", "mode", "--set", "off", "--json", env=env)
    spawn_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "spawn_agent",
            "agent_id": "off-mode-child",
            "agent_type": "explorer",
            "session_id": "off-mode-child-session",
            "cwd": str(ROOT),
        }),
        "--json",
        env=env,
    )
    spawn = parse_json_result(spawn_result)

    assert off["data"]["agent_policy"]["mode"] == "off"
    assert spawn_result.returncode != 0
    assert spawn["data"]["agent_policy"]["mode"] == "off"
    assert spawn["data"]["hook_result"]["event"] == "agent.spawn_rejected"

    override = json_result("agent", "policy", "--json", env={**env, "QWENDEX_AGENT_USE": "Heavy"})
    assert override["data"]["agent_policy"]["mode"] == "heavy"
    assert override["data"]["agent_policy"]["source"] == "qwendex-env"


def test_qwendex_manager_untrusted_stop_allows_process_exit(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "Done."}),
        "--json",
        env=env,
    )
    stop = parse_json_result(stop_result)

    assert stop_result.returncode == 0
    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    assert stop["data"]["launch_health"]["trusted"] is False




def test_qwendex_manager_stop_does_not_recover_preflight_without_exported_env(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
    }

    json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    stop = json_result(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "No edits. Validation: not required. Risks: none."}),
        "--json",
        env=env,
    )

    repeated_stop = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({"last_assistant_message": "No edits. Validation: already closed. Risks: none."}),
        "--codex-hook-output",
        env=env,
    )

    assert preflight["data"]["routing_decision"]["direct_work_exception"] is True
    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    decision = json_result("manager", "decision", "--json", env=env)["data"]["manager_decision"]
    assert decision["ledger_id"] == preflight["data"]["ledger_id"]
    assert decision["final_status"] == "preflight_ready"
    assert repeated_stop.returncode == 0
    repeated_output = json.loads(repeated_stop.stdout)
    assert repeated_output["continue"] is True
    assert "systemMessage" not in repeated_output


def test_qwendex_manager_root_tools_do_not_require_preflight_identity_or_locks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    root_event = {
        "session_id": "codex-root-session",
        "turn_id": "root-turn-1",
        "cwd": str(repo),
    }
    unattached_write = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_input": {"cmd": "touch generated.txt"},
        }),
        "--json",
        env=env,
    )

    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    manager_env = {**env, **preflight["data"]["exports"]}
    json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({**root_event, "prompt": "Implement the requested change."}),
        "--json",
        env=manager_env,
    )
    attached_write = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "apply_patch",
            "tool_use_id": "root-write",
            "tool_input": {"path": "README.md"},
        }),
        "--json",
        env=manager_env,
    )
    with sqlite3.connect(tmp_path / "qwendex.sqlite") as conn:
        active_root_locks = conn.execute(
            "SELECT COUNT(*) FROM qwendex_agent_file_locks WHERE released_at = '' AND agent_id LIKE 'manager-root-%'"
        ).fetchone()[0]

    assert unattached_write.returncode == 0
    assert attached_write.returncode == 0
    assert parse_json_result(unattached_write)["data"]["hook_result"].get("decision") != "block"
    assert parse_json_result(attached_write)["data"]["hook_result"].get("decision") != "block"
    assert active_root_locks == 0


def test_qwendex_codex_hook_adapter_keeps_root_advisory_and_observes_worker_stop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    root_identity = {
        "session_id": "adapter-root-session",
        "turn_id": "adapter-root-turn",
        "cwd": str(repo),
    }

    prompt_result = run_qwendex(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            **root_identity,
            "prompt": "Inspect routing across files and verify the bounded change",
        }),
        "--codex-hook-output",
        env=manager_env,
    )
    prompt_output = json.loads(prompt_result.stdout)
    assert prompt_result.returncode == 0
    assert set(prompt_output) == {"hookSpecificOutput"}
    assert prompt_output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    prompt_context = prompt_output["hookSpecificOutput"]["additionalContext"]
    assert "advisory" in prompt_context.lower()
    assert "(suggested, read-only)" in prompt_context
    assert "(required, read-only)" not in prompt_context

    publish_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            **root_identity,
            "tool_name": "exec_command",
            "tool_input": {"cmd": "git push origin main"},
        }),
        "--codex-hook-output",
        env=manager_env,
    )
    assert publish_result.returncode == 0
    assert json.loads(publish_result.stdout) == {}

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "adapter-worker",
        "--lane",
        "implementation",
        "--task-id",
        "adapter-task",
        "--repo-root",
        str(repo),
        "--write-surface",
        "tracked.txt",
        "--json",
        env=env,
    )
    child_start = run_qwendex(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "adapter-worker",
            "agent_type": "implementer",
            "session_id": "adapter-child-session",
            "turn_id": "adapter-child-turn",
            "cwd": str(repo),
        }),
        "--codex-hook-output",
        env=env,
    )
    child_output = json.loads(child_start.stdout)
    assert child_start.returncode == 0
    assert set(child_output) == {"hookSpecificOutput"}
    assert child_output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"

    json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "adapter-worker",
            "profile": "implementer",
            "path": "tracked.txt",
            "cwd": str(repo),
        }),
        "--json",
        env=env,
    )
    assert json_result("agent", "locks", "--json", env=env)["data"]["write_safety"][
        "active_writer_count"
    ] == 1

    child_stop = run_qwendex(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "adapter-worker",
            "cwd": str(repo),
            "last_assistant_message": "Bounded implementation inspection complete.",
        }),
        "--codex-hook-output",
        env=env,
    )
    assert child_stop.returncode == 0
    assert json.loads(child_stop.stdout) == {}
    status = json_result("agent", "status", "--json", env=env)["data"]
    worker = next(
        item for item in status["agent_sessions"] if item["agent_id"] == "adapter-worker"
    )
    assert worker["status"] == "completed"
    assert worker["final_report_present"] is False
    assert worker["stop_reason"] == "unstructured_worker_outcome"
    assert status["write_safety"]["active_writer_count"] == 0


def test_qwendex_managed_hooks_fail_open_when_lifecycle_state_is_unavailable(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    invalid_parent = tmp_path / "not-a-directory"
    invalid_parent.write_text("file", encoding="utf-8")
    env = {
        "QWENDEX_STATE_DB": str(invalid_parent / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_MANAGER_ROOT_AGENT_ID": "manager-root-unavailable-state",
    }
    root = {
        "session_id": "unavailable-root-session",
        "turn_id": "unavailable-root-turn",
        "cwd": str(repo),
    }
    cases = {
        "SessionStart": root,
        "UserPromptSubmit": {**root, "prompt": "Use a worker to inspect this repository"},
        "PreToolUse": {
            **root,
            "tool_name": "spawn_agent",
            "tool_input": {"task_name": "inspection"},
        },
        "PostToolUse": {**root, "tool_name": "apply_patch", "tool_use_id": "root-write"},
        "PreCompact": root,
        "PostCompact": root,
        "Stop": {**root, "last_assistant_message": "Done."},
        "SubagentStart": {
            "agent_id": "unavailable-worker",
            "agent_type": "explorer",
            "session_id": "unavailable-child-session",
            "turn_id": "unavailable-child-turn",
            "cwd": str(repo),
        },
        "SubagentStop": {
            "agent_id": "unavailable-worker",
            "agent_type": "explorer",
            "session_id": "unavailable-child-session",
            "turn_id": "unavailable-child-turn",
            "cwd": str(repo),
            "last_assistant_message": "Inspection complete.",
        },
    }

    outputs = {}
    for event_name, event in cases.items():
        result = run_qwendex(
            "--agent-use",
            "Manager",
            "agent",
            "hook",
            event_name,
            "--event-json",
            json.dumps(event),
            "--codex-hook-output",
            env=env,
        )
        assert result.returncode == 0, f"{event_name}: {result.stderr or result.stdout}"
        outputs[event_name] = json.loads(result.stdout)

    assert outputs["Stop"]["continue"] is True
    assert outputs["PreToolUse"] == {}
    assert outputs["PostToolUse"] == {}
    assert outputs["SubagentStop"] == {}
    assert outputs["SessionStart"]["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert outputs["UserPromptSubmit"]["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert outputs["SubagentStart"]["hookSpecificOutput"]["hookEventName"] == "SubagentStart"


@pytest.mark.parametrize("failure_stage", ["resolver", "reservation"])
def test_qwendex_root_spawn_bookkeeping_exception_is_advisory(monkeypatch, failure_stage):
    qwendex = load_qwendex()
    policy = qwendex.agent_policy_defaults("manager")

    def unavailable_bookkeeping(*_args, **_kwargs):
        raise OSError(f"{failure_stage} store unavailable")

    if failure_stage == "resolver":
        monkeypatch.setattr(qwendex, "resolve_manager_decision", unavailable_bookkeeping)
    else:
        monkeypatch.setattr(
            qwendex,
            "resolve_manager_decision",
            lambda *_args, **_kwargs: {
                "status": "attached",
                "decision": {"ledger_id": "ledger"},
            },
        )
        monkeypatch.setattr(qwendex, "reserve_manager_native_spawn", unavailable_bookkeeping)
    result = qwendex.pre_tool_gate(
        {},
        {
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(ROOT),
            "tool_name": "spawn_agent",
            "tool_input": {"task_name": "inspection"},
        },
        policy,
    )

    assert result["event"] == "manager.subagent_plan_unavailable"
    assert result.get("decision") != "block"
    assert result["reason_code"].startswith("bookkeeping_unavailable:")


def test_qwendex_root_post_tool_cleanup_exception_is_advisory(monkeypatch):
    qwendex = load_qwendex()
    policy = qwendex.agent_policy_defaults("manager")
    monkeypatch.setenv("QWENDEX_MANAGER_ROOT_AGENT_ID", "manager-root-cleanup-test")

    def unavailable_cleanup(*_args, **_kwargs):
        raise OSError("cleanup store unavailable")

    monkeypatch.setattr(
        qwendex,
        "manager_root_cleanup_identity_for_event",
        unavailable_cleanup,
    )
    status, hook_result, data = qwendex.evaluate_agent_hook(
        {},
        event_name="PostToolUse",
        event={
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(ROOT),
            "tool_name": "apply_patch",
            "tool_use_id": "root-write",
        },
        agent_policy=policy,
    )

    assert status == "pass"
    assert qwendex.codex_hook_output("PostToolUse", hook_result) == {}
    assert data["released_root_locks"] == []
    assert data["cleanup_warning"].startswith(
        "root tool cleanup bookkeeping unavailable:"
    )


def test_qwendex_non_manager_codex_root_keeps_normal_write_plane(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    json_result("manager", "mode", "--set", "off", "--json", env=env)
    result = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-root-session",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=env,
    )
    structured_outside = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-root-session",
            "cwd": str(repo),
            "tool_name": "write",
            "tool_input": {"path": "/tmp/non-manager-output.txt"},
        }),
        "--json",
        env=env,
    )
    locks = json_result("agent", "locks", "--json", env=env)
    assert result["data"]["hook_result"] == {}
    assert structured_outside["data"]["hook_result"] == {}
    assert locks["data"]["write_safety"]["active_count"] == 0


def test_qwendex_medium_native_child_keeps_legacy_explicit_path_locking(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    json_result("manager", "mode", "--set", "medium", "--json", env=env)
    result = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "medium-child-session",
            "turn_id": "medium-child-turn",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "unregistered-medium-child",
            "agent_type": "implementer",
            "tool_input": {"path": "file.txt"},
        }),
        "--json",
        env=env,
    )
    assert result["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert result["data"]["hook_result"]["acquired"][0]["path"] == "file.txt"


def test_qwendex_manager_cwdless_stop_never_attaches_by_repository(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight_a = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json",
        env={**env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_a)},
    )
    preflight_b = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json",
        env={**env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_b)},
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE qwendex_manager_decisions SET timestamp_updated = '9999-01-01T00:00:00Z' WHERE ledger_id = ?",
            (preflight_b["data"]["ledger_id"],),
        )

    stop = json_result(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({"last_assistant_message": "No edits were needed.", "edit_happened": False}),
        "--json",
        env={
            **env,
            "QWENDEX_MANAGER_TARGET_REPO": str(repo_a),
            "QWENDEX_MANAGER_LEDGER_ID": "",
            "QWENDEX_MANAGER_SESSION_ID": "",
        },
    )
    with sqlite3.connect(state_db) as conn:
        statuses = dict(conn.execute("SELECT ledger_id, final_status FROM qwendex_manager_decisions"))

    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    assert statuses[preflight_a["data"]["ledger_id"]] == "preflight_ready"
    assert statuses[preflight_b["data"]["ledger_id"]] == "preflight_ready"


def test_qwendex_manager_stop_with_only_generated_state_env_allows_untrusted_exit(tmp_path):
    qwendex = load_qwendex()
    real_root = tmp_path / "real"
    link_root = tmp_path / "linked"
    work_root = real_root / ".qwendex-dev"
    codex_home = work_root / "codex_home"
    real_root.mkdir()
    link_root.symlink_to(real_root, target_is_directory=True)
    linked_codex_home = link_root / ".qwendex-dev" / "codex_home"
    env = {
        "CODEX_HOME": str(linked_codex_home),
        "QWENDEX_AGENT_USE": "Manager",
        "QWENDEX_STATE_DB": "",
        "QWENDEX_LEDGER_DB": "",
        "QWENDEX_RESULTS_ROOT": "",
    }

    installed = json_result("agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    payload = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    stop_command = payload["hooks"]["Stop"][0]["hooks"][0]["command"]
    hook_env = {
        "HOME": os.environ.get("HOME", ""),
        "PATH": os.environ.get("PATH", ""),
        "CODEX_HOME": str(codex_home),
        "QWENDEX_AGENT_USE": "Manager",
        "QWENDEX_STATE_DB": "",
        "QWENDEX_LEDGER_DB": "",
        "QWENDEX_RESULTS_ROOT": "",
    }
    stop = subprocess.run(
        stop_command,
        cwd=ROOT,
        env=hook_env,
        input=json.dumps({"last_assistant_message": "No edits. Validation: not required. Risks: none."}),
        text=True,
        shell=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    decision = json_result("manager", "decision", "--json", env=env)["data"]["manager_decision"]

    assert installed["data"]["hook_status"]["missing_runtime_env_events"] == []
    assert "QWENDEX_STATE_DB=" in stop_command
    assert stop.returncode == 0, stop.stderr or stop.stdout
    stop_output = json.loads(stop.stdout)
    assert stop_output["continue"] is True
    assert "systemMessage" not in stop_output
    assert preflight["data"]["ledger_id"] == decision["ledger_id"]
    assert qwendex.path_digest_policy(codex_home) == qwendex.path_digest_policy(linked_codex_home)
    assert decision["codex_home_digest_or_path_policy"] == qwendex.path_digest_policy(codex_home)
    assert decision["stop_status"] == "STOP_MANAGER_PREFLIGHT_READY"
    assert (work_root / "state" / "qwendex.sqlite").is_file()
    receipt_paths = decision["receipt_paths"]
    assert receipt_paths
    assert all(".qwendex-dev/results/qwendex" in receipt for receipt in receipt_paths)


def test_qwendex_manager_preflight_records_decision_ledger_and_hook_status(tmp_path):
    qwendex = load_qwendex()
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    cli_mode = json_result(
        "manager",
        "preflight",
        "--mode",
        "manager",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env=env,
    )
    assert cli_mode["data"]["mode"] == "manager"
    assert cli_mode["data"]["selected_manager_mode"] == "manager"
    assert cli_mode["data"]["effective_agent_mode"] == "manager"
    assert cli_mode["data"]["policy_source"] == "manager-mode"
    assert cli_mode["data"]["manager_required"] is True

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    missing_hook_result = run_qwendex("manager", "preflight", "--interactive-prompt-unknown", "--dry-run", "--json", env=env)
    missing_hook = parse_json_result(missing_hook_result)
    assert missing_hook_result.returncode == 0
    assert missing_hook["data"]["mode"] == "manager"
    assert missing_hook["data"]["agent_use"] == "Manager"
    assert missing_hook["data"]["policy_source"] == "manager-mode"
    assert missing_hook["data"]["hook_status"]["hook_source_count"] == 0
    assert missing_hook["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert missing_hook["data"]["stop_status"] == "STOP_MANAGER_PREFLIGHT_READY"

    partial_codex_home = tmp_path / "partial_codex_home"
    partial_codex_home.mkdir()
    (partial_codex_home / "hooks.json").write_text(
        json.dumps({
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "scripts/qwendex agent hook Stop --json",
                            }
                        ]
                    }
                ]
            }
        }),
        encoding="utf-8",
    )
    partial_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(partial_codex_home)},
    )
    partial = parse_json_result(partial_result)
    assert partial_result.returncode == 0
    assert partial["data"]["hook_status"]["hook_source_count"] == 1
    assert partial["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert partial["data"]["hook_status"]["verified"] is False
    assert partial["data"]["hook_status"]["incompatible_events"] == ["Stop"]
    assert "UserPromptSubmit" in partial["data"]["hook_status"]["missing_events"]
    assert partial["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert partial["data"]["stop_status"] == "STOP_MANAGER_PREFLIGHT_READY"

    stale_codex_home = tmp_path / "stale_codex_home"
    stale_codex_home.mkdir()
    stale_hooks = {
        "hooks": {
            event_name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"scripts/qwendex agent hook {event_name} --json",
                        }
                    ]
                }
            ]
            for event_name in qwendex.MANAGED_AGENT_HOOKS
        }
    }
    (stale_codex_home / "hooks.json").write_text(json.dumps(stale_hooks), encoding="utf-8")
    stale_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(stale_codex_home)},
    )
    stale = parse_json_result(stale_result)
    assert stale_result.returncode == 0
    assert stale["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert stale["data"]["hook_status"]["verified"] is False
    assert set(stale["data"]["hook_status"]["incompatible_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert stale["data"]["stop_status"] == "STOP_MANAGER_PREFLIGHT_READY"

    plain_codex_home = tmp_path / "plain_codex_home"
    plain_codex_home.mkdir()
    plain_hooks = {
        "hooks": {
            event_name: [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"scripts/qwendex agent hook {event_name} --codex-hook-output",
                        }
                    ]
                }
            ]
            for event_name in qwendex.MANAGED_AGENT_HOOKS
        }
    }
    (plain_codex_home / "hooks.json").write_text(json.dumps(plain_hooks), encoding="utf-8")
    plain_result = run_qwendex(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "CODEX_HOME": str(plain_codex_home)},
    )
    plain = parse_json_result(plain_result)
    assert plain_result.returncode == 0
    assert plain["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["verified"] is False
    assert set(plain["data"]["hook_status"]["missing_runtime_env_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert plain["data"]["stop_status"] == "STOP_MANAGER_PREFLIGHT_READY"

    installed = json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    assert installed["data"]["hook_status"]["verified"] is True
    assert installed["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)

    env = with_live_manager_identity(env)
    ready = json_result(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--json",
        env=env,
    )
    assert ready["data"]["ok"] is True
    assert ready["data"]["hook_status"]["verified"] is True
    assert ready["data"]["hook_status"]["override"] is False
    assert ready["data"]["ledger_id"].startswith("mgrldg_")
    assert ready["data"]["root_agent_id"].startswith("manager-root-mgrldg_")
    assert (
        ready["data"]["exports"]["QWENDEX_MANAGER_ROOT_AGENT_ID"]
        == ready["data"]["root_agent_id"]
    )
    assert ready["data"]["prompt"]["known"] is False
    prompt_text = "Small edit with validation evidence"
    admitted = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "preflight-record-session",
            "turn_id": "preflight-record-turn",
            "cwd": str(ROOT),
            "prompt": prompt_text,
        }),
        "--json",
        env={**env, **ready["data"]["exports"]},
    )
    admitted_decision = admitted["data"]["manager_decision"]
    assert admitted_decision["prompt_known"] is True
    assert admitted_decision["prompt_digest"]
    assert admitted_decision["prompt_summary"] == "privacy_safe_prompt_metadata"
    assert admitted_decision["prompt_source"] == "UserPromptSubmit"
    assert admitted_decision["prompt_length"] == len(prompt_text)
    assert prompt_text not in json.dumps(admitted_decision)
    assert admitted_decision["agent_plan"]["required_lanes"] == []
    assert admitted_decision["agent_plan"]["optional_lanes"] == [
        {"lane": "verification", "profile": "verifier", "write": False}
    ]
    receipt = Path(ready["data"]["receipt_paths"][0])
    assert receipt.exists()
    assert prompt_text not in receipt.read_text(encoding="utf-8")

    decision = json_result("manager", "decision", "--agent-id", ready["data"]["ledger_id"], "--json", env=env)
    assert decision["data"]["manager_decision"]["record_type"] == "manager_decision"
    assert decision["data"]["manager_decision"]["ledger_id"] == ready["data"]["ledger_id"]
    assert decision["data"]["manager_decision"]["root_agent_id"] == ready["data"]["root_agent_id"]

    env_override = json_result(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--dry-run",
        "--json",
        env={**env, "QWENDEX_AGENT_USE": "Heavy"},
    )
    assert env_override["data"]["manager_required"] is True
    assert env_override["data"]["selected_manager_mode"] == "manager"
    assert env_override["data"]["effective_agent_mode"] == "heavy"
    assert env_override["data"]["policy_source"] == "qwendex-env"


def test_qwendex_manager_local_toggle_controls_local_lane_eligibility(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    off = json_result("manager", "local", "--set", "off", "--json", env=env)
    estimate_off = json_result("manager", "estimate", "--prompt", "Summarize receipts from results/qwendex.", "--json", env=env)
    on = json_result("manager", "local", "--toggle", "--json", env=env)
    estimate_on = json_result("manager", "estimate", "--prompt", "Summarize receipts from results/qwendex.", "--json", env=env)

    assert off["data"]["local_indicator"] == "(Alt+L) Local: [Off]"
    assert off["data"]["local_subagents"]["enabled"] is False
    assert estimate_off["data"]["local_subagents"]["usable"] is False
    assert estimate_off["data"]["estimate"]["higher_reasoning_lanes"] == []
    assert estimate_off["data"]["reasoning_policy"]["default_lane"]["local_qwen_eligible"] is False

    assert on["data"]["local_indicator"] == "(Alt+L) Local: [Ready]"
    assert on["data"]["local_subagents"]["enabled"] is True
    assert estimate_on["data"]["local_subagents"]["usable"] is True
    assert estimate_on["data"]["reasoning_policy"]["default_lane"]["local_qwen_eligible"] is True


def test_manager_prompt_routing_keeps_launch_local_routing_after_global_toggle(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    json_result("manager", "local", "--set", "on", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    session_hash = preflight["data"]["policy_hash"]
    assert preflight["data"]["policy_snapshot"]["local_routing_snapshot"]["enabled"] is True

    json_result("manager", "local", "--set", "off", "--json", env=env)
    prompt = (
        "Investigate and summarize the existing receipt artifacts and report their "
        "current validation state without editing files."
    )
    routed = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "immutable-local-session",
            "turn_id": "immutable-local-turn",
            "cwd": str(repo),
            "prompt": prompt,
        }),
        "--json",
        env={**env, **preflight["data"]["exports"]},
    )
    decision = routed["data"]["manager_decision"]
    assignment = decision["agent_plan"]["assignments"][0]
    health = json_result(
        "manager",
        "launch-status",
        "--pid",
        env["QWENDEX_MANAGER_LAUNCH_PID"],
        "--repo-root",
        str(repo),
        "--json",
        env={**env, **preflight["data"]["exports"]},
    )["data"]

    assert decision["policy_hash"] == session_hash
    assert decision["local_enabled"] is True
    assert assignment["routing"]["token_saver_used"] is True
    assert assignment["routing"]["local_qwen_available"] is True
    assert health["session_policy_hash"] == session_hash
    assert health["desired_global_policy_hash"] != session_hash
    assert health["policy_drift"] is True
    assert health["session_policy_valid"] is True
    assert health["restart_required"] is True


def test_qwendex_session_context_respects_local_toggle_for_token_saver_context(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    json_result("manager", "local", "--set", "off", "--json", env=env)
    off_hook = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "SessionStart",
        "--event-json",
        "{}",
        "--json",
        env=env,
    )
    off_context = off_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]

    json_result("manager", "local", "--set", "on", "--json", env=env)
    on_hook = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "SessionStart",
        "--event-json",
        "{}",
        "--json",
        env=env,
    )
    on_context = on_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]

    assert "default lane model=qwen-local" not in off_context
    assert "token_saver=true" not in off_context
    assert "default lane model=qwen-local" not in on_context
    assert "token_saver=true" not in on_context
    assert "root orchestrator" in on_context
    assert "bounded follow-up to that verifier" in on_context
    assert "do not retry wait_agent" in on_context


def test_qwendex_manager_estimate_is_bounded_and_reasoning_agnostic():
    simple = json_result("manager", "estimate", "--prompt", "Fix a typo in public docs.", "--json")
    top_level = json_result("estimate", "--prompt", "Fix a typo in public docs.", "--json")
    heavy = json_result(
        "manager",
        "estimate",
        "--prompt",
        "Change security architecture, protocol routing, docs, and release tests across several files.",
        "--json",
    )

    assert simple["data"]["estimate"]["recommended_mode"] in {"auto", "lite"}
    assert top_level["command"] == "estimate"
    assert top_level["data"]["estimate"] == simple["data"]["estimate"]
    assert top_level["data"]["reasoning_policy"] == simple["data"]["reasoning_policy"]
    assert simple["data"]["estimator"] == {
        "kind": "deterministic_heuristic",
        "implementation": "qwendex_cli_rules",
        "model_invoked": False,
        "skill_invoked": False,
        "recommendation_model": "gpt-5.5",
        "default_reasoning": "medium",
    }
    assert simple["data"]["reasoning_policy"]["main_session"]["reasoning_source"] == "user_selected"
    for field in (
        "task_complexity",
        "risk",
        "likely_file_scope",
        "validation_depth",
        "subagent_usefulness",
        "recommended_mode",
        "confidence",
        "higher_reasoning_lanes",
    ):
        assert field in simple["data"]["estimate"]

    assert heavy["data"]["estimate"]["recommended_mode"] in {"heavy", "manager"}
    assert heavy["data"]["estimate"]["higher_reasoning_lanes"]
    escalated = heavy["data"]["estimate"]["higher_reasoning_lanes"][0]
    assert escalated["selected_reasoning"] in {"high", "xhigh"}
    assert escalated["escalation_reason"]
    assert len(heavy["data"]["high_value_add"]) <= 2


def test_qwendex_agent_policy_selector_precedence_fallback_and_strict(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_AGENT_USE": "manager-mode",
    }
    env_policy = json_result("agent", "policy", "--json", env=env)
    cli_policy = json_result(
        "--agent-use",
        "Lite",
        "agent",
        "policy",
        "--json",
        env={**env, "QWENDEX_AGENT_USE": "Manager"},
    )
    fallback = json_result(
        "agent",
        "policy",
        "--json",
        env={"QWENDEX_STATE_DB": str(tmp_path / "fallback.sqlite"), "QWENDEX_AGENT_USE": "sideways"},
    )
    strict_result = run_qwendex(
        "agent",
        "policy",
        "--json",
        env={
            "QWENDEX_STATE_DB": str(tmp_path / "strict.sqlite"),
            "QWENDEX_AGENT_USE": "sideways",
            "QWENDEX_AGENT_USE_STRICT": "1",
        },
    )
    strict = parse_json_result(strict_result)

    assert env_policy["data"]["agent_policy"]["mode"] == "manager"
    assert env_policy["data"]["agent_policy"]["source"] == "qwendex-env"
    assert len(env_policy["data"]["agent_policy"]["policy_hash"]) == 64
    assert env_policy["data"]["agent_policy"]["env"]["QWENDEX_EFFECTIVE_AGENT_USE"] == "Manager"
    assert env_policy["data"]["agent_policy"]["require_agent_ledger"] is False
    assert env_policy["data"]["agent_policy"]["child_can_spawn"] is False
    assert env_policy["data"]["agent_policy"]["max_threads"] == 4
    assert env_policy["data"]["agent_policy"]["capacity_source"] == "orchestration.mode_profiles"
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["root_management_tools"]
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["denied_child_tools"]

    assert cli_policy["data"]["agent_policy"]["mode"] == "lite"
    assert cli_policy["data"]["agent_policy"]["source"] == "cli"
    assert cli_policy["data"]["agent_policy"]["root_can_spawn"] is True
    assert cli_policy["data"]["agent_policy"]["max_threads"] == 1

    assert fallback["data"]["agent_policy"]["mode"] == "medium"
    assert fallback["data"]["agent_policy"]["warnings"]
    assert fallback["data"]["agent_policy"]["source"] == "qwendex-env-fallback"
    assert fallback["data"]["agent_policy"]["max_threads"] == 2

    assert strict_result.returncode != 0
    assert strict["status"] == "blocked"
    assert "invalid agent use selector" in " ".join(strict["errors"])


def test_mode_profile_capacity_drives_status_and_agent_policy(tmp_path):
    config_path = tmp_path / "capacity.json"
    config_path.write_text(
        json.dumps({"orchestration": {"mode_profiles": {"heavy": {"max_subagents": 5}}}}),
        encoding="utf-8",
    )
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result(
        "--config", str(config_path), "manager", "mode", "--set", "heavy", "--json", env=env
    )
    status = json_result("--config", str(config_path), "manager", "status", "--json", env=env)
    policy = json_result(
        "--config", str(config_path), "--agent-use", "Heavy", "agent", "policy", "--json", env=env
    )

    assert status["data"]["max_subagents"] == 5
    assert status["data"]["agent_policy"]["max_threads"] == 5
    assert policy["data"]["agent_policy"]["max_threads"] == 5
    assert policy["data"]["agent_policy"]["capacity_source"] == "orchestration.mode_profiles"


@pytest.mark.parametrize(
    ("selector", "worker_cap", "native_cap"),
    [
        ("Off", 0, 1),
        ("Lite", 1, 2),
        ("Medium", 2, 3),
        ("Heavy", 3, 4),
        ("Manager", 4, 5),
    ],
)
def test_agent_policy_default_capacity_contract(tmp_path, selector, worker_cap, native_cap):
    policy = json_result(
        "--agent-use",
        selector,
        "agent",
        "policy",
        "--json",
        env={"QWENDEX_STATE_DB": str(tmp_path / f"{selector}.sqlite")},
    )["data"]["agent_policy"]

    assert policy["max_workers"] == worker_cap
    assert policy["max_threads"] == worker_cap
    assert policy["native_max_concurrent_threads"] == native_cap
    assert policy["max_workers"] <= 8


def test_qwendex_agent_status_alias_tracks_manager_ledger_and_bounded_close(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-ledger-1",
        "--lane",
        "verification",
        "--task-id",
        "task-agent",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    status = json_result("--agent-use", "Manager", "agent", "status", "--json", env=env)
    inspected = json_result("agent", "inspect", "agent-ledger-1", "--json", env=env)
    closed = json_result("agent", "close", "agent-ledger-1", "--timeout", "1s", "--reason", "integrated", "--json", env=env)
    after = json_result("--agent-use", "Manager", "agent", "status", "--json", env=env)

    assert assigned["data"]["agent_session"]["status"] == "active"
    assert status["data"]["mode"] == "manager"
    assert status["data"]["agent_use"] == "Manager"
    assert status["data"]["agent_policy"]["source"] == "cli"
    assert status["data"]["active_subagents"]["count"] == 1
    assert inspected["data"]["agent_session"]["agent_id"] == "agent-ledger-1"
    assert closed["data"]["closed_count"] == 1
    assert closed["data"]["bounded_close"] is True
    assert closed["data"]["close_timeout_ms"] == 1000
    assert closed["data"]["closed"][0]["status"] == "close_requested"
    assert closed["data"]["closed"][0]["stop_reason"] == "integrated"
    assert after["data"]["active_subagents"]["count"] == 1
    assert after["data"]["active_subagents"]["agents"][0]["status"] == "close_requested"


def test_qwendex_agent_profiles_and_team_are_visible():
    profiles = json_result("agent", "profiles", "--json")
    team = json_result("agent", "team", "--json")

    assert {"explorer", "implementer", "verifier", "docs_researcher", "release_manager", "scribe"} <= set(profiles["data"]["profiles"])
    assert profiles["data"]["profiles"]["explorer"]["sandbox_mode"] == "read-only"
    assert profiles["data"]["profiles"]["explorer"]["can_spawn"] is False
    assert "publish" in profiles["data"]["profiles"]["release_manager"]["tools_deny"]
    assert all(
        profile["default_required"] is False
        and profile["final_report_required"] is False
        for profile in profiles["data"]["profiles"].values()
    )
    assert team["data"]["team"]["default_mode"] == "Manager"
    assert team["data"]["team"]["required_lanes_by_task"] == {}
    assert "verifier" in team["data"]["team"]["suggested_lanes_by_task"]["code_edit_complex"]


def test_qwendex_agent_plan_routes_direct_team_and_release(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    direct = json_result(
        "--agent-use",
        "Lite",
        "agent",
        "plan",
        "--prompt",
        "What command lists active Qwendex agents?",
        "--task-id",
        "task-direct",
        "--json",
        env=env,
    )
    team = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "plan",
        "--prompt",
        "Team, change manager routing across code and tests.",
        "--task-id",
        "task-team",
        "--json",
        env=env,
    )
    release = json_result(
        "--agent-use",
        "Heavy",
        "agent",
        "plan",
        "--prompt",
        "Prepare release notes and version bump for publish.",
        "--task-id",
        "task-release",
        "--json",
        env=env,
    )
    local = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "plan",
        "--prompt",
        "Team, summarize small artifact receipts.",
        "--task-id",
        "task-local",
        "--json",
        env=env,
    )

    direct_plan = direct["data"]["agent_plan"]
    team_plan = team["data"]["agent_plan"]
    release_plan = release["data"]["agent_plan"]
    local_plan = local["data"]["agent_plan"]

    assert direct_plan["direct_work"] is True
    assert direct_plan["assignments"] == []
    assert "trivial" in direct_plan["direct_work_exception"]

    assert team_plan["direct_work"] is False
    assert team_plan["profiles"] == ["explorer", "verifier", "reviewer"]
    assert team_plan["required_lanes"] == []
    assert team_plan["optional_lanes"] == [
        {"lane": "exploration", "profile": "explorer", "write": False},
        {"lane": "verification", "profile": "verifier", "write": False},
        {"lane": "review", "profile": "reviewer", "write": False}
    ]
    assert all(item["write_surface"] == "read-only" for item in team_plan["assignments"])
    assert all(item["assign_command"].startswith("qwendex manager assign") for item in team_plan["assignments"])
    assert all("--required" not in item["assign_command"] for item in team_plan["assignments"])
    assert all(item["required"] is False for item in team_plan["assignments"])

    assert release_plan["profiles"] == ["reviewer", "verifier"]
    assert release_plan["required_lanes"] == []
    assert release_plan["optional_lanes"] == [
        {"lane": "review", "profile": "reviewer", "write": False},
        {"lane": "verification", "profile": "verifier", "write": False},
    ]
    assert release_plan["assignments"][0]["routing"]["selected_model"] == "gpt-5.5"
    assert release_plan["assignments"][0]["routing"]["selected_reasoning"] in {"high", "xhigh"}
    assert "task-release" in release_plan["assignments"][0]["assign_command"]

    local_assignment = local_plan["assignments"][0]
    assert local_assignment["write_surface"] == "read-only"
    assert "gpt-5.5" not in local_assignment["spawn_instruction"]
    assert "model selection inherited from Codex" in local_assignment["spawn_instruction"]


def test_manager_turn_classifier_and_auto_mode_matrix_are_deterministic():
    qwendex = load_qwendex()
    cases = [
        ("What is 2 + 2?", "trivial_direct", "lite"),
        ("Read one file scripts/qwendex_cli.py", "single_file_read", "lite"),
        ("Map the repository files and implementation flow", "repository_mapping", "medium"),
        (
            "Map the end-to-end repository implementation flow. Do not edit files.",
            "repository_mapping",
            "medium",
        ),
        ("Investigate the runtime behavior", "read_heavy_investigation", "medium"),
        ("Fix a typo", "small_edit", "lite"),
        ("Refactor the runtime implementation", "nontrivial_edit", "heavy"),
        (
            "Implement required_tags filtering in the report implementation with existing tag normalization and focused regression coverage",
            "nontrivial_edit",
            "heavy",
        ),
        ("Update routing across multiple files", "cross_cutting_edit", "manager"),
        ("Run pytest regression tests", "test_or_regression", "medium"),
        ("Audit the security protocol", "security_or_protocol", "manager"),
        ("Prepare the release for publish", "release_or_publish", "manager"),
        ("Create a git tag for the release", "release_or_publish", "manager"),
        ("Run live acceptance against the harness", "live_acceptance", "manager"),
    ]

    for prompt, task_class, auto_mode in cases:
        assert qwendex.classify_manager_turn(prompt) == task_class
        assert qwendex.classify_manager_turn(prompt) == task_class
        assert qwendex.effective_manager_turn_mode("auto", task_class) == auto_mode


@pytest.mark.parametrize("mode", ["medium", "heavy", "manager"])
def test_manager_prompt_bookkeeping_is_advisory_by_mode(tmp_path, mode):
    repo = tmp_path / mode / "repo"
    repo.mkdir(parents=True)
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / mode / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / mode / "results"),
        "CODEX_HOME": str(tmp_path / mode / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", mode, "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    result = run_qwendex(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({"session_id": f"{mode}-session", "turn_id": f"{mode}-turn", "cwd": str(repo)}),
        "--json",
        env={**env, **preflight["data"]["exports"]},
    )
    payload = parse_json_result(result)
    decision = payload["data"]["manager_decision"]

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["hook_result"].get("decision") != "block"
    assert payload["data"]["hook_result"]["event"] == "manager.prompt_bookkeeping_unavailable"
    assert decision["admission_error_code"] == "prompt_field_missing"
    assert decision["prompt_known"] is False
    assert decision["prompt_summary"] == "privacy_safe_prompt_metadata_unavailable"
    assert decision["selected_route"] == "direct_single_writer"


def test_manager_hook_messages_never_expose_configured_gpt_model(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config_path = tmp_path / "routing.json"
    config_path.write_text(
        json.dumps({"seats": {"primary": {"model": "gpt-5.5"}}}),
        encoding="utf-8",
    )
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    common = ("--config", str(config_path))
    json_result(*common, "manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        *common, "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    session_start = json_result(
        *common, "agent", "hook", "SessionStart", "--event-json", "{}", "--json", env=manager_env
    )
    prompt_hook = json_result(
        *common,
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "model-private-session",
            "turn_id": "model-private-turn",
            "cwd": str(repo),
            "prompt": "Map the repository files and implementation flow",
        }),
        "--json",
        env=manager_env,
    )
    planned_agent_id = prompt_hook["data"]["agent_plan"]["assignments"][0]["agent_id"]
    reservation = json_result(
        *common,
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "session_id": "model-private-session",
            "turn_id": "model-private-turn",
            "cwd": str(repo),
            "tool_name": "spawn_agent",
            "tool_use_id": "model-private-spawn",
            "tool_input": {"task_name": planned_agent_id},
        }),
        "--json",
        env=manager_env,
    )
    subagent_start = json_result(
        *common,
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "runtime-model-private-explorer",
            "agent_type": "explorer",
            "task_name": planned_agent_id,
            "parent_session_id": "model-private-session",
            "session_id": "model-private-child-session",
            "turn_id": "model-private-child-turn",
            "cwd": str(repo),
        }),
        "--json",
        env=manager_env,
    )

    for payload in (session_start, prompt_hook, reservation, subagent_start):
        hook_result = payload["data"]["hook_result"]
        additional_context = (hook_result.get("hookSpecificOutput") or {}).get("additionalContext", "")
        user_facing_messages = " ".join(
            str(hook_result.get(field) or "")
            for field in ("reason", "systemMessage")
        ) + f" {additional_context}"
        assert "gpt-5.5" not in user_facing_messages


def test_manager_subagent_start_attaches_advisory_plan_without_pretool_reservation(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    prompt = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "native-root-session",
            "turn_id": "native-root-turn",
            "cwd": str(repo),
            "prompt": "Implement a cross-file routing change and add regression tests",
        }),
        "--json",
        env=manager_env,
    )
    assignment = prompt["data"]["agent_plan"]["assignments"][0]
    planned_agent_id = assignment["agent_id"]
    native_task_name = f"{'/' + 'root'}/{planned_agent_id}"

    started = json_result(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "native-runtime-explorer",
            "agent_type": "explorer",
            "task_name": native_task_name,
            "parent_session_id": "native-root-session",
            "session_id": "native-child-session",
            "turn_id": "native-child-turn",
            "cwd": str(repo),
        }),
        "--json",
        env=manager_env,
    )
    session = started["data"]["agent_session"]
    assert session["agent_id"] == "native-runtime-explorer"
    assert session["task_id"] == prompt["data"]["manager_decision"]["agent_task_id"]
    assert session["status"] == "active"
    assert session["write_surface"] == "read-only"
    assert session["context_packet"]["planned_agent_id"] == planned_agent_id
    assert session["context_packet"]["registration_source"] == "SubagentStart"
    assert session["context_packet"]["parent_session_id"] == "native-root-session"
    assert session["context_packet"]["review_requirement"] == "root review suggested"

    status_result = run_qwendex("manager", "status", "--json", env=manager_env)
    status_payload = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status_payload["status"] == "warning"
    assert status_payload["data"]["deployment_contract"]["status"] == "ready"
    assert status_payload["data"]["deployment_contract"]["healthy"] is True
    status = status_payload["data"]["session_status"]
    assert status["registered_agent_count"] == 1
    assert status["active_agent_count"] == 1
    assert status["reserved_agent_count"] == 0

    duplicate = run_qwendex(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({
            "agent_id": "native-runtime-duplicate",
            "agent_type": "explorer",
            "task_name": native_task_name,
            "parent_session_id": "native-root-session",
            "session_id": "native-duplicate-child-session",
            "turn_id": "native-duplicate-child-turn",
            "cwd": str(repo),
        }),
        "--json",
        env=manager_env,
    )
    duplicate_payload = parse_json_result(duplicate)
    assert duplicate.returncode == 0
    assert duplicate_payload["data"]["hook_result"]["reason_code"] == (
        "native_spawn_assignment_duplicate"
    )
    duplicate_context = duplicate_payload["data"]["hook_result"][
        "hookSpecificOutput"
    ]["additionalContext"]
    assert "Continue the assigned task normally" in duplicate_context
    assert "Do not perform the task or call tools" not in duplicate_context
    deduplicated_result = run_qwendex("manager", "status", "--json", env=manager_env)
    deduplicated_payload = parse_json_result(deduplicated_result)
    assert deduplicated_result.returncode == 0
    assert deduplicated_payload["status"] == "warning"
    deduplicated_status = deduplicated_payload["data"]["session_status"]
    assert deduplicated_status["registered_agent_count"] == 1


def test_manager_ultra_source_survives_prompt_routing_and_session_status(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_NATIVE_PROACTIVE_SOURCE": "native_ultra",
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    routed = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "ultra-native-root",
            "turn_id": "ultra-native-turn",
            "cwd": str(repo),
            "prompt": "Map the repository implementation flow and report the evidence",
        }),
        "--json",
        env=manager_env,
    )

    assert routed["data"]["manager_decision"]["policy_snapshot"][
        "native_proactive_source"
    ] == "native_ultra"
    assert routed["data"]["agent_plan"]["native_proactive_source"] == "native_ultra"
    status_result = run_qwendex("manager", "status", "--json", env=manager_env)
    status_payload = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status_payload["status"] == "ready"
    assert status_payload["data"]["deployment_contract"]["status"] == "ready"
    assert status_payload["data"]["deployment_contract"]["healthy"] is True
    status = status_payload["data"]["session_status"]
    assert status["native_proactive_source"] == "native_ultra"


def test_codex_status_and_preflight_hash_the_same_native_ultra_launch_policy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "codex-status.json"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_NATIVE_PROACTIVE_SOURCE": "native_ultra",
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    status = json_result(
        "codex-status",
        "--write",
        env["QWENDEX_CODEX_STATUS_FILE"],
        "--json",
        env=env,
    )
    preflight = json_result(
        "manager",
        "preflight",
        "--interactive-prompt-unknown",
        "--json",
        env=env,
    )

    assert status["data"]["agent_policy"]["native_proactive_source"] == "native_ultra"
    assert preflight["data"]["policy_snapshot"]["native_proactive_source"] == "native_ultra"
    assert status["data"]["agent_policy_hash"] == preflight["data"]["policy_hash"]


def test_manager_tampered_policy_snapshot_does_not_gate_session_start(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    ledger_id = preflight["data"]["ledger_id"]
    with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
        row = conn.execute(
            "SELECT policy_snapshot_json FROM qwendex_manager_decisions WHERE ledger_id = ?",
            (ledger_id,),
        ).fetchone()
        assert row is not None
        snapshot = json.loads(row[0])
        snapshot["max_workers"] = int(snapshot["max_workers"]) + 1
        conn.execute(
            "UPDATE qwendex_manager_decisions SET policy_snapshot_json = ? WHERE ledger_id = ?",
            (json.dumps(snapshot, sort_keys=True), ledger_id),
        )
    manager_env = {**env, **preflight["data"]["exports"]}

    result = run_qwendex(
        "agent",
        "hook",
        "SessionStart",
        "--event-json",
        json.dumps({"session_id": "tampered-root", "cwd": str(repo)}),
        "--json",
        env=manager_env,
    )
    payload = parse_json_result(result)

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["hook_result"].get("decision") != "block"
    assert "Manager Mode" in payload["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]


def test_manager_suggested_lanes_are_advisory_in_status_and_stop(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    turn = {"session_id": "lane-session", "turn_id": "lane-turn", "cwd": str(repo)}
    prompt_hook = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({**turn, "prompt": "Update routing across multiple files"}),
        "--json",
        env=manager_env,
    )
    plan = prompt_hook["data"]["manager_decision"]["agent_plan"]
    assert plan["required_lanes"] == []
    suggested_lanes = plan["optional_lanes"]
    assert suggested_lanes == [
        {"lane": "exploration", "profile": "explorer", "write": False},
        {"lane": "verification", "profile": "verifier", "write": False},
        {"lane": "review", "profile": "reviewer", "write": False},
    ]

    status_result = run_qwendex("manager", "status", "--json", env=manager_env)
    status_payload = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status_payload["status"] == "ready"
    assert status_payload["data"]["deployment_contract"]["status"] == "ready"
    assert status_payload["data"]["deployment_contract"]["healthy"] is True
    status = status_payload["data"]["session_status"]
    assert status["task_class"] == "cross_cutting_edit"
    assert status["route"] == "orchestrated_single_writer"
    assert status["schema_version"] == "qwendex.manager_session_status.v2"
    assert status["suggested_lane_count"] == 3
    assert status["planned_lane_count"] == 3
    assert status["registered_agent_count"] == 0
    assert status["why_no_agent"] == "suggested lanes have not been registered"
    assert status["unstarted_suggested_lanes"] == suggested_lanes
    assert status["unresolved_suggested_lanes"] == []
    assert status["suggested_lanes"] == suggested_lanes

    stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({**turn, "last_assistant_message": "Done.", "edit_happened": False}),
        "--json",
        env=manager_env,
    )
    stop = parse_json_result(stop_result)
    assert stop_result.returncode == 0
    assert stop["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert stop["data"]["hook_result"].get("decision") != "block"
    assert "the advisory subagent plan produced no recorded worker sessions" in stop["data"]["advisories"]


def test_manager_preflight_reports_qdex_permission_mutation_as_advisory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_QDEX_PERMISSION_MODE": "workspace-write",
        "QWENDEX_QDEX_PERMISSION_SOURCE": "published-config",
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    decision = preflight["data"]
    assert decision["qdex_permission_mode"] == "workspace-write"
    assert decision["qdex_permission_source"] == "published-config"
    assert decision["qdex_permission"] == {
        "mode": "workspace-write",
        "source": "published-config",
        "valid": True,
    }
    with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
        row = conn.execute(
            "SELECT qdex_permission_mode, qdex_permission_source FROM qwendex_manager_decisions WHERE ledger_id = ?",
            (decision["ledger_id"],),
        ).fetchone()
    assert row == ("workspace-write", "published-config")

    mutated_env = {
        **env,
        **decision["exports"],
        "QWENDEX_QDEX_PERMISSION_MODE": "yolo",
        "QWENDEX_QDEX_PERMISSION_SOURCE": "environment",
    }
    result = run_qwendex(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            "session_id": "permission-root",
            "turn_id": "permission-turn",
            "cwd": str(repo),
            "prompt": "Explain status",
        }),
        "--json",
        env=mutated_env,
    )
    payload = parse_json_result(result)
    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["hook_result"]["event"] == "manager.prompt_bookkeeping_unavailable"
    assert payload["data"]["hook_result"]["reason_code"] == "qdex_permission_mismatch"
    assert payload["data"]["hook_result"].get("decision") != "block"


def test_manager_idle_standby_and_attached_direct_work_are_healthy(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    standby = json_result("manager", "status", "--json", env=env)["data"]
    assert standby["deployment_contract"]["status"] == "standby"
    assert standby["deployment_contract"]["healthy"] is True
    assert "standing by for an attached prompt" in standby["deployment_contract"]["summary"]

    preflight = json_result(
        "manager", "preflight", "--prompt", "Explain status", "--json", env=env
    )
    assert preflight["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    direct = json_result("manager", "status", "--json", env=env)["data"]
    assert direct["session_status"]["route"] == "direct"
    assert direct["session_status"]["direct_reason"]
    assert direct["deployment_contract"]["status"] == "ready"
    assert direct["deployment_contract"]["healthy"] is True
    assert "allows direct work" in direct["deployment_contract"]["summary"]


def test_qwendex_agent_metrics_track_ledger_and_artifacts(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    empty = json_result("agent", "metrics", "--json", env=env)
    assert empty["data"]["agent_metrics"]["session_count"] == 0
    assert empty["data"]["agent_metrics"]["schema_version"] == "qwendex.agent_metrics.v2"
    assert empty["data"]["agent_metrics"]["structured_outcome_observation_rate"] is None

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "metrics-agent",
        "--lane",
        "verification",
        "--task-id",
        "task-metrics",
        "--required",
        "--json",
        env=env,
    )
    active = json_result("agent", "metrics", "--json", env=env)
    assert active["data"]["agent_metrics"]["active_count"] == 1
    assert active["data"]["agent_metrics"]["attention_flagged_incomplete_count"] == 1

    json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "metrics-agent",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nsummary: metric proof\nevidence:\n- ok",
        }),
        "--json",
        env=env,
    )
    metrics = json_result("agent", "metrics", "--json", env=env)["data"]["agent_metrics"]
    assert metrics["terminal_count"] == 1
    assert metrics["attention_flagged_incomplete_count"] == 0
    assert metrics["structured_outcome_observed_count"] == 1
    assert metrics["structured_outcome_observation_rate"] == 1.0
    assert metrics["raw_output_artifact_count"] == 1
    assert metrics["managed_hook_event_count"] >= 5


def test_qwendex_concurrent_agent_output_index_retains_every_entry(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    monkeypatch.setattr(qwendex, "ROOT", tmp_path)
    agent_ids = [f"concurrent-agent-{index}" for index in range(16)]

    def capture(agent_id):
        return qwendex.write_agent_output_artifacts(
            event={"run_id": "shared-run"},
            session={"lane": "review", "task_id": "shared-task"},
            agent_id=agent_id,
            message=f"raw output for {agent_id}",
            report_message=f"FINAL_REPORT\nstatus: completed\nsummary: {agent_id}",
            final_status={"status": "completed", "validation_status": "pass"},
            now="2026-07-09T00:00:00Z",
        )

    with ThreadPoolExecutor(max_workers=len(agent_ids)) as executor:
        captures = list(executor.map(capture, agent_ids))

    aggregate = tmp_path / ".qwendex" / "runs" / "shared-run" / "raw-agent-output.md"
    text = aggregate.read_text(encoding="utf-8")
    assert text.count("# Raw Agent Outputs") == 1
    for agent_id in agent_ids:
        assert text.count(f"## {agent_id} - review - 2026-07-09T00:00:00Z") == 1
    assert all(
        capture["compact_report"]["aggregate_raw_output_artifact"].endswith(
            "/shared-run/raw-agent-output.md"
        )
        for capture in captures
    )


def test_qwendex_agent_output_uses_writable_runtime_root_when_source_is_sealed(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    immutable_root = tmp_path / "immutable-generation" / "tree"
    immutable_root.mkdir(parents=True)
    immutable_root.chmod(0o555)
    artifact_root = tmp_path / "operator-root" / ".qwendex"
    monkeypatch.setattr(qwendex, "ROOT", immutable_root)
    monkeypatch.setenv("QWENDEX_AGENT_ARTIFACT_ROOT", str(artifact_root))

    capture = qwendex.write_agent_output_artifacts(
        event={"run_id": "immutable-runtime"},
        session={"lane": "verification", "task_id": "immutable-runtime"},
        agent_id="sealed-runtime-verifier",
        message="raw verifier output",
        report_message="FINAL_REPORT\nstatus: completed\nsummary: verified",
        final_status={"status": "completed", "validation_status": "pass"},
        now="2026-07-13T00:00:00Z",
    )

    assert not (immutable_root / ".qwendex").exists()
    assert (artifact_root / "runs" / "immutable-runtime" / "sealed-runtime-verifier" / "raw-output.md").is_file()
    assert all(Path(path).is_file() for path in capture["artifacts"])


def test_qwendex_agent_output_paths_separate_repositories_with_reused_task_ids(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    monkeypatch.setattr(qwendex, "ROOT", tmp_path)
    captures = []
    for label in ("a", "b"):
        captures.append(
            qwendex.write_agent_output_artifacts(
                event={},
                session={
                    "lane": "review",
                    "task_id": "shared-task",
                    "repo_root": str(tmp_path / f"repo-{label}"),
                },
                agent_id=f"agent-{label}",
                message=f"raw-{label}",
                report_message=f"FINAL_REPORT\nstatus: completed\nsummary: {label}",
                final_status={"status": "completed", "validation_status": "pass"},
                now="2026-07-09T00:00:00Z",
            )
        )

    aggregate_paths = [
        capture["compact_report"]["aggregate_raw_output_artifact"]
        for capture in captures
    ]
    assert aggregate_paths[0] != aggregate_paths[1]
    for label, path in zip(("a", "b"), aggregate_paths, strict=True):
        text = (tmp_path / path).read_text(encoding="utf-8")
        assert f"## agent-{label}" in text
        assert f"## agent-{'b' if label == 'a' else 'a'}" not in text


def test_qwendex_worker_and_root_stop_contracts_are_advisory(tmp_path):
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "manager-stop-session",
        "turn_id": "manager-stop-turn",
        "cwd": str(ROOT),
    }
    prompt = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            **turn_identity,
            "prompt": "Use Manager subagents to inspect and verify this edit",
        }),
        "--json",
        env=manager_env,
    )
    assignment = prompt["data"]["agent_plan"]["assignments"][0]
    agent_id = assignment["agent_id"]
    json_result(
        "manager",
        "assign",
        "--agent-id",
        agent_id,
        "--lane",
        assignment["lane"],
        "--task-id",
        prompt["data"]["manager_decision"]["agent_task_id"],
        "--required",
        "--json",
        env=env,
    )

    worker_stop = run_qwendex(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": agent_id,
            "cwd": str(ROOT),
            "last_assistant_message": "Inspection complete; no structured report.",
        }),
        "--json",
        env=env,
    )
    root_stop = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Done.",
            "edit_happened": True,
        }),
        "--json",
        env=manager_env,
    )
    worker_payload = parse_json_result(worker_stop)
    root_payload = parse_json_result(root_stop)

    assert worker_stop.returncode == 0
    assert worker_payload["data"]["hook_result"]["event"] == "agent.completed"
    assert worker_payload["data"]["final_status"]["has_contract"] is False
    assert worker_payload["data"]["final_status"]["reason"] == "verifier_evidence_not_recorded"
    assert worker_payload["data"]["agent_session"]["status"] == "completed"
    assert worker_payload["data"]["agent_session"]["final_report_present"] is False

    assert root_stop.returncode == 0
    assert root_payload["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert root_payload["data"]["hook_result"].get("decision") != "block"
    assert root_payload["data"]["manager_decision"]["final_status"] == "closed"
    assert not any("workers were still active" in item for item in root_payload["data"]["advisories"])
    assert root_payload["data"]["manager_decision"]["final_status"] == "closed"
    assert root_payload["data"]["manager_decision"]["stop_status"] == "STOP_MANAGER_CLOSED"
    advisories = " ".join(root_payload["data"]["hook_result"]["advisories"])
    assert "validation evidence was not recorded" in advisories
    assert "dirty worktree classification was not recorded" in advisories


def test_qwendex_manager_root_work_never_requires_closeout_wording(tmp_path):
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
    })
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "manager-direct-session",
        "turn_id": "manager-direct-turn",
        "cwd": str(ROOT),
    }
    prompt = json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({**turn_identity, "prompt": "Fix a typo"}),
        "--json",
        env=manager_env,
    )
    root_write = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            **turn_identity,
            "tool_name": "apply_patch",
            "tool_input": {"path": "README.md"},
        }),
        "--json",
        env=manager_env,
    )
    stop = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Done.",
            "edit_happened": True,
        }),
        "--json",
        env=manager_env,
    )
    payload = parse_json_result(stop)

    assert prompt["data"]["manager_decision"]["selected_route"] in {"direct_single_writer", "manager_subagents"}
    assert root_write.returncode == 0
    assert stop.returncode == 0
    assert payload["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert payload["data"]["manager_decision"]["final_status"] == "closed"
    assert payload["data"]["manager_decision"]["validation_result"] == "not_recorded"
def test_qwendex_stop_validation_evidence_requires_positive_outcome_language(tmp_path):
    qwendex = load_qwendex()
    config = qwendex.load_qwendex_config(
        cli_overrides={"receipts": {"dir": str(tmp_path)}}
    )
    passing_receipt = {
        "schema_version": "qwendex.receipt.v1",
        "version": qwendex.VERSION,
        "run_id": "validation-pass",
        "started_at": "2026-07-09T00:00:00Z",
        "repo_root": str(ROOT),
        "status": "pass",
        "sha256": "",
    }
    passing_receipt["sha256"] = qwendex.digest_json(passing_receipt)
    passing_receipt_path = tmp_path / "validation-pass.json"
    passing_receipt_path.write_text(json.dumps(passing_receipt), encoding="utf-8")
    failed_receipt = {**passing_receipt, "run_id": "validation-fail", "status": "fail", "sha256": ""}
    failed_receipt["sha256"] = qwendex.digest_json(failed_receipt)
    failed_receipt_path = tmp_path / "validation-fail.json"
    failed_receipt_path.write_text(json.dumps(failed_receipt), encoding="utf-8")

    for message in (
        "pytest not run",
        "pytest failed",
        "ruff missing",
        "receipt missing",
        "Validation: pytest not tested",
        "Validation: ruff error",
        "Validation: skipped",
        "Validation: pytest",
    ):
        assert qwendex.stop_event_has_validation_evidence({}, message, config=config) is False, message

    for message in (
        "pytest passed",
        "ruff check succeeded",
        "receipt verified",
        "Validation: all tests passed",
        "Validation: clean; no errors",
        "commands_run:\n- `pytest -q`\n- Outcome: 15 passed in 0.02s",
        (
            "validation_status: PASS\n"
            "commands_run:\n"
            "- `pytest -q` — exploratory launcher failed during collection\n"
            "- `python -m pytest -q` — final canonical suite: 15 passed"
        ),
    ):
        assert qwendex.stop_event_has_validation_evidence({}, message, config=config) is True, message

    assert qwendex.stop_event_has_validation_evidence(
        {},
        "commands_run:\n- `pytest -q`\n- Outcome: 1 failed in 0.02s",
        config=config,
    ) is False
    assert qwendex.stop_event_has_validation_evidence(
        {},
        "validation_status: FAIL\ncommands_run:\n- `pytest -q` — 15 passed",
        config=config,
    ) is False

    for event in (
        {"validation_evidence": ["pytest"]},
        {"validation_evidence": ["pytest failed"]},
        {"commands_run": ["pytest -q"]},
        {"commands_run": [{"command": "pytest -q", "returncode": 1}]},
        {"commands_run": [
            {"command": "pytest -q", "returncode": 0},
            {"command": "ruff check", "returncode": 1},
        ]},
        {"receipt_paths": [str(tmp_path / "missing.json")]},
        {"receipt_paths": [str(failed_receipt_path)]},
    ):
        assert qwendex.stop_event_has_validation_evidence(
            event, "Validation: pytest passed", config=config
        ) is False, event

    for event in (
        {"validation_evidence": ["pytest external structured result passed"]},
        {"validation_evidence": [{"status": "pass", "summary": "verified"}]},
        {"commands_run": [{"command": "pytest -q", "returncode": 0}]},
        {"receipt_paths": [str(passing_receipt_path)]},
    ):
        assert qwendex.stop_event_has_validation_evidence(
            event, "pytest not run", config=config
        ) is True, event


def test_qwendex_agent_hook_config_generation_and_write_gate(tmp_path):
    target = tmp_path / "hooks.json"
    codex_home = tmp_path / "codex_home"

    env = {
        "CODEX_HOME": str(codex_home),
        "QWENDEX_STATE_DB": str(tmp_path / "state" / "qwendex.sqlite"),
        "QWENDEX_LEDGER_DB": str(tmp_path / "state" / "qwendex_ledger.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results" / "qwendex"),
        "QWENDEX_CODEX_STATUS_FILE": str(tmp_path / "codex_status.json"),
    }

    rendered = json_result("agent", "hook-config", "--qwendex-command", "scripts/qwendex", "--json", env=env)
    hooks = rendered["data"]["hook_config"]["hooks"]
    stop_command = hooks["Stop"][0]["hooks"][0]["command"]
    assert set(hooks) == set(load_qwendex().MANAGED_AGENT_HOOKS)
    assert "PostToolUse" in hooks
    assert stop_command.startswith("env ")
    assert "QWENDEX_STATE_DB=" in stop_command
    assert "QWENDEX_CODEX_STATUS_FILE=" not in stop_command
    assert stop_command.endswith("scripts/qwendex agent hook Stop --codex-hook-output")
    assert hooks["PreToolUse"][0]["hooks"][0]["timeout"] == 5

    blocked_result = run_qwendex("agent", "hook-config", "--write", str(target), "--json", env=env)
    blocked = parse_json_result(blocked_result)
    assert blocked_result.returncode != 0
    assert blocked["status"] == "blocked"
    assert not target.exists()

    written = json_result(
        "agent",
        "hook-config",
        "--qwendex-command",
        "scripts/qwendex",
        "--write",
        str(target),
        "--approve",
        "--json",
        env=env,
    )
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert written["artifacts"] == [str(target)]
    assert "QWENDEX_STATE_DB=" in payload["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert "QWENDEX_CODEX_STATUS_FILE=" not in payload["hooks"]["SubagentStop"][0]["hooks"][0]["command"]
    assert payload["hooks"]["SubagentStop"][0]["hooks"][0]["command"].endswith("scripts/qwendex agent hook SubagentStop --codex-hook-output")

    overwrite_result = run_qwendex("agent", "hook-config", "--write", str(target), "--approve", "--json", env=env)
    overwrite = parse_json_result(overwrite_result)
    assert overwrite_result.returncode != 0
    assert overwrite["status"] == "blocked"

    verify_missing_result = run_qwendex("agent", "hook-config", "--verify", "--codex-home", str(codex_home), "--json", env=env)
    verify_missing = parse_json_result(verify_missing_result)
    assert verify_missing_result.returncode != 0
    assert verify_missing["data"]["hook_status"]["hook_source_count"] == 0
    assert verify_missing["data"]["hook_status"]["compatible_hook_source_count"] == 0

    installed = json_result("agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json", env=env)
    assert installed["data"]["operator_action"] == "install"
    assert installed["data"]["hook_status"]["verified"] is True
    assert installed["data"]["hook_status"]["compatible_hook_source_count"] >= len(hooks)
    installed_payload = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    installed_payload["hooks"]["PreToolUse"].insert(0, {
        "matcher": "^custom$",
        "hooks": [{"type": "command", "command": "custom-hook", "timeout": 3}],
    })
    (codex_home / "hooks.json").write_text(json.dumps(installed_payload), encoding="utf-8")
    updated = json_result(
        "agent", "hook-config", "--install", "--codex-home", str(codex_home), "--json", env=env
    )
    updated_payload = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    pre_tool_commands = [
        hook["command"]
        for entry in updated_payload["hooks"]["PreToolUse"]
        for hook in entry.get("hooks", [])
    ]
    assert updated["data"]["hook_status"]["verified"] is True
    assert "custom-hook" in pre_tool_commands
    assert sum("agent hook PreToolUse" in command for command in pre_tool_commands) == 1
    verified = json_result("agent", "hook-config", "--verify", "--codex-home", str(codex_home), "--json", env=env)
    assert verified["data"]["hook_status"]["hook_source_count"] >= len(hooks)
    assert verified["data"]["hook_status"]["compatible_hook_source_count"] >= len(hooks)
    assert verified["data"]["hook_status"]["missing_runtime_env_events"] == []
    assert verified["data"]["hook_status"]["verified"] is True

    legacy_payload = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    for entries in legacy_payload["hooks"].values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = str(hook.get("command") or "")
                if "agent hook" in command:
                    hook["command"] = command.replace(
                        "env ",
                        f"env QWENDEX_CODEX_STATUS_FILE={tmp_path / 'legacy-status.json'} ",
                        1,
                    )
    (codex_home / "hooks.json").write_text(json.dumps(legacy_payload), encoding="utf-8")
    legacy_result = run_qwendex("agent", "hook-config", "--verify", "--codex-home", str(codex_home), "--json", env=env)
    legacy = parse_json_result(legacy_result)
    assert legacy_result.returncode != 0
    assert legacy["status"] == "blocked"
    assert set(legacy["data"]["hook_status"]["status_file_override_events"]) == set(load_qwendex().MANAGED_AGENT_HOOKS)


@pytest.mark.parametrize(
    "command",
    [
        "git push --tags",
        "git push origin main",
        "gh release create v0.3.1",
        "npm publish",
    ],
)
def test_qwendex_root_pre_tool_allows_release_without_secondary_approval(tmp_path, command):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    result = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(ROOT),
            "tool_name": "exec_command",
            "command": command,
        }),
        "--json",
        env=env,
    )
    payload = parse_json_result(result)

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["hook_result"].get("decision") != "block"
    assert payload["data"]["hook_result"].get("event") != "agent.release_command_rejected"


def test_qwendex_pre_tool_keeps_intrinsic_child_boundaries_but_never_gates_root(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    child_spawn = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent", "depth": 1}),
        "--json",
        env=env,
    )
    read_only_write = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "session_id": "child-session",
            "turn_id": "child-turn",
            "cwd": str(ROOT),
            "agent_id": "child-a",
            "agent_type": "explorer",
            "tool_name": "apply_patch",
            "profile": "explorer",
        }),
        "--json",
        env=env,
    )
    root_write = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "session_id": "root-session",
            "turn_id": "root-turn",
            "cwd": str(ROOT),
            "tool_name": "apply_patch",
            "tool_input": {"path": "README.md"},
        }),
        "--json",
        env=env,
    )

    assert child_spawn.returncode != 0
    assert parse_json_result(child_spawn)["data"]["hook_result"]["event"] == "agent.spawn_rejected"
    assert read_only_write.returncode != 0
    assert parse_json_result(read_only_write)["data"]["hook_result"]["event"] == "agent.write_rejected"
    assert root_write.returncode == 0
    assert parse_json_result(root_write)["status"] == "pass"
def test_qwendex_read_only_shell_gate_is_fail_closed_and_quote_aware(tmp_path):
    qwendex = load_qwendex()
    safe_commands = (
        "pwd",
        "ls -la",
        'rg -n "needle;literal" scripts',
        'grep -R "needle|literal" scripts',
        "git status --short",
        "git -C . --no-pager diff -- scripts/qwendex_cli.py",
        "git log -n 3 --oneline",
        "git show --stat HEAD",
        "git rev-parse --show-toplevel",
        "cat receipt.json | jq -r '.status'",
        "head -n 5 README.md; tail -n 5 README.md",
        'stat -c "%n %s" README.md',
        "find . -maxdepth 2 -type f -print",
        'rg "foo|bar" scripts | head -n 5',
        "python3 -V",
        "python3.12 --version",
        "file README.md | head -n 1",
        "pwd &&\nls",
        "nl -ba README.md",
        "sed -n '1,160p' README.md",
        'pwd && rg -n "manager" scripts || true && rg --files | sed -n \'1,20p\'',
        "rg '$HOME' scripts",
    )
    rejected_commands = (
        "rm -f output.txt",
        "mv before after",
        "cp source target",
        "touch output.txt",
        "mkdir output",
        "git apply change.patch",
        "perl -pi -e 's/a/b/' file.txt",
        "python3 -c 'open(\"output.txt\", \"w\").write(\"x\")'",
        "python3 -c 'from pathlib import Path; Path(\"x\").write_text(\"x\")'",
        "bash -c 'git status'",
        "sh -c pwd",
        "env git status",
        "command git status",
        "sudo git status",
        "git checkout main",
        "git clean -fd",
        "git reset --hard",
        "git push origin main",
        "git diff --output=diff.txt",
        "git diff --ext-diff",
        "git show --textconv HEAD",
        "rg --pre=touch needle",
        "rg --hostname-bin=touch needle",
        "find . -delete",
        "find . -exec rm {} +",
        "find . -fprintf output.txt '%p\\n'",
        "pwd > output.txt",
        "pwd & touch output.txt",
        "ls | tee output.txt",
        "git status $(touch output.txt)",
        "git status `touch output.txt`",
        "PATH=/tmp git status",
        "rg *",
        "r{m,g} output.txt",
        "cat <(touch output.txt)",
        "pwd &&\n",
        "pwd && git status\nrm -f output.txt",
        "rg 'unterminated",
        "python3 -c 'print(1)'",
        "python3 -VV extra",
        "file -C -m magic",
        "file --compile -m magic",
        "sed -n '1e touch output.txt' README.md",
    )

    for command in safe_commands:
        assert qwendex.read_only_shell_command_allowed(command), command
        assert qwendex.pre_tool_gate(
            {},
            {"tool_name": "functions.exec_command", "profile": "verifier", "command": command},
            {},
        ) == {}, command
    for command in rejected_commands:
        assert not qwendex.read_only_shell_command_allowed(command), command
        rejected = qwendex.pre_tool_gate(
            {},
            {"tool_name": "exec_command", "profile": "explorer", "command": command},
            {},
        )
        assert rejected["decision"] == "block", command
        assert rejected["event"] == "agent.write_rejected", command

    assert not qwendex.read_only_shell_command_allowed("pytest -q")
    assert qwendex.read_only_shell_command_allowed("pytest -q", allow_validation=True)
    assert qwendex.read_only_shell_command_allowed(
        "python3 -m pytest -q",
        allow_validation=True,
    )
    for command in (
        "python -B -m pytest -p no:cacheprovider -q",
        "PYTHONDONTWRITEBYTECODE=1 python -B -m pytest -p no:cacheprovider -q",
        "PYTHONDONTWRITEBYTECODE=1 pytest -p no:cacheprovider -q",
    ):
        assert qwendex.read_only_shell_command_allowed(command, allow_validation=True), command
        assert qwendex.pre_tool_gate(
            {},
            {"tool_name": "exec_command", "profile": "verifier", "command": command},
            {},
        ) == {}, command
    for command in (
        "PYTHONDONTWRITEBYTECODE=0 python -B -m pytest -q",
        "FOO=1 python -B -m pytest -q",
        "PYTHONDONTWRITEBYTECODE=1 FOO=1 python -B -m pytest -q",
        "PYTHONDONTWRITEBYTECODE=1 /usr/bin/python -B -m pytest -q",
        "python -E -m pytest -q",
        "python -B -B -m pytest -q",
        "python -B -m pytest --cache-clear",
        "python -B -m pytest --basetemp=generated",
    ):
        assert not qwendex.read_only_shell_command_allowed(command, allow_validation=True), command
    assert qwendex.pre_tool_gate(
        {},
        {"tool_name": "exec_command", "profile": "verifier", "command": "pytest -q"},
        {},
    ) == {}
    explorer_pytest = qwendex.pre_tool_gate(
        {},
        {"tool_name": "exec_command", "profile": "explorer", "command": "pytest -q"},
        {},
    )
    assert explorer_pytest["event"] == "agent.write_rejected"

    for event in (
        {"tool_name": "exec_command", "profile": "audit"},
        {"tool_name": "python3.12", "profile": "review", "code": "print(1)"},
        {"tool_name": "functions.apply_patch", "profile": "docs_researcher"},
        {"tool_name": "exec_command", "profile": "verifier", "command": ["git", "status"]},
        {"tool_name": "exec_command", "sandbox_mode": "read-only", "command": "touch output.txt"},
    ):
        rejected = qwendex.pre_tool_gate({}, event, {})
        assert rejected["decision"] == "block", event
        assert rejected["event"] == "agent.write_rejected", event

    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    allowed_cli = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "profile": "verifier", "command": "git status --short | head -n 5"}),
        "--json",
        env=env,
    )
    blocked_cli = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "profile": "verifier", "command": "rm -f output.txt"}),
        "--json",
        env=env,
    )
    assert allowed_cli["data"]["hook_result"] == {}
    assert blocked_cli.returncode != 0
    assert parse_json_result(blocked_cli)["data"]["hook_result"]["event"] == "agent.write_rejected"


def test_qwendex_non_shell_tools_allow_root_and_restrict_read_only_children():
    qwendex = load_qwendex()
    safe_events = (
        {"tool_name": "mcp__github__get_pull_request", "profile": "explorer"},
        {"tool_name": "mcp__github__getPullRequest", "profile": "explorer"},
        {"tool_name": "codex_apps.google_drive.search_files", "profile": "review"},
        {"tool_name": "functions.view_image", "profile": "verifier"},
        {"tool_name": "collaboration.send_message", "profile": "docs_researcher"},
        {"tool_name": "mcp__collaboration__send_message", "profile": "docs_researcher"},
    )
    rejected_events = (
        {"tool_name": "mcp__filesystem__write_file", "profile": "explorer"},
        {"tool_name": "codex_apps.google_drive.upload_file", "profile": "review"},
        {"tool_name": "functions.write_stdin", "profile": "verifier"},
        {"tool_name": "mcp__slack__send_message", "profile": "docs_researcher"},
        {"tool_name": "mcp__github__createPullRequest", "profile": "review"},
        {"tool_name": "codex_apps.gmail.send_email", "profile": "review"},
        {"tool_name": "mcp__unknown__frobnicate", "profile": "docs_researcher"},
    )

    root_envelope = {"session_id": "root-session", "cwd": str(ROOT)}
    for event in (*safe_events, *rejected_events):
        result = qwendex.pre_tool_gate({}, {**event, **root_envelope}, {})
        assert result.get("decision") != "block", event

    for index, event in enumerate(safe_events):
        child_event = {
            **event,
            "agent_id": f"read-only-child-{index}",
            "agent_type": event["profile"],
            "session_id": f"read-only-child-session-{index}",
            "cwd": str(ROOT),
        }
        assert qwendex.pre_tool_gate({}, child_event, {}) == {}, event
    for index, event in enumerate(rejected_events):
        child_event = {
            **event,
            "agent_id": f"read-only-child-{index}",
            "agent_type": event["profile"],
            "session_id": f"read-only-child-session-{index}",
            "cwd": str(ROOT),
        }
        result = qwendex.pre_tool_gate({}, child_event, {})
        assert result["decision"] == "block", event
        assert result["event"] == "agent.write_rejected", event

    recursive_management = qwendex.pre_tool_gate(
        {},
        {
            "tool_name": "spawn_agent",
            "agent_id": "read-only-child-manager",
            "agent_type": "explorer",
            "session_id": "read-only-child-manager-session",
            "cwd": str(ROOT),
        },
        {},
    )
    assert recursive_management["decision"] == "block"
    assert recursive_management["event"] == "agent.spawn_rejected"


def test_qwendex_writer_shell_gate_requires_identity_paths_and_locks(tmp_path):
    qwendex = load_qwendex()
    safe_inspections = (
        "pwd",
        "ls -la | head -n 5",
        'rg -n "manager" scripts/qwendex_cli.py',
        "git status --short && git diff --stat",
        "find scripts -maxdepth 1 -type f -print",
    )
    presumed_write_commands = (
        "awk 'BEGIN { system(\"touch output.txt\") }'",
        "ruby -e 'File.write(\"output.txt\", \"x\")'",
        "node -e 'require(\"fs\").writeFileSync(\"output.txt\", \"x\")'",
        "git checkout -- README.md",
        "curl https://example.invalid/archive -o archive.tar",
        "tar -xf archive.tar",
        "unzip archive.zip",
        "make all",
        "npm install",
        "pytest -q",
        "bash -c 'git status'",
    )

    for command in safe_inspections:
        allowed = qwendex.pre_tool_gate(
            {},
            {
                "tool_name": "exec_command",
                "profile": "implementer",
                "session_id": "root-shell-session",
                "cwd": str(ROOT),
                "command": command,
            },
            {},
        )
        assert allowed == {}, command
    for command in presumed_write_commands:
        root_allowed = qwendex.pre_tool_gate(
            {},
            {
                "tool_name": "exec_command",
                "profile": "implementer",
                "session_id": "root-shell-session",
                "cwd": str(ROOT),
                "command": command,
            },
            {},
        )
        missing_identity = qwendex.pre_tool_gate(
            {},
            {
                "tool_name": "exec_command",
                "profile": "implementer",
                "depth": 1,
                "command": command,
            },
            {},
        )
        missing_paths = qwendex.pre_tool_gate(
            {},
            {
                "tool_name": "exec_command",
                "profile": "implementer",
                "agent_id": "writer-shell",
                "depth": 1,
                "command": command,
            },
            {},
        )
        assert root_allowed == {}, command
        assert missing_identity["decision"] == "block", command
        assert missing_identity["event"] == "agent.write_lock_rejected", command
        assert "agent_id" in missing_identity["reason"], command
        assert missing_paths["decision"] == "block", command
        assert missing_paths["event"] == "agent.write_lock_rejected", command
        assert "target file path" in missing_paths["reason"], command

    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "writer-shell",
        "--lane",
        "implementation",
        "--write-surface",
        "generated/output.txt",
        "--json",
        env=env,
    )
    acquired = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "exec_command",
            "profile": "implementer",
            "agent_id": "writer-shell",
            "agent_type": "implementer",
            "session_id": "writer-shell-session",
            "cwd": str(ROOT),
            "path": "generated/output.txt",
            "command": "node build.js",
        }),
        "--json",
        env=env,
    )
    assert acquired["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert acquired["data"]["hook_result"]["acquired"][0]["path"] == "generated/output.txt"


def test_qwendex_agent_file_locks_enforce_single_writer_and_release_on_final_report(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "writer-a",
        "--lane",
        "implementation",
        "--write-surface",
        "scripts/qwendex_cli.py",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "writer-b",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    acquired = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-a",
            "profile": "implementer",
            "path": "scripts/qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    locks = json_result("agent", "locks", "--json", env=env)
    conflict_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-b",
            "profile": "implementer",
            "path": "tests/smoke/test_qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    completed = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "writer-a",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nagent_id: writer-a\nevidence:\n- done",
        }),
        "--json",
        env=env,
    )
    acquired_after_release = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "apply_patch",
            "agent_id": "writer-b",
            "profile": "implementer",
            "path": "tests/smoke/test_qwendex_cli.py",
        }),
        "--json",
        env=env,
    )
    status = json_result("agent", "status", "--json", env=env)
    scribe_reject_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "write",
            "agent_id": "scribe-1",
            "profile": "scribe",
            "path": "README.md",
        }),
        "--json",
        env=env,
    )

    conflict = parse_json_result(conflict_result)
    scribe_reject = parse_json_result(scribe_reject_result)

    assert acquired["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert acquired["data"]["hook_result"]["acquired"][0]["path"] == "scripts/qwendex_cli.py"
    assert locks["data"]["write_safety"]["active_writer_count"] == 1
    assert conflict_result.returncode != 0
    assert conflict["data"]["hook_result"]["event"] == "agent.file_lock_conflict"
    assert conflict["data"]["hook_result"]["conflicts"][0]["agent_id"] == "writer-a"
    assert completed["data"]["agent_session"]["status"] == "completed"
    assert acquired_after_release["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert acquired_after_release["data"]["hook_result"]["acquired"][0]["agent_id"] == "writer-b"
    assert status["data"]["write_safety"]["active_writer_count"] == 1
    assert status["data"]["write_safety"]["active_writers"][0]["agent_id"] == "writer-b"
    assert scribe_reject_result.returncode != 0
    assert scribe_reject["data"]["hook_result"]["event"] == "agent.write_rejected"
    assert "Scribe can write only" in scribe_reject["data"]["hook_result"]["reason"]


def test_qwendex_manager_assign_generates_context_packet_and_routing(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-sec",
        "--lane",
        "security-review",
        "--task-id",
        "task-sec",
        "--objective",
        "Review manager routing risk",
        "--task-class",
        "security",
        "--file",
        "scripts/qwendex_cli.py",
        "--needed-doc",
        "public/qwendex/security.md",
        "--expected-artifact",
        "compact findings",
        "--receipt-path",
        "results/qwendex/security-review.json",
        "--context-budget",
        "12000",
        "--risk",
        "high",
        "--review-requirement",
        "main session validates receipt",
        "--optional",
        "--json",
        env=env,
    )
    session = assigned["data"]["agent_session"]
    packet = session["context_packet"]
    routing = packet["model_reasoning_assignment"]

    assert packet["objective"] == "Review manager routing risk"
    assert packet["task_class"] == "security"
    assert packet["exact_files"] == ["scripts/qwendex_cli.py"]
    assert packet["needed_docs"] == ["public/qwendex/security.md"]
    assert packet["receipt_path"] == "results/qwendex/security-review.json"
    assert packet["context_budget"] == 12000
    assert packet["required"] is False
    assert routing["selected_model"] == "gpt-5.5"
    assert routing["selected_reasoning"] in {"high", "xhigh"}
    assert routing["reasoning_source"] == "lane_escalation"
    assert routing["local_qwen_eligible"] is False
    assert routing["token_saver_used"] is False
    assert routing["escalation_reason"]
    assert "gpt-5.5" not in packet["spawn_instruction"]
    assert "model selection inherited from Codex" in packet["spawn_instruction"]
    assert "reasoning=" in packet["spawn_instruction"]

    subagent = json_result(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({"agent_id": "agent-sec", "agent_type": "security-review"}),
        "--json",
        env=env,
    )
    subagent_context = subagent["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert "gpt-5.5" not in subagent_context
    assert "reasoning=high" in subagent_context or "reasoning=xhigh" in subagent_context
    assert "ordinary clear output is accepted" in subagent_context
    assert "required terminal report" not in subagent_context

    status = json_result("manager", "status", "--json", env=env)
    assert status["data"]["active_subagents"]["count"] == 1
    assert status["data"]["deployment_contract"]["status"] == "ready"
    assert status["data"]["subagent_state"]["receipts"] == ["results/qwendex/security-review.json"]
    assert status["data"]["subagent_state"]["validation_status"]["pending"] == 1


def test_qwendex_manager_reconciles_stale_read_only_and_warns_on_stale_writers(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-reader",
        "--lane",
        "review",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "scripts",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    status_result = run_qwendex("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    status = parse_json_result(status_result)
    assert status_result.returncode == 0
    assert status["status"] == "warning"
    assert status["data"]["stale_reconciliation"]["closed_count"] == 0
    assert status["data"]["stale_reconciliation"]["close_requested_count"] == 1
    assert status["data"]["stale_reconciliation"]["close_requested"][0]["agent_id"] == "stale-reader"
    assert status["data"]["stale_reconciliation"]["skipped_writer_count"] == 1
    assert status["data"]["active_subagents"]["count"] == 1
    assert status["data"]["active_subagents"]["agents"][0]["status"] == "close_requested"
    assert status["data"]["stale_writer_sessions"]["count"] == 1
    assert status["data"]["manager_health"]["issues"] == []
    assert "stale manager writer sessions" in " ".join(status["data"]["manager_health"]["warnings"])

    closed = json_result("manager", "close", "--agent-id", "stale-writer", "--reason", "integrated", "--json", env=env)
    closed_session = closed["data"]["agent_session"]
    assert closed_session["status"] == "close_requested"
    assert closed_session["stop_reason"] == "integrated"
    assert closed_session["close_receipt"]

    cleared = json_result("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    assert cleared["data"]["stale_writer_sessions"]["count"] == 0


def test_qwendex_manager_repair_safe_closes_only_harmless_stale_sessions(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_DEPLOY_POLICY": "disabled",
    }

    json_result(
        "manager",
        "assign",
        "--agent-id",
        "stale-reader",
        "--lane",
        "review",
        "--write-surface",
        "read-only",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "empty-writer",
        "--lane",
        "empty-writer",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "nonempty-writer",
        "--lane",
        "implementation",
        "--write-surface",
        "tests/smoke/test_qwendex_cli.py",
        "--file",
        "tests/smoke/test_qwendex_cli.py",
        "--artifact",
        "results/qwendex/nonempty.json",
        "--json",
        env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute("UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z'")

    repair_result = run_qwendex("manager", "repair", "--safe", "--stale-after-minutes", "5", "--json", env=env)
    repair = parse_json_result(repair_result)
    status = run_qwendex("manager", "status", "--stale-after-minutes", "5", "--json", env=env)
    status_data = parse_json_result(status)

    assert repair_result.returncode != 0
    assert repair["status"] == "blocked"
    assert repair["data"]["safe"] is True
    assert repair["data"]["closed_count"] == 0
    assert repair["data"]["close_requested_count"] == 2
    assert {session["agent_id"] for session in repair["data"]["close_requested"]} == {"stale-reader", "empty-writer"}
    assert repair["data"]["skipped_writer_count"] == 1
    assert repair["data"]["skipped_writers"][0]["agent_id"] == "nonempty-writer"
    assert "nonempty-writer" in " ".join(repair["errors"])
    assert status.returncode == 0
    assert status_data["status"] == "warning"
    assert status_data["data"]["manager_health"]["issues"] == []
    assert status_data["data"]["stale_writer_sessions"]["count"] == 1
    assert status_data["data"]["stale_writer_sessions"]["agents"][0]["agent_id"] == "nonempty-writer"


def test_qwendex_auto_manager_estimator_skill_contract():
    path = ROOT / ".codex" / "skills" / "qwendex-auto-manager-estimator" / "SKILL.md"
    text = path.read_text(encoding="utf-8")

    assert "qwendex-auto-manager-estimator" in text
    assert "GPT-5.5" in text
    assert "medium" in text
    assert len(text.splitlines()) <= 90
    for field in (
        "task_complexity",
        "risk",
        "likely_file_scope",
        "validation_depth",
        "subagent_usefulness",
        "recommended_mode",
        "confidence",
        "higher_reasoning_lanes",
    ):
        assert field in text


def test_qwendex_manager_rejects_out_of_range_cli_overrides():
    result = run_qwendex("manager", "--max-subagents", "999", "--stale-after-minutes", "-1", "--json")
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "max_subagents" in " ".join(data["errors"])
    assert "stale_after_minutes" in " ".join(data["errors"])


def test_qwendex_state_plane_tracks_task_context_handoff_evidence_and_agent_session(tmp_path):
    state_env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    task = json_result(
        "task",
        "create",
        "--title",
        "Ship routing",
        "--priority",
        "P1",
        "--owner",
        "main",
        "--phase",
        "build",
        "--json",
        env=state_env,
    )
    task_id = task["data"]["task"]["task_id"]
    assert task["data"]["task"]["status"] == "open"

    started = json_result("task", "start", "--task-id", task_id, "--json", env=state_env)
    assert started["data"]["task"]["status"] == "in_progress"

    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-1",
        "--lane",
        "review",
        "--task-id",
        task_id,
        "--owner",
        "Rawls",
        "--write-surface",
        "read-only",
        "--stop-condition",
        "return findings",
        "--json",
        env=state_env,
    )
    assert assigned["data"]["agent_session"]["status"] == "active"

    heartbeat = json_result("manager", "heartbeat", "--agent-id", "agent-1", "--json", env=state_env)
    assert heartbeat["data"]["agent_session"]["agent_id"] == "agent-1"

    snapshot = json_result(
        "context",
        "snapshot",
        "--task-id",
        task_id,
        "--objective",
        "finish Qwendex",
        "--decision",
        "prefer qwen when available",
        "--open-file",
        "scripts/qwendex_cli.py",
        "--evidence",
        "results/qwendex/example.json",
        "--next-action",
        "run tests",
        "--json",
        env=state_env,
    )
    assert snapshot["data"]["snapshot"]["task_id"] == task_id

    report = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "agent-1",
            "last_assistant_message": (
                "FINAL_REPORT\n"
                "status: completed\n"
                "agent_id: agent-1\n"
                "task_name: review state plane\n"
                "summary: verified context pack keeps compact agent output\n"
                "evidence:\n"
                "- context pack smoke passed"
            ),
        }),
        "--json",
        env=state_env,
    )
    assert report["data"]["agent_session"]["status"] == "completed"

    compact = json_result("context", "compact-plan", "--task-id", task_id, "--budget", "12000", "--json", env=state_env)
    assert "summary" in compact["data"]["compact_plan"]
    outcome = compact["data"]["compact_plan"]["agent_outcomes"][0]
    assert outcome["agent_id"] == "agent-1"
    assert outcome["raw_output_artifact"].endswith("/raw-output.md")
    assert compact["data"]["compact_plan"]["raw_output_policy"].startswith("preserve raw child output")

    reminder = json_result(
        "context",
        "reminder",
        "--task-id",
        task_id,
        "--tool-calls",
        "55",
        "--phase",
        "after-milestone",
        "--json",
        env=state_env,
    )
    assert reminder["data"]["reminder"]["recommendation"] == "compact_now"
    assert "compact-plan" in reminder["data"]["reminder"]["next_command"]

    handoff = json_result("handoff", "create", "--task-id", task_id, "--status", "ready", "--next-action", "review", "--json", env=state_env)
    handoff_id = handoff["data"]["handoff"]["handoff_id"]
    shown = json_result("handoff", "show", "--handoff-id", handoff_id, "--json", env=state_env)
    assert shown["data"]["handoff"]["task_id"] == task_id

    pack = json_result("context", "pack", "--task-id", task_id, "--json", env=state_env)
    assert pack["data"]["agent_outcomes"][0]["compact_report_artifact"].endswith("/compact-report.json")

    evidence_path = tmp_path / "evidence.txt"
    evidence_path.write_text("evidence", encoding="utf-8")
    evidence = json_result(
        "evidence",
        "add",
        "--task-id",
        task_id,
        "--claim",
        "routing verified",
        "--path",
        str(evidence_path),
        "--json",
        env=state_env,
    )
    assert evidence["data"]["evidence"]["sha256"]
    queried = json_result("evidence", "query", "--task-id", task_id, "--json", env=state_env)
    assert queried["data"]["evidence"][0]["claim"] == "routing verified"

    closed = json_result("manager", "close-stale", "--stale-after-minutes", "5", "--json", env=state_env)
    assert closed["data"]["closed_count"] == 0


def test_qwendex_context_pack_and_manager_decisions_are_repository_scoped_for_reused_task_ids(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    task_id = "shared-task"
    base_env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    env_a = {**base_env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_a)}
    env_b = {**base_env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_b)}
    json_result("manager", "mode", "--set", "manager", "--json", env=base_env)
    preflight_a = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env_a
    )
    preflight_b = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env_b
    )
    ledger_a = preflight_a["data"]["ledger_id"]
    ledger_b = preflight_b["data"]["ledger_id"]
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE qwendex_manager_decisions SET agent_task_id = ? WHERE ledger_id IN (?, ?)",
            (task_id, ledger_a, ledger_b),
        )

    json_result(
        "manager", "assign", "--agent-id", "writer-a", "--lane", "implementation",
        "--task-id", task_id, "--repo-root", str(repo_a), "--json", env=base_env,
    )
    with sqlite3.connect(state_db) as conn:
        used = dict(
            conn.execute(
                "SELECT ledger_id, subagents_used FROM qwendex_manager_decisions WHERE ledger_id IN (?, ?)",
                (ledger_a, ledger_b),
            )
        )
    assert used == {ledger_a: 1, ledger_b: 0}

    json_result(
        "manager", "assign", "--agent-id", "writer-b", "--lane", "implementation",
        "--task-id", task_id, "--repo-root", str(repo_b), "--json", env=base_env,
    )
    for agent_id, repo in (("writer-a", repo_a), ("writer-b", repo_b)):
        json_result(
            "agent", "hook", "PreToolUse", "--event-json",
            json.dumps({
                "tool_name": "apply_patch",
                "agent_id": agent_id,
                "profile": "implementer",
                "path": "shared.txt",
                "cwd": str(repo),
            }),
            "--json", env=base_env,
        )
    for label, env in (("a", env_a), ("b", env_b)):
        json_result(
            "context", "snapshot", "--task-id", task_id,
            "--objective", f"objective-{label}", "--decision", f"decision-{label}", "--json", env=env,
        )
        json_result(
            "handoff", "create", "--handoff-id", f"handoff-{label}", "--task-id", task_id,
            "--summary", f"handoff-{label}", "--json", env=env,
        )
        json_result(
            "evidence", "add", "--evidence-id", f"evidence-{label}", "--task-id", task_id,
            "--claim", f"claim-{label}", "--path", str(tmp_path / f"{label}.txt"), "--json", env=env,
        )
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            """
            INSERT INTO qwendex_context_snapshots
            (snapshot_id, task_id, objective, decisions_json, open_files_json, evidence_refs_json,
             blocked_items_json, next_actions_json, budget, created_at)
            VALUES ('legacy-snapshot', ?, 'legacy', '[]', '[]', '[]', '[]', '[]', 0, '9999-01-01T00:00:00Z')
            """,
            (task_id,),
        )
        conn.execute(
            """
            INSERT INTO qwendex_handoffs
            (handoff_id, task_id, status, summary, evidence_refs_json, next_actions_json, created_at)
            VALUES ('legacy-handoff', ?, 'ready', 'legacy', '[]', '[]', '9999-01-01T00:00:00Z')
            """,
            (task_id,),
        )
        conn.execute(
            """
            INSERT INTO qwendex_evidence
            (evidence_id, task_id, claim, path, sha256, kind, created_at)
            VALUES ('legacy-evidence', ?, 'legacy', 'legacy.txt', 'digest', 'artifact', '9999-01-01T00:00:00Z')
            """,
            (task_id,),
        )
        conn.execute(
            "UPDATE qwendex_manager_decisions SET timestamp_updated = '9999-01-01T00:00:00Z' WHERE ledger_id = ?",
            (ledger_b,),
        )

    pack_a = json_result("context", "pack", "--task-id", task_id, "--json", env=env_a)
    plan_a = json_result("context", "compact-plan", "--task-id", task_id, "--json", env=env_a)
    latest_a = json_result("manager", "decision", "--json", env=env_a)
    hidden_decision = run_qwendex(
        "manager", "decision", "--agent-id", ledger_b, "--json", env=env_a
    )
    hidden_handoff = run_qwendex(
        "handoff", "show", "--handoff-id", "handoff-b", "--json", env=env_a
    )
    legacy_handoff = run_qwendex(
        "handoff", "show", "--handoff-id", "legacy-handoff", "--json", env=env_a
    )
    evidence_a = json_result("evidence", "query", "--task-id", task_id, "--json", env=env_a)
    data = pack_a["data"]

    assert data["repo_root"] == str(repo_a)
    assert data["snapshot"]["objective"] == "objective-a"
    assert data["manager_decision"]["ledger_id"] == ledger_a
    assert [item["claim"] for item in data["evidence"]] == ["claim-a"]
    assert [item["summary"] for item in data["handoffs"]] == ["handoff-a"]
    assert [item["agent_id"] for item in data["agent_sessions"]] == ["writer-a"]
    assert {item["agent_id"] for item in data["file_locks"]} == {"writer-a"}
    assert plan_a["data"]["compact_plan"]["manager_decision"]["ledger_id"] == ledger_a
    assert {item["agent_id"] for item in plan_a["data"]["compact_plan"]["file_locks"]} == {"writer-a"}
    assert latest_a["data"]["manager_decision"]["ledger_id"] == ledger_a
    assert hidden_decision.returncode != 0
    assert hidden_handoff.returncode != 0
    assert legacy_handoff.returncode != 0
    assert [item["claim"] for item in evidence_a["data"]["evidence"]] == ["claim-a"]


def test_qwendex_handoff_and_evidence_public_ids_are_repository_local(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    base_env = {"QWENDEX_STATE_DB": str(state_db)}
    env_a = {**base_env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_a)}
    env_b = {**base_env, "QWENDEX_MANAGER_TARGET_REPO": str(repo_b)}

    for label, env in (("a", env_a), ("b", env_b)):
        handoff = json_result(
            "handoff", "create", "--handoff-id", "shared-handoff", "--task-id", "shared-task",
            "--summary", f"summary-{label}", "--json", env=env,
        )
        evidence = json_result(
            "evidence", "add", "--evidence-id", "shared-evidence", "--task-id", "shared-task",
            "--claim", f"claim-{label}", "--path", str(tmp_path / f"{label}.txt"), "--json", env=env,
        )
        assert handoff["data"]["handoff"]["handoff_id"] == "shared-handoff"
        assert evidence["data"]["evidence"]["evidence_id"] == "shared-evidence"

    handoff_a = json_result(
        "handoff", "show", "--handoff-id", "shared-handoff", "--json", env=env_a
    )
    handoff_b = json_result(
        "handoff", "show", "--handoff-id", "shared-handoff", "--json", env=env_b
    )
    evidence_a = json_result("evidence", "query", "--task-id", "shared-task", "--json", env=env_a)
    evidence_b = json_result("evidence", "query", "--task-id", "shared-task", "--json", env=env_b)
    duplicate_handoff = run_qwendex(
        "handoff", "create", "--handoff-id", "shared-handoff", "--task-id", "other-task", "--json", env=env_a
    )
    duplicate_evidence = run_qwendex(
        "evidence", "add", "--evidence-id", "shared-evidence", "--task-id", "other-task",
        "--claim", "duplicate", "--path", "duplicate.txt", "--json", env=env_a,
    )
    duplicate_handoff_payload = parse_json_result(duplicate_handoff)
    duplicate_evidence_payload = parse_json_result(duplicate_evidence)
    with sqlite3.connect(state_db) as conn:
        handoff_rows = conn.execute(
            "SELECT handoff_id, repo_root, public_id FROM qwendex_handoffs WHERE public_id = 'shared-handoff'"
        ).fetchall()
        evidence_rows = conn.execute(
            "SELECT evidence_id, repo_root, public_id FROM qwendex_evidence WHERE public_id = 'shared-evidence'"
        ).fetchall()

    assert handoff_a["data"]["handoff"]["summary"] == "summary-a"
    assert handoff_b["data"]["handoff"]["summary"] == "summary-b"
    assert [item["claim"] for item in evidence_a["data"]["evidence"]] == ["claim-a"]
    assert [item["claim"] for item in evidence_b["data"]["evidence"]] == ["claim-b"]
    assert duplicate_handoff.returncode != 0
    assert duplicate_handoff_payload["errors"] == ["duplicate handoff_id: shared-handoff"]
    assert duplicate_handoff_payload["data"]["repo_root"] == str(repo_a)
    assert duplicate_evidence.returncode != 0
    assert duplicate_evidence_payload["errors"] == ["duplicate evidence_id: shared-evidence"]
    assert duplicate_evidence_payload["data"]["repo_root"] == str(repo_a)
    assert len({row[0] for row in handoff_rows}) == 2
    assert {row[1] for row in handoff_rows} == {str(repo_a), str(repo_b)}
    assert {row[2] for row in handoff_rows} == {"shared-handoff"}
    assert len({row[0] for row in evidence_rows}) == 2
    assert {row[1] for row in evidence_rows} == {str(repo_a), str(repo_b)}
    assert {row[2] for row in evidence_rows} == {"shared-evidence"}


def test_qwendex_scoped_public_id_migration_preserves_existing_storage_rows(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    json_result("evidence", "query", "--json", env=env)
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            """
            INSERT INTO qwendex_handoffs
            (handoff_id, task_id, status, summary, evidence_refs_json, next_actions_json, created_at, repo_root)
            VALUES ('existing-handoff', 'legacy-task', 'ready', 'preserved', '[]', '[]',
                    '2026-01-01T00:00:00Z', ?)
            """,
            (str(repo),),
        )
        conn.execute(
            """
            INSERT INTO qwendex_evidence
            (evidence_id, task_id, claim, path, sha256, kind, created_at, repo_root)
            VALUES ('existing-evidence', 'legacy-task', 'preserved', 'artifact.txt', 'digest',
                    'artifact', '2026-01-01T00:00:00Z', ?)
            """,
            (str(repo),),
        )

    shown = json_result(
        "handoff", "show", "--handoff-id", "existing-handoff", "--json", env=env
    )
    evidence = json_result("evidence", "query", "--task-id", "legacy-task", "--json", env=env)
    with sqlite3.connect(state_db) as conn:
        handoff_row = conn.execute(
            "SELECT handoff_id, public_id FROM qwendex_handoffs WHERE handoff_id = 'existing-handoff'"
        ).fetchone()
        evidence_row = conn.execute(
            "SELECT evidence_id, public_id FROM qwendex_evidence WHERE evidence_id = 'existing-evidence'"
        ).fetchone()

    assert shown["data"]["handoff"]["handoff_id"] == "existing-handoff"
    assert shown["data"]["handoff"]["summary"] == "preserved"
    assert evidence["data"]["evidence"][0]["evidence_id"] == "existing-evidence"
    assert handoff_row == ("existing-handoff", "existing-handoff")
    assert evidence_row == ("existing-evidence", "existing-evidence")


def test_qwendex_queue_facade_delegates_to_artifact_queue(tmp_path):
    queue_dir = tmp_path / "queue"
    queue_dir.mkdir()
    env = {"ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS": str(queue_dir)}

    init = json_result("queue", "init", "--dir", str(queue_dir), "--item", "one.md::First", "--item", "two.md::Second", "--json", env=env)
    start = json_result("queue", "start", "--dir", str(queue_dir), "--file", "one.md", "--json", env=env)
    (queue_dir / "one.md").write_text("done\n", encoding="utf-8")
    done = json_result("queue", "done", "--dir", str(queue_dir), "--file", "one.md", "--json", env=env)
    next_item = json_result("queue", "next", "--dir", str(queue_dir), "--json", env=env)

    assert init["data"]["queue"]["counts"]["pending"] == 2
    assert start["data"]["queue"]["started"]["file"] == "one.md"
    assert done["data"]["queue"]["completed"]["file"] == "one.md"
    assert next_item["data"]["queue"]["status"] == "next"
    assert next_item["data"]["queue"]["next"]["file"] == "two.md"


def test_qwendex_public_docs_and_naming_audit_pass():
    qwendex = load_qwendex()
    configuration = (ROOT / "public" / "qwendex" / "configuration.md").read_text(encoding="utf-8")

    audit = qwendex.public_docs_audit(ROOT / "public" / "qwendex")

    assert audit["status"] == "pass"
    assert audit["missing"] == []
    assert audit["dead_links"] == []
    assert audit["secret_hits"] == []
    assert audit["naming_hits"] == []
    assert "security.md" in audit["files"]
    assert "staging-receipt.md" in audit["files"]
    assert "uses the resolved `workspace-write` permission\nposture" in configuration
    assert "`qdex` defaults to\n`--dangerously-bypass-approvals-and-sandbox`" not in configuration


def test_qwendex_primary_authority_and_local_off_cannot_be_overridden(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
    }

    forced_security = json_result(
        "route", "--task-class", "security review", "--prefer-local", "--json", env=env
    )
    explicit_release = json_result(
        "route", "--seat", "qwen", "--task-class", "release acceptance", "--json", env=env
    )
    json_result("manager", "local", "--set", "off", "--json", env=env)
    explicit_off = json_result(
        "route", "--seat", "qwen", "--task-class", "exec", "--json", env=env
    )

    assert forced_security["data"]["seat"] == "primary"
    assert forced_security["data"]["reason"] == "primary_authority_required"
    assert forced_security["data"]["token_saver_used"] is False
    assert explicit_release["data"]["seat"] == "primary"
    assert explicit_release["data"]["reason"] == "primary_authority_required"
    assert explicit_off["data"]["seat"] == "primary"
    assert explicit_off["data"]["reason"] == "local_subagents_disabled"
    assert explicit_off["data"]["local_subagents"]["local_state"] == "off"


def test_qwendex_estimator_uses_contract_scope_values_and_word_boundaries(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0",
    }

    many = json_result(
        "estimate", "--prompt", "Update routing across multiple modules", "--json", env=env
    )["data"]["estimate"]
    company = json_result(
        "estimate", "--prompt", "Update the company name in one file", "--json", env=env
    )["data"]["estimate"]
    author = json_result(
        "estimate", "--prompt", "Update the author field in one file", "--json", env=env
    )["data"]["estimate"]

    assert many["likely_file_scope"] == "many_files"
    assert company["likely_file_scope"] == "single_file"
    assert author["task_class"] != "security"
    assert author["risk"] == "low"


def test_qwendex_manager_receipts_are_digest_verified_by_receipt_latest(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    preflight = json_result(
        "manager", "preflight", "--mode", "manager", "--prompt", "Use agents to inspect routing", "--json", env=env
    )
    latest = json_result("receipt", "latest", "--json", env=env)

    assert latest["data"]["verification"]["verified"] is True
    assert latest["data"]["receipt"]["schema_version"] == "qwendex.manager_decision.v1"
    assert latest["data"]["receipt"]["ledger_id"] == preflight["data"]["ledger_id"]
    assert latest["data"]["receipt"]["sha256"]


def test_qwendex_invalid_stale_override_is_rejected_before_reconciliation(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result(
        "manager", "assign", "--agent-id", "keep-active", "--lane", "review",
        "--write-surface", "read-only", "--json", env=env,
    )
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE qwendex_agent_sessions SET heartbeat_at = '2000-01-01T00:00:00Z' WHERE agent_id = 'keep-active'"
        )

    result = run_qwendex(
        "manager", "status", "--stale-after-minutes", "-1", "--json", env=env
    )
    data = parse_json_result(result)
    with sqlite3.connect(state_db) as conn:
        status = conn.execute(
            "SELECT status FROM qwendex_agent_sessions WHERE agent_id = 'keep-active'"
        ).fetchone()[0]

    assert result.returncode != 0
    assert data["status"] == "blocked"
    assert "stale_after_minutes" in " ".join(data["errors"])
    assert status == "active"


def test_qwendex_manager_reports_suggested_subagent_capacity_per_repository(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result("manager", "mode", "--set", "lite", "--json", env=env)

    for repo, prefix in ((repo_a, "a"), (repo_b, "b")):
        json_result(
            "manager", "assign", "--agent-id", f"{prefix}-0", "--lane", "review",
            "--repo-root", str(repo), "--json", env=env,
        )
    overflow = run_qwendex(
        "manager", "assign", "--agent-id", "a-overflow", "--lane", "review",
        "--repo-root", str(repo_a), "--json", env=env,
    )
    overflow_data = parse_json_result(overflow)
    with sqlite3.connect(state_db) as conn:
        counts = dict(
            conn.execute(
                "SELECT repo_root, COUNT(*) FROM qwendex_agent_sessions GROUP BY repo_root"
            )
        )

    assert overflow.returncode == 0
    assert overflow_data["status"] == "warning"
    assert overflow_data["data"]["agent_session"]["agent_id"] == "a-overflow"
    assert overflow_data["data"]["advisories"] == [
        "recorded workers (1) already meet the suggested capacity (1)"
    ]
    assert counts == {str(repo_a): 2, str(repo_b): 1}


def test_qwendex_concurrent_manager_assignments_record_capacity_advisories(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result("manager", "mode", "--set", "lite", "--json", env=env)
    argument_sets = [
        (
            "manager", "assign", "--agent-id", f"racer-{index}", "--lane", "review",
            "--repo-root", str(repo), "--max-subagents", "1", "--json",
        )
        for index in range(4)
    ]

    results = run_qwendex_concurrently(argument_sets, env=env)
    payloads = [parse_json_result(result) for result in results]
    with sqlite3.connect(state_db) as conn:
        active_count = conn.execute(
            "SELECT COUNT(*) FROM qwendex_agent_sessions WHERE status = 'active' AND repo_root = ?",
            (str(repo),),
        ).fetchone()[0]

    assert all(result.returncode == 0 for result in results)
    assert active_count == 4
    assert sum(payload["status"] == "pass" for payload in payloads) == 1
    assert sum(payload["status"] == "warning" for payload in payloads) == 3
    for payload in payloads:
        if payload["status"] == "warning":
            assert any(
                "suggested capacity (1)" in advisory
                for advisory in payload["data"]["advisories"]
            )


def test_qwendex_interactive_prompt_updates_manager_ledger_and_ignores_codex_session_id(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}

    prompt_hook = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({
            "session_id": "codex-thread-id-must-not-replace-manager-session",
            "turn_id": "turn-1",
            "cwd": str(ROOT),
            "prompt": "Use manager mode with subagents to update routing and tests across modules",
        }),
        "--json", env=manager_env,
    )

    decision = prompt_hook["data"]["manager_decision"]
    context = prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert decision["session_id"] == preflight["data"]["session_id"]
    assert decision["turn_id"] == "turn-1"
    assert decision["prompt_known"] is True
    assert decision["selected_route"] == "manager_subagents"
    assignments = prompt_hook["data"]["agent_plan"]["assignments"]
    assert assignments
    assert all(re.fullmatch(r"[a-z0-9_]+", assignment["agent_id"]) for assignment in assignments)
    assert all(assignment["agent_id"] != "root" for assignment in assignments)
    assert "These lanes are suggestions" in context
    assert "spawn the workers that materially help" in context


def test_qwendex_manager_prompt_without_qdex_identity_proceeds_without_decision_mutation(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    result = run_qwendex(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({"cwd": str(repo), "prompt": "Implement the change and use agents."}),
        "--json", env=env,
    )
    payload = parse_json_result(result)
    with sqlite3.connect(state_db) as conn:
        decision_count = conn.execute("SELECT COUNT(*) FROM qwendex_manager_decisions").fetchone()[0]

    assert result.returncode == 0
    assert payload["status"] == "pass"
    assert payload["data"]["hook_result"]["event"] == "manager.prompt_bookkeeping_unavailable"
    assert payload["data"]["hook_result"].get("decision") != "block"
    assert decision_count == 0


def test_qwendex_manager_launch_status_validates_process_repo_start_and_policy(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    other_repo = tmp_path / "other"
    repo.mkdir()
    other_repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    pid = int(env["QWENDEX_MANAGER_LAUNCH_PID"])
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    selected = json_result(
        "manager", "decision", "--repo-root", str(repo), "--json", env={**env, "QWENDEX_MANAGER_TARGET_REPO": ""}
    )

    trusted = json_result(
        "manager", "launch-status", "--pid", str(pid), "--repo-root", str(repo), "--json", env=env
    )
    mismatch_result = run_qwendex(
        "manager", "launch-status", "--pid", str(pid), "--repo-root", str(other_repo), "--json", env=env
    )
    missing_result = run_qwendex(
        "manager", "launch-status", "--pid", "999999999", "--repo-root", str(repo), "--json", env=env
    )
    mismatch = parse_json_result(mismatch_result)
    missing = parse_json_result(missing_result)

    assert trusted["data"]["trusted"] is True
    assert selected["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]
    assert trusted["data"]["pid_alive"] is True
    assert trusted["data"]["repo_match"] is True
    assert {
        "trusted", "pid_alive", "repo_match", "decision_state", "reason",
        "recovery_command", "identity_present", "policy_match", "hook_trusted",
        "session_policy_hash", "desired_global_policy_hash", "policy_drift",
        "session_policy_valid", "restart_required",
    } <= set(trusted["data"])
    assert trusted["data"]["policy_drift"] is False
    assert trusted["data"]["session_policy_valid"] is True
    assert trusted["data"]["restart_required"] is False
    assert mismatch_result.returncode != 0
    assert mismatch["data"]["reason"] == "qwendex_repo_mismatch"
    assert missing_result.returncode != 0
    assert missing["data"]["reason"] == "qwendex_identity_missing"

    json_result("manager", "mode", "--set", "heavy", "--json", env=env)
    drifted = json_result(
        "manager", "launch-status", "--pid", str(pid), "--repo-root", str(repo), "--json", env=env
    )["data"]
    assert drifted["trusted"] is True
    assert drifted["policy_match"] is True
    assert drifted["session_policy_valid"] is True
    assert drifted["policy_drift"] is True
    assert drifted["restart_required"] is True
    assert drifted["session_policy_hash"] == preflight["data"]["policy_hash"]
    assert drifted["desired_global_policy_hash"] != drifted["session_policy_hash"]
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE qwendex_manager_decisions SET launch_start_ticks = 'reused-pid' WHERE ledger_id = ?",
            (preflight["data"]["ledger_id"],),
        )
    stale_result = run_qwendex(
        "manager", "launch-status", "--pid", str(pid), "--repo-root", str(repo), "--json", env=env
    )
    stale = parse_json_result(stale_result)
    assert stale_result.returncode != 0
    assert stale["data"]["reason"] == "qwendex_identity_stale"



def test_qwendex_manager_stop_without_launch_identity_allows_exit_without_mutation(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    first_preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    first_prompt = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({"session_id": "codex-thread-a", "turn_id": "turn-a", "prompt": "Explain status."}),
        "--json", env={**env, **first_preflight["data"]["exports"]},
    )
    first_ledger = first_prompt["data"]["manager_decision"]["ledger_id"]

    stop = json_result(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({"turn_id": "turn-a", "last_assistant_message": "Status explained.", "edit_happened": False}),
        "--json",
        env={
            **env,
            "QWENDEX_MANAGER_LEDGER_ID": "",
            "QWENDEX_MANAGER_SESSION_ID": "",
        },
    )
    with sqlite3.connect(state_db) as conn:
        statuses = dict(conn.execute("SELECT ledger_id, final_status FROM qwendex_manager_decisions"))

    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    assert stop["data"]["launch_health"]["trusted"] is False
    assert statuses[first_ledger] == "preflight_ready"


def test_qwendex_manager_stop_without_launch_identity_never_attaches_by_repo(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    prompt_result = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({"session_id": "codex-thread", "turn_id": "shared-turn", "prompt": "Explain status."}),
        "--json", env={**env, **preflight["data"]["exports"]},
    )
    ledgers = [prompt_result["data"]["manager_decision"]["ledger_id"]]

    stop_result = run_qwendex(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({"turn_id": "shared-turn", "last_assistant_message": "Status explained.", "edit_happened": False}),
        "--json",
        env={
            **env,
            "QWENDEX_MANAGER_LEDGER_ID": "",
            "QWENDEX_MANAGER_SESSION_ID": "",
        },
    )
    stop = parse_json_result(stop_result)
    with sqlite3.connect(state_db) as conn:
        statuses = dict(conn.execute("SELECT ledger_id, final_status FROM qwendex_manager_decisions"))

    assert stop_result.returncode == 0
    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    assert all(statuses[ledger] == "preflight_ready" for ledger in ledgers)


def test_qwendex_manager_rolls_decision_and_validation_scope_per_turn(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    first_prompt = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({
            "session_id": "codex-thread",
            "turn_id": "turn-one",
            "cwd": str(ROOT),
            "prompt": "Use manager mode with subagents to update routing and tests across modules",
        }),
        "--json", env=manager_env,
    )
    first_decision = first_prompt["data"]["manager_decision"]
    reports = {
        "exploration": "FINAL_REPORT\nstatus: completed\nsummary: mapped routing scope\nevidence:\n- routing files mapped",
        "verification": "FINAL_REPORT\nstatus: completed\nsummary: verified routing change\nValidation: pytest passed",
    }
    required_assignments = [
        assignment
        for assignment in first_decision["agent_plan"]["assignments"]
        if assignment["required"]
    ]
    for assignment in required_assignments:
        agent_id = assignment["agent_id"]
        lane = assignment["lane"]
        report = reports[lane]
        json_result(
            "manager", "assign", "--agent-id", agent_id, "--lane", lane,
            "--task-id", first_decision["agent_task_id"], "--required", "--json", env=env,
        )
        json_result(
            "agent", "hook", "SubagentStop", "--event-json",
            json.dumps({
                "agent_id": agent_id,
                "cwd": str(ROOT),
                "last_assistant_message": report,
            }),
            "--json", env=env,
        )
    first_stop = json_result(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            "session_id": "codex-thread",
            "turn_id": "turn-one",
            "cwd": str(ROOT),
            "last_assistant_message": "Agent outcomes: verifier completed.\nValidation: pytest passed.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
            "validation_evidence": ["pytest passed"],
        }),
        "--json", env=manager_env,
    )
    second_prompt = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({
            "session_id": "codex-thread",
            "turn_id": "turn-two",
            "cwd": str(ROOT),
            "prompt": "Use manager mode with subagents for a different routing edit across modules",
        }),
        "--json", env=manager_env,
    )
    second_decision = second_prompt["data"]["manager_decision"]
    second_stop_result = run_qwendex(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            "session_id": "codex-thread",
            "turn_id": "turn-two",
            "cwd": str(ROOT),
            "last_assistant_message": "Agent outcomes: prior verifier passed.\nValidation: prior pytest.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
            "validation_evidence": ["prior pytest"],
        }),
        "--json", env=manager_env,
    )
    second_stop = parse_json_result(second_stop_result)

    assert first_stop["data"]["manager_decision"]["final_status"] == "closed"
    assert second_decision["ledger_id"] != first_decision["ledger_id"]
    assert second_decision["launch_ledger_id"] == preflight["data"]["ledger_id"]
    assert second_decision["turn_id"] == "turn-two"
    assert second_decision["agent_task_id"] != first_decision["agent_task_id"]
    assert second_stop_result.returncode == 0
    assert second_stop["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert second_stop["data"]["hook_result"].get("decision") != "block"
    assert second_stop["data"].get("agent_sessions", []) == []
    assert "the advisory subagent plan produced no recorded worker sessions" in second_stop["data"]["advisories"]


def test_qwendex_manager_missing_turn_id_is_advisory_for_prompt_and_stop(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}

    prompt_result = run_qwendex(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({
            "session_id": "stock-codex-thread",
            "cwd": str(ROOT),
            "prompt": "Explain status.",
        }),
        "--json", env=manager_env,
    )
    prompt = parse_json_result(prompt_result)
    stop = json_result(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            "session_id": "stock-codex-thread",
            "cwd": str(ROOT),
            "last_assistant_message": "Status explained.",
            "edit_happened": False,
        }),
        "--json", env=manager_env,
    )
    with sqlite3.connect(tmp_path / "qwendex.sqlite") as conn:
        decision = conn.execute(
            "SELECT turn_id, root_session_id, final_status FROM qwendex_manager_decisions WHERE ledger_id = ?",
            (preflight["data"]["ledger_id"],),
        ).fetchone()

    assert prompt_result.returncode == 0
    assert prompt["data"]["hook_result"]["reason_code"] == "turn_unattached"
    assert prompt["data"]["hook_result"]["event"] == "manager.prompt_bookkeeping_unavailable"
    assert stop["data"]["hook_result"]["event"] == "manager.untrusted_stop_allowed"
    assert stop["data"]["hook_result"]["reason_code"] == "turn_unattached"
    assert decision == ("", "", "preflight_ready")


def test_qwendex_manager_stop_uses_only_its_decision_task_sessions(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }
    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    json_result(
        "manager", "assign", "--agent-id", "historical-verifier", "--lane", "verification",
        "--task-id", "unrelated-task", "--required", "--json", env=env,
    )
    json_result(
        "agent", "hook", "SubagentStop", "--event-json",
        json.dumps({
            "agent_id": "historical-verifier",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nValidation: pytest passed",
        }),
        "--json", env=env,
    )
    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({
            "session_id": "real-codex-thread",
            "turn_id": "real-codex-turn",
            "prompt": "Use subagents to update routing and tests",
        }),
        "--json", env=manager_env,
    )
    stop = run_qwendex(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            "session_id": "real-codex-thread",
            "turn_id": "real-codex-turn",
            "last_assistant_message": "Agent outcomes: historical verifier.\nValidation: pytest.\nRisks: none.",
            "edit_happened": False,
        }),
        "--json", env=manager_env,
    )
    stop_data = parse_json_result(stop)

    assert stop.returncode == 0
    assert stop_data["data"]["hook_result"]["event"] == "manager.finalized_with_advisories"
    assert stop_data["data"]["hook_result"].get("decision") != "block"
    assert stop_data["data"]["agent_sessions"] == []
    assert "the advisory subagent plan produced no recorded worker sessions" in stop_data["data"]["advisories"]


def test_qwendex_worker_contract_parser_does_not_misread_prose_failures():
    qwendex = load_qwendex()

    parsed = qwendex.parse_worker_final_status(
        "FINAL_REPORT\nstatus: completed\nsummary: A previously FAILED probe is fixed.\nblockers: not BLOCKED"
    )

    assert parsed["status"] == "completed"
    assert parsed["validation_status"] == "pass"


def test_qwendex_status_file_write_is_atomic(tmp_path, monkeypatch):
    qwendex = load_qwendex()
    target = tmp_path / "status.json"
    target.write_text("old", encoding="utf-8")
    real_replace = qwendex.os.replace
    observed = {}

    def inspect_replace(source, destination):
        observed["old"] = Path(destination).read_text(encoding="utf-8")
        observed["new"] = Path(source).read_text(encoding="utf-8")
        real_replace(source, destination)

    monkeypatch.setattr(qwendex.os, "replace", inspect_replace)
    qwendex.atomic_write_text(target, '{"text":"complete"}\n')

    assert observed == {"old": "old", "new": '{"text":"complete"}\n'}
    assert target.read_text(encoding="utf-8") == '{"text":"complete"}\n'
    assert not list(tmp_path.glob(".*.tmp"))


def test_qwendex_file_locks_are_single_writer_within_repo_not_across_repos(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    for agent_id, repo in (("writer-a", repo_a), ("writer-b", repo_b)):
        json_result(
            "manager", "assign", "--agent-id", agent_id, "--lane", "implementation",
            "--write-surface", "file.txt", "--repo-root", str(repo), "--json", env=env,
        )

    first = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({"tool_name": "apply_patch", "agent_id": "writer-a", "profile": "implementer", "path": "file.txt", "cwd": str(repo_a)}),
        "--json", env=env,
    )
    second = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({"tool_name": "apply_patch", "agent_id": "writer-b", "profile": "implementer", "path": "file.txt", "cwd": str(repo_b)}),
        "--json", env=env,
    )

    assert first["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert second["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert first["data"]["hook_result"]["repo_root"] != second["data"]["hook_result"]["repo_root"]


def test_qwendex_concurrent_write_lock_acquisition_serializes_conflict_check_and_insert(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    for agent_id in ("writer-one", "writer-two"):
        json_result(
            "manager", "assign", "--agent-id", agent_id, "--lane", "implementation",
            "--write-surface", "shared.txt", "--repo-root", str(repo), "--json", env=env,
        )
    argument_sets = [
        (
            "agent", "hook", "PreToolUse", "--event-json",
            json.dumps({
                "tool_name": "apply_patch",
                "agent_id": agent_id,
                "profile": "implementer",
                "path": "shared.txt",
                "cwd": str(repo),
            }),
            "--json",
        )
        for agent_id in ("writer-one", "writer-two")
    ]

    results = run_qwendex_concurrently(argument_sets, env=env)
    payloads = [parse_json_result(result) for result in results]
    with sqlite3.connect(state_db) as conn:
        active_writers = conn.execute(
            """
            SELECT agent_id FROM qwendex_agent_file_locks
            WHERE repo_root = ? AND path = 'shared.txt' AND lock_type = 'write' AND released_at = ''
            """,
            (str(repo),),
        ).fetchall()

    assert sorted(result.returncode for result in results) == [0, 1]
    assert len(active_writers) == 1
    assert {payload["data"]["hook_result"]["event"] for payload in payloads} == {
        "agent.file_locks_acquired",
        "agent.file_lock_conflict",
    }


def test_qwendex_begin_immediate_reports_bounded_busy_state(tmp_path):
    qwendex = load_qwendex()
    state_db = tmp_path / "qwendex.sqlite"
    holder = sqlite3.connect(state_db)
    contender = sqlite3.connect(state_db, timeout=0.01)
    try:
        holder.execute("CREATE TABLE state_test (value TEXT)")
        holder.commit()
        contender.execute("PRAGMA busy_timeout = 10")
        holder.execute("BEGIN IMMEDIATE")

        busy_error = qwendex.begin_immediate(contender)

        assert "locked" in busy_error.lower() or "busy" in busy_error.lower()
        assert contender.in_transaction is False
    finally:
        holder.rollback()
        contender.close()
        holder.close()


def test_qwendex_manager_status_counts_full_ledger_with_bounded_samples(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result("manager", "status", "--json", env=env)
    with sqlite3.connect(state_db) as conn:
        for index in range(25):
            agent_id = f"legacy-{index:02d}"
            conn.execute(
                """
                INSERT INTO qwendex_agent_sessions
                (agent_id, lane, task_id, owner, write_surface, stop_condition,
                 artifacts_json, status, heartbeat_at, created_at, updated_at,
                 stop_reason, close_receipt, validation_status, repo_root)
                VALUES (?, 'review', ?, 'legacy', 'read-only', 'done', '[]', 'closed',
                        '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                        '2026-01-01T00:00:00Z', 'operator_closed', '', 'pending', '')
                """,
                (agent_id, f"legacy-task-{index}"),
            )

    status = json_result("manager", "status", "--limit", "5", "--json", env=env)
    health = status["data"]["manager_health"]
    reconcile = json_result(
        "manager", "reconcile", "--pending-validation", "--json", env=env
    )["data"]
    legacy_close = run_qwendex("agent", "close", "legacy-00", "--json", env=env)
    legacy_heartbeat = run_qwendex(
        "manager", "heartbeat", "--agent-id", "legacy-00", "--json", env=env
    )
    legacy_stop = run_qwendex(
        "agent", "hook", "SubagentStop", "--event-json",
        json.dumps({
            "agent_id": "legacy-00",
            "cwd": str(ROOT),
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nValidation: pytest passed",
        }),
        "--json", env=env,
    )

    assert status["data"]["displayed_session_count"] == 0
    assert status["data"]["scoped_session_count"] == 0
    assert status["data"]["ledger_session_count"] == 25
    assert health["validation_debt"]["pending_validation_count"] == 25
    assert health["validation_debt"]["counts"]["closed_without_validation_evidence"] == 25
    assert len(health["validation_debt"]["classifications"]["closed_without_validation_evidence"]) == 20
    assert health["validation_debt"]["truncated"]["closed_without_validation_evidence"] == 5
    assert health["scope_validation_debt"]["pending_validation_count"] == 0
    assert health["ledger_scope"]["legacy_unscoped_count"] == 25
    assert reconcile["validation_reconciliation"]["pending_validation_count"] == 0
    assert reconcile["ledger_validation_debt"]["pending_validation_count"] == 25
    assert reconcile["legacy_unscoped_count"] == 25
    assert legacy_close.returncode != 0
    assert legacy_heartbeat.returncode != 0
    legacy_stop_payload = parse_json_result(legacy_stop)
    assert legacy_stop.returncode == 0
    assert legacy_stop_payload["data"]["hook_result"]["event"] == "agent.completed"
    assert legacy_stop_payload["data"]["hook_result"].get("decision") != "block"
    assert "worker has legacy unscoped lifecycle state" in legacy_stop_payload["data"]["advisories"]


def test_qwendex_local_toggle_rolls_back_when_status_sync_fails(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    status_directory = tmp_path / "status.json"
    status_directory.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(state_db),
        "QWENDEX_CODEX_STATUS_FILE": str(status_directory),
    }

    result = run_qwendex("manager", "local", "--set", "off", "--json", env=env)
    data = parse_json_result(result)
    with sqlite3.connect(state_db) as conn:
        stored = json.loads(
            conn.execute(
                "SELECT value_json FROM qwendex_manager_settings WHERE key = 'local_subagents_enabled'"
            ).fetchone()[0]
        )

    assert result.returncode != 0
    assert data["data"]["status_sync"]["state_restored"] is True
    assert data["data"]["status_sync"]["error"]
    assert stored is True


def test_qwendex_unscoped_legacy_write_lock_blocks_new_scoped_writer(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result("manager", "status", "--json", env=env)
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            """
            INSERT INTO qwendex_agent_file_locks
            (lock_id, agent_id, path, lock_type, acquired_at, released_at, reason, repo_root)
            VALUES ('legacy-lock', 'legacy-writer', 'old.txt', 'write',
                    '2026-01-01T00:00:00Z', '', 'legacy unscoped writer', '')
            """
        )
    json_result(
        "manager", "assign", "--agent-id", "new-writer", "--lane", "implementation",
        "--write-surface", "new.txt", "--repo-root", str(repo), "--json", env=env,
    )

    result = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({"tool_name": "apply_patch", "agent_id": "new-writer", "profile": "implementer", "path": "new.txt", "cwd": str(repo)}),
        "--json", env=env,
    )
    data = parse_json_result(result)

    assert result.returncode != 0
    assert data["data"]["hook_result"]["event"] == "agent.file_lock_conflict"
    assert data["data"]["hook_result"]["conflicts"][0]["lock_id"] == "legacy-lock"
