import json

from test_qwendex_advisory_workers import hook, launch
from test_qwendex_cli import json_result


def exec_dry(env, prompt="Inspect the fixture and explain the result in detail.", seat="auto"):
    return json_result(
        "exec", "--seat", seat, "--prefer-local", "--dry-run", "--json", "--", prompt,
        env={**env, "QWENDEX_QDEX_PERMISSION_MODE": "read-only"},
    )["data"]


def output_directive(result):
    if "--developer-instructions" in result["command"]:
        return result["command"][result["command"].index("--developer-instructions") + 1]
    override = next(arg for arg in result["command"] if arg.startswith("developer_instructions="))
    return json.loads(override.split("=", 1)[1])


def test_exec_uses_accepted_output_policy_and_local_off_veto(tmp_path):
    env, root, _ = launch(tmp_path, session_controls=True)
    json_result("manager", "local", "--set", "on", "--json", env=env)
    json_result("manager", "kaveman", "--set", "on", "--json", env=env)
    # A toggle cannot enable Local midway through an already accepted Off turn.
    assert exec_dry(env)["seat"] == "primary"
    hook(env, "UserPromptSubmit", {**root, "turn_id": "on-turn", "prompt": "Inspect the fixture."})
    local = exec_dry(env)
    assert local["seat"] == "qwen"
    assert local["execution_policy"]["sandbox_mode"] == "read-only"
    assert local["execution_policy"]["output_policy"]["kaveman_enabled"] is True
    assert "Kaveman enabled" in output_directive(local)
    assert "Include detail requested by the user" in output_directive(local)
    assert local["command"][-1].endswith("explain the result in detail.")

    # Minimal Local exec and the fallback receive the same explicit directive.
    unavailable = exec_dry({**env, "QWENDEX_FORCE_LOCAL_QWEN_AVAILABLE": "0"})
    assert unavailable["seat"] == "primary"
    assert unavailable["execution_policy"]["sandbox_mode"] == "read-only"
    assert output_directive(unavailable) == output_directive(local)
    authority = exec_dry(env, "Perform a security review of the authentication design.")
    assert authority["seat"] == "primary"

    json_result("manager", "local", "--set", "off", "--json", env=env)
    json_result("manager", "kaveman", "--set", "off", "--json", env=env)
    vetoed = exec_dry(env, seat="qwen")
    assert vetoed["seat"] == "primary"
    assert "Kaveman enabled" in output_directive(vetoed)
    hook(env, "UserPromptSubmit", {**root, "turn_id": "off-turn", "prompt": "Inspect the fixture."})
    next_turn = exec_dry(env)
    assert next_turn["seat"] == "primary"
    assert "Kaveman disabled" in output_directive(next_turn)

    writable = json_result(
        "exec", "--seat", "primary", "--dry-run", "--json", "--", "Inspect the fixture.", env=env,
    )["data"]
    assert not any(arg.startswith("developer_instructions=") for arg in writable["command"])


def test_local_guidance_keeps_read_only_children_within_their_permissions(tmp_path):
    env, root, _ = launch(tmp_path, session_controls=True)
    json_result("manager", "local", "--set", "on", "--json", env=env)
    prompt = hook(env, "UserPromptSubmit", {**root, "turn_id": "local-turn", "prompt": "Inspect the fixture."})
    context = prompt["hook_result"]["hookSpecificOutput"]["additionalContext"]
    assert "the root may use QWENDEX_QDEX_PERMISSION_MODE=read-only" in context
    assert "qwendex exec --seat auto --prefer-local" in context
    assert "Read-only workers should return local task suggestions to the root" in context


def test_launch_mode_override_initializes_controls_and_allows_later_mode_selection(tmp_path):
    env = {
        "QWENDEX_STATE_DB": str(tmp_path / "state.sqlite"),
        "QWENDEX_MANAGER_SESSION_STATE_FILE": str(tmp_path / "controls.json"),
        "QWENDEX_QDEX_LAUNCH_ID": "launch",
        "QWENDEX_AGENT_USE": "Manager",
    }
    # The repository default is Auto; a fresh explicit Manager launch starts
    # with Manager selected and no false capacity restart after toggling.
    first = json_result("codex-status", "--json", env=env)["data"]
    assert first["policy_transition"]["requested_mode"] == "manager"
    active = {**env, "QWENDEX_QDEX_LAUNCH_MODE": "manager"}
    json_result("manager", "kaveman", "--set", "on", "--json", env=active)
    toggled = json_result("codex-status", "--json", env=active)["data"]
    assert toggled["policy_transition"]["restart_required"] is False
    # Alt+M's requested state supersedes the launch selector; native capacity
    # stays pinned until restart, so report both states truthfully.
    json_result("manager", "mode", "--set", "off", "--json", env=active)
    requested = json_result("codex-status", "--json", env=active)["data"]
    assert requested["policy_transition"]["requested_mode"] == "off"
    assert requested["policy_transition"]["effective_turn_mode"] == "manager"
    assert requested["policy_transition"]["restart_required"] is True
    assert requested["requested_agent_policy"]["mode"] == "off"
    assert "Requested Off → active Manager Mode (restart)" in requested["text"]
    surface = json_result("manager", "status", "--json", env=active)["data"]
    assert surface["requested_agent_policy"]["mode"] == "off"
