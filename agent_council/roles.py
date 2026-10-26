"""Reusable role descriptions; roles never change a CLI's model or permissions."""
import re

PRESETS = {
    "architect": "Design the system and its interfaces. Explain tradeoffs, constraints, and a minimal coherent plan.",
    "implementer": "Turn the plan into complete working code. Verify behavior and identify implementation edge cases.",
    "researcher": "Investigate factual claims and primary sources. Separate inspected evidence from assumptions and uncertainty.",
    "reviewer": "Review correctness, maintainability, and scope. Challenge concrete defects and propose focused fixes.",
    "tester": "Design and execute meaningful tests, including failure cases. Report actual commands, outputs, and coverage gaps.",
    "security": "Review trust boundaries, permissions, sensitive data, and abuse cases. Propose proportionate mitigations.",
    "skeptic": "Challenge consensus, hidden assumptions, and unsupported claims. Seek counterexamples and revise when evidence warrants.",
    "product": "Clarify user needs, acceptance criteria, usability, and scope. Keep the solution focused on the intended outcome.",
    "coordinator": "Break the goal into assignments, reconcile evidence and conflicting reports, and track the definition of done.",
}


def role_catalog(cfg: dict) -> dict[str, str]:
    custom = cfg.get("roles", {})
    if not isinstance(custom, dict):
        raise ValueError("roles must map role names to nonempty descriptions")
    for name, description in custom.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_-]*", name):
            raise ValueError("custom role names must use lowercase letters, digits, underscores, or hyphens")
        if name in PRESETS or name in ("keep", "custom"):
            raise ValueError(f"custom role {name!r} conflicts with a built-in role")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"role {name!r} needs a nonempty description")
    return PRESETS | custom


def resolve_role(agent: dict, catalog: dict[str, str]) -> tuple[str, str]:
    preset = agent.get("role_preset")
    custom = agent.get("role", "")
    if not isinstance(custom, str):
        raise ValueError(f"{agent['name']}.role must be a string")
    if preset is not None:
        if not isinstance(preset, str) or preset not in catalog:
            raise ValueError(f"{agent['name']}: unknown role preset {preset!r}")
        if custom.strip():
            raise ValueError(f"{agent['name']}: use either role_preset or custom role, not both")
        return preset, catalog[preset]
    return ("custom", custom) if custom.strip() else ("reviewer", PRESETS["reviewer"])


def assign_role(cfg: dict, assignment: str, *, custom: bool = False) -> None:
    name, separator, value = assignment.partition("=")
    if not separator or not name.strip() or not value.strip():
        raise ValueError("role assignment must be AGENT=VALUE with a nonempty value")
    agent = next((a for a in cfg["agents"] if a["name"] == name.strip() and a.get("enabled", True)), None)
    if agent is None:
        raise ValueError(f"role assignment names an unknown or disabled agent: {name.strip()}")
    if not custom and value.strip() not in role_catalog(cfg):
        raise ValueError(f"unknown role {value.strip()!r}; use --roles to list names or --custom-role for free text")
    agent.pop("role", None)
    agent.pop("role_preset", None)
    agent["role" if custom else "role_preset"] = value.strip()
