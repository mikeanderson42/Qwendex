from __future__ import annotations

import os

import pytest


_AMBIENT_QWENDEX_TEST_KEYS = {
    "CODEX_HOME",
    "QWENDEX_AGENT_ARTIFACT_ROOT",
    "QWENDEX_CODEX_HOME",
    "QWENDEX_CODEX_RUNTIME",
    "QWENDEX_CODEX_STATUS_FILE",
    "QWENDEX_DEV_ROOT",
    "QWENDEX_DEV_SOURCE_ROOT",
    "QWENDEX_HOOK_GENERATION",
    "QWENDEX_LEDGER_DB",
    "QWENDEX_META_ROOT",
    "QWENDEX_PERFORMANCE_DB",
    "QWENDEX_RESULTS_ROOT",
    "QWENDEX_ROOT",
    "QWENDEX_RUN_ID",
    "QWENDEX_STATE_DB",
}


@pytest.fixture(autouse=True)
def isolate_parent_qwendex_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent a selected operator generation from contaminating temp fixtures."""
    for key in tuple(os.environ):
        if (
            key in _AMBIENT_QWENDEX_TEST_KEYS
            or key.startswith("QWENDEX_AGENT_")
            or key.startswith("QWENDEX_MANAGER_")
            or key.startswith("QWENDEX_RUNTIME_")
        ):
            monkeypatch.delenv(key, raising=False)
