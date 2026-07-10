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
- llama.cpp GGUF backend: `scripts/run_llamacpp_qwen_gguf.sh`
- vLLM GGUF backend: `scripts/run_vllm_qwen_gguf.sh`
- KoboldCPP GGUF backend: `scripts/run_koboldcpp_gguf.sh`
- LiteLLM proxy: `scripts/run_litellm_local_proxy.sh`
- Codex bridge: `scripts/run_codex_textgen_bridge.sh`

Each backend launcher is bring-your-own-runtime. Set executable and model path
environment variables in `stack_manager.local.json` or
`config/local_llm_stack/local_harness.env`.
They are example integrations rather than release-tested backend packages;
validate the selected runtime's supported flags before use. The vLLM launcher
keeps remote model code disabled unless `VLLM_TRUST_REMOTE_CODE=1` is set
explicitly.

The published 32k backend profile, local Qwendex seats, launcher fallback, and
sample environment all use a 32768-token context with auto-compaction at
28672. A local backend override may lower both values, but its compact limit
must remain below the actual served context window.

## Windows Launcher

`scripts/windows/open.ps1` is a best-effort bring-your-own-WSL PowerShell
launcher. It is not covered by the Linux release CI. Run it from a reviewed
user-owned location rather than copying it into a system directory.

```powershell
.\scripts\windows\open.ps1 status
.\scripts\windows\open.ps1 doctor
.\scripts\windows\open.ps1 model-list
```

By default it opens the WSL checkout at `$HOME/qwendex-dev` in the `ubuntu`
distro. Override `-Distro` and `-RepoDir`, or set `QWENDEX_WSL_REPO` inside
WSL, when that is not the local layout.

Open WebUI is an optional personal/external chat interface for inference,
not a dependency of Qwendex core routing, manager mode, or Codex bridge
operation. When `powershell.exe` is unavailable,
`scripts/local_llm_stack.py open-webui` can fall back to the native user service
`~/.config/systemd/user/open-webui-local.service` if that service file exists.
The fallback starts it with `systemctl --user`, waits for `/health`, then opens
Open WebUI with `xdg-open`.

## Validation

```bash
scripts/qwendex llmstack check --json
scripts/qwendex llmstack doctor --json
scripts/qwendex check --json
scripts/qwendex eval --json
```

When the live stack is intentionally running:

```bash
qwendex-dev verify --tier live
```

This produces three distinct proofs: `live_launcher` checks the launcher and
canonical bridge `/status`; `live_reliability` parses an exact assistant
`QWENDEX_OK`; and `live_codex_acceptance` uses a fresh isolated Codex home for
one to three bounded successful `TOOL_OK` shell commands while proving a decoy
normal home was unchanged.

The canonical status response is
`qwendex.responses_bridge.status.v1` with `status: ok`. Launcher and stack
readiness reject a merely responsive endpoint that does not satisfy that
contract. `/__tabby_proxy_status` remains a legacy compatibility alias only.

Visible tool markup, loop markers, timeout markers, or malformed receipt digests
block release acceptance until repaired and rerun.
