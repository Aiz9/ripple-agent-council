"""Evaluate answer quality: council vs. single-agent baselines on a case file.

Each case runs as a normal council session in its own subprocess (so cases cannot
interfere), then its final answer is scored with deterministic checks and,
optionally, a blind judge agent that grades against a rubric without being told
which system wrote the answer. Baselines send the same request to one agent in a
single call and are scored identically.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

import yaml

CHECKS = ("contains", "not_contains", "regex", "build_passes")
JUDGE_MARKER = "\nEVALUATION JUDGE."
BASELINE_MARKER = "\nBASELINE REQUEST."


def load_cases(path: Path) -> list[dict]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("an eval file needs a nonempty 'cases' list")
    seen = set()
    for index, case in enumerate(cases, 1):
        if not isinstance(case, dict) or not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
            raise ValueError(f"case {index} needs a nonempty 'prompt'")
        case.setdefault("id", f"case-{index}")
        if case["id"] in seen:
            raise ValueError(f"duplicate case id {case['id']!r}")
        seen.add(case["id"])
        if case.setdefault("type", "auto") not in ("auto", "question", "build"):
            raise ValueError(f"case {case['id']}: type must be auto, question, or build")
        expect = case.setdefault("expect", {})
        if not isinstance(expect, dict) or set(expect) - set(CHECKS):
            raise ValueError(f"case {case['id']}: expect may contain {', '.join(CHECKS)}")
        for key in ("contains", "not_contains", "regex"):
            value = expect.get(key, [])
            expect[key] = [value] if isinstance(value, str) else value
            if not isinstance(expect[key], list) or not all(isinstance(v, str) for v in expect[key]):
                raise ValueError(f"case {case['id']}: expect.{key} must be text or a list of text")
            if key == "regex":
                for pattern in expect[key]:
                    re.compile(pattern)
        if "judge" in case and not (isinstance(case["judge"], str) and case["judge"].strip()):
            raise ValueError(f"case {case['id']}: judge must be a rubric sentence")
    return cases


def score_checks(expect: dict, answer: str, build_status: str | None) -> list[dict]:
    results = []
    lowered = answer.lower()
    for text in expect.get("contains", []):
        results.append({"check": f"contains {text!r}", "ok": text.lower() in lowered})
    for text in expect.get("not_contains", []):
        results.append({"check": f"does not contain {text!r}", "ok": text.lower() not in lowered})
    for pattern in expect.get("regex", []):
        results.append({"check": f"matches /{pattern}/", "ok": re.search(pattern, answer) is not None})
    if "build_passes" in expect:
        results.append({"check": "build passes" if expect["build_passes"] else "build fails",
                        "ok": (build_status == "passed") == bool(expect["build_passes"])})
    return results


def judge_prompt(rubric: str, request: str, answer: str) -> str:
    return (JUDGE_MARKER + " Grade one answer to a user's request against the rubric. You are not told who or "
            "what produced it. Be strict: reward correct, evidenced, complete answers; penalize unsupported "
            "claims and missing requirements. Write a short justification, then end with exactly one line "
            "'SCORE: N' where N is 0-10.\n\nRUBRIC:\n" + rubric + "\n\nUSER REQUEST:\n" + request
            + "\n\nANSWER TO GRADE:\n" + answer)


def parse_score(text: str) -> float | None:
    found = re.findall(r"(?mi)^\s*SCORE:\s*(\d+(?:\.\d+)?)\s*(?:/\s*10)?\s*$", text)
    return min(max(float(found[-1]), 0.0), 10.0) if found else None


def run_council_case(case: dict, config: Path, sessions: Path, timeout: float) -> dict:
    started = time.monotonic()
    proc = subprocess.run([sys.executable, "-m", "agent_council", "--config", str(config), "--sessions-dir",
                           str(sessions), "--non-interactive", "--quiet", "--type", case["type"], case["prompt"]],
                          capture_output=True, text=True, timeout=timeout)
    match = re.findall(r"(?m)^\[council\] Session: (.+)$", proc.stdout)
    session = Path(match[-1].strip()) if match else None
    meta = json.loads((session / "session.json").read_text()) if session and (session / "session.json").exists() else {}
    final = (json.loads((session / "final-answer.json").read_text())
             if session and (session / "final-answer.json").exists() else {})
    return {"system": "council", "session": str(session) if session else None, "exit_code": proc.returncode,
            "status": meta.get("status"), "answer": final.get("text", ""),
            "build": (meta.get("build") or {}).get("status"), "seconds": round(time.monotonic() - started, 2),
            "cost_usd": (meta.get("usage") or {}).get("total_cost_usd"),
            "tokens": (meta.get("usage") or {}).get("total_tokens"), "consensus": meta.get("consensus")}


def evaluate(cases: list[dict], cfg: dict, agents: dict, *, output: Path, baselines: list[str], judge: str | None,
             run_agent, verify_build, build_note: str, timeout: float = 3600) -> dict:
    """Run every case through the council and each baseline; return the summary.
    ``agents`` maps names to validated agent entries usable as baselines or judge."""
    output.mkdir(parents=True, exist_ok=True)
    config = output / "council-config.yaml"
    config.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    for name in baselines + ([judge] if judge else []):
        if name not in agents:
            raise ValueError(f"unknown agent {name!r} for --baseline/--judge")
    rows = []
    for case in cases:
        print(f"[eval] {case['id']}: council", flush=True)
        results = [run_council_case(case, config, output / "sessions", timeout)]
        for name in baselines:
            print(f"[eval] {case['id']}: baseline {name}", flush=True)
            results.append(run_baseline(case, agents[name], output / "baseline" / case["id"], run_agent,
                                        verify_build, build_note))
        for result in results:
            result["checks"] = score_checks(case["expect"], result["answer"], result.get("build"))
            result["passed"] = all(c["ok"] for c in result["checks"]) and bool(result["answer"])
            result["judge_score"] = None
            if judge and case.get("judge"):
                directory = output / "judge" / case["id"] / result["system"].replace(":", "-")
                directory.mkdir(parents=True, exist_ok=True)
                verdict = run_agent(dict(agents[judge]), judge_prompt(case["judge"], case["prompt"], result["answer"]),
                                    directory, directory, "judge")
                result["judge_score"] = parse_score(verdict["answer"]) if verdict["ok"] else None
        rows.append({"case": case["id"], "results": results})
    summary = summarize(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (output / "results.json").write_text(json.dumps({"created": stamp, "cases": rows, "summary": summary},
                                                    indent=2), encoding="utf-8")
    (output / "report.md").write_text(markdown(rows, summary), encoding="utf-8")
    return summary


def run_baseline(case, agent, directory: Path, run_agent, verify_build, build_note: str) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    is_build = case["type"] == "build"
    prompt = (BASELINE_MARKER + " Answer the user's request on your own, with evidence for your claims.\n\n"
              "USER REQUEST:\n" + case["prompt"] + (build_note if is_build else ""))
    with tempfile.TemporaryDirectory(prefix="council-baseline-") as work:
        result = run_agent(dict(agent), prompt, Path(work), directory, "baseline")
    build = None
    if is_build and result["ok"]:
        workspace = directory / "workspace"
        workspace.mkdir(exist_ok=True)
        build = verify_build(result["answer"], workspace, directory)["status"]
    usage = result.get("usage") or {}
    return {"system": f"baseline:{agent['name']}", "status": "completed" if result["ok"] else "failed",
            "answer": result["answer"], "build": build, "seconds": result["seconds"],
            "cost_usd": usage.get("cost_usd"), "tokens": usage.get("total_tokens"), "error": result["error"]}


def summarize(rows: list[dict]) -> dict:
    systems: dict[str, dict] = {}
    for row in rows:
        for result in row["results"]:
            entry = systems.setdefault(result["system"], {"cases": 0, "passed": 0, "scores": [], "seconds": 0.0,
                                                           "cost_usd": None})
            entry["cases"] += 1
            entry["passed"] += int(result["passed"])
            entry["seconds"] = round(entry["seconds"] + (result["seconds"] or 0), 2)
            if result.get("judge_score") is not None:
                entry["scores"].append(result["judge_score"])
            if result.get("cost_usd") is not None:
                entry["cost_usd"] = round((entry["cost_usd"] or 0) + result["cost_usd"], 6)
    for entry in systems.values():
        entry["pass_rate"] = round(entry["passed"] / entry["cases"], 3)
        entry["mean_judge_score"] = round(sum(entry["scores"]) / len(entry["scores"]), 2) if entry["scores"] else None
    return systems


def markdown(rows: list[dict], summary: dict) -> str:
    lines = ["# Council evaluation", "", "| System | Passed | Pass rate | Mean judge score | Time (s) | Reported cost |",
             "| --- | --- | --- | --- | --- | --- |"]
    for system, s in summary.items():
        cost = f"${s['cost_usd']:.4f}" if s["cost_usd"] is not None else "—"
        lines.append(f"| {system} | {s['passed']}/{s['cases']} | {s['pass_rate']:.0%} | "
                     f"{s['mean_judge_score'] if s['mean_judge_score'] is not None else '—'} | {s['seconds']} | {cost} |")
    for row in rows:
        lines += ["", f"## {row['case']}", ""]
        for result in row["results"]:
            failed = [c["check"] for c in result["checks"] if not c["ok"]]
            lines.append(f"- **{result['system']}**: {'pass' if result['passed'] else 'FAIL'}"
                         + (f" (failed: {', '.join(failed)})" if failed else "")
                         + (f"; judge {result['judge_score']}/10" if result.get("judge_score") is not None else "")
                         + f"; {result['seconds']}s")
    return "\n".join(lines) + "\n"


def print_summary(summary: dict, output: Path) -> None:
    print(f"\n{'System':<24}{'Passed':>8}{'Rate':>8}{'Judge':>8}{'Time':>9}")
    for system, s in summary.items():
        judge = f"{s['mean_judge_score']}" if s["mean_judge_score"] is not None else "—"
        print(f"{system:<24}{s['passed']:>4}/{s['cases']:<3}{s['pass_rate']:>8.0%}{judge:>8}{s['seconds']:>8}s")
    print(f"\nResults: {output / 'results.json'}\nReport:  {output / 'report.md'}")
