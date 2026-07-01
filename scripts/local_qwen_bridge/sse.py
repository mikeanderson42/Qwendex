"""Server-sent event emission helpers for Responses streaming."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler
from typing import Any

from . import server


def sse_event(event: dict[str, Any]) -> bytes:
    return server.sse_event(event)


def emit_responses_stream(
    handler: BaseHTTPRequestHandler,
    response_payload: dict[str, Any],
) -> None:
    server.emit_responses_stream(handler, response_payload)
