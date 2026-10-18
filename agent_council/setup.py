"""Terminal setup for coordination and roles. No model calls are made here."""
import copy
import os
from pathlib import Path
import shutil
import tempfile

import yaml

from .roles import role_catalog, resolve_role


def choose(question: str, choices: list[str], default: str) -> str:
    while True:
        answer = input(f"{question} ({', '.join(choices)}) [{default}]: ").strip()
        if not answer:
            return default
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        if answer in choices:
            return answer
        print("Choose a listed name or its number.")


def describe(cfg: dict) -> str:
    """One-line summary of the coordination and role choices."""
    settings = cfg["settings"]
    catalog = role_catalog(cfg)
    flow = (f"hierarchical, leader {settings['leader']}" if settings["mode"] == "hierarchical"
            else f"peer-to-peer, {settings.get('peer_turns', 'sequential')} turns")
    roles = ", ".join(f"{a['name']}={resolve_role(a, catalog)[0]}"
                      for a in cfg["agents"] if a.get("enabled", True))
    return f"{flow}; {roles}"


def confirm_or_configure(cfg: dict, *, leader: str | None = None,
                         peer_turns: str | None = None) -> tuple[dict, bool]:
    """Offer the current choices in one question; 'n' opens the full questionnaire.
    Returns the configuration to use and whether the user changed it."""
    print(f"\nCouncil setup: {describe(cfg)}")
    while True:
        answer = input("Use these choices? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            return cfg, False
        if answer in ("n", "no"):
            return configure(cfg, leader=leader, peer_turns=peer_turns), True
        print("Answer y or n.")


def configure(cfg: dict, *, mode: str | None = None, leader: str | None = None,
              peer_turns: str | None = None) -> dict:
    """Return a configured copy; the caller decides whether to persist it."""
    result = copy.deepcopy(cfg)
    settings = result["settings"]
    print("\nHow should the agents work together?")
    print("  1. peer-to-peer: direct turns in a shared conversation, without a leader.")
    print("  2. hierarchical: a leader assigns work, reviews reports, and writes the final answer.")
    settings["mode"] = mode or choose("Coordination", ["peer-to-peer", "hierarchical"], settings["mode"])
    enabled = [a for a in result["agents"] if a.get("enabled", True)]
    if settings["mode"] == "hierarchical":
        settings["leader"] = leader or choose("Leader", [a["name"] for a in enabled], settings["leader"])
    else:
        print("\nHow should peers take turns after their independent first answers?")
        print("  1. sequential: one at a time; later speakers see earlier replies from the same round.")
        print("  2. parallel: all at once, seeing the conversation through the previous round (faster).")
        settings["peer_turns"] = peer_turns or choose(
            "Peer turns", ["sequential", "parallel"], settings.get("peer_turns", "sequential"))
    print("\nChoose a premade role, a named custom role, or write a custom role for each agent.")
    catalog = role_catalog(result)
    for name, description in catalog.items():
        print(f"  {name}: {description}")
    for agent in enabled:
        name, description = resolve_role(agent, catalog)
        print(f"\n{agent['name']} currently uses {name}: {description}")
        selection = choose(f"Role for {agent['name']}", ["keep", *catalog, "custom"], "keep")
        if selection == "keep":
            continue
        agent.pop("role", None)
        agent.pop("role_preset", None)
        if selection == "custom":
            while True:
                description = input(f"Describe {agent['name']}'s responsibilities: ").strip()
                if description:
                    agent["role"] = description
                    break
                print("A custom role needs a description.")
        else:
            agent["role_preset"] = selection
    return result


def save_configuration(cfg: dict, target: Path) -> Path | None:
    """Atomically save setup, keeping a unique backup of an existing file."""
    target = target.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    if target.exists():
        descriptor, filename = tempfile.mkstemp(prefix=f"{target.name}.backup-", dir=target.parent)
        os.close(descriptor)
        backup = Path(filename)
        shutil.copyfile(target, backup)
    descriptor, filename = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(cfg, handle, sort_keys=False, allow_unicode=True)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return backup
