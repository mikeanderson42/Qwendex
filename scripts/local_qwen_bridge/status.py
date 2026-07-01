"""Bridge status payload helpers."""

from __future__ import annotations

from typing import Any

from local_qwen_bridge_status import build_status_payload as _build_status_payload
from local_qwen_bridge_status import runtime_guard_status_payload

from . import BRIDGE_PACKAGE_VERSION


def build_status_payload(**kwargs: Any) -> dict[str, Any]:
    payload = _build_status_payload(**kwargs)
    payload["bridge_package_version"] = BRIDGE_PACKAGE_VERSION
    return payload


__all__ = ["build_status_payload", "runtime_guard_status_payload"]
