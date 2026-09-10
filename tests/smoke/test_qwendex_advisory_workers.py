import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_qwendex_cli import json_result, load_qwendex, run_qwendex, with_live_manager_identity


def launch(tmp_path, *, session_controls=False, capacity_env=None):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = with_live_manager_identity({
        "QWENDEX_STATE_DB": str(tmp_path / "state.sqlite"),
        "QWENDEX_RESULTS_ROOT": str(tmp_path / "results"),
        "CODEX_HOME": str(tmp_path / "codex_home"),
        "QWENDEX_MANAGER_ALLOW_UNHOOKED": "1",
        "QWENDEX_MANAGER_TARGET_REPO": str(repo),
    })
    if session_controls:
        env.update({
            "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "session-controls.json"),
            "QWENDEX_QDEX_LAUNCH_ID": "test-launch",
            "QWENDEX_QDEX_LAUNCH_MODE": "manager",
            "QWENDEX_QDEX_LAUNCH_LOCAL_ENABLED": "0",
            "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "1",
        })
    env.update(capacity_env or {})
    if env.get("QWENDEX_NATIVE_RESERVATION_MODE") == "strict":
        json_result("agent", "hook-config", "--install", "--codex-home", env["CODEX_HOME"], "--json", env=env)
    json_result("manager", "mode", "--set", "manager", "--json", env=env)
    json_result("manager", "local", "--set", "off", "--json", env=env)
    preflight = json_result("manager", "preflight", "--interactive-prompt-unknown", "--json", env=env)
    env.update(preflight["data"]["exports"])
    root = {"session_id": "root", "turn_id": "root-turn", "cwd": str(repo)}
    prompt = hook(env, "UserPromptSubmit", {
        **root, "prompt": "Implement a cross-file routing change and add regression tests",
    })
    return env, root, prompt


def hook(env, name, event):
    result = run_qwendex("agent", "hook", name, "--event-json", json.dumps(event), "--json", env=env)
    assert result.returncode in (0, 1), result.stderr
    return json.loads(result.stdout)["data"]


def child(root, name, runtime_id):
    return {
        "agent_id": runtime_id, "agent_type": "default", "task_name": name,
        "parent_session_id": root["session_id"], "session_id": runtime_id,
        "turn_id": runtime_id + "-turn", "cwd": root["cwd"],
    }


@pytest.mark.parametrize("route", ["manager_subagents", "direct_single_writer"])
def test_custom_advisory_workers_register_without_consuming_suggested_lanes(tmp_path, route):
    env, root, prompt = launch(tmp_path)
    with sqlite3.connect(env["QWENDEX_STATE_DB"]) as conn:
        conn.execute("UPDATE qwendex_manager_decisions SET selected_route = ?", (route,))
    planned = {a["agent_id"] for a in prompt["agent_plan"]["assignments"]}
    for index in range(4):
        event = child(root, f"/root/custom-review-{index}", f"worker-{index}")
        started = hook(env, "SubagentStart", event)
        session = started["agent_session"]
        assert session["status"] == "active"
        assert session["write_surface"] == "read-only"
        assert session["context_packet"]["required"] is False
        assert session["context_packet"]["planned_agent_id"] not in planned
        assert hook(env, "SubagentStart", event)["agent_session"]["agent_id"] == f"worker-{index}"
    overflow = hook(env, "SubagentStart", child(root, "/root/overflow", "overflow"))
    assert overflow["hook_result"]["reason_code"] == "native_spawn_capacity_reached"


@pytest.mark.parametrize("changed", ["parent", "repository", "launch", "policy"])
def test_custom_advisory_worker_requires_matching_launch(tmp_path, changed):
    env, root, _ = launch(tmp_path)
    event = child(root, "/root/custom-review", "worker")
    if changed == "parent":
        event["parent_session_id"] = "other-root"
    elif changed == "repository":
        event["cwd"] = str(tmp_path)
    elif changed == "launch":
        env["QWENDEX_MANAGER_LAUNCH_NONCE"] = "wrong-launch"
    else:
        env["QWENDEX_MANAGER_POLICY_HASH"] = "0" * 64
    result = hook(env, "SubagentStart", event)
    assert result.get("agent_session") is None
    assert result["hook_result"]["event"] == "manager.native_worker_bookkeeping_unavailable"


