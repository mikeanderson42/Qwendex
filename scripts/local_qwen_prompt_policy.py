#!/usr/bin/env python3
"""Reviewed system-prompt loading for the local-Qwen bridge."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path


def load_system_prompt_text(path: str | None) -> str:
    if not path:
        return ""
    try:
        prompt_path = Path(path)
        text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if prompt_path.suffix.lower() in {".yaml", ".yml"}:
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not re.fullmatch(r"context:\s*\|[+-]?\s*", line):
                continue
            block: list[str] = []
            for candidate in lines[index + 1 :]:
                if candidate and not candidate[0].isspace():
                    break
                block.append(candidate)
            context = textwrap.dedent("\n".join(block)).strip()
            if context:
                return context
    return text
