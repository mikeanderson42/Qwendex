"""Exec-command sanitation and bounded command rewriting facade."""

from __future__ import annotations

from typing import Any

from . import server


def sanitize_exec_command(cmd: str) -> str:
    return server.sanitize_exec_command(cmd)


def normalize_function_arguments(
    function_name: str,
    arguments: Any,
    *,
    latest_user_text: str = "",
) -> str:
    return server.normalize_function_arguments(
        function_name,
        arguments,
        latest_user_text=latest_user_text,
    )


def command_looks_like_unbounded_read_dump(cmd: str) -> bool:
    return server.command_looks_like_unbounded_read_dump(cmd)
