#!/usr/bin/env bash
set -euo pipefail

TEXTGEN_HOME="${TEXTGEN_HOME:-$HOME/Text-Generation-WebUI}"
VENV_PY="$TEXTGEN_HOME/venv/bin/python"
HOST="${TEXTGEN_HOST:-127.0.0.1}"
WEB_PORT="${TEXTGEN_WEB_PORT:-7860}"
API_PORT="${TEXTGEN_API_PORT:-5000}"
CHAT_TEMPLATE="${TEXTGEN_CHAT_TEMPLATE:-$HOME/llama.cpp-codex/qwen3_codex_no_think.jinja}"
MMPROJ="${TEXTGEN_MMPROJ:-}"
MODEL="${TEXTGEN_MODEL:-qwen36-27Bb}"
LOADER="${TEXTGEN_LOADER:-ExLlamav3}"
MODEL_DIR="${TEXTGEN_MODEL_DIR:-}"
MODEL_DRAFT="${TEXTGEN_MODEL_DRAFT:-}"
CTX_SIZE="${TEXTGEN_CTX_SIZE:-98304}"
CACHE_TYPE="${TEXTGEN_CACHE_TYPE:-q8}"
CACHE_TYPE_K="${TEXTGEN_CACHE_TYPE_K:-}"
CACHE_TYPE_V="${TEXTGEN_CACHE_TYPE_V:-}"
GPU_SPLIT="${TEXTGEN_GPU_SPLIT:-22}"
GPU_LAYERS="${TEXTGEN_GPU_LAYERS:--1}"
GPU_LAYERS_DRAFT="${TEXTGEN_GPU_LAYERS_DRAFT:-256}"
DEVICE_DRAFT="${TEXTGEN_DEVICE_DRAFT:-}"
FIT_TARGET="${TEXTGEN_FIT_TARGET:-2048}"
BATCH_SIZE="${TEXTGEN_BATCH_SIZE:-4096}"
UBATCH_SIZE="${TEXTGEN_UBATCH_SIZE:-2048}"
PARALLEL="${TEXTGEN_PARALLEL:-1}"
THREADS="${TEXTGEN_THREADS:-8}"
THREADS_BATCH="${TEXTGEN_THREADS_BATCH:-8}"
SPEC_TYPE="${TEXTGEN_SPEC_TYPE:-none}"
DRAFT_MAX="${TEXTGEN_DRAFT_MAX:-3}"
EXTRA_FLAGS="${TEXTGEN_EXTRA_FLAGS:-}"
TOP_K="${TEXTGEN_TOP_K:-20}"
TOP_P="${TEXTGEN_TOP_P:-0.95}"
MIN_P="${TEXTGEN_MIN_P:-0.05}"
REPETITION_PENALTY="${TEXTGEN_REPETITION_PENALTY:-1.0}"
PRESENCE_PENALTY="${TEXTGEN_PRESENCE_PENALTY:-1.0}"
FREQUENCY_PENALTY="${TEXTGEN_FREQUENCY_PENALTY:-0.0}"
ENABLE_THINKING="${TEXTGEN_ENABLE_THINKING:-1}"
PRESERVE_THINKING="${TEXTGEN_PRESERVE_THINKING:-1}"
TEMPERATURE="${TEXTGEN_TEMPERATURE:-0.55}"
DISABLE_VISION="${TEXTGEN_DISABLE_VISION:-0}"

if [[ "${ALLOW_TEXTGEN_START:-0}" != "1" ]]; then
  echo "Refusing to start TextGen by default." >&2
  echo "This launcher loads $MODEL with $LOADER, so startup is opt-in." >&2
  echo "Run with: ALLOW_TEXTGEN_START=1 scripts/run_textgen_safe_no_model.sh" >&2
  exit 2
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "Missing TextGen venv at $VENV_PY" >&2
  echo "Install first: python3 -m venv ~/Text-Generation-WebUI/venv && ~/Text-Generation-WebUI/venv/bin/python -m pip install -r ~/Text-Generation-WebUI/requirements/portable/requirements_cpu_only.txt" >&2
  exit 1
fi

if [[ ! -f "$TEXTGEN_HOME/server.py" ]]; then
  echo "Missing TextGen server.py under $TEXTGEN_HOME" >&2
  exit 1
fi

USE_CHAT_TEMPLATE=1
case "${CHAT_TEMPLATE,,}" in
  ""|"auto"|"none"|"embedded")
    USE_CHAT_TEMPLATE=0
    ;;
esac

if [[ "$USE_CHAT_TEMPLATE" == "1" && ! -f "$CHAT_TEMPLATE" ]]; then
  echo "Missing chat template: $CHAT_TEMPLATE" >&2
  exit 1
fi

cd "$TEXTGEN_HOME"
echo "Starting TextGen model API."
echo "Model: $MODEL"
if [[ -n "$CACHE_TYPE_K" || -n "$CACHE_TYPE_V" ]]; then
  echo "Loader/cache/context: $LOADER / split / $CTX_SIZE"
  echo "GGUF cache split K/V: ${CACHE_TYPE_K:-default} / ${CACHE_TYPE_V:-default}"
