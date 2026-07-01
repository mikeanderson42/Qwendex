#!/usr/bin/env bash
set -euo pipefail

DEFAULT_BIN="$HOME/.local/share/qwendex/koboldcpp/koboldcpp-linux-x64"

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

BIN="${KOBOLDCPP_BIN:-$DEFAULT_BIN}"
HOST="${KOBOLDCPP_HOST:-127.0.0.1}"
PORT="${KOBOLDCPP_PORT:-5000}"
MODEL_ALIAS="${KOBOLDCPP_MODEL_ALIAS:-qwen-local}"
MODEL_PATH="$(windows_to_wsl_path "${KOBOLDCPP_MODEL_PATH:-$HOME/models/qwen-coder/example-model.gguf}")"
JINJA_TEMPLATE="$(windows_to_wsl_path "${KOBOLDCPP_JINJA_TEMPLATE:-}")"
JINJA_KWARGS="${KOBOLDCPP_JINJA_KWARGS:-}"
if [[ -z "$JINJA_KWARGS" ]]; then
  JINJA_KWARGS='{"preserve_thinking":true}'
fi
CTX_SIZE="${KOBOLDCPP_CTX_SIZE:-32768}"
GPU_LAYERS="${KOBOLDCPP_GPU_LAYERS:--1}"
CUDA_DEVICE="${KOBOLDCPP_CUDA_DEVICE:-0}"
BATCH_SIZE="${KOBOLDCPP_BATCH_SIZE:-4096}"
THREADS="${KOBOLDCPP_THREADS:-8}"
BLAS_THREADS="${KOBOLDCPP_BLAS_THREADS:-8}"
QUANT_KV="${KOBOLDCPP_QUANT_KV:-q8_0}"
MULTIUSER="${KOBOLDCPP_MULTIUSER:-1}"
SMARTCACHE="${KOBOLDCPP_SMARTCACHE:-0}"
DEFAULT_GEN_AMT="${KOBOLDCPP_DEFAULT_GEN_AMT:-2048}"
GEN_LIMIT="${KOBOLDCPP_GEN_LIMIT:-}"
REQ_TIMEOUT="${KOBOLDCPP_REQ_TIMEOUT:-600}"
CHAT_COMPLETIONS_ADAPTER="$(windows_to_wsl_path "${KOBOLDCPP_CHAT_COMPLETIONS_ADAPTER:-}")"
DEBUGMODE="${KOBOLDCPP_DEBUGMODE:-}"
TEMP="${KOBOLDCPP_TEMPERATURE:-0.2}"
TOP_K="${KOBOLDCPP_TOP_K:-20}"
TOP_P="${KOBOLDCPP_TOP_P:-0.95}"
MIN_P="${KOBOLDCPP_MIN_P:-0.05}"
REPETITION_PENALTY="${KOBOLDCPP_REPETITION_PENALTY:-1.0}"
REASONING_EFFORT="${KOBOLDCPP_REASONING_EFFORT:-none}"
JINJA_THINK="${KOBOLDCPP_JINJA_THINK:-false}"
EXTRA_ARGS="${KOBOLDCPP_EXTRA_ARGS:-}"

if [[ ! -x "$BIN" ]]; then
  echo "Missing KoboldCPP binary: $BIN" >&2
  exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Missing KoboldCPP GGUF model: $MODEL_PATH" >&2
  exit 1
fi

if [[ -n "$JINJA_TEMPLATE" && ! -f "$JINJA_TEMPLATE" ]]; then
  echo "Missing KoboldCPP jinja template: $JINJA_TEMPLATE" >&2
  exit 1
fi

GEN_DEFAULTS="$(printf '{"temperature":%s,"top_k":%s,"top_p":%s,"min_p":%s,"rep_pen":%s}' \
  "$TEMP" "$TOP_K" "$TOP_P" "$MIN_P" "$REPETITION_PENALTY")"

cmd=(
  "$BIN"
  --skiplauncher
  --host "$HOST"
  --port "$PORT"
  --model "$MODEL_PATH"
  --contextsize "$CTX_SIZE"
  --usecuda "$CUDA_DEVICE"
  --gpulayers "$GPU_LAYERS"
  --batchsize "$BATCH_SIZE"
  --threads "$THREADS"
  --blasthreads "$BLAS_THREADS"
  --quantkv "$QUANT_KV"
  --multiuser "$MULTIUSER"
  --defaultgenamt "$DEFAULT_GEN_AMT"
  --reqtimeout "$REQ_TIMEOUT"
  --jinja
  --jinja_tools
  --jinja_kwargs "$JINJA_KWARGS"
  --jinjathink "$JINJA_THINK"
  --reasoningeffort "$REASONING_EFFORT"
  --gendefaults "$GEN_DEFAULTS"
  --quiet
)

if [[ -n "$GEN_LIMIT" ]]; then
  cmd+=(--genlimit "$GEN_LIMIT")
fi

if [[ -n "$CHAT_COMPLETIONS_ADAPTER" ]]; then
  cmd+=(--chatcompletionsadapter "$CHAT_COMPLETIONS_ADAPTER")
fi

if [[ -n "$DEBUGMODE" ]]; then
  cmd+=(--debugmode "$DEBUGMODE")
fi

if [[ "$SMARTCACHE" != "0" && "${SMARTCACHE,,}" != "off" && "${SMARTCACHE,,}" != "false" ]]; then
  cmd+=(--smartcache "$SMARTCACHE")
fi

if [[ -n "$JINJA_TEMPLATE" ]]; then
  cmd+=(--jinjatemplate "$JINJA_TEMPLATE")
fi

if [[ -n "$EXTRA_ARGS" ]]; then
  # shellcheck disable=SC2206
  extra=($EXTRA_ARGS)
  cmd+=("${extra[@]}")
fi

echo "Starting KoboldCPP GGUF OpenAI API."
echo "Binary: $BIN"
echo "Version: $("$BIN" --version 2>/dev/null || true)"
echo "Model: $MODEL_PATH"
echo "Alias: $MODEL_ALIAS"
echo "Jinja template: ${JINJA_TEMPLATE:-model/default}"
echo "Jinja kwargs: $JINJA_KWARGS"
echo "Jinja thinking: $JINJA_THINK"
echo "Context: $CTX_SIZE"
echo "KV cache: $QUANT_KV"
echo "Context shifting: enabled"
echo "Smart cache: $SMARTCACHE"
echo "Default generation amount: $DEFAULT_GEN_AMT"
echo "Generation limit: ${GEN_LIMIT:-unset}"
echo "Request timeout: $REQ_TIMEOUT"
echo "Chat completions adapter: ${CHAT_COMPLETIONS_ADAPTER:-unset}"
echo "API: http://$HOST:$PORT/v1"

if [[ "${KOBOLDCPP_DRY_RUN:-0}" == "1" ]]; then
  printf 'DRY_RUN '
  printf '%q ' "${cmd[@]}"
  printf '\n'
  exit 0
fi

exec "${cmd[@]}"
