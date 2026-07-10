#!/usr/bin/env python3
# ruff: noqa: E402,F401,F403,I001
"""Legacy compatibility entrypoint for the local-Qwen Codex Responses bridge.

New integrations should use ``scripts/qwendex_responses_bridge.py``. This file
retains the historical import surface for existing local wrappers.
"""

from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

BRIDGE_VERSION = "qwendex-local-qwen-responses-v2"

from local_qwen_bridge import server as _server  # noqa: E402
_server = importlib.reload(_server)
from local_qwen_bridge.server import *  # noqa: F401,F403,E402
from local_qwen_bridge.server import main  # noqa: E402

class _BridgeFacade(types.ModuleType):
    def __getattr__(self, name: str):
        return getattr(_server, name)

    def __setattr__(self, name: str, value):
        setattr(_server, name, value)
        super().__setattr__(name, value)

if __name__ in sys.modules:
    sys.modules[__name__].__class__ = _BridgeFacade

if __name__ == "__main__":
    raise SystemExit(main())
