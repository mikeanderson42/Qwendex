#!/usr/bin/env bash
set -euo pipefail

export ALLOW_TEXTGEN_START=1
exec "$(dirname "$0")/run_textgen_safe_no_model.sh" "$@"
