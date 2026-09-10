from test_qwendex_cli import json_result


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
