from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load_validator():
    path = ROOT / "scripts/validate_local_qwen_codex_acceptance.py"
    spec = importlib.util.spec_from_file_location("qwendex_live_acceptance_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fake_launcher(
    path: Path,
    *,
    normal_home: Path,
    mutate_normal: bool = False,
    final: str = "TOOL_OK",
    command: str = "printf TOOL_OK",
    stdout_noise: bool = False,
) -> None:
    agent_event = json.dumps(
        {
            "type": "item.completed",
            "item": {"type": "agent_message", "text": final},
        }
    )
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "fresh = pathlib.Path(args[args.index('--fresh-home') + 1])\n"
        "output = pathlib.Path(args[args.index('--output-last-message') + 1])\n"
        "fresh.mkdir(parents=True, exist_ok=True)\n"
        f"output.write_text({final!r} + '\\n', encoding='utf-8')\n"
        + (
            f"pathlib.Path({str(normal_home / 'mutated')!r}).write_text('changed', encoding='utf-8')\n"
            if mutate_normal
            else ""
        )
        + ("print('not-json')\n" if stdout_noise else "")
        + "print(json.dumps({'type':'thread.started','thread_id':'thread-test'}))\n"
        f"print(json.dumps({{'type':'item.completed','item':{{'type':'command_execution','command':{command!r},'status':'completed','exit_code':0,'aggregated_output':'TOOL_OK'}}}}))\n"
        f"print({agent_event!r})\n"
        "print(json.dumps({'type':'turn.completed'}))\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def invoke(
    tmp_path: Path,
    *,
    mutate_normal: bool = False,
    final: str = "TOOL_OK",
    command: str = "printf TOOL_OK",
    stdout_noise: bool = False,
):
    validator = load_validator()
    workdir = tmp_path / "repo"
    normal_home = tmp_path / "normal-home"
    workdir.mkdir()
    normal_home.mkdir()
    (normal_home / "sentinel").write_text("stable", encoding="utf-8")
    launcher = tmp_path / "launcher"
    codex = tmp_path / "codex"
    codex.write_text("fixture", encoding="utf-8")
    codex.chmod(0o755)
    fake_launcher(
        launcher,
        normal_home=normal_home,
        mutate_normal=mutate_normal,
        final=final,
        command=command,
        stdout_noise=stdout_noise,
    )
    return validator.run_acceptance(
        launcher=launcher,
        codex_bin=codex,
        workdir=workdir,
        fresh_home=tmp_path / "fresh-home",
        normal_home=normal_home,
        final_output=tmp_path / "final.txt",
        timeout=30,
    )


def test_fresh_home_tool_acceptance_passes_with_exact_evidence(tmp_path: Path) -> None:
    payload = invoke(tmp_path)

    assert payload["status"] == "pass"
    assert payload["normal_home_unchanged"] is True
    assert payload["command_execution_count"] == 1
    assert payload["successful_tool_result_count"] == 1
    assert payload["matching_command_count"] == 1
    assert payload["tool_round_trip_proven"] is True
    assert payload["final_text_exact"] is True
    assert payload["event_final_text_exact"] is True
    assert payload["blockers"] == []


def test_fresh_home_acceptance_blocks_normal_home_mutation(tmp_path: Path) -> None:
    payload = invoke(tmp_path, mutate_normal=True)

    assert payload["status"] == "fail"
    assert payload["normal_home_unchanged"] is False
    assert any("normal Codex home changed" in item for item in payload["blockers"])


def test_fresh_home_acceptance_blocks_nonexact_final_text(tmp_path: Path) -> None:
    payload = invoke(tmp_path, final="TOOL_OK extra")

    assert payload["status"] == "fail"
    assert payload["final_text_exact"] is False
    assert payload["event_final_text_exact"] is False


def test_fresh_home_acceptance_blocks_wrong_command_despite_matching_output(tmp_path: Path) -> None:
    payload = invoke(tmp_path, command="echo TOOL_OK")

    assert payload["status"] == "fail"
    assert payload["matching_command_count"] == 0
    assert payload["tool_round_trip_proven"] is False


def test_fresh_home_acceptance_blocks_non_json_stdout(tmp_path: Path) -> None:
    payload = invoke(tmp_path, stdout_noise=True)

    assert payload["status"] == "fail"
    assert payload["malformed_event_count"] == 1
