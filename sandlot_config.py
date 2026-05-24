"""Runtime config helpers for Sandlot."""

from __future__ import annotations

import os


TRUE_VALUES = {"1", "true", "yes", "on"}


def env_flag(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def profile_warm_enabled() -> bool:
    if env_flag("SANDLOT_PROFILE_WARM_DISABLED"):
        return False
    return env_flag("SANDLOT_PROFILE_WARM_ENABLED")


def waiver_ai_warm_enabled() -> bool:
    if env_flag("SANDLOT_WAIVER_AI_WARM_DISABLED"):
        return False
    return env_flag("SANDLOT_WAIVER_AI_WARM_ENABLED")
