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
        env={**os.environ, **(env or {})},
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
            env={**os.environ, **env},
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

    assert qwendex.VERSION == "0.5.7"
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


def test_qwendex_check_and_doctor_health_mode_handles_stale_writer(tmp_path):
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
    assert strict_check.returncode != 0
    assert strict_check_data["status"] == "fail"
    assert strict_check_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_check_data["errors"])
    assert strict_doctor.returncode != 0
    assert strict_doctor_data["status"] == "fail"
    assert strict_doctor_data["data"]["manager_health_mode"] == "strict"
    assert "stale manager writer sessions" in " ".join(strict_doctor_data["errors"])


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
    assert 'QWENDEX_CODEX_REQUIRED_VERSION:-0.144.0' in installer_text
    assert 'QWENDEX_CODEX_NPM_SPEC:-@openai/codex@$QWENDEX_CODEX_REQUIRED_VERSION' in installer_text
    assert 'npm install -g --prefix "$HOME/.local" "$codex_npm_spec"' in installer_text
    assert '"pytest==$QWENDEX_PYTEST_REQUIRED_VERSION"' in installer_text
    assert '"ruff==$QWENDEX_RUFF_REQUIRED_VERSION"' in installer_text
    assert "cargo install ripgrep --locked" in installer_text
    assert "QWENDEX_DEV_ROOT" in text
    assert "$HOME/qwendex-dev" in text
    assert "WORK_ROOT=\"$DEV_ROOT/.qwendex-dev\"" in text
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
    assert "use manager planning/preflight first" in text
    assert "write-surface separation" in text
    assert "treat subagent output as advisory" in text
    assert "direct-work reason and validation path" in text
    assert "close spawned agents and matching Qwendex manager sessions" in text
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
    assert payload["required_codex_version"] == "0.144.0"
    assert payload["codex_compatible"] is False
    assert payload["tools"]["codex"]["path"] == str(fake_codex)
    assert payload["tools"]["codex"]["version"] == "codex-cli 9.9.9"
    assert payload["incompatible_required"] == [
        "codex version 'codex-cli 9.9.9' does not match required 'codex-cli 0.144.0'"
    ]


