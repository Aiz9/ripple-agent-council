"""Human checkpoints during a run (opt in with --human-review).

At each enabled point the user can continue, add guidance (included in every later
prompt), replace the definition of done (framing), end the debate early
(rounds), or stop and save. Before build verification the user must approve the
exact command. Decisions are stored in the session so a resume replays them
instead of asking again; "stop" is never stored, so a resume asks again.
"""
from __future__ import annotations

import re

POINTS = ("framing", "rounds", "synthesis", "build")


def parse_points(value) -> list[str]:
    """Accept a list or a comma-separated string; 'all' enables every point."""
    if value is None:
        return []
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError("human_review must be a list of checkpoint names")
    points = [item.strip() for item in items if item.strip()]
    if points == ["all"]:
        return list(POINTS)
    unknown = [point for point in points if point not in POINTS]
    if unknown:
        raise ValueError(f"unknown human_review point(s) {', '.join(unknown)}; use {', '.join(POINTS)}, or all")
    return [point for point in POINTS if point in points]


def _read_block(prompt: str, input_fn) -> str:
    print(prompt + " (finish with an empty line)")
    lines = []
    while True:
        line = input_fn("> ")
        if not line.strip():
            return "\n".join(lines).strip()
        lines.append(line.rstrip())


def ask(title: str, summary: str, *, edit_checklist: bool = False, synthesize: bool = False,
        input_fn=None) -> dict:
    """Show a checkpoint and return {"action": continue|synthesize|stop, ...}."""
    input_fn = input_fn or input  # looked up per call, not bound when this module loads
    print(f"\n=== Human checkpoint: {title} ===\n{summary}")
    menu = ["Enter = continue", "g = add guidance"]
    if edit_checklist:
        menu.append("e = replace the definition of done")
    if synthesize:
        menu.append("s = stop debating and synthesize now")
    menu.append("q = stop here and save (resume later)")
    while True:
        choice = input_fn("[" + " | ".join(menu) + "]: ").strip().lower()
        if choice == "":
            return {"action": "continue"}
        if choice == "q":
            return {"action": "stop"}
        if choice == "s" and synthesize:
            return {"action": "synthesize"}
        if choice == "g":
            text = _read_block("Guidance for the agents", input_fn)
            if text:
                return {"action": "continue", "guidance": text}
        elif choice == "e" and edit_checklist:
            text = _read_block("New definition of done, one item per line", input_fn)
            if text:
                items = [line if line.lstrip().startswith("- [") else f"- [ ] {line.lstrip('- ').strip()}"
                         for line in text.splitlines() if line.strip()]
                return {"action": "continue", "checklist": "\n".join(items)}
        else:
            print("Choose one of the listed options.")


def ask_build(command: str | None, files: list[str], input_fn=None) -> dict:
    input_fn = input_fn or input
    print("\n=== Human checkpoint: build verification ===")
    print("Files to write: " + (", ".join(files) or "(none)"))
    print(f"Command to run on this machine: {command or '(none)'}")
    while True:
        answer = input_fn("Run it? [y/N]: ").strip().lower()
        if answer in ("", "n", "no"):
            return {"action": "decline"}
        if answer in ("y", "yes"):
            return {"action": "run"}
        print("Answer y or n.")


def guidance_block(notes: list[str]) -> str:
    if not notes:
        return ""
    return ("\n\nHUMAN GUIDANCE (from the user, who outranks every agent; follow it):\n"
            + "\n".join(f"- {note}" for note in notes))


def round_summary(results: list[dict]) -> str:
    """One line per agent: dropped, or its challenge count, vote, and opening line."""
    lines = []
    for result in results:
        if not result.get("ok"):
            lines.append(f"- {result['name']}: dropped ({result.get('error', '')[:100]})")
            continue
        text = result["answer"]
        votes = re.findall(r"(?mi)^CONSENSUS:\s*(yes|no)\s*$", text)
        opening = next((line.strip() for line in text.splitlines() if line.strip()), "")[:120]
        lines.append(f"- {result['name']}: {text.count('CHALLENGE:')} challenge(s)"
                     + (f", votes {votes[-1]}" if votes else "") + f" — {opening}")
    return "\n".join(lines)
