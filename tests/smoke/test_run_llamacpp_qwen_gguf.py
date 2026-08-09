from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "run_llamacpp_qwen_gguf.sh"


def launcher_env(tmp_path: Path) -> dict[str, str]:
    model = tmp_path / "model.gguf"
    template = tmp_path / "chat.jinja"
    model.touch()
    template.write_text("{{ messages }}", encoding="utf-8")
    return {
        **os.environ,
        "LLAMACPP_SERVER": "/bin/true",
        "LLAMACPP_MODEL_PATH": str(model),
        "LLAMACPP_CHAT_TEMPLATE": str(template),
        "LLAMACPP_DRY_RUN": "1",
        "LLAMACPP_SPEC_TYPE": "draft-mtp",
    }


def test_launcher_adds_validated_speculative_draft_token_cap(tmp_path: Path) -> None:
    env = launcher_env(tmp_path)
    env["LLAMACPP_SPEC_DRAFT_N_MAX"] = "2"

    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Speculative draft token cap: 2" in result.stdout
    assert "--spec-draft-n-max 2" in result.stdout


def test_launcher_rejects_invalid_speculative_draft_token_cap(tmp_path: Path) -> None:
    env = launcher_env(tmp_path)
    env["LLAMACPP_SPEC_DRAFT_N_MAX"] = "two"

    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "must be an integer from 1 through 128" in result.stderr


def test_launcher_omits_speculative_draft_token_cap_when_unset(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=launcher_env(tmp_path),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--spec-draft-n-max" not in result.stdout


def test_launcher_can_use_the_gguf_embedded_template_with_explicit_kwargs(tmp_path: Path) -> None:
    env = launcher_env(tmp_path)
    env["LLAMACPP_CHAT_TEMPLATE"] = "embedded"
    env["LLAMACPP_CHAT_TEMPLATE_KWARGS"] = '{"enable_thinking":false,"preserve_thinking":false}'

    result = subprocess.run(
        [str(LAUNCHER)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Chat template: embedded in GGUF" in result.stdout
    assert "--chat-template-file" not in result.stdout
    assert "enable_thinking" in result.stdout
    assert "preserve_thinking" in result.stdout