def test_qwendex_install_deps_check_rejects_codex_version_with_extra_tokens(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    fake_home.mkdir()
    fake_bin.mkdir()
    fake_codex.write_text(
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.0 extra\\n'\n",
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
        "codex-cli 0.144.0 extra"
    )
    assert payload["incompatible_required"] == [
        "codex version 'codex-cli 0.144.0 extra' does not match required "
        "'codex-cli 0.144.0'"
    ]


def test_qwendex_install_deps_failed_npm_logs_real_rc_and_stays_blocked(tmp_path):
    fake_home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_log = tmp_path / "install.log"
    fake_home.mkdir()
    fake_bin.mkdir()

    scripts = {
        "codex": "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.0 extra\\n'\n",
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
        "@openai/codex@0.144.0"
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
        "codex": "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.0\\n'\n",
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
  printf 'codex-cli 0.144.0\\n'
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
    assert config_text.count(f'[projects."{checkout}"]') == 1
    assert config["projects"] == {str(checkout): {"trust_level": "trusted"}}


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
    assert codex_main.stdout.strip() == "codex-cli 0.144.0"
    assert str(fake_codex) in (checkout / "bin" / "codex-main").read_text(encoding="utf-8")
    assert qdex.returncode == 0, qdex.stderr or qdex.stdout
    dry_run = json.loads(qdex.stdout)
    assert dry_run["schema_version"] == "qwendex.qdex.dry_run.v1"
    assert dry_run["target_repo"] == str(checkout)
    assert not (checkout / "bin" / "codex").exists()
    assert dry_run["command"][0] == str(checkout / ".qwendex-dev" / "bin" / "qwendex-codex-runtime")


def test_qwendex_upgrade_ignores_stale_main_codex_and_installed_qdex_opens_other_repo(tmp_path):
    fake_home, checkout, fake_codex, env = same_root_dev_env_fixture(tmp_path)
    args_file = tmp_path / "installed-qdex-args.txt"
    fake_codex.write_text(
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > "$QWENDEX_FAKE_CODEX_ARGS"
for arg in "$@"; do
  if [[ "$arg" == "--version" ]]; then
    printf 'codex-cli 0.144.0\\n'
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
    assert launched.stdout.strip() == "codex-cli 0.144.0"
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
    assert cache_file == "models_cache.qwendex-0.144.0.json"
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
        "#!/usr/bin/env bash\nprintf 'codex-cli 0.144.0\\n'\n",
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
    assert ready.stdout.strip() == "codex-cli 0.144.0"


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
    assert json.loads(qwendex.stdout)["data"]["version"] == "0.5.7"
    assert qwendex_dev.returncode == 0, qwendex_dev.stderr or qwendex_dev.stdout
    assert sourced_env.returncode == 0, sourced_env.stderr or sourced_env.stdout
    assert sourced_env.stdout.strip() == str(checkout)
    assert codex.returncode == 0, codex.stderr or codex.stdout
    assert codex.stdout.strip() == "codex-cli 0.144.0"
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
    assert args[:2] == ["--no-alt-screen", "--dangerously-bypass-approvals-and-sandbox"]
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


def test_qdex_manager_preflight_blocks_and_exports_env_before_launch(tmp_path):
    fake_home = tmp_path / "home"
    dev_root = tmp_path / "qwendex-dev"
    fake_bin = tmp_path / "bin"
    fake_codex = fake_bin / "codex"
    args_file = tmp_path / "qdex-codex-call.json"

    fake_bin.mkdir()
    fake_home.mkdir()
    fake_codex.write_text(
        """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
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
    }
    for key in (
        "CODEX_HOME",
        "QWENDEX_AGENT_USE",
        "CODEX_AGENT_USE",
        "QWENDEX_MANAGER_ALLOW_UNHOOKED",
        "QWENDEX_MANAGER_UNHOOKED_REASON",
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
    assert default_home_payload["permission_mode"] == "yolo"

    blocked = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert blocked.returncode != 0
    assert "Qwendex Manager preflight: blocked" in blocked.stderr
    assert "STOP_MANAGER_BLOCKED_UNHOOKED" in blocked.stderr
    assert not args_file.exists()

    env_override_blocked = subprocess.run(
        [str(qdex), "--repo", str(ROOT)],
        cwd=ROOT,
        env={**env, "QWENDEX_AGENT_USE": "Heavy"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert env_override_blocked.returncode != 0
    assert "Qwendex Manager preflight: blocked" in env_override_blocked.stderr
    assert "STOP_MANAGER_BLOCKED_UNHOOKED" in env_override_blocked.stderr
    assert not args_file.exists()

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
    assert call["args"][:3] == [
        "--no-alt-screen",
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
    ]
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
    assert json_call["args"][-3:] == ["exec", "--json", "report status"]

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
    assert native_cd_call["args"][-4:] == ["-C", str(ROOT), "exec", "report cwd"]
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
    assert relative_cd_call["args"][-4:] == ["-C", ROOT.name, "exec", "report relative cwd"]
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
    assert add_dir_call["args"][-4:] == ["exec", "--add-dir", str(tmp_path), "report roots"]

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
    assert directory_prompt_call["args"][-2:] == ["exec", str(tmp_path)]

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
    assert literal_call["args"][-4:] == ["exec", "--", "--repo", "literal-value"]

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
    assert plain.stdout.strip() == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    written = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["text"] == "{Qwendex} Agent Manager: [Manager Mode] | Kaveman: [N] | Local: [Ready] (Alt+M/K/L)"
    assert written["agent_use"] == "Manager"
    assert written["agent_policy_source"] == "manager-mode"
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
    }
    env_b = {
        "QWENDEX_STATE_DB": str(tmp_path / "state-b.sqlite"),
        "QWENDEX_CODEX_STATUS_FILE": str(status_file),
        "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
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
    assert written["warnings"] == []


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


def test_qwendex_codex_patch_preflight_rejects_partially_applied_source(tmp_path):
    qwendex = load_qwendex()
    source = tmp_path / "codex"
    manifest = qwendex.CODEX_PATCH_MANIFESTS["0.144.0"]
    for index, spec in enumerate(manifest["source_anchors"]):
        rel = str(spec["path"])
        path = source / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n".join(str(anchor) for anchor in spec["anchors"])
        if index == 0:
            text += f"\n// {qwendex.QWENDEX_CODEX_PATCH_MARKER}\n"
        path.write_text(text + "\n", encoding="utf-8")

    fake_codex = tmp_path / "codex-bin"
    fake_codex.write_text("#!/usr/bin/env bash\nprintf 'codex-cli 0.144.0\\n'\n", encoding="utf-8")
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
        "10",
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
    assert legacy["data"]["deployment_contract"]["required"] is True
    assert legacy["data"]["deployment_contract"]["healthy"] is False
    assert "shortcut" not in legacy["data"]
    assert "shortcut_command" not in legacy["data"]
    assert legacy["data"]["max_subagents"] == 6
    assert legacy["data"]["stale_after_minutes"] == 45
    assert "borrowed_patterns" not in legacy["data"]
    assert {"selected_model", "selected_reasoning", "reasoning_source", "escalation_reason", "token_saver_used", "local_qwen_eligible"} <= set(legacy["data"]["lane_template"][0])

    assert disabled["status"] == "ready"
    assert disabled["data"]["manager_deploy_policy"] == "disabled"
    assert disabled["data"]["deployment_contract"]["required"] is False
    assert disabled["data"]["deployment_contract"]["healthy"] is True
    assert disabled["data"]["max_subagents"] == 10


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
    assert status["data"]["agent_policy"]["require_agent_ledger"] is True

    preflight = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Use manager mode with subagents to prove selected manager mode gates finalization",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "selected-manager-required",
        "--lane",
        "review",
        "--task-id",
        preflight["data"]["session_id"],
        "--objective",
        "prove selected manager mode gates finalization",
        "--required",
        "--json",
        env=env,
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "selected-manager-session",
        "turn_id": "selected-manager-turn",
        "cwd": str(ROOT),
    }
    json_result(
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            **turn_identity,
            "prompt": "Prove selected manager mode gates finalization",
        }),
        "--json",
        env=manager_env,
    )
    blocked_stop_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({**turn_identity, "last_assistant_message": "Done."}),
        "--json",
        env=manager_env,
    )
    blocked_stop = parse_json_result(blocked_stop_result)

    assert blocked_stop_result.returncode != 0
    assert blocked_stop["data"]["agent_policy"]["mode"] == "manager"
    assert blocked_stop["data"]["agent_policy"]["source"] == "manager-mode"
    assert blocked_stop["data"]["hook_result"]["event"] == "manager.stop_gate_continued"
    assert blocked_stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]

    off = json_result("manager", "mode", "--set", "off", "--json", env=env)
    spawn_result = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent"}),
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
    assert "allowed this untrusted process to stop" in repeated_output["systemMessage"]


def test_qwendex_manager_root_uses_preflight_identity_and_repo_lock(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    }
    env = with_live_manager_identity(env)
    json_result(
        "agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env
    )
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    root_event = {
        "session_id": "codex-root-session",
        "turn_id": "root-turn-1",
        "cwd": str(repo),
    }
    unattached = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_input": {"agent_id": "main", "cmd": "touch generated.txt"},
        }),
        "--json",
        env=env,
    )
    unattached_payload = parse_json_result(unattached)
    assert unattached.returncode != 0
    assert unattached_payload["data"]["hook_result"]["event"] == "manager.root_unattached"

    malformed_identity = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "apply_patch",
            "agent_type": "implementer",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=env,
    )
    malformed_payload = parse_json_result(malformed_identity)
    assert malformed_identity.returncode != 0
    assert malformed_payload["data"]["hook_result"]["event"] == "agent.identity_malformed"

    malformed_agent_id = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "apply_patch",
            "agent_id": "impersonated-child",
            "tool_input": {"path": "file.txt"},
        }),
        "--json",
        env=env,
    )
    assert malformed_agent_id.returncode != 0
    assert parse_json_result(malformed_agent_id)["data"]["hook_result"]["event"] == "agent.identity_malformed"

    preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    manager_env = {**env, **preflight["data"]["exports"]}
    prompt = json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({**root_event, "prompt": "Explain status."}),
        "--json",
        env=manager_env,
    )
    manager_task_id = prompt["data"]["manager_decision"]["agent_task_id"]
    unregistered_child = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-unregistered-child",
            "turn_id": "unregistered-child-turn",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "unregistered-child",
            "agent_type": "implementer",
            "tool_input": {"path": "file.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert unregistered_child.returncode != 0
    assert parse_json_result(unregistered_child)["data"]["hook_result"]["event"] == "agent.unregistered"
    json_result(
        "manager", "assign", "--agent-id", "read-only-child", "--lane", "review",
        "--task-id", manager_task_id, "--write-surface", "read-only",
        "--file", "file.txt", "--repo-root", str(repo), "--json", env=env,
    )
    read_only_write = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-read-only-child",
            "turn_id": "read-only-child-turn",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "read-only-child",
            "agent_type": "default",
            "tool_input": {"path": "file.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert read_only_write.returncode != 0
    assert parse_json_result(read_only_write)["data"]["hook_result"]["event"] == "agent.write_rejected"
    json_result(
        "manager", "assign", "--agent-id", "invalid-scope-child", "--lane", "implementation",
        "--task-id", manager_task_id, "--write-surface", "declared-scope",
        "--file", "../outside.txt", "--repo-root", str(repo), "--json", env=env,
    )
    invalid_scope = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-invalid-scope-child",
            "turn_id": "invalid-scope-child-turn",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "invalid-scope-child",
            "agent_type": "implementer",
            "tool_input": {"path": "file.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert invalid_scope.returncode != 0
    assert parse_json_result(invalid_scope)["data"]["hook_result"]["event"] == "agent.path_scope_mismatch"
    for control_tool in ("create_goal", "update_goal", "update_plan"):
        control = json_result(
            "agent", "hook", "PreToolUse", "--event-json",
            json.dumps({
                **root_event,
                "tool_name": control_tool,
                "tool_input": {"status": "in_progress"},
            }),
            "--json",
            env=manager_env,
        )
        assert control["data"]["hook_result"] == {}
    spoofed_control = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "mcp__untrusted__update_plan",
            "tool_use_id": "spoofed-control",
            "tool_input": {"status": "in_progress"},
        }),
        "--json",
        env=manager_env,
    )
    assert spoofed_control["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    json_result(
        "agent", "hook", "PostToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "mcp__untrusted__update_plan",
            "tool_use_id": "spoofed-control",
            "tool_input": {"status": "in_progress"},
        }),
        "--json",
        env=manager_env,
    )
    for inspection_command in ("python3 -V", "file README.md", "git status | head -n 1"):
        inspection = json_result(
            "agent", "hook", "PreToolUse", "--event-json",
            json.dumps({
                **root_event,
                "tool_name": "exec_command",
                "tool_input": {"cmd": inspection_command},
            }),
            "--json",
            env=manager_env,
        )
        assert inspection["data"]["hook_result"] == {}
    shell = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_use_id": "root-shell",
            "tool_input": {"cmd": "touch generated.txt"},
        }),
        "--json",
        env=manager_env,
    )
    opaque_patch = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "apply_patch",
            "tool_use_id": "root-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )

    for result in (shell, opaque_patch):
        hook = result["data"]["hook_result"]
        assert hook["event"] == "agent.file_locks_acquired"
        assert hook["root_agent_id"] == preflight["data"]["root_agent_id"]
        assert hook["agent_id"].startswith(f'{preflight["data"]["root_agent_id"]}--tool-')
        assert hook["ownership_source"] == "manager_preflight"
        assert hook["acquired"][0]["path"] == "<repo-root>"

    root_only = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-child-session",
            "cwd": str(repo),
            "tool_name": "spawn_agent",
            "agent_id": "registered-writer",
            "agent_type": "implementer",
        }),
        "--json",
        env=manager_env,
    )
    assert root_only.returncode != 0
    assert parse_json_result(root_only)["data"]["hook_result"]["event"] == "agent.spawn_rejected"

    json_result(
        "manager", "assign", "--agent-id", "registered-writer", "--lane", "implementation",
        "--task-id", manager_task_id, "--write-surface", "file.txt",
        "--file", "file.txt",
        "--repo-root", str(repo), "--json", env=env,
    )
    conflict = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-child-session",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "registered-writer",
            "agent_type": "implementer",
            "tool_use_id": "child-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )
    conflict_payload = parse_json_result(conflict)
    assert conflict.returncode != 0
    assert conflict_payload["data"]["hook_result"]["event"] == "agent.file_lock_conflict"

    released_shell = json_result(
        "agent", "hook", "PostToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_use_id": "root-shell",
            "tool_input": {"cmd": "touch generated.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert len(released_shell["data"]["released_root_locks"]) == 1

    still_conflicted = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-child-session",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "registered-writer",
            "agent_type": "implementer",
            "tool_use_id": "child-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )
    assert still_conflicted.returncode != 0
    assert parse_json_result(still_conflicted)["data"]["hook_result"]["event"] == "agent.file_lock_conflict"

    released_patch = json_result(
        "agent", "hook", "PostToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "apply_patch",
            "tool_use_id": "root-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )
    assert len(released_patch["data"]["released_root_locks"]) == 1

    for denied_path in ("other.txt", "file.txt/nested", "/etc/passwd", "../outside.txt"):
        out_of_scope = run_qwendex(
            "agent", "hook", "PreToolUse", "--event-json",
            json.dumps({
                "session_id": "codex-child-session",
                "turn_id": "child-turn-1",
                "cwd": str(repo),
                "tool_name": "apply_patch",
                "agent_id": "registered-writer",
                "agent_type": "implementer",
                "tool_use_id": "child-out-of-scope",
                "tool_input": {"path": denied_path},
            }),
            "--json",
            env=manager_env,
        )
        assert out_of_scope.returncode != 0
        assert parse_json_result(out_of_scope)["data"]["hook_result"]["event"] == "agent.path_scope_mismatch"

    child_acquired = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-child-session",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "registered-writer",
            "agent_type": "implementer",
            "tool_use_id": "child-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )
    child_hook = child_acquired["data"]["hook_result"]
    assert child_hook["ownership_source"] == "hook_agent_id"
    assert child_hook["acquired"][0]["path"] == "file.txt"

    json_result(
        "agent", "hook", "SubagentStop", "--event-json",
        json.dumps({
            "session_id": "codex-child-session",
            "cwd": str(repo),
            "agent_id": "registered-writer",
            "agent_type": "implementer",
            "last_assistant_message": (
                "FINAL_REPORT\nstatus: completed\nsummary: contract probe\nevidence:\n- lock released"
            ),
        }),
        "--json",
        env=manager_env,
    )

    json_result(
        "manager", "assign", "--agent-id", "declared-writer", "--lane", "implementation",
        "--task-id", manager_task_id, "--write-surface", "declared-scope",
        "--repo-root", str(repo), "--json", env=env,
    )
    declared_scope = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            "session_id": "codex-declared-child",
            "turn_id": "declared-child-turn",
            "cwd": str(repo),
            "tool_name": "apply_patch",
            "agent_id": "declared-writer",
            "agent_type": "implementer",
            "tool_use_id": "declared-child-patch",
            "tool_input": {"patch": "*** Begin Patch\n*** End Patch"},
        }),
        "--json",
        env=manager_env,
    )
    assert declared_scope["data"]["hook_result"]["acquired"][0]["path"] == "<repo-root>"
    json_result(
        "agent", "hook", "SubagentStop", "--event-json",
        json.dumps({
            "session_id": "codex-declared-child",
            "cwd": str(repo),
            "agent_id": "declared-writer",
            "agent_type": "implementer",
            "last_assistant_message": (
                "FINAL_REPORT\nstatus: completed\nsummary: declared scope probe\nevidence:\n- lock released"
            ),
        }),
        "--json",
        env=manager_env,
    )

    json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_use_id": "root-validation",
            "tool_input": {"cmd": "touch validation.txt"},
        }),
        "--json",
        env=manager_env,
    )

    validation_pending = run_qwendex(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            **root_event,
            "last_assistant_message": "Done without validation.",
            "edit_happened": True,
        }),
        "--json",
        env=manager_env,
    )
    assert validation_pending.returncode != 0
    assert parse_json_result(validation_pending)["data"]["hook_result"]["event"] == "manager.validation_pending"
    released_after_block = json_result("agent", "locks", "--json", env=manager_env)
    assert released_after_block["data"]["write_safety"]["active_count"] == 0

    reacquired = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **root_event,
            "tool_name": "exec_command",
            "tool_use_id": "root-final",
            "tool_input": {"cmd": "touch final.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert reacquired["data"]["hook_result"]["event"] == "agent.file_locks_acquired"

    stopped = json_result(
        "agent", "hook", "Stop", "--event-json",
        json.dumps({
            **root_event,
            "last_assistant_message": "No edits. Validation: not required. Risks: none.",
            "edit_happened": False,
        }),
        "--json",
        env=manager_env,
    )
    locks = json_result("agent", "locks", "--json", env=manager_env)
    assert stopped["data"]["hook_result"]["event"] == "manager.finalized"
    assert len(stopped["data"]["released_root_locks"]) == 1
    assert locks["data"]["write_safety"]["active_count"] == 0


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


def test_qwendex_manager_root_reclaims_dead_launcher_lease(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    qwendex = load_qwendex()
    live_pid = os.getpid()
    live_start_ticks = qwendex.process_start_ticks(live_pid)
    assert live_start_ticks
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
        "QWENDEX_MANAGER_LAUNCH_PID": str(live_pid),
        "QWENDEX_MANAGER_LAUNCH_START_TICKS": live_start_ticks,
    }
    json_result(
        "agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env
    )
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    first = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    first_env = {**env, **first["data"]["exports"]}
    first_event = {
        "session_id": "first-root-session",
        "turn_id": "first-root-turn",
        "cwd": str(repo),
    }
    json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({**first_event, "prompt": "Explain status."}),
        "--json", env=first_env,
    )
    json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **first_event,
            "tool_name": "exec_command",
            "tool_use_id": "stranded-root-tool",
            "tool_input": {"cmd": "touch first.txt"},
        }),
        "--json", env=first_env,
    )
    second = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    second_env = {**env, **second["data"]["exports"]}
    second_event = {
        "session_id": "second-root-session",
        "turn_id": "second-root-turn",
        "cwd": str(repo),
    }
    json_result(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({**second_event, "prompt": "Explain status."}),
        "--json", env=second_env,
    )
    live_conflict = run_qwendex(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **second_event,
            "tool_name": "exec_command",
            "tool_use_id": "second-root-tool",
            "tool_input": {"cmd": "touch second.txt"},
        }),
        "--json", env=second_env,
    )
    live_conflict_payload = parse_json_result(live_conflict)
    assert live_conflict.returncode != 0
    assert live_conflict_payload["data"]["hook_result"]["event"] == "agent.file_lock_conflict"
    assert live_conflict_payload["data"]["hook_result"]["reclaimed_root_locks"] == []

    with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
        conn.execute(
            """
            UPDATE qwendex_manager_decisions
            SET launch_pid = 999999999, launch_start_ticks = 'dead-launch'
            WHERE launch_ledger_id = ? OR ledger_id = ?
            """,
            (first["data"]["ledger_id"], first["data"]["ledger_id"]),
        )

    acquired = json_result(
        "agent", "hook", "PreToolUse", "--event-json",
        json.dumps({
            **second_event,
            "tool_name": "exec_command",
            "tool_use_id": "second-root-tool",
            "tool_input": {"cmd": "touch second.txt"},
        }),
        "--json", env=second_env,
    )
    reclaimed = acquired["data"]["hook_result"]["reclaimed_root_locks"]
    assert len(reclaimed) == 1
    assert reclaimed[0]["reclaim_reason"] == "dead_manager_launch"
    json_result(
        "agent", "hook", "PostToolUse", "--event-json",
        json.dumps({
            **second_event,
            "tool_name": "exec_command",
            "tool_use_id": "second-root-tool",
            "tool_input": {"cmd": "touch second.txt"},
        }),
        "--json", env=second_env,
    )


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
    assert "allowed this untrusted process to stop" in stop_output["systemMessage"]
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
        env={**env, "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1"},
    )
    assert cli_mode["data"]["mode"] == "manager"
    assert cli_mode["data"]["selected_manager_mode"] == "manager"
    assert cli_mode["data"]["effective_agent_mode"] == "manager"
    assert cli_mode["data"]["policy_source"] == "manager-mode"
    assert cli_mode["data"]["manager_required"] is True

    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    blocked_result = run_qwendex("manager", "preflight", "--interactive-prompt-unknown", "--dry-run", "--json", env=env)
    blocked = parse_json_result(blocked_result)
    assert blocked_result.returncode != 0
    assert blocked["data"]["mode"] == "manager"
    assert blocked["data"]["agent_use"] == "Manager"
    assert blocked["data"]["policy_source"] == "manager-mode"
    assert blocked["data"]["hook_status"]["hook_source_count"] == 0
    assert blocked["data"]["routing_decision"]["selected_route"] == "blocked"
    assert blocked["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

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
    assert partial_result.returncode != 0
    assert partial["data"]["hook_status"]["hook_source_count"] == 1
    assert partial["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert partial["data"]["hook_status"]["verified"] is False
    assert partial["data"]["hook_status"]["incompatible_events"] == ["Stop"]
    assert "UserPromptSubmit" in partial["data"]["hook_status"]["missing_events"]
    assert partial["data"]["routing_decision"]["selected_route"] == "blocked"
    assert partial["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

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
    assert stale_result.returncode != 0
    assert stale["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["hook_status"]["compatible_hook_source_count"] == 0
    assert stale["data"]["hook_status"]["verified"] is False
    assert set(stale["data"]["hook_status"]["incompatible_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert stale["data"]["routing_decision"]["selected_route"] == "blocked"
    assert stale["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

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
    assert plain_result.returncode != 0
    assert plain["data"]["hook_status"]["hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["hook_status"]["verified"] is False
    assert set(plain["data"]["hook_status"]["missing_runtime_env_events"]) == set(qwendex.MANAGED_AGENT_HOOKS)
    assert plain["data"]["routing_decision"]["selected_route"] == "blocked"
    assert plain["data"]["stop_status"] == "STOP_MANAGER_BLOCKED_UNHOOKED"

    installed = json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    assert installed["data"]["hook_status"]["verified"] is True
    assert installed["data"]["hook_status"]["compatible_hook_source_count"] == len(qwendex.MANAGED_AGENT_HOOKS)

    ready = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Small edit with validation evidence",
        "--json",
        env={**env, "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1"},
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
    assert ready["data"]["prompt"]["known"] is True
    assert ready["data"]["prompt"]["prompt_digest"]
    assert ready["data"]["prompt"]["prompt_summary"] == "Small edit with validation evidence"
    assert ready["data"]["manager_estimate"]["created"] is True
    assert ready["data"]["routing_decision"]["selected_route"] in {"direct_single_writer", "manager_subagents"}
    assert ready["data"]["routing_decision"]["verifier_required"] is True
    assert ready["data"]["routing_decision"]["validation_plan"]
    receipt = Path(ready["data"]["receipt_paths"][0])
    assert receipt.exists()

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
    assert "default lane model=qwen-local, reasoning=low with token_saver=true" in on_context


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
    assert env_policy["data"]["agent_policy"]["require_agent_ledger"] is True
    assert env_policy["data"]["agent_policy"]["child_can_spawn"] is False
    assert env_policy["data"]["agent_policy"]["max_threads"] == 10
    assert env_policy["data"]["agent_policy"]["capacity_source"] == "orchestration.mode_profiles"
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["root_management_tools"]
    assert "spawn_agent" in env_policy["data"]["agent_policy"]["tool_surface"]["denied_child_tools"]

    assert cli_policy["data"]["agent_policy"]["mode"] == "lite"
    assert cli_policy["data"]["agent_policy"]["source"] == "cli"
    assert cli_policy["data"]["agent_policy"]["root_can_spawn"] is False
    assert cli_policy["data"]["agent_policy"]["max_threads"] == 2

    assert fallback["data"]["agent_policy"]["mode"] == "medium"
    assert fallback["data"]["agent_policy"]["warnings"]
    assert fallback["data"]["agent_policy"]["source"] == "qwendex-env-fallback"
    assert fallback["data"]["agent_policy"]["max_threads"] == 4

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
    assert closed["data"]["closed"][0]["status"] == "closed"
    assert closed["data"]["closed"][0]["stop_reason"] == "integrated"
    assert after["data"]["active_subagents"]["count"] == 0


def test_qwendex_agent_profiles_and_team_are_visible():
    profiles = json_result("agent", "profiles", "--json")
    team = json_result("agent", "team", "--json")

    assert {"explorer", "implementer", "verifier", "docs_researcher", "release_manager", "scribe"} <= set(profiles["data"]["profiles"])
    assert profiles["data"]["profiles"]["explorer"]["sandbox_mode"] == "read-only"
    assert profiles["data"]["profiles"]["explorer"]["can_spawn"] is False
    assert "publish" in profiles["data"]["profiles"]["release_manager"]["tools_deny"]
    assert team["data"]["team"]["default_mode"] == "Manager"
    assert "verifier" in team["data"]["team"]["required_lanes_by_task"]["code_edit_complex"]


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
    assert {"explorer", "implementer", "verifier", "scribe"} <= set(team_plan["profiles"])
    assert all(item["assign_command"].startswith("qwendex manager assign") for item in team_plan["assignments"])
    assert any("--required" in item["assign_command"] for item in team_plan["assignments"])
    assert any(item["profile"] == "scribe" and item["required"] is False for item in team_plan["assignments"])

    assert release_plan["profiles"] == ["release_manager", "verifier"]
    assert release_plan["assignments"][0]["routing"]["selected_model"] == "gpt-5.5"
    assert release_plan["assignments"][0]["routing"]["selected_reasoning"] in {"high", "xhigh"}
    assert "task-release" in release_plan["assignments"][0]["assign_command"]

    local_assignment = local_plan["assignments"][0]
    assert local_assignment["routing"]["selected_model"] == "qwen-local"
    assert local_assignment["routing"]["selected_reasoning"] == "low"
    assert local_assignment["routing"]["token_saver_used"] is True
    assert "qwen-local" in local_assignment["spawn_instruction"]
    assert "reasoning=low" in local_assignment["spawn_instruction"]


def test_qwendex_agent_metrics_track_ledger_and_artifacts(tmp_path):
    env = {"QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite")}

    empty = json_result("agent", "metrics", "--json", env=env)
    assert empty["data"]["agent_metrics"]["session_count"] == 0
    assert empty["data"]["agent_metrics"]["final_contract_compliance"] is None

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
    assert active["data"]["agent_metrics"]["required_incomplete_count"] == 1

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
    assert metrics["required_incomplete_count"] == 0
    assert metrics["final_contract_compliance"] == 1.0
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


def test_qwendex_agent_hooks_enforce_final_contract_and_manager_stop_gate(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    env = with_live_manager_identity(env)
    standard_policy = json_result("--agent-use", "Manager", "agent", "policy", "--json", env=env)
    kaveman = json_result("manager", "kaveman", "--set", "on", "--json", env=env)
    directive = kaveman["data"]["kaveman_directive"]
    terse_policy = json_result("--agent-use", "Manager", "agent", "policy", "--json", env=env)
    output_policy = terse_policy["data"]["agent_policy"]["output_policy"]
    agent_plan = json_result("--agent-use", "Manager", "agent", "plan", "--prompt", "Use manager mode with verifier evidence.", "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    assert standard_policy["data"]["agent_policy"]["output_policy"]["terse_output"] is False
    assert output_policy["name"] == "kaveman"
    assert output_policy["terse_output"] is True
    assert "agent_policy" in output_policy["enforced_by"]
    assert terse_policy["data"]["agent_policy"]["policy_hash"] != standard_policy["data"]["agent_policy"]["policy_hash"]
    assert terse_policy["data"]["agent_policy"]["env"]["QWENDEX_OUTPUT_POLICY"] == "kaveman"
    assert terse_policy["data"]["agent_policy"]["env"]["QWENDEX_KAVEMAN_DIRECTIVE"] == directive
    assert agent_plan["data"]["agent_plan"]["output_policy"]["terse_output"] is True

    context_preflight = json_result(
        "manager", "preflight", "--interactive-prompt-unknown", "--json", env=env
    )
    context_env = {**env, **context_preflight["data"]["exports"]}
    context_event = {
        "session_id": "manager-context-session",
        "turn_id": "manager-context-turn",
        "cwd": str(ROOT),
        "prompt": "Show the Manager root-orchestrator context",
    }
    prompt_hook = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps(context_event),
        "--json",
        env=context_env,
    )
    raw_prompt_hook = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps(context_event),
        "--codex-hook-output",
        env=context_env,
    )
    raw_prompt = json.loads(raw_prompt_hook.stdout)
    assert raw_prompt_hook.returncode == 0
    assert set(raw_prompt) == {"hookSpecificOutput"}
    assert "root orchestrator" in raw_prompt["hookSpecificOutput"]["additionalContext"]
    assert "Qwendex output policy: Kaveman enabled" in raw_prompt["hookSpecificOutput"]["additionalContext"]
    assert directive in raw_prompt["hookSpecificOutput"]["additionalContext"]
    assert "status" not in raw_prompt
    assigned = json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-hook-verifier",
        "--lane",
        "verification",
        "--task-id",
        "task-hook",
        "--required",
        "--json",
        env=env,
    )
    subagent_start = json_result(
        "agent",
        "hook",
        "SubagentStart",
        "--event-json",
        json.dumps({"agent_id": "agent-hook-verifier", "agent_type": "verifier"}),
        "--json",
        env=env,
    )
    assert "Qwendex output policy: Kaveman enabled" in subagent_start["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert directive in subagent_start["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]

    preflight = json_result(
        "manager",
        "preflight",
        "--prompt",
        "Use manager mode with subagents and verifier evidence for this edit",
        "--json",
        env=env,
    )
    json_result(
        "manager",
        "assign",
        "--agent-id",
        "agent-hook-verifier",
        "--lane",
        "verification",
        "--task-id",
        preflight["data"]["session_id"],
        "--required",
        "--json",
        env=env,
    )
    assert preflight["data"]["output_policy"]["terse_output"] is True
    assert preflight["data"]["exports"]["QWENDEX_OUTPUT_POLICY"] == "kaveman"
    assert preflight["data"]["exports"]["QWENDEX_KAVEMAN_ENABLED"] == "1"
    assert preflight["data"]["exports"]["QWENDEX_KAVEMAN_DIRECTIVE"] == directive
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "manager-stop-gate-session",
        "turn_id": "manager-stop-gate-turn",
        "cwd": str(ROOT),
    }
    json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "UserPromptSubmit",
        "--event-json",
        json.dumps({
            **turn_identity,
            "prompt": "Use Manager subagents and verifier evidence for this edit",
        }),
        "--json",
        env=manager_env,
    )
    blocked_stop_result = run_qwendex(
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
    missing_contract_result = run_qwendex(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({"agent_id": "agent-hook-verifier", "last_assistant_message": "Ready when you are."}),
        "--json",
        env=env,
    )
    completed = json_result(
        "agent",
        "hook",
        "SubagentStop",
        "--event-json",
        json.dumps({
            "agent_id": "agent-hook-verifier",
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nagent_id: agent-hook-verifier\nevidence:\n- pytest passed",
        }),
        "--json",
        env=env,
    )
    missing_summary_result = run_qwendex(
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
    passed_stop = json_result(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Agent outcomes: verifier passed.\nValidation: pytest.\nRisks: none.",
            "edit_happened": True,
        }),
        "--json",
        env=manager_env,
    )

    blocked_stop = parse_json_result(blocked_stop_result)
    missing_contract = parse_json_result(missing_contract_result)
    missing_summary = parse_json_result(missing_summary_result)

    assert "root orchestrator" in prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert "Qwendex output policy: Kaveman enabled" in prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert directive in prompt_hook["data"]["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert assigned["data"]["agent_session"]["context_packet"]["required"] is True
    assert blocked_stop_result.returncode != 0
    assert blocked_stop["data"]["hook_result"]["event"] == "manager.stop_gate_continued"
    assert "agent-hook-verifier:active" in blocked_stop["data"]["hook_result"]["reason"]
    assert blocked_stop["data"]["manager_decision"]["ledger_id"] == preflight["data"]["ledger_id"]
    assert missing_contract_result.returncode != 0
    assert missing_contract["data"]["hook_result"]["event"] == "agent.final_contract_missing"
    assert completed["data"]["hook_result"]["event"] == "agent.completed"
    assert completed["data"]["agent_session"]["status"] == "completed"
    assert completed["data"]["agent_session"]["validation_status"] == "pass"
    artifacts = completed["data"]["agent_session"]["artifacts"]
    raw_artifact = next(path for path in artifacts if path.endswith("/raw-output.md"))
    compact_artifact = next(path for path in artifacts if path.endswith("/compact-report.json"))
    assert "FINAL_REPORT" in (ROOT / raw_artifact).read_text(encoding="utf-8")
    compact_report = json.loads((ROOT / compact_artifact).read_text(encoding="utf-8"))
    assert compact_report["agent_id"] == "agent-hook-verifier"
    assert compact_report["status"] == "completed"
    logs = json_result("agent", "logs", "agent-hook-verifier", "--json", env=env)
    assert raw_artifact in logs["data"]["raw_output_artifacts"]
    assert missing_summary_result.returncode != 0
    assert missing_summary["data"]["hook_result"]["event"] == "manager.final_contract_missing"
    assert passed_stop["data"]["hook_result"]["event"] == "manager.finalized"
    assert passed_stop["data"]["manager_decision"]["stop_status"] == "STOP_MANAGER_CLOSED"


def test_qwendex_manager_direct_work_exception_requires_validation_evidence(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "qwendex.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
    }

    env = with_live_manager_identity(env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    manager_env = {**env, **preflight["data"]["exports"]}
    turn_identity = {
        "session_id": "manager-direct-session",
        "turn_id": "manager-direct-turn",
        "cwd": str(ROOT),
    }
    attached = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            **turn_identity,
            "tool_name": "apply_patch",
            "tool_use_id": "manager-direct-attach",
            "tool_input": {"path": "manager-direct.txt"},
        }),
        "--json",
        env=manager_env,
    )
    assert attached["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert preflight["data"]["routing_decision"]["selected_route"] == "direct_single_writer"
    assert preflight["data"]["routing_decision"]["direct_work_exception"] is True

    missing_validation_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Agent outcomes: direct writer.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
        }),
        "--json",
        env=manager_env,
    )
    missing_validation = parse_json_result(missing_validation_result)
    assert missing_validation_result.returncode != 0
    assert missing_validation["data"]["hook_result"]["event"] == "manager.validation_pending"
    assert missing_validation["data"]["hook_result"]["stop_status"] == "STOP_MANAGER_VALIDATION_PENDING"

    negative_validation_result = run_qwendex(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Agent outcomes: direct writer.\nValidation: not run.\nDirty: in-scope docs only.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
        }),
        "--json",
        env=manager_env,
    )
    negative_validation = parse_json_result(negative_validation_result)
    assert negative_validation_result.returncode != 0
    assert negative_validation["data"]["hook_result"]["event"] == "manager.validation_pending"
    assert negative_validation["data"]["hook_result"]["stop_status"] == "STOP_MANAGER_VALIDATION_PENDING"

    closed = json_result(
        "agent",
        "hook",
        "Stop",
        "--event-json",
        json.dumps({
            **turn_identity,
            "last_assistant_message": "Agent outcomes: direct writer.\nValidation: pytest.\nDirty: in-scope docs only.\nRisks: none.",
            "edit_happened": True,
            "dirty_worktree_classification": "in-scope",
            "validation_evidence": ["pytest passed"],
        }),
        "--json",
        env=manager_env,
    )
    assert closed["data"]["hook_result"]["event"] == "manager.finalized"
    assert closed["data"]["manager_decision"]["final_status"] == "closed"
    assert closed["data"]["manager_decision"]["stop_status"] == "STOP_MANAGER_CLOSED"
    receipt_payload = json.loads(Path(closed["data"]["manager_decision"]["receipt_paths"][0]).read_text(encoding="utf-8"))
    assert receipt_payload["stop_status"] == "STOP_MANAGER_CLOSED"
    assert receipt_payload["routing_decision"]["direct_work_exception"] is True


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
    ):
        assert qwendex.stop_event_has_validation_evidence({}, message, config=config) is True, message

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


def test_qwendex_agent_pre_tool_hook_denies_unsafe_actions(tmp_path):
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
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "apply_patch", "profile": "explorer"}),
        "--json",
        env=env,
    )
    release_publish = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "git push --tags"}),
        "--json",
        env=env,
    )
    event_release_approval_attempt = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "git push --tags", "release_approved": True}),
        "--json",
        env=env,
    )
    stop_hook_active_bypass_attempt = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "exec_command",
            "command": "gh release create v0.3.1",
            "stop_hook_active": True,
        }),
        "--json",
        env=env,
    )
    inline_approval_attempts = [
        run_qwendex(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            "QWENDEX_RELEASE_APPROVED=1 gh release create v0.3.1",
            "env QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz",
            "env -i QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz",
            "env -u HOME QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz",
            "env -S 'QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz'",
            "env --split-string='QWENDEX_RELEASE_APPROVED=1 gh release upload v0.3.1 dist.tgz'",
            "export QWENDEX_RELEASE_APPROVED=1; gh release edit v0.3.1 --draft=false",
        )
    ]
    parent_env_release_approved = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "exec_command",
            "command": "gh release delete v0.3.1 --yes",
            "agent_id": "release-operator",
            "profile": "implementer",
            "path": "RELEASE.md",
            "cwd": str(ROOT),
        }),
        "--json",
        env={**env, "QWENDEX_RELEASE_APPROVED": "true"},
    )
    dev_branch_push = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "exec_command",
            "command": "git push origin dev/some-branch",
            "agent_id": "release-operator",
            "profile": "implementer",
            "path": "RELEASE.md",
            "cwd": str(ROOT),
        }),
        "--json",
        env=env,
    )
    safe_release_results = [
        json_result(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({
                "tool_name": "exec_command",
                "command": command,
                "agent_id": "release-operator",
                "profile": "implementer",
                "path": "RELEASE.md",
                "cwd": str(ROOT),
            }),
            "--json",
            env=env,
        )
        for command in (
            "gh release view v0.3.1",
            "gh release list",
            "gh release download v0.3.1",
            "gh release verify v0.3.1",
            "gh release verify-asset v0.3.1 dist.tgz",
            "gh release -R owner/repo view v0.3.1",
            "gh -R owner/repo release list",
            "gh release --repo owner/repo download v0.3.1",
            "gh api -XGET repos/owner/repo/releases",
            "gh api -XGET -fper_page=1 repos/owner/repo/releases",
            "gh api repos/owner/repo/git/refs",
            "gh api --method GET -fper_page=1 repos/owner/repo/git/refs",
            "/usr/bin/gh release view v0.5.0",
            "/usr/bin/git push origin dev/some-branch",
            "git -C . push origin dev/some-branch",
            "git --no-pager push origin dev/some-branch",
            "command git status --short",
            "command -v git push --tags",
            "bash -c 'git status --short'",
            "zsh -c 'printf \"%s\\n\" \"git push --tags\"'",
            "printf '%s\\n' 'true | git push --tags'",
            "true | /usr/bin/git status --short",
            "echo '$(git push --tags)'",
            "echo '`git push --tags`'",
            "eval 'git status --short'",
            "printf '%s\\n' 'git push --tags' | cat",
            "printf '%s\\n' 'echo safe' | sh safe-script.sh",
        )
    ]
    equivalent_release_results = [
        run_qwendex(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            "gh release upload v0.3.1 dist.tgz",
            "gh release edit v0.3.1 --draft=false",
            "gh release delete v0.3.1 --yes",
            "gh release delete-asset v0.3.1 dist.tgz --yes",
            "gh release new v0.3.1",
            "gh release -R owner/repo create v0.3.1",
            "gh -R owner/repo release create v0.3.1",
            "gh --repo owner/repo release new v0.3.1",
            "gh release --repo owner/repo create v0.3.1",
            "env -i gh release upload v0.3.1 dist.tgz",
            "env -u HOME gh release upload v0.3.1 dist.tgz",
            "env -S 'gh release upload v0.3.1 dist.tgz'",
            "env --split-string='gh release upload v0.3.1 dist.tgz'",
            "gh api repos/owner/repo/releases -X POST -f tag_name=v0.3.1",
            "gh api -XPOST repos/owner/repo/releases",
            "gh api repos/owner/repo/releases -XDELETE",
            "gh api -ftag_name=v0.3.1 repos/owner/repo/releases",
            "gh api -Ftag_name=v0.3.1 repos/owner/repo/releases",
            "gh api -XPOST repos/owner/repo/git/refs",
            "gh api repos/owner/repo/git/refs/tags/v0.3.1 -XDELETE",
            "gh api graphql -f query=mutation",
            "gh api --input payload.json repos/owner/repo/git/refs",
            "gh api -Fref=refs/tags/v0.3.1 repos/owner/repo/git/refs",
            "gh api repos/owner/repo/releases/123 --method PATCH -f name=v0.3.1",
            "QWENDEX_RELEASE_APPROVED=1 echo ok && gh release create v0.3.1",
            "twine upload",
            "python -m twine upload",
            "python3 -m twine upload",
            "poetry publish",
            "uv publish",
            "hatch publish",
            "git push origin v0.3.1",
            "git push",
            "git push origin",
            "git push origin HEAD",
            "git push --force origin",
            "git -C . push origin",
            "git push --all origin",
            "git push origin --all",
            "git push --mirror origin",
            "git push origin --mirror",
            "git -C . push origin main",
            "git -C. push --tags origin",
            "git --git-dir=.git --work-tree=. push origin main",
            "git -c protocol.version=2 push origin main",
            "git --no-pager push origin main",
            "git push origin refs/tags/v0.3.1",
            "git push origin HEAD:main",
            "git push origin HEAD:refs/heads/main",
            "git push origin main:main",
            "git push HEAD:main",
            "git push main:main",
            "/usr/bin/git push --tags",
            "command git push --tags",
            "bash -c 'git push --tags'",
            "/usr/bin/gh release create v0.5.0",
            "env -i PATH=/usr/bin /usr/bin/git push --tags",
            "command env FOO=bar /usr/bin/gh release create v0.5.0",
            "true | git push --tags",
            "echo safe-first-line\ngit push --tags",
            "bash -c 'true | /usr/bin/git push --tags'",
            "echo $(git push --tags)",
            "echo `git push --tags`",
            "eval 'git push --tags'",
            "bash -c \"eval 'git push --tags'\"",
            "bash -c 'echo $(/usr/bin/gh release create v0.5.0)'",
            "printf '%s\\n' 'git push --tags' | sh",
            "printf '%s\\n' 'gh release create v0.5.0' | command env -i /bin/sh",
        )
    ]
    raw_child_spawn = run_qwendex(
        "--agent-use",
        "Manager",
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "spawn_agent", "depth": 1}),
        "--codex-hook-output",
        env=env,
    )
    raw_child_spawn_data = json.loads(raw_child_spawn.stdout)

    child_spawn_data = parse_json_result(child_spawn)
    read_only_write_data = parse_json_result(read_only_write)
    release_publish_data = parse_json_result(release_publish)

    assert child_spawn.returncode != 0
    assert raw_child_spawn.returncode == 0
    assert raw_child_spawn_data["decision"] == "block"
    assert "Child agents cannot use" in raw_child_spawn_data["reason"]
    assert "status" not in raw_child_spawn_data
    assert child_spawn_data["data"]["hook_result"]["event"] == "agent.spawn_rejected"
    assert read_only_write.returncode != 0
    assert read_only_write_data["data"]["hook_result"]["event"] == "agent.write_rejected"
    assert release_publish.returncode != 0
    assert release_publish_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    event_release_approval_data = parse_json_result(event_release_approval_attempt)
    assert event_release_approval_attempt.returncode != 0
    assert event_release_approval_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    stop_hook_active_bypass_data = parse_json_result(stop_hook_active_bypass_attempt)
    assert stop_hook_active_bypass_attempt.returncode != 0
    assert stop_hook_active_bypass_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    for result in inline_approval_attempts:
        result_data = parse_json_result(result)
        assert result.returncode != 0
        assert result_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    assert parent_env_release_approved["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    assert dev_branch_push["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    for result in safe_release_results:
        assert result["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    for result in equivalent_release_results:
        result_data = parse_json_result(result)
        assert result.returncode != 0
        assert result_data["data"]["hook_result"]["event"] == "agent.release_command_rejected"
    comparison_command = "python3 -c \"print(1 >= 0); print(2 > 1)\""
    comparison = json_result(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({
            "tool_name": "exec_command",
            "command": comparison_command,
            "agent_id": "release-operator",
            "profile": "implementer",
            "path": "RELEASE.md",
            "cwd": str(ROOT),
        }),
        "--json",
        env=env,
    )
    inspection_mentions = [
        json_result(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            'rg -n "apply_patch" scripts/qwendex_cli.py',
            'grep -R "delete_file" scripts',
        )
    ]
    arbitrary_mentions = [
        run_qwendex(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            'python3 -c \'print("apply_patch")\'',
            'echo apply_patch',
        )
    ]
    real_redirect = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "echo ok > out.txt"}),
        "--json",
        env=env,
    )
    fd_redirect = run_qwendex(
        "agent",
        "hook",
        "PreToolUse",
        "--event-json",
        json.dumps({"tool_name": "exec_command", "command": "echo err 2> err.log"}),
        "--json",
        env=env,
    )
    real_write_commands = [
        run_qwendex(
            "agent",
            "hook",
            "PreToolUse",
            "--event-json",
            json.dumps({"tool_name": "exec_command", "command": command}),
            "--json",
            env=env,
        )
        for command in (
            'apply_patch',
            'printf ok | tee out.txt',
            "sed -i 's/a/b/' file.txt",
            'python3 -c \'from pathlib import Path; Path("out.txt").write_text("ok")\'',
        )
    ]
    real_redirect_data = parse_json_result(real_redirect)
    fd_redirect_data = parse_json_result(fd_redirect)
    real_write_data = [parse_json_result(result) for result in real_write_commands]
    assert comparison["data"]["hook_result"]["event"] == "agent.file_locks_acquired"
    for result in inspection_mentions:
        assert result["data"]["hook_result"] == {}
    for result in arbitrary_mentions:
        result_data = parse_json_result(result)
        assert result.returncode != 0
        assert result_data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"
    assert real_redirect.returncode != 0
    assert real_redirect_data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"
    assert fd_redirect.returncode != 0
    assert fd_redirect_data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"
    for result, data in zip(real_write_commands, real_write_data, strict=True):
        assert result.returncode != 0
        assert data["data"]["hook_result"]["event"] == "agent.write_lock_rejected"


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


def test_qwendex_read_only_non_shell_tool_gate_is_fail_closed():
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

    for event in safe_events:
        assert qwendex.pre_tool_gate({}, event, {}) == {}, event
    for event in rejected_events:
        result = qwendex.pre_tool_gate({}, event, {})
        assert result["decision"] == "block", event
        assert result["event"] == "agent.write_rejected", event

    for event in (
        {"tool_name": "mcp__filesystem__write_file", "profile": "implementer"},
        {
            "tool_name": "codex_apps.google_drive.upload_file",
            "profile": "implementer",
            "agent_id": "writer",
        },
        {"tool_name": "mcp__slack__send_message", "profile": "implementer"},
        {"tool_name": "mcp__github__createPullRequest", "profile": "implementer"},
    ):
        result = qwendex.pre_tool_gate({}, event, {})
        assert result["decision"] == "block", event
        assert result["event"] == "agent.write_lock_rejected", event


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
            {"tool_name": "exec_command", "profile": "implementer", "command": command},
            {},
        )
        assert allowed == {}, command
    for command in presumed_write_commands:
        missing_identity = qwendex.pre_tool_gate(
            {},
            {"tool_name": "exec_command", "profile": "implementer", "command": command},
            {},
        )
        missing_paths = qwendex.pre_tool_gate(
            {},
            {
                "tool_name": "exec_command",
                "profile": "implementer",
                "agent_id": "writer-shell",
                "command": command,
            },
            {},
        )
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
    assert routing["selected_model"] == "gpt-5.5"
    assert routing["selected_reasoning"] in {"high", "xhigh"}
    assert routing["reasoning_source"] == "lane_escalation"
    assert routing["local_qwen_eligible"] is False
    assert routing["token_saver_used"] is False
    assert routing["escalation_reason"]
    assert "gpt-5.5" in packet["spawn_instruction"]
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
    assert "gpt-5.5" in subagent_context
    assert "reasoning=high" in subagent_context or "reasoning=xhigh" in subagent_context

    status = json_result("manager", "status", "--json", env=env)
    assert status["data"]["active_subagents"]["count"] == 1
    assert status["data"]["deployment_contract"]["status"] == "ready"
    assert status["data"]["subagent_state"]["receipts"] == ["results/qwendex/security-review.json"]
    assert status["data"]["subagent_state"]["validation_status"]["pending"] == 1


def test_qwendex_manager_reconciles_stale_read_only_and_blocks_stale_writers(tmp_path):
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
    assert status["data"]["stale_reconciliation"]["closed_count"] == 1
    assert status["data"]["stale_reconciliation"]["closed"][0]["agent_id"] == "stale-reader"
    assert status["data"]["stale_reconciliation"]["skipped_writer_count"] == 1
    assert status["data"]["active_subagents"]["count"] == 0
    assert status["data"]["stale_writer_sessions"]["count"] == 1
    assert "stale writer lane" in " ".join(status["data"]["subagent_state"]["blockers"])

    closed = json_result("manager", "close", "--agent-id", "stale-writer", "--reason", "integrated", "--json", env=env)
    closed_session = closed["data"]["agent_session"]
    assert closed_session["status"] == "closed"
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
    assert repair["data"]["closed_count"] == 2
    assert {session["agent_id"] for session in repair["data"]["closed"]} == {"stale-reader", "empty-writer"}
    assert repair["data"]["skipped_writer_count"] == 1
    assert repair["data"]["skipped_writers"][0]["agent_id"] == "nonempty-writer"
    assert "nonempty-writer" in " ".join(repair["errors"])
    assert status.returncode == 0
    assert status_data["status"] == "warning"
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

    audit = qwendex.public_docs_audit(ROOT / "public" / "qwendex")

    assert audit["status"] == "pass"
    assert audit["missing"] == []
    assert audit["dead_links"] == []
    assert audit["secret_hits"] == []
    assert audit["naming_hits"] == []
    assert "security.md" in audit["files"]
    assert "staging-receipt.md" in audit["files"]


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


def test_qwendex_manager_enforces_subagent_limit_per_repository(tmp_path):
    state_db = tmp_path / "qwendex.sqlite"
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    env = {"QWENDEX_STATE_DB": str(state_db)}
    json_result("manager", "mode", "--set", "lite", "--json", env=env)

    for repo, prefix in ((repo_a, "a"), (repo_b, "b")):
        for index in range(2):
            json_result(
                "manager", "assign", "--agent-id", f"{prefix}-{index}", "--lane", "review",
                "--repo-root", str(repo), "--json", env=env,
            )
    overflow = run_qwendex(
        "manager", "assign", "--agent-id", "a-overflow", "--lane", "review",
        "--repo-root", str(repo_a), "--json", env=env,
    )
    overflow_data = parse_json_result(overflow)

    assert overflow.returncode != 0
    assert overflow_data["data"]["active_count"] == 2
    assert overflow_data["data"]["max_subagents"] == 2


def test_qwendex_concurrent_manager_assignments_cannot_exceed_subagent_limit(tmp_path):
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

    assert sum(result.returncode == 0 for result in results) == 1
    assert active_count == 1
    for result, payload in zip(results, payloads, strict=True):
        if result.returncode != 0:
            assert payload["status"] == "blocked"
            assert payload["data"]["active_count"] == 1
            assert payload["data"]["max_subagents"] == 1


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
    assert prompt_hook["data"]["agent_plan"]["assignments"]
    assert "Registration templates" in context


def test_qwendex_manager_prompt_without_qdex_identity_fails_before_decision_mutation(tmp_path):
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
    json_result("manager", "mode", "--set", "manager", "--json", env=env)

    blocked_result = run_qwendex(
        "agent", "hook", "UserPromptSubmit", "--event-json",
        json.dumps({"cwd": str(repo), "prompt": "Implement the change and use agents."}),
        "--json", env=env,
    )
    blocked = parse_json_result(blocked_result)
    with sqlite3.connect(state_db) as conn:
        decision_count = conn.execute("SELECT COUNT(*) FROM qwendex_manager_decisions").fetchone()[0]

    assert blocked_result.returncode != 0
    assert blocked["data"]["hook_result"]["event"] == "manager.launch_untrusted"
    assert "qdex -C" in blocked["data"]["hook_result"]["reason"]
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
    assert set(trusted["data"]) == {
        "trusted", "pid_alive", "repo_match", "decision_state", "reason",
        "recovery_command", "identity_present", "policy_match", "hook_trusted",
    }
    assert mismatch_result.returncode != 0
    assert mismatch["data"]["reason"] == "qwendex_repo_mismatch"
    assert missing_result.returncode != 0
    assert missing["data"]["reason"] == "qwendex_identity_missing"

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

    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE qwendex_manager_decisions SET launch_start_ticks = ?, policy_hash = 'forged-policy' WHERE ledger_id = ?",
            (env["QWENDEX_MANAGER_LAUNCH_START_TICKS"], preflight["data"]["ledger_id"]),
        )
    policy_result = run_qwendex(
        "manager", "launch-status", "--pid", str(pid), "--repo-root", str(repo), "--json", env=env
    )
    policy = parse_json_result(policy_result)
    assert policy_result.returncode != 0
    assert policy["data"]["reason"] == "qwendex_policy_mismatch"


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
    json_result(
        "manager", "assign", "--agent-id", "turn-one-verifier", "--lane", "verification",
        "--task-id", first_decision["agent_task_id"], "--required", "--json", env=env,
    )
    json_result(
        "agent", "hook", "SubagentStop", "--event-json",
        json.dumps({
            "agent_id": "turn-one-verifier",
            "cwd": str(ROOT),
            "last_assistant_message": "FINAL_REPORT\nstatus: completed\nValidation: pytest passed",
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
    assert second_stop_result.returncode != 0
    assert second_stop["data"]["hook_result"]["event"] in {
        "manager.verifier_required",
        "manager.validation_pending",
    }
    assert second_stop["data"].get("agent_sessions", []) == []


def test_qwendex_manager_missing_turn_id_rejects_admission_and_allows_stop(tmp_path):
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

    assert prompt_result.returncode != 0
    assert prompt["data"]["hook_result"]["reason_code"] == "turn_unattached"
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
        "manager", "preflight", "--prompt", "Use subagents to update routing and tests", "--json", env=env
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

    assert stop.returncode != 0
    assert stop_data["data"]["hook_result"]["event"] == "manager.validation_pending"
    assert stop_data["data"]["agent_sessions"] == []


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
    assert parse_json_result(legacy_stop)["data"]["hook_result"]["event"] == "agent.legacy_scope_unresolved"


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
