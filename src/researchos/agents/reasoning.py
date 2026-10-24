"""Reasoning effort is a run setting, separate from an agent's identity.

Claude Code: https://code.claude.com/docs/en/model-config (2026-10-24).
Harness: released 0.2.1-rc.2's dsh-llm-deepseek adapter metadata.
Codex capabilities come from its installed CLI's model catalog, never a guessed list.
"""

from __future__ import annotations

import re

LEVELS = ("none", "off", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_CLAUDE = re.compile(r"claude-(sonnet|opus|haiku|fable)-(\d+)(?:-(\d+))?(?:-\d{8})?(?:\[1m\])?\Z")


def selection(value: object) -> str | None:
    """None/empty means leave the runner's default configuration in force."""
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in LEVELS:
        raise ValueError("not a valid reasoning effort")
    return value


def known_levels(runner: str, model: str) -> tuple[str, ...]:
    if runner == "deepseek-harness" and model.partition("/")[2] in ("deepseek-flash", "deepseek-v4-pro"):
        return ("off", "low", "high", "max")
    if runner == "claude-code":
        match = _CLAUDE.fullmatch(model)
        if match:
            family, major, minor = match.groups()
            version = (int(major), int(minor or 0))
            if ((family in ("sonnet", "opus") and version in ((5, 0), (5, 5)))
                    or (family == "fable" and version in ((5, 0), (5, 1)))
                    or (family == "haiku" and version == (5, 5))
                    or (family == "opus" and version in ((4, 7), (4, 8)))):
                return ("low", "medium", "high", "xhigh", "max")
            if family in ("sonnet", "opus") and version == (4, 6):
                return ("low", "medium", "high", "max")
    return ()


def check(runner: str, model: str, value: object) -> str | None:
    """Validate transport values too, so callers cannot inject arbitrary CLI options."""
    level = selection(value)
    if level is not None:
        allowed = tuple(v for v in LEVELS if v != "off") if runner == "codex" else known_levels(runner, model)
        if level not in allowed:
            raise ValueError(f"{runner} / {model} does not support reasoning effort {level!r}")
    return level
