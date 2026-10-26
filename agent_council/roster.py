"""Automatic roster choice (``--agents auto``): pick available, suitable agents.

Candidates are configured agents (preferred) and installed built-in adapters. An
agent is skipped when its executable is missing, or when a session in the last
few hours recorded it as limited by its account (rate or usage limits) or as
signed out. Remaining candidates are ordered by how well their role suits the
task, then by whether they are already enabled in the configuration.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

from .adapters import ADAPTERS

RECENT_SECONDS = 3 * 3600
BLOCKING = {"capacity": "hit an account or rate limit", "authentication": "was not signed in"}
FAVORED = {"build": ("implementer", "tester", "reviewer", "architect", "security"),
           "question": ("researcher", "skeptic", "architect", "reviewer", "product")}


def recent_blocks(sessions_root: Path, now: float | None = None) -> dict[str, tuple[str, float]]:
    """Agent name -> (reason, age in seconds) from recent sessions' availability records."""
    now = time.time() if now is None else now
    blocked: dict[str, tuple[str, float]] = {}
    root = Path(sessions_root)
    for session in sorted(root.iterdir() if root.is_dir() else [], reverse=True)[:30]:
        meta_path = session / "session.json"
        try:
            age = now - meta_path.stat().st_mtime
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if age > RECENT_SECONDS or not isinstance(meta, dict):
            continue
        for name, kind in (meta.get("unavailable") or {}).items():
            if kind in BLOCKING and (name not in blocked or age < blocked[name][1]):
                blocked[name] = (BLOCKING[kind], age)
    return blocked


def choose(cfg: dict, *, sessions_root: Path, is_build: bool, limit: int = 4,
           path: str | None = None, now: float | None = None) -> tuple[list[str], list[str]]:
    """Return (chosen names, human-readable notes about skipped candidates)."""
    path = os.environ.get("PATH") if path is None else path
    configured = {agent["name"]: agent for agent in cfg["agents"]}
    candidates = list(configured.values()) + [ADAPTERS[name].configuration(name)
                                              for name in ADAPTERS if name not in configured]
    blocked = recent_blocks(sessions_root, now)
    favored = FAVORED["build" if is_build else "question"]
    usable, notes = [], []
    for order, agent in enumerate(candidates):
        name = agent["name"]
        if shutil.which(agent["argv"][0], path=path) is None:
            continue  # not installed: silently ineligible
        if name in blocked:
            reason, age = blocked[name]
            notes.append(f"skipped {name}: {reason} {int(age // 60)} min ago")
            continue
        role = agent.get("role_preset") or "reviewer"
        fit = favored.index(role) if role in favored else len(favored)
        enabled = name in configured and agent.get("enabled", True)
        usable.append(((fit, not enabled, name not in configured, order), name))
    usable.sort()
    chosen = [name for _, name in usable[:max(limit, 1)]]
    minimum = cfg["settings"].get("min_agents", 2)
    if len(chosen) < minimum:
        raise ValueError(f"--agents auto found only {len(chosen)} usable agent(s); need {minimum}"
                         + (" (" + "; ".join(notes) + ")" if notes else ""))
    return chosen, notes
