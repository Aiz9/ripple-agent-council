"""Named council presets: bundles of roster, roles, mode, and settings.

Built-in presets are agent-agnostic. Configuration presets (top-level ``presets``)
may also name agents and roles. Explicit command-line options always win over a
preset, which in turn wins over saved settings.
"""
from __future__ import annotations

BUILTIN = {
    "quick": {"description": "Fast answer: two rounds, parallel turns.",
              "max_rounds": 2, "peer_turns": "parallel"},
    "deep": {"description": "Thorough: four rounds, then a blind peer ranking.",
             "max_rounds": 4, "anonymous_review": True},
    "blind": {"description": "Parallel turns plus anonymous peer ranking before synthesis.",
              "peer_turns": "parallel", "anonymous_review": True},
    "safe-build": {"description": "Build task; you approve the test command before it runs.",
                   "type": "build", "human_review": ["build"]},
    "hierarchy": {"description": "A leader assigns work and reviews specialist reports.",
                  "mode": "hierarchical"},
}
SETTING_KEYS = ("mode", "peer_turns", "leader", "max_rounds", "anonymous_review", "human_review",
                "max_cost_usd", "max_tokens")
ALLOWED = set(SETTING_KEYS) | {"description", "agents", "roles", "type"}


def catalog(cfg: dict) -> dict[str, dict]:
    custom = cfg.get("presets") or {}
    if not isinstance(custom, dict):
        raise ValueError("presets must map preset names to settings")
    for name, preset in custom.items():
        if not isinstance(name, str) or not isinstance(preset, dict):
            raise ValueError("each preset must be a name mapped to settings")
        unknown = set(preset) - ALLOWED
        if unknown:
            raise ValueError(f"preset {name!r} has unknown keys: {', '.join(sorted(unknown))}")
        if "roles" in preset and not isinstance(preset["roles"], dict):
            raise ValueError(f"preset {name!r}: roles must map agent names to roles")
        if "type" in preset and preset["type"] not in ("auto", "question", "build"):
            raise ValueError(f"preset {name!r}: type must be auto, question, or build")
    return BUILTIN | custom


def resolve(cfg: dict, name: str) -> dict:
    presets = catalog(cfg)
    if name not in presets:
        raise ValueError(f"unknown preset {name!r}; use --presets to list them")
    return presets[name]


def apply_settings(cfg: dict, preset: dict) -> None:
    """Copy the preset's settings into cfg (roster and roles are applied by the caller)."""
    for key in SETTING_KEYS:
        if key in preset:
            cfg["settings"][key] = preset[key]


def describe(cfg: dict) -> list[str]:
    lines = []
    for name, preset in catalog(cfg).items():
        details = ", ".join(f"{k}={v}" for k, v in preset.items() if k != "description")
        origin = "built-in" if name in BUILTIN and name not in (cfg.get("presets") or {}) else "config"
        lines.append(f"{name} ({origin}): {preset.get('description', '')} [{details}]")
    return lines
