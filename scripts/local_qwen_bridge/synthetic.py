"""Synthetic deterministic recovery registry."""

from __future__ import annotations

from typing import Any

from . import server


def handler_names() -> list[str]:
    return server.synthetic_response_handler_names()


def synthetic_response_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    return server.synthetic_response_from_payload(payload)
