#!/usr/bin/env bash
set -euo pipefail

QWENDEX_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LLAMACPP_ROOT="${LLAMACPP_ROOT:-$HOME/llama.cpp-codex}"
DEFAULT_SERVER="$LLAMACPP_ROOT/src/build/bin/llama-server"

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

SERVER="${LLAMACPP_SERVER:-$DEFAULT_SERVER}"
HOST="${LLAMACPP_HOST:-127.0.0.1}"
PORT="${LLAMACPP_PORT:-5000}"
MODEL_ALIAS="${LLAMACPP_MODEL_ALIAS:-qwen-local}"
MODEL_PATH="$(windows_to_wsl_path "${LLAMACPP_MODEL_PATH:-$HOME/models/qwen-coder/example-model.gguf}")"
CHAT_TEMPLATE="$(windows_to_wsl_path "${LLAMACPP_CHAT_TEMPLATE:-$QWENDEX_ROOT/config/local_llm_stack/qwen3_codex_tool_plain.jinja}")"
DEFAULT_CHAT_TEMPLATE_KWARGS='{"preserve_thinking":true}'
CHAT_TEMPLATE_KWARGS="${LLAMACPP_CHAT_TEMPLATE_KWARGS:-$DEFAULT_CHAT_TEMPLATE_KWARGS}"
CTX_SIZE="${LLAMACPP_CTX_SIZE:-32768}"
BATCH_SIZE="${LLAMACPP_BATCH_SIZE:-4096}"
UBATCH_SIZE="${LLAMACPP_UBATCH_SIZE:-2048}"
THREADS="${LLAMACPP_THREADS:-8}"
THREADS_BATCH="${LLAMACPP_THREADS_BATCH:-8}"
GPU_LAYERS="${LLAMACPP_GPU_LAYERS:-all}"
CACHE_TYPE_K="${LLAMACPP_CACHE_TYPE_K:-q8_0}"
CACHE_TYPE_V="${LLAMACPP_CACHE_TYPE_V:-q8_0}"
CACHE_RAM="${LLAMACPP_CACHE_RAM:-4096}"
CACHE_PROMPT="${LLAMACPP_CACHE_PROMPT:-1}"
PARALLEL="${LLAMACPP_PARALLEL:-1}"
TEMP="${LLAMACPP_TEMPERATURE:-0.55}"
TOP_K="${LLAMACPP_TOP_K:-20}"
TOP_P="${LLAMACPP_TOP_P:-0.95}"
MIN_P="${LLAMACPP_MIN_P:-0.05}"
REASONING="${LLAMACPP_REASONING:-off}"
REASONING_FORMAT="${LLAMACPP_REASONING_FORMAT:-deepseek}"
REASONING_BUDGET="${LLAMACPP_REASONING_BUDGET:-}"
SPEC_TYPE="${LLAMACPP_SPEC_TYPE:-none}"
SPEC_DRAFT_N_MAX="${LLAMACPP_SPEC_DRAFT_N_MAX:-}"
DEVICE="${LLAMACPP_DEVICE:-}"
MAIN_GPU="${LLAMACPP_MAIN_GPU:-}"
EXTRA_ARGS="${LLAMACPP_EXTRA_ARGS:-}"

if [[ ! -x "$SERVER" ]]; then
  echo "Missing llama-server: $SERVER" >&2
  exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Missing llama.cpp GGUF model: $MODEL_PATH" >&2
  exit 1
fi

if [[ "$CHAT_TEMPLATE" != "embedded" && ! -f "$CHAT_TEMPLATE" ]]; then
  echo "Missing llama.cpp chat template: $CHAT_TEMPLATE" >&2
  exit 1
fi

IFS=',' read -r -a spec_types <<< "$SPEC_TYPE"
for spec_type in "${spec_types[@]}"; do
  case "$spec_type" in
    none|draft-simple|draft-eagle3|draft-mtp|draft-dflash|ngram-simple|ngram-map-k|ngram-map-k4v|ngram-mod|ngram-cache) ;;
    *)
      echo "Unsupported LLAMACPP_SPEC_TYPE=$SPEC_TYPE" >&2
      exit 1
      ;;
  esac
done

