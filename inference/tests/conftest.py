"""Shared test setup for the inference package.

Wave-1 of the inference branch splits ``config.py`` and ``logging_utils.py``
across parallel agents. Until those land in this worktree, tests still need
to import ``inference.model_loader``, which imports from those modules at
module load time. We install in-memory stubs for them via ``sys.modules``
*before* the model_loader import happens.

Once the real modules are merged, these stubs become harmless overrides for
the duration of a pytest run (pytest imports conftest before collection), and
the real modules still exist on disk for production code paths.
"""
from __future__ import annotations

import logging
import sys
import types


def _install_stub_config() -> None:
    if "inference.config" in sys.modules:
        mod = sys.modules["inference.config"]
        # Ensure required attributes exist even if the real module is partial.
        for name, value in (
            ("GCP_PROJECT", "test-project"),
            ("GCP_REGION", "us-central1"),
            ("MODEL_DISPLAY_NAME", "distilbert-priority"),
            ("MODEL_VERSION_ENV", "MODEL_VERSION"),
        ):
            if not hasattr(mod, name):
                setattr(mod, name, value)
        return

    mod = types.ModuleType("inference.config")
    mod.GCP_PROJECT = "test-project"
    mod.GCP_REGION = "us-central1"
    mod.MODEL_DISPLAY_NAME = "distilbert-priority"
    mod.MODEL_VERSION_ENV = "MODEL_VERSION"
    sys.modules["inference.config"] = mod


def _install_stub_logging_utils() -> None:
    if "inference.logging_utils" in sys.modules:
        mod = sys.modules["inference.logging_utils"]
        if not hasattr(mod, "get_logger"):
            mod.get_logger = logging.getLogger  # type: ignore[attr-defined]
        return

    mod = types.ModuleType("inference.logging_utils")

    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

    mod.get_logger = get_logger
    sys.modules["inference.logging_utils"] = mod


_install_stub_config()
_install_stub_logging_utils()
