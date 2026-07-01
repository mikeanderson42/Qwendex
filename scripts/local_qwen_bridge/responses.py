"""Responses request translation and response shaping facade."""

from __future__ import annotations

from typing import Any

from . import server


def responses_payload_to_chat(payload: dict[str, Any]) -> dict[str, Any]:
    return server.responses_payload_to_tabby_chat(payload)


def responses_payload_to_tabby_chat(payload: dict[str, Any]) -> dict[str, Any]:
    return server.responses_payload_to_tabby_chat(payload)


def chat_completion_to_response(
    chat_payload: dict[str, Any],
    request_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return server.chat_completion_to_response(chat_payload, request_payload)


def response_payload_with_message(text: str, model: str = "") -> dict[str, Any]:
    return server.response_payload_with_message(text, model=model)


def response_payload_with_function_call(call: dict[str, Any], model: str = "") -> dict[str, Any]:
    return server.response_payload_with_function_call(call, model=model)


def responses_raw_items(payload: dict[str, Any]) -> list[Any]:
    return server.responses_raw_items(payload)