if [[ -n "$SPEC_DRAFT_N_MAX" ]]; then
  if [[ ! "$SPEC_DRAFT_N_MAX" =~ ^[1-9][0-9]*$ ]] || (( 10#$SPEC_DRAFT_N_MAX > 128 )); then
    echo "LLAMACPP_SPEC_DRAFT_N_MAX must be an integer from 1 through 128, got: $SPEC_DRAFT_N_MAX" >&2
    exit 1
  fi
fi

if [[ -n "$MAIN_GPU" && ! "$MAIN_GPU" =~ ^[0-9]+$ ]]; then
  echo "LLAMACPP_MAIN_GPU must be a numeric device index, got: $MAIN_GPU" >&2
  exit 1
fi

cmd=(
  "$SERVER"
  --host "$HOST"
  --port "$PORT"
  --model "$MODEL_PATH"
  --alias "$MODEL_ALIAS"
  --ctx-size "$CTX_SIZE"
  -b "$BATCH_SIZE"
  -ub "$UBATCH_SIZE"
  -t "$THREADS"
  -tb "$THREADS_BATCH"
  --gpu-layers "$GPU_LAYERS"
  --flash-attn on
  --cache-type-k "$CACHE_TYPE_K"
  --cache-type-v "$CACHE_TYPE_V"
  --cache-ram "$CACHE_RAM"
  -np "$PARALLEL"
  --temp "$TEMP"
  --top-k "$TOP_K"
  --top-p "$TOP_P"
  --min-p "$MIN_P"
  --reasoning "$REASONING"
  --reasoning-format "$REASONING_FORMAT"
  --jinja
  --spec-type "$SPEC_TYPE"
  -fit off
)

if [[ "$CHAT_TEMPLATE" != "embedded" ]]; then
  cmd+=(--chat-template-file "$CHAT_TEMPLATE")
fi
cmd+=(--chat-template-kwargs "$CHAT_TEMPLATE_KWARGS")

if [[ -n "$DEVICE" ]]; then
  cmd+=(--device "$DEVICE")
fi

if [[ -n "$MAIN_GPU" ]]; then
  cmd+=(--main-gpu "$MAIN_GPU")
fi

if [[ -n "$SPEC_DRAFT_N_MAX" ]]; then
  cmd+=(--spec-draft-n-max "$SPEC_DRAFT_N_MAX")
fi

case "${CACHE_PROMPT,,}" in
  1|true|yes|on) cmd+=(--cache-prompt) ;;
  0|false|no|off) ;;
  *) echo "Invalid LLAMACPP_CACHE_PROMPT=$CACHE_PROMPT" >&2; exit 1 ;;
esac

if [[ -n "$REASONING_BUDGET" ]]; then
  cmd+=(--reasoning-budget "$REASONING_BUDGET")
fi

if [[ -n "$EXTRA_ARGS" ]]; then
  # shellcheck disable=SC2206
  extra=($EXTRA_ARGS)
  cmd+=("${extra[@]}")
fi

echo "Starting llama.cpp GGUF OpenAI API."
echo "Server: $SERVER"
echo "Model: $MODEL_PATH"
echo "Alias: $MODEL_ALIAS"
echo "Context: $CTX_SIZE"
echo "KV cache: $CACHE_TYPE_K / $CACHE_TYPE_V"
if [[ "$CHAT_TEMPLATE" == "embedded" ]]; then
  echo "Chat template: embedded in GGUF"
else
  echo "Chat template: $CHAT_TEMPLATE"
fi
echo "Prompt cache RAM: ${CACHE_RAM} MiB"
echo "Speculative decoding: $SPEC_TYPE"
if [[ -n "$SPEC_DRAFT_N_MAX" ]]; then
  echo "Speculative draft token cap: $SPEC_DRAFT_N_MAX"
fi
if [[ -n "$DEVICE" ]]; then
  echo "GPU device: $DEVICE (main GPU: ${MAIN_GPU:-default})"
fi
echo "API: http://$HOST:$PORT/v1"

if [[ "${LLAMACPP_DRY_RUN:-0}" == "1" ]]; then
  printf 'DRY_RUN '
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

exec "${cmd[@]}"
