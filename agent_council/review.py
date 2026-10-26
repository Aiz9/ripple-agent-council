"""Anonymous peer ranking, after the approach of karpathy/llm-council.

Every surviving agent ranks all final positions under shuffled labels with agent
names scrubbed from the text, so no one can favor a known author (or recognize
its own answer by name). Valid ballots are combined with a Borda count, and the
result is given to the synthesizer. Labels are shuffled with a seed derived from
the session, so a resumed session rebuilds identical prompts and replays its
checkpointed ballots instead of calling the agents again.
"""
from __future__ import annotations

import random
import re

LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
INSTRUCTION = ("\nPEER RANKING. Below are the council's final positions, anonymized as Response A, B, and so on. "
               "One of them may be yours; judge every response by the same standard. Rank all of them from best "
               "to worst for correctness, quality of evidence, and usefulness for the user's goal. Give one sentence "
               "of justification per response, then end with exactly one line in this form, naming every label once:\n"
               "RANKING: A > B > C\n")


def assign_labels(names: list[str], seed: str) -> dict[str, str]:
    """Map labels (A, B, ...) to agent names in a session-stable shuffled order."""
    order = sorted(names)
    random.Random(seed).shuffle(order)
    return {LETTERS[index]: name for index, name in enumerate(order)}


def scrub(text: str, names: list[str]) -> str:
    for name in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?i)(?<![\w-]){re.escape(name)}(?![\w-])", "[peer]", text)
    return text


def ballot_section(labels: dict[str, str], answers: dict[str, str]) -> str:
    names = list(labels.values())
    return "\n\n".join(f"--- Response {label} ---\n{scrub(answers[name], names)}" for label, name in labels.items())


def parse_ballot(text: str, labels: list[str]) -> list[str] | None:
    """Return the ranked labels from the last RANKING line, or None if it does
    not name every label exactly once."""
    lines = re.findall(r"(?mi)^\s*RANKING:\s*(.+?)\s*$", text)
    if not lines:
        return None
    order = [re.sub(r"[^A-Z]", "", part.upper().replace("RESPONSE", "")) for part in lines[-1].split(">")]
    return order if sorted(order) == sorted(labels) and len(set(order)) == len(order) else None


def tally(ballots: dict[str, list[str] | None], labels: dict[str, str]) -> dict:
    """Borda count over valid ballots: last place earns 0 points, first earns n-1."""
    points = {name: 0 for name in labels.values()}
    valid = {voter: order for voter, order in ballots.items() if order}
    for order in valid.values():
        for position, label in enumerate(order):
            points[labels[label]] += len(order) - 1 - position
    ranking = sorted(points, key=lambda name: (-points[name], name))
    return {"labels": labels, "ballots": ballots, "valid_ballots": len(valid),
            "scores": points, "ranking": ranking if valid else []}


def synthesis_note(result: dict) -> str:
    if result["valid_ballots"] < 2:
        return (f"\nANONYMOUS PEER RANKING: only {result['valid_ballots']} valid ballot(s); "
                "treat the ranking as inconclusive.\n")
    lines = ", ".join(f"{place}. {name} ({result['scores'][name]} points)"
                      for place, name in enumerate(result["ranking"], 1))
    return (f"\nANONYMOUS PEER RANKING (Borda count over {result['valid_ballots']} blind ballots): {lines}. "
            "Use it as evidence about answer quality, not as proof; preserve well-supported dissent.\n")
