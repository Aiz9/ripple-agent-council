"""Guardrails as code: validate every agent answer before it enters the debate.

Rules live in the configuration's top-level ``guardrails`` list. Each has an
``action``: ``flag`` records a violation and keeps the answer; ``reject`` asks
the agent for one revision with the violations explained, and drops the answer if
the revision still violates. ``phases`` limits a rule to framing, discussion,
ranking, or final answers. A ``python`` rule calls ``module:function(answer,
context)``, which returns a message, a list of messages, or nothing.
"""
from __future__ import annotations

import importlib
import re

ACTIONS = ("reject", "flag")
PHASES = ("framing", "discussion", "ranking", "final")
EVIDENCE_TAG = re.compile(r"\[(?:VERIFIED|ASSUMPTION|UNVERIFIED)\b", re.I)
SECRET_PATTERNS = {
    "Anthropic API key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI API key": r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}",
    "GitHub token": r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "AWS access key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "Slack token": r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
    "private key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}


def phase_of(tag: str) -> str:
    tag = tag.removesuffix("-revision")
    if tag == "round0":
        return "framing"
    if tag == "ranking":
        return "ranking"
    if tag == "final":
        return "final"
    return "discussion"


def validate_rules(raw) -> list[dict]:
    """Normalize the guardrails list, raising ValueError for invalid rules."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("guardrails must be a list of rules")
    rules = []
    for index, item in enumerate(raw, 1):
        where = f"guardrails[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} must be a mapping")
        rule = {"rule": item.get("rule", "python" if "python" in item else None),
                "action": item.get("action", "reject"),
                "phases": item.get("phases", list(PHASES)), "agents": item.get("agents")}
        if rule["action"] not in ACTIONS:
            raise ValueError(f"{where}.action must be reject or flag")
        if (not isinstance(rule["phases"], list) or not rule["phases"]
                or any(phase not in PHASES for phase in rule["phases"])):
            raise ValueError(f"{where}.phases must list some of: {', '.join(PHASES)}")
        if rule["agents"] is not None and (not isinstance(rule["agents"], list)
                                           or not all(isinstance(a, str) for a in rule["agents"])):
            raise ValueError(f"{where}.agents must be a list of agent names")
        kind = rule["rule"]
        if kind in ("forbid", "require"):
            pattern = item.get("pattern")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"{where}: {kind} needs a nonempty regular expression in 'pattern'")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"{where}: invalid pattern: {exc}") from exc
            rule["pattern"] = pattern
        elif kind == "max_chars":
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{where}: max_chars needs a positive integer 'value'")
            rule["value"] = value
        elif kind == "python":
            spec = item.get("python")
            if not isinstance(spec, str) or not re.fullmatch(r"[\w.]+:\w+", spec):
                raise ValueError(f"{where}: python must be 'module.path:function'")
            rule["python"] = spec
        elif kind not in ("require_evidence_tags", "no_secrets"):
            raise ValueError(f"{where}: unknown rule {kind!r}; use forbid, require, max_chars, "
                             "require_evidence_tags, no_secrets, or python")
        if isinstance(item.get("message"), str):
            rule["message"] = item["message"]
        rules.append(rule)
    return rules


def _problems(rule: dict, answer: str, context: dict) -> list[str]:
    kind = rule["rule"]
    if kind == "forbid":
        match = re.search(rule["pattern"], answer)
        return [rule.get("message") or f"contains forbidden text {match[0]!r}"] if match else []
    if kind == "require":
        return [] if re.search(rule["pattern"], answer) else [rule.get("message") or
                                                               f"must match {rule['pattern']!r}"]
    if kind == "max_chars":
        return [f"is {len(answer):,} characters; the limit is {rule['value']:,}"] if len(answer) > rule["value"] else []
    if kind == "require_evidence_tags":
        return [] if EVIDENCE_TAG.search(answer) else [
            "tags no claims as [VERIFIED: ...], [ASSUMPTION], or [UNVERIFIED]"]
    if kind == "no_secrets":
        return [f"appears to contain a {label}" for label, pattern in SECRET_PATTERNS.items()
                if re.search(pattern, answer)]
    module_name, function_name = rule["python"].split(":")
    found = getattr(importlib.import_module(module_name), function_name)(answer, dict(context))
    if not found:
        return []
    return [found] if isinstance(found, str) else [str(item) for item in found]


def check(rules: list[dict], answer: str, *, agent: str, tag: str) -> list[dict]:
    """Return violations as {rule, action, message} for one answer."""
    phase = phase_of(tag)
    violations = []
    for rule in rules:
        if phase not in rule["phases"] or (rule["agents"] is not None and agent not in rule["agents"]):
            continue
        label = rule.get("python") or rule["rule"]
        try:
            problems = _problems(rule, answer, {"agent": agent, "tag": tag, "phase": phase})
        except Exception as exc:  # A broken validator must not silently reject every answer.
            violations.append({"rule": label, "action": "flag", "message": f"validator failed: {exc}"})
            continue
        violations += [{"rule": label, "action": rule["action"], "message": message} for message in problems]
    return violations


def feedback(violations: list[dict]) -> str:
    lines = "\n".join(f"- {v['message']} ({v['rule']})" for v in violations)
    return ("\n\nGUARDRAIL FEEDBACK: Your previous answer was rejected because it:\n" + lines +
            "\nRewrite your complete answer so it complies, keeping your substance and evidence.\n")
