#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_VLLM_BIN="$HOME/.local/share/qwendex/vllm-venv/bin/vllm"

windows_to_wsl_path() {
  local raw="$1"
  case "$raw" in
    C:\\*|c:\\*)
      raw="${raw//\\//}"
      printf '/mnt/c/%s\n' "${raw:3}"
      ;;
    *)
      printf '%s\n' "$raw"
      ;;
  esac
}

HOST="${VLLM_HOST:-127.0.0.1}"
PORT="${VLLM_PORT:-5000}"
MODEL_ALIAS="${VLLM_MODEL_ALIAS:-Qwopucode-full-v15-27B-FP8-Block-i1-Q4_K_M}"
MODEL_PATH="$(windows_to_wsl_path "${VLLM_MODEL_PATH:-/mnt/c/Users/Tweak/.lmstudio/models/Qwen36/mradermacherQwopucode-full-v15-27B/Qwopucode-full-v15-27B-FP8-Block.i1-Q4_K_M.gguf}")"
TOKENIZER="$(windows_to_wsl_path "${VLLM_TOKENIZER:-$HOME/Text-Generation-WebUI/user_data/models/Jackrong-Qwopus3.6-27B-Coder-EXL3-4.5bpw-h6-textonly-nonmtp}")"
MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.90}"
DTYPE="${VLLM_DTYPE:-auto}"
CHAT_TEMPLATE="${VLLM_CHAT_TEMPLATE:-$ROOT/config/local_llm_stack/qwen3_codex_tool_plain.jinja}"
EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Missing vLLM GGUF model: $MODEL_PATH" >&2
  exit 1
fi

if [[ ! -e "$TOKENIZER" ]]; then
  echo "Missing vLLM tokenizer path: $TOKENIZER" >&2
  echo "GGUF serving in vLLM needs an explicit tokenizer; set VLLM_TOKENIZER to a local tokenizer dir or HF repo." >&2
  exit 1
fi

if [[ -n "$CHAT_TEMPLATE" && "$CHAT_TEMPLATE" != "auto" && "$CHAT_TEMPLATE" != "none" && ! -f "$CHAT_TEMPLATE" ]]; then
  echo "Missing vLLM chat template: $CHAT_TEMPLATE" >&2
  exit 1
fi

if [[ -n "${VLLM_BIN:-}" ]]; then
  VLLM_CMD=("$VLLM_BIN")
elif [[ -x "$DEFAULT_VLLM_BIN" ]]; then
  VLLM_CMD=("$DEFAULT_VLLM_BIN")
elif command -v vllm >/dev/null 2>&1; then
  VLLM_CMD=("$(command -v vllm)")
elif [[ "${VLLM_DRY_RUN:-0}" == "1" ]]; then
  VLLM_CMD=("vllm")
else
  echo "Missing vLLM CLI on PATH." >&2
  echo "Install vLLM in the active environment or set VLLM_BIN to the vllm executable." >&2
  exit 1
fi

cmd=(
  "${VLLM_CMD[@]}" serve "$MODEL_PATH"
  --host "$HOST"
  --port "$PORT"
  --served-model-name "$MODEL_ALIAS"
  --tokenizer "$TOKENIZER"
  --max-model-len "$MAX_MODEL_LEN"
  --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
  --dtype "$DTYPE"
  --generation-config vllm
  --trust-remote-code
)

if [[ -n "$CHAT_TEMPLATE" && "$CHAT_TEMPLATE" != "auto" && "$CHAT_TEMPLATE" != "none" ]]; then
  cmd+=(--chat-template "$CHAT_TEMPLATE")
fi

if [[ -n "$EXTRA_ARGS" ]]; then
  # shellcheck disable=SC2206
  extra=($EXTRA_ARGS)
  cmd+=("${extra[@]}")
fi

echo "Starting vLLM GGUF OpenAI API."
echo "Model: $MODEL_PATH"
echo "Alias: $MODEL_ALIAS"
echo "Tokenizer: $TOKENIZER"
echo "Context: $MAX_MODEL_LEN"
echo "API: http://$HOST:$PORT/v1"

if [[ "${VLLM_DRY_RUN:-0}" == "1" ]]; then
  printf 'DRY_RUN '
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

cd "$ROOT"
exec "${cmd[@]}"
