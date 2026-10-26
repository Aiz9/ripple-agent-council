"""Normalize token and cost usage reported by agent CLIs, and total it per session.

Only formats seen in real CLI output are parsed: Claude Code's JSON envelope
(one object, or the final ``result`` event of stream-json)
(`usage` token counts and `total_cost_usd`) and Codex's closing ``tokens used``
line on stderr. Other CLIs print nothing reliable, so their calls are recorded as
unmetered rather than estimated.
"""
from __future__ import annotations

import json
import math
import re

from .streaming import claude_envelope

CODEX_TOKENS = re.compile(r"(?mi)^tokens used\s*\n\s*([\d,]+)\s*$")
CLAUDE_INPUT_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _count(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def parse_usage(agent: dict, stdout: str, stderr: str) -> dict | None:
    """Return {input_tokens, output_tokens, total_tokens, cost_usd} (fields may be
    None when a CLI does not report them), or None when nothing was reported."""
    if agent.get("output_mode") in ("claude_json", "claude_stream_json"):
        try:
            envelope = claude_envelope(agent["output_mode"], stdout)
        except ValueError:
            envelope = None
        if isinstance(envelope, dict):
            usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else None
            cost = envelope.get("total_cost_usd")
            cost = float(cost) if (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                                   and math.isfinite(cost) and cost >= 0) else None
            if usage is not None or cost is not None:
                usage = usage or {}
                tokens_in = sum(_count(usage.get(field)) for field in CLAUDE_INPUT_FIELDS)
                tokens_out = _count(usage.get("output_tokens"))
                return {"input_tokens": tokens_in, "output_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out, "cost_usd": cost}
    reported = CODEX_TOKENS.findall(stderr)
    if reported:
        total = int(reported[-1].replace(",", ""))
        return {"input_tokens": None, "output_tokens": None, "total_tokens": total, "cost_usd": None}
    return None


def summarize(results: list[dict]) -> dict:
    """Total usage over call results (every attempt, including failed ones)."""
    by_agent: dict[str, dict] = {}
    for result in results:
        name = result.get("name")
        if not isinstance(name, str) or result.get("tag") == "preflight":
            continue
        entry = by_agent.setdefault(name, {"calls": 0, "metered_calls": 0, "total_tokens": 0, "cost_usd": None})
        entry["calls"] += 1
        usage = result.get("usage")
        if not isinstance(usage, dict):
            continue
        entry["metered_calls"] += 1
        entry["total_tokens"] += _count(usage.get("total_tokens"))
        if isinstance(usage.get("cost_usd"), (int, float)):
            entry["cost_usd"] = round((entry["cost_usd"] or 0) + usage["cost_usd"], 6)
    costs = [e["cost_usd"] for e in by_agent.values() if e["cost_usd"] is not None]
    return {"by_agent": by_agent,
            "total_tokens": sum(e["total_tokens"] for e in by_agent.values()),
            "total_cost_usd": round(sum(costs), 6) if costs else None,
            "unmetered_agents": sorted(n for n, e in by_agent.items() if e["metered_calls"] == 0)}


def budget_exceeded(summary: dict, settings: dict) -> str | None:
    """Explain which cap is reached, or None while work may continue."""
    cost_cap, token_cap = settings.get("max_cost_usd"), settings.get("max_tokens")
    if cost_cap is not None and (summary["total_cost_usd"] or 0) >= cost_cap:
        return f"cost budget reached: ${summary['total_cost_usd']:.4f} of ${cost_cap:g}"
    if token_cap is not None and summary["total_tokens"] >= token_cap:
        return f"token budget reached: {summary['total_tokens']:,} of {token_cap:,} tokens"
    return None


def describe(summary: dict) -> str:
    parts = [f"{summary['total_tokens']:,} metered tokens"]
    if summary["total_cost_usd"] is not None:
        parts.append(f"${summary['total_cost_usd']:.4f} reported cost")
    if summary["unmetered_agents"]:
        parts.append("no usage reported by " + ", ".join(summary["unmetered_agents"]))
    return "; ".join(parts)