def test_concurrent_custom_workers_share_capacity_without_identity_race(tmp_path, monkeypatch):
    env, root, prompt = launch(tmp_path)
    qwendex = load_qwendex()
    for key in tuple(os.environ):
        if key.startswith("QWENDEX_") or key == "CODEX_AGENT_USE":
            monkeypatch.delenv(key)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    config = qwendex.deep_merge(qwendex.DEFAULT_CONFIG, {"state": {"db": env["QWENDEX_STATE_DB"]}})
    health = qwendex.manager_launch_health
    barrier = threading.Barrier(2)

    def synchronized_health(*args, **kwargs):
        result = health(*args, **kwargs)
        barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(qwendex, "manager_launch_health", synchronized_health)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(
            qwendex.activate_manager_native_worker, config,
            child(root, f"/root/concurrent-{index}", f"concurrent-{index}"), prompt["agent_policy"],
        ) for index in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert [error for _, error in results] == ["", ""]
    assert all(session["status"] == "active" for session, _ in results)


def test_default_native_verifier_can_validate_but_cannot_write_or_spawn(tmp_path):
    env, root, prompt = launch(tmp_path)
    assignment = next(a for a in prompt["agent_plan"]["assignments"] if a["profile"] == "verifier")
    event = child(root, assignment["agent_id"], "verifier-runtime")
    started = hook(env, "SubagentStart", event)
    assert started["agent_session"]["status"] == "active"
    for command, blocked in [("python3 -B -m pytest -q tests/smoke", False), ("touch changed.txt", True)]:
        result = hook(env, "PreToolUse", {**event, "tool_name": "exec_command", "tool_input": {"cmd": command}})
        assert (result["hook_result"].get("decision") == "block") is blocked
    spawned = hook(env, "PreToolUse", {**event, "depth": 1, "tool_name": "spawn_agent", "tool_input": {"task_name": "leaf"}})
    assert spawned["hook_result"]["decision"] == "block"
    wrong_session = hook(env, "PreToolUse", {
        **event, "session_id": "other-child", "tool_name": "exec_command",
        "tool_input": {"cmd": "python3 -B -m pytest -q tests/smoke"},
    })
    assert wrong_session["hook_result"]["decision"] == "block"


@pytest.mark.parametrize("capacity_env", [{}, {
    "QWENDEX_NATIVE_RESERVATION_MODE": "strict", "QWENDEX_GLOBAL_WORKER_CAP": "2",
}])
def test_attached_manager_plans_local_from_each_accepted_turn(tmp_path, capacity_env):
    env, root, _ = launch(tmp_path, session_controls=True, capacity_env=capacity_env)
    initial = json_result(
        "manager", "launch-status", "--pid", env["QWENDEX_MANAGER_LAUNCH_PID"],
        "--repo-root", root["cwd"], "--json", env=env,
    )["data"]
    assert initial["trusted"] is True
    assert initial["restart_required"] is False
    hook(env, "Stop", {**root, "last_assistant_message": "Inspection complete. No files changed."})
    launch_hash = env["QWENDEX_MANAGER_POLICY_HASH"]
    for index, enabled in enumerate((True, False, True)):
        json_result("manager", "local", "--set", "on" if enabled else "off", "--json", env=env)
        event = {**root, "turn_id": f"local-turn-{index}", "prompt": (
            "Investigate and summarize the existing receipt artifacts and report their "
            "current validation state without editing files."
        )}
        data = hook(env, "UserPromptSubmit", event)
        decision = data["manager_decision"]
        plan = decision["agent_plan"]
        assert decision["policy_hash"] == launch_hash
        assert plan["local_routing_snapshot"]["enabled"] is enabled
        assert plan["local_subagents"]["usable"] is enabled
        assert plan["assignments"]
        assert plan["assignments"][0]["routing"]["token_saver_used"] is enabled
        health = json_result(
            "manager", "launch-status", "--pid", env["QWENDEX_MANAGER_LAUNCH_PID"],
            "--repo-root", root["cwd"], "--json", env=env,
        )["data"]
        assert health["trusted"] is True
        assert health["restart_required"] is False
        status = json_result("manager", "status", "--json", env=env)["data"]
        assert status["session_status"]["restart_required"] is False
        hook(env, "Stop", {**event, "last_assistant_message": "Inspection complete. No files changed."})