else
  echo "Loader/cache/context: $LOADER / $CACHE_TYPE / $CTX_SIZE"
fi
if [[ -n "$MODEL_DIR" ]]; then
  echo "Model dir: $MODEL_DIR"
fi
if [[ -n "$MODEL_DRAFT" ]]; then
  echo "Draft model: $MODEL_DRAFT"
  echo "Draft tokens: $DRAFT_MAX"
fi
if [[ -n "$MMPROJ" ]]; then
  echo "mmproj: $MMPROJ"
fi
if [[ "$DISABLE_VISION" == "1" || "${DISABLE_VISION,,}" == "true" || "${DISABLE_VISION,,}" == "yes" ]]; then
  echo "Vision/media component: disabled"
fi
echo "API: http://$HOST:$API_PORT"

cmd=(
  "$VENV_PY" server.py
  --api \
  --nowebui \
  --listen-host "$HOST" \
  --listen-port "$WEB_PORT" \
  --api-port "$API_PORT" \
  --model "$MODEL" \
  --loader "$LOADER" \
  --ctx-size "$CTX_SIZE" \
  --top-k "$TOP_K" \
  --top-p "$TOP_P" \
  --min-p "$MIN_P" \
  --repetition-penalty "$REPETITION_PENALTY" \
  --presence-penalty "$PRESENCE_PENALTY" \
  --frequency-penalty "$FREQUENCY_PENALTY" \
  --temperature "$TEMPERATURE"
)

if [[ "$ENABLE_THINKING" == "1" || "${ENABLE_THINKING,,}" == "true" || "${ENABLE_THINKING,,}" == "yes" ]]; then
  cmd+=(--enable-thinking)
else
  cmd+=(--no-enable-thinking)
fi

if [[ "$PRESERVE_THINKING" == "1" || "${PRESERVE_THINKING,,}" == "true" || "${PRESERVE_THINKING,,}" == "yes" ]]; then
  cmd+=(--preserve-thinking)
else
  cmd+=(--no-preserve-thinking)
fi

if [[ "$USE_CHAT_TEMPLATE" == "1" ]]; then
  cmd+=(--chat-template-file "$CHAT_TEMPLATE")
else
  echo "Chat template: embedded/default"
fi

if [[ -n "$MODEL_DIR" ]]; then
  cmd+=(--model-dir "$MODEL_DIR")
fi
if [[ -n "$MODEL_DRAFT" ]]; then
  cmd+=(--model-draft "$MODEL_DRAFT" --draft-max "$DRAFT_MAX")
  if [[ -n "$GPU_LAYERS_DRAFT" ]]; then
    cmd+=(--gpu-layers-draft "$GPU_LAYERS_DRAFT")
  fi
  if [[ -n "$DEVICE_DRAFT" ]]; then
    cmd+=(--device-draft "$DEVICE_DRAFT")
  fi
fi

if [[ "$LOADER" == "llama.cpp" ]]; then
  cmd+=(
    --gpu-layers "$GPU_LAYERS"
    --fit-target "$FIT_TARGET"
    --batch-size "$BATCH_SIZE"
    --ubatch-size "$UBATCH_SIZE"
    --parallel "$PARALLEL"
    --threads "$THREADS"
    --threads-batch "$THREADS_BATCH"
    --spec-type "$SPEC_TYPE"
    --draft-max "$DRAFT_MAX"
  )
  if [[ -n "$MMPROJ" ]]; then
    cmd+=(--mmproj "$MMPROJ")
  fi
  extra_flags=()
  if [[ -n "$CACHE_TYPE_K" || -n "$CACHE_TYPE_V" ]]; then
    [[ -n "$CACHE_TYPE_K" ]] && extra_flags+=(--cache-type-k "$CACHE_TYPE_K")
    [[ -n "$CACHE_TYPE_V" ]] && extra_flags+=(--cache-type-v "$CACHE_TYPE_V")
  else
    cmd+=(--cache-type "$CACHE_TYPE")
  fi
  if [[ -n "$EXTRA_FLAGS" ]]; then
    # shellcheck disable=SC2206
    extra_flags+=($EXTRA_FLAGS)
  fi
  if [[ "${#extra_flags[@]}" -gt 0 ]]; then
    cmd+=(--extra-flags "${extra_flags[*]}")
  fi
else
  cmd+=(
    --cache-type "$CACHE_TYPE"
    --gpu-split "$GPU_SPLIT"
  )
  if [[ "$SPEC_TYPE" != "none" ]]; then
    cmd+=(--spec-type "$SPEC_TYPE")
  fi
  if [[ "$DRAFT_MAX" -gt 0 ]]; then
    cmd+=(--draft-max "$DRAFT_MAX")
  fi
fi

exec "${cmd[@]}"
