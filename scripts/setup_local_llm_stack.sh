#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LITELLM_HOME="${LITELLM_HOME:-$HOME/litellm}"
TEXTGEN_HOME="${TEXTGEN_HOME:-$HOME/Text-Generation-WebUI}"
LLAMA_CODEX_HOME="${LLAMA_CODEX_HOME:-$HOME/llama.cpp-codex}"
LITELLM_SOURCE_CONFIG="${LITELLM_SOURCE_CONFIG:-$ROOT/config/local_llm_stack/litellm.textgen.local.yaml}"

mkdir -p "$LITELLM_HOME" "$TEXTGEN_HOME/user_data/characters"

install -m 0644 "$LITELLM_SOURCE_CONFIG" "$LITELLM_HOME/config.yaml"
sed "s#__QWENDEX_ROOT__#$ROOT#g" "$ROOT/config/local_llm_stack/textgen_cmd_flags.txt" > "$TEXTGEN_HOME/user_data/CMD_FLAGS.txt"
install -m 0644 "$ROOT/config/local_llm_stack/Codex Local Harness.yaml" "$TEXTGEN_HOME/user_data/characters/Codex Local Harness.yaml"

if [[ -f "$LLAMA_CODEX_HOME/runtime/system_prompt.txt" ]]; then
  install -m 0644 "$LLAMA_CODEX_HOME/runtime/system_prompt.txt" "$LITELLM_HOME/system_prompt.txt"
fi

echo "Configured LiteLLM: $LITELLM_HOME/config.yaml"
echo "Configured TextGen flags: $TEXTGEN_HOME/user_data/CMD_FLAGS.txt"
echo "Configured TextGen character: $TEXTGEN_HOME/user_data/characters/Codex Local Harness.yaml"
echo "System prompt source: $LLAMA_CODEX_HOME/runtime/system_prompt.txt"
