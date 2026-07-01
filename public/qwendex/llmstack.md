# LLMStack

LLMStack is Qwendex's optional local-model runtime module. It ships the control
plane: launch wrappers, sample config, LiteLLM routing, the Codex-facing bridge,
guard markers, receipts, and validation commands.

It does not ship model weights, backend binaries, backend Python environments,
Open WebUI, credentials, logs, transcripts, or machine-local profile catalogs.

## Open The Console

From the repository root:

```bash
./llmstack
```

Use JSON commands for automation:

```bash
./llmstack status --json
scripts/qwendex llmstack check --json
scripts/qwendex llmstack restart bridge --dry-run --json
```

The tracked public config is `config/local_llm_stack/stack_manager.json`, which
matches `stack_manager.sample.json`. For a real machine, copy it to the ignored
`config/local_llm_stack/stack_manager.local.json` and set local model paths,
backend paths, and profile names there. The loader automatically prefers the
local file when it exists. `QWENDEX_LLMSTACK_CONFIG` can point to another file.

## Wiring Points

The standard chain is:

```text
model backend :5000 -> LiteLLM :4000 -> Qwendex bridge :1234 -> Codex local seat
```

Backend launcher examples:

- TextGen/OpenAI-compatible backend: `scripts/run_textgen_qwen_exl3.sh`
- llama.cpp GGUF backend: `scripts/run_llamacpp_qwopucode_gguf.sh`
- vLLM GGUF backend: `scripts/run_vllm_qwopucode_gguf.sh`
- KoboldCPP GGUF backend: `scripts/run_koboldcpp_gguf.sh`
- LiteLLM proxy: `scripts/run_litellm_local_proxy.sh`
- Codex bridge: `scripts/run_codex_textgen_bridge.sh`

Each backend launcher is bring-your-own-runtime. Set executable and model path
environment variables in `stack_manager.local.json` or
`config/local_llm_stack/local_harness.env`.

## Windows Launcher

`scripts/windows/open.ps1` is the portable PowerShell launcher. You can run it
from Windows PowerShell or copy it to a convenient local path such as
`C:\Windows\System32\open.ps1`.

```powershell
.\scripts\windows\open.ps1 status
.\scripts\windows\open.ps1 doctor
.\scripts\windows\open.ps1 model-list
```

By default it opens the WSL checkout at `$HOME/Qwendex`. Override with
`-RepoDir` or set `QWENDEX_WSL_REPO` inside WSL.

## Validation

```bash
scripts/qwendex llmstack check --json
scripts/qwendex llmstack doctor --json
scripts/qwendex check --json
scripts/qwendex eval --json
```

When the live stack is intentionally running:

```bash
scripts/run_local_qwen_codex.sh --check
python3 scripts/validate_local_qwen_reliability.py --require-live-bridge --json
```

Visible tool markup, loop markers, timeout markers, or malformed receipt digests
block release acceptance until repaired and rerun.
