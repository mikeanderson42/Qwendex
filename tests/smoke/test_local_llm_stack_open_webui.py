import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PS_CHECK = ["bash", "-lc", "command -v powershell.exe " + chr(62) + "/dev/null 2" + chr(62) + "&1"]
XDG_CHECK = ["bash", "-lc", "command -v xdg-open " + chr(62) + "/dev/null 2" + chr(62) + "&1"]
SYSTEMD_START = ["systemctl", "--user", "start", "open-webui-local.service"]


def load_stack_module():
    module_path = ROOT / "scripts" / "local_llm_stack.py"
    spec = importlib.util.spec_from_file_location("local_llm_stack_open_webui_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_cfg(stack, tmp_path):
    return stack.StackConfig(
        name="test stack",
        tmux_session="qwendex",
        repo_root=tmp_path,
        safe_runtime_dir=tmp_path / "runtime",
        transcript_dir=tmp_path / "transcripts",
        prompt_template_roots=[],
        task_prompt_policy={},
        user_state_file=tmp_path / "state.json",
        default_project=tmp_path,
        services=[],
        provider_profiles=[],
        model_profiles=[],
        backend_profiles=[],
        active_backend_profile="textgen",
        default_backend_profile="textgen",
        context_presets=[],
        chat_interfaces={
            "open-webui": {
                "url": "http://127.0.0.1:7070",
                "backend_url": "http://127.0.0.1:4000/v1",
            }
        },
        raw_path=tmp_path / "stack.json",
    )


def completed(args, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def install_service_file(tmp_path):
    service_path = tmp_path / ".config" / "systemd" / "user" / "open-webui-local.service"
    service_path.parent.mkdir(parents=True)
    with service_path.open("w", encoding="utf-8") as handle:
        handle.write("[Service]\nExecStart=true\n")


def test_optional_personal_chat_open_webui_uses_native_service_when_powershell_unavailable(monkeypatch, tmp_path):
    stack = load_stack_module()
    install_service_file(tmp_path)
    calls = []
    opened = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args == PS_CHECK:
            return completed(args, returncode=1)
        if args == SYSTEMD_START:
            return completed(args)
        if args == XDG_CHECK:
            return completed(args)
        raise AssertionError(args)

    def fake_popen(args, **kwargs):
        opened.append((args, kwargs))
        return object()

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(stack, "run", fake_run)
    monkeypatch.setattr(stack, "wait_for_http_ok", lambda url, timeout_seconds: (True, "HTTP 200"))
    monkeypatch.setattr(stack.subprocess, "Popen", fake_popen)

    result = stack.launch_open_webui(make_cfg(stack, tmp_path))

    assert result.ok is True
    assert result.message == (
        "opened open-webui at http://127.0.0.1:7070; backend target is "
        "http://127.0.0.1:4000/v1 using current profile textgen"
    )
    assert "started native open-webui-local.service" in result.details
    assert "http://127.0.0.1:7070/health: HTTP 200" in result.details
    assert "opened browser at http://127.0.0.1:7070" in result.details
    assert any(call[0] == SYSTEMD_START for call in calls)
    assert opened[0][0] == ["xdg-open", "http://127.0.0.1:7070"]


def test_optional_personal_chat_open_webui_reports_missing_native_service_when_powershell_unavailable(monkeypatch, tmp_path):
    stack = load_stack_module()

    def fake_run(args, **kwargs):
        if args == PS_CHECK:
            return completed(args, returncode=1)
        raise AssertionError(args)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(stack, "run", fake_run)

    result = stack.launch_open_webui(make_cfg(stack, tmp_path))

    assert result.ok is False
    assert result.message == (
        "powershell.exe is unavailable and native open-webui-local.service is not installed"
    )
    assert result.details == []


def test_optional_personal_chat_open_webui_reports_unhealthy_native_service(monkeypatch, tmp_path):
    stack = load_stack_module()
    install_service_file(tmp_path)

    def fake_run(args, **kwargs):
        if args == PS_CHECK:
            return completed(args, returncode=1)
        if args == SYSTEMD_START:
            return completed(args)
        raise AssertionError(args)

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(stack, "run", fake_run)
    monkeypatch.setattr(stack, "wait_for_http_ok", lambda url, timeout_seconds: (False, "refused"))

    result = stack.launch_open_webui(make_cfg(stack, tmp_path))

    assert result.ok is False
    assert result.message == "native Open WebUI service did not become healthy"
    assert result.details == [
        "started native open-webui-local.service",
        "http://127.0.0.1:7070/health: refused",
    ]
